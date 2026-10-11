"""
Prueba "1xBet + Polymarket con orden LÍMITE" (simulación, no envía nada).

Flujo que se simula, igual que lo haría una persona:
  1. aparece una surebet de prepartido 1xBet + Polymarket apta para la cuenta real;
  2. se apuesta en 1xBet a la cuota de ese momento (esa pata queda fija);
  3. en Polymarket, en vez de comprar al precio publicado (taker, paga comisión), se deja una orden
     límite un centavo por encima del mejor comprador (maker: sin comisión), sin pasar del precio
     máximo que todavía asegura ganancia;
  4. se llena si en algún ciclo posterior el mejor vendedor baja hasta nuestro precio;
  5. si no se llenó al vencer el plazo (MAX_WAIT_S o 10 min antes del inicio), se cubre comprando al
     precio publicado de ese momento, con comisión (puede salir mejor o peor).
Resultado: ganancia neta de cada intento comparada con la de haber comprado al instante.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .arb import POLYMARKET, size_legs
from .models import Opportunity, market_label
from .pricing import fill_detail, poly_cost_per_share  # noqa: F401

MAX_WAIT_S = 15 * 60
TICK = 0.01

SCHEMA = """
CREATE TABLE IF NOT EXISTS maker_sim (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE,
    created_ts REAL, title TEXT, market TEXT, start_ts REAL,
    book_payout REAL,      -- lo que cobra la pata de 1xBet si gana
    book_stake REAL,
    token TEXT, shares REAL,
    taker_cost REAL,       -- costo de la pata de Polymarket comprando al instante (con comisión)
    limit_price REAL,
    status TEXT,           -- waiting | filled | hedged
    done_ts REAL, poly_cost REAL,
    net_pct REAL,          -- ganancia neta asegurada del resultado final
    taker_net_pct REAL     -- la que habría dado comprar al instante
);
"""


class MakerSimulator:
    def __init__(self, db_path: Path, stake: float = 100.0) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.stake = stake

    def step(self, opps: list[Opportunity], poly_quotes: dict, eligible) -> None:
        """`poly_quotes`: token -> Quote de Polymarket de este ciclo (con asks y mejor bid)."""
        now = time.time()
        for o in opps:
            srcs = [l.quote.source for l in o.legs]
            if len(o.legs) != 2 or POLYMARKET not in srcs or not any(s in ("1xbet", "onexbet") for s in srcs):
                continue
            if not eligible(o) or self.conn.execute("SELECT 1 FROM maker_sim WHERE key=?", (o.key,)).fetchone():
                continue
            target = min(self.stake, o.max_total_stake) if o.max_total_stake else self.stake
            legs = size_legs([l.quote for l in o.legs], round(target, 2))
            if not legs:
                continue
            book = next(l for l in legs if l.quote.source != POLYMARKET)
            poly = next(l for l in legs if l.quote.source == POLYMARKET)
            q = poly.quote
            fd = fill_detail(q.asks, poly.stake, q.fee_rate, q.fee_exponent)
            shares = fd["shares"]
            book_payout = book.stake * book.odds
            total = book.stake + poly.stake
            taker_net = 100 * (min(book_payout, shares) / total - 1)
            # Precio máximo sin comisión que todavía deja ganancia con las mismas shares
            max_price = (min(book_payout, shares) - book.stake) / shares - 1e-6
            best_ask = q.asks[0].price if q.asks else 1.0
            bid = q.bid or 0.0
            limit = round(min(max(bid + TICK, TICK), best_ask - TICK, max_price), 2)
            if limit <= 0:
                continue
            start = o.event.anchor.start.timestamp() if o.event.anchor.start else None
            self.conn.execute(
                "INSERT INTO maker_sim (key, created_ts, title, market, start_ts, book_payout, book_stake, token, shares, "
                "taker_cost, limit_price, status, taker_net_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'waiting', ?)",
                (o.key, now, o.event.anchor.title, market_label(o.market), start, round(book_payout, 4),
                 round(book.stake, 2), q.token_id, round(shares, 4), round(poly.stake, 4), limit, round(taker_net, 3)))
        self._advance(poly_quotes, now)
        self.conn.commit()

    def _advance(self, poly_quotes: dict, now: float) -> None:
        rows = self.conn.execute("SELECT id, created_ts, start_ts, book_payout, book_stake, token, shares, limit_price "
                                 "FROM maker_sim WHERE status='waiting'").fetchall()
        for op_id, created, start, payout, bstake, token, shares, limit in rows:
            q = poly_quotes.get(token)
            deadline = min(created + MAX_WAIT_S, (start or created + MAX_WAIT_S) - 600)
            if q and q.asks and q.asks[0].price <= limit + 1e-9:
                cost, status = shares * limit, "filled"              # nos vendieron a nuestro precio: sin comisión
            elif now >= deadline:
                if not q or not q.asks:
                    continue                                      # sin libro a la vista: se reintenta el próximo ciclo
                # no se llenó a tiempo: se compra al precio publicado de ese momento, con comisión
                cost = shares * poly_cost_per_share(q.asks[0].price, q.fee_rate, q.fee_exponent)
                status = "hedged"
            else:
                continue
            total = bstake + cost
            net = 100 * (min(payout, shares) / total - 1)
            self.conn.execute("UPDATE maker_sim SET status=?, done_ts=?, poly_cost=?, net_pct=? WHERE id=?",
                              (status, now, round(cost, 4), round(net, 3), op_id))


def summary(conn) -> dict:
    rows = [dict(zip(("created_ts", "title", "market", "status", "limit_price", "net_pct", "taker_net_pct"), r))
            for r in conn.execute("SELECT created_ts, title, market, status, limit_price, net_pct, taker_net_pct "
                                  "FROM maker_sim ORDER BY created_ts DESC LIMIT 40")]
    agg = conn.execute("SELECT COUNT(*), SUM(status='filled'), SUM(status='hedged'), AVG(net_pct), AVG(taker_net_pct) "
                       "FROM maker_sim WHERE status!='waiting'").fetchone()
    total, filled, hedged, net, taker = agg
    return {"total": total or 0, "filled": filled or 0, "hedged": hedged or 0,
            "avg_net": round(net, 3) if net is not None else None,
            "avg_taker": round(taker, 3) if taker is not None else None, "rows": rows}
