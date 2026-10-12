"""
Ejecución automática en MODO SIMULACIÓN (nunca envía nada): Cloudbet / SX Bet por su API oficial
y Polymarket por su API. Sirve para medir si un bot llegaría a tiempo antes de arriesgar dinero.

Por cada surebet de prepartido apta para la cuenta real cuyas patas son TODAS automatizables:
  1. arma las órdenes exactas que se mandarían (payloads de cada API) con sus límites:
     cuota mínima en la casa / precio máximo por share en Polymarket;
  2. espera la latencia real de ejecución (LATENCY_S) y vuelve a mirar los precios del ciclo actual;
  3. si la surebet sigue en pie y cada pata respeta su límite al monto planeado -> "llenada",
     si no -> "rechazada" con el motivo (ya no existe, la cuota bajó, el libro no alcanza).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .alerts import _max_poly_price, _min_odds
from .arb import POLYMARKET, best_stake, size_legs
from .models import Opportunity, market_label
from .pricing import effective_odds, fill_detail

AUTOMATABLE = {POLYMARKET, "cloudbet", "sxbet"}
LATENCY_S = 5.0        # Cloudbet (~1-2 s) + Polymarket (1-3 s de demora en deportes) + red
EXPIRE_S = 90.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS exec_sim (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE,
    created_ts REAL,
    title TEXT,
    market TEXT,
    sources TEXT,
    margin_pct REAL,          -- margen al detectar
    stake REAL,
    plan_json TEXT,           -- órdenes que se habrían enviado
    status TEXT,              -- pending | filled | rejected
    reason TEXT,
    checked_ts REAL,
    net_pct REAL,             -- ganancia neta asegurada con los precios del momento de ejecutar
    book_max REAL             -- lo que aguantaba el libro al detectar (monto con margen promedio >= 0,5 %)
);
"""


def _payload(q, stake: float, odds: float, min_odds: float) -> dict:
    if q.source == POLYMARKET:
        fd = fill_detail(q.asks, stake, q.fee_rate, q.fee_exponent)
        return {"api": "polymarket CLOB POST /order", "tokenID": q.token_id, "side": "BUY", "orderType": "FOK",
                "price": round(_max_poly_price(min_odds, q.fee_rate, q.fee_exponent), 4),
                "size": round(fd["shares"], 2)}
    if q.source == "cloudbet":
        event_id, _, market_url = q.ref.partition("|")
        return {"api": "cloudbet POST /pub/v3/bets/place", "referenceId": str(uuid.uuid4()), "eventId": event_id,
                "marketUrl": market_url, "price": f"{min_odds:.3f}", "stake": f"{stake:.2f}", "currency": "USDT",
                "acceptPriceChange": "BETTER"}
    if q.source == "sxbet":
        market_hash, _, side = q.ref.partition("|")
        return {"api": "sxbet POST /orders-v3 (taker, FOK)", "marketHash": market_hash, "outcome": side,
                "stake": round(stake, 2), "worstOdds": round(min_odds, 3)}
    return {"api": q.source}


class ExecSimulator:
    def __init__(self, db_path: Path, stake: float = 100.0, cash_fn=None) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        if "book_max" not in {r[1] for r in self.conn.execute("PRAGMA table_info(exec_sim)")}:
            self.conn.execute("ALTER TABLE exec_sim ADD COLUMN book_max REAL")
        self.stake = stake          # tope por surebet; se usa lo que aguante el libro hasta este monto
        self.cash_fn = cash_fn      # plata libre de la cuenta automática (no se pone más de la mitad en una sola)

    def step(self, opps: list[Opportunity], eligible, on_fill=None) -> None:
        """Llamado en cada ciclo: `eligible(o)` dice si la surebet entraría en la cuenta real;
        `on_fill(o, plan)` anota en la cuenta automática las que se habrían llenado."""
        now = time.time()
        current = {o.key: o for o in opps}
        self._check(current, now, on_fill)
        for o in opps:
            if not all(l.quote.source in AUTOMATABLE for l in o.legs) or not eligible(o):
                continue
            if self.conn.execute("SELECT 1 FROM exec_sim WHERE key=?", (o.key,)).fetchone():
                continue
            target = min(self.stake, o.max_total_stake) if o.max_total_stake else self.stake
            if self.cash_fn:
                target = min(target, max(0.0, self.cash_fn()) * 0.5)
            if target < 5:
                continue
            quotes = [l.quote for l in o.legs]
            target = best_stake(quotes, target) or target   # el monto que deja más ganancia, no siempre el tope
            legs = size_legs(quotes, round(target, 2))
            if not legs:
                continue
            plan = []
            for i, l in enumerate(legs):
                mo = _min_odds(legs, i)
                plan.append({"source": l.quote.source, "label": l.quote.label, "covers": sorted(l.quote.covers),
                             "stake": round(l.stake, 2), "odds": round(l.odds, 4), "min_odds": round(mo, 4),
                             "order": _payload(l.quote, l.stake, l.odds, mo)})
            self.conn.execute(
                "INSERT INTO exec_sim (key, created_ts, title, market, sources, margin_pct, stake, plan_json, status, "
                "book_max) VALUES (?,?,?,?,?,?,?,?, 'pending', ?)",
                (o.key, now, o.event.anchor.title, market_label(o.market),
                 " + ".join(sorted({l.quote.source for l in legs})), o.margin_pct,
                 round(sum(l.stake for l in legs), 2), json.dumps(plan, ensure_ascii=False),
                 round(o.max_total_stake, 2) if o.max_total_stake else None))
        self.conn.commit()

    def _check(self, current: dict[str, Opportunity], now: float, on_fill=None) -> None:
        rows = self.conn.execute("SELECT id, key, created_ts, plan_json FROM exec_sim WHERE status='pending'").fetchall()
        for op_id, key, created, plan_json in rows:
            if now - created < LATENCY_S:
                continue
            o = current.get(key)
            status, reason, net = "rejected", "", None
            if o is None:
                reason = "la surebet ya no existía al ejecutar (precios movidos)"
            else:
                plan = json.loads(plan_json)
                by_cov = {tuple(sorted(l.quote.covers)): l.quote for l in o.legs}
                pays, total, bad = [], 0.0, []
                for p in plan:
                    q = by_cov.get(tuple(p["covers"]))
                    odds = effective_odds(q, p["stake"]) if q else None
                    if not odds or odds < p["min_odds"] - 1e-9:
                        bad.append(f"{p['source']}: cuota {odds or 0:.3f} < mínima {p['min_odds']:.3f}")
                        continue
                    pays.append(p["stake"] * odds)
                    total += p["stake"]
                if bad:
                    reason = "; ".join(bad)
                else:
                    status, net = "filled", round(100 * (min(pays) / total - 1), 3)
                    reason = "las dos patas se habrían llenado dentro de sus límites"
                    if on_fill:
                        on_fill(o, plan)
            if status == "filled" or now - created >= LATENCY_S:
                self.conn.execute("UPDATE exec_sim SET status=?, reason=?, checked_ts=?, net_pct=? WHERE id=?",
                                  (status, reason, now, net, op_id))
        self.conn.execute("UPDATE exec_sim SET status='rejected', reason='sin revisar a tiempo' "
                          "WHERE status='pending' AND created_ts < ?", (now - EXPIRE_S,))
