"""
Pruebas "1xBet + Polymarket con orden LÍMITE" (simulación, no envía nada). Dos órdenes posibles:

A) book_first: primero 1xBet, después límite en Polymarket.
  1. aparece una surebet de prepartido 1xBet + Polymarket apta para la cuenta real;
  2. se apuesta en 1xBet a la cuota de ese momento (esa pata queda fija);
  3. en Polymarket, en vez de comprar al precio publicado (taker, paga comisión), se deja una orden
     límite un centavo por encima del mejor comprador (maker: sin comisión), sin pasar del precio
     máximo que todavía asegura ganancia;
  4. se llena si en algún ciclo posterior el mejor vendedor baja hasta nuestro precio;
  5. si no se llenó al vencer el plazo (MAX_WAIT_S o 10 min antes del inicio), se cubre comprando al
     precio publicado de ese momento, con comisión (puede salir mejor o peor).

B) poly_first: primero la límite en Polymarket, y 1xBet solo si se llenó.
  1. misma orden límite, pero sin haber apostado nada todavía;
  2. si no se llena a tiempo se cancela: no se perdió nada (solo la oportunidad);
  3. si se llena, se apuesta en 1xBet en ese mismo ciclo a la cuota que haya EN ESE MOMENTO,
     con el monto que iguala el cobro:
       - si la cuota sigue dejando ganancia → "llenada y cubierta";
       - si bajó (o desapareció), quedamos expuestos: se toma lo menos malo entre apostar igual a la
         cuota nueva o vender lo comprado en Polymarket al mejor comprador (con comisión) → "expuesta".
  Mide justo el riesgo de esta forma: cuando alguien te vende barato en Polymarket suele ser porque
  el partido se movió, y entonces la cuota de 1xBet del otro lado también suele haber bajado.

C) value_net: "valor con red de seguridad".
  1. aparece una apuesta de valor "oro" en 1xBet (se apuesta a mano, sin apuro: dura minutos);
  2. en Polymarket se deja ESPERANDO una orden límite (maker, sin comisión) del lado contrario, al precio que
     asegura VALUE_LOCK_PCT de ganancia pase lo que pase;
  3. como el CLV de estas apuestas es positivo, el precio del lado contrario tiende a bajar hacia nuestra orden:
     si se llena, la apuesta de valor se convirtió en surebet ("asegurada");
  4. si no se llena antes del partido, se cancela y queda la apuesta de valor sola ("se queda como valor").
  Estudio con 81 apuestas oro (oct-2026): con +1 % se llenó ~60 %; comprando al instante (con comisión) solo ~25 %.

D) poly_first AUTOMÁTICO (track "auto", desde 14/10): lo mismo que B pero con las casas que se operan por API
  (SX Bet, Cloudbet, Kalshi, Azuro) en vez de 1xBet. Cuando se llena la límite de Polymarket, el bot le pide a
  la otra casa SU PRECIO DE ESE MOMENTO (fresh_odds) y cubre si todavía hay ganancia; si no, deshace vendiendo
  en Polymarket. Se compara contra comprar al instante (taker_net_pct).

Resultado: ganancia neta de cada intento comparada con la de haber comprado al instante.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

from .arb import POLYMARKET, size_legs
from .models import Opportunity, market_label, market_outcomes
from .pricing import fill_detail, poly_cost_per_share, poly_fee_per_share

MAX_WAIT_S = 15 * 60
TICK = 0.01
MAX_BOOK_AGE_S = 180   # cuota de 1xBet más vieja que esto no sirve para apostar "ya"
BOOK_FIRST, POLY_FIRST, VALUE_NET = "book_first", "poly_first", "value_net"
VALUE_LOCK_PCT = 1.0   # ganancia segura que se busca con la orden límite de la red de seguridad

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
    status TEXT,           -- waiting | filled | hedged | covered | exposed | cancelled
    done_ts REAL, poly_cost REAL,
    net_pct REAL,          -- ganancia neta asegurada del resultado final
    taker_net_pct REAL     -- la que habría dado comprar al instante
);
"""
MIGRATIONS = {
    "mode": "TEXT DEFAULT 'book_first'",
    "book_ref": "TEXT",         # para encontrar la cuota de 1xBet de ese momento (poly_first)
    "book_odds": "REAL",  # cuota de 1xBet al aparecer la surebet
    "book_odds_fill": "REAL",   # cuota de 1xBet cuando se llenó la límite
    "pnl": "REAL",              # ganancia/pérdida en $ (expuestas: la pérdida real)
    "value_key": "TEXT",        # value_net: señal en value_bets (para su CLV)
    "track": "TEXT DEFAULT 'manual'",   # manual = 1xBet + Polymarket; auto = casas con API + Polymarket
    "alt_pnl": "REAL",          # auto: lo que daría deshacer en Polymarket al llenarse la límite
}
AUTO_HOUSES = ("sxbet", "cloudbet", "kalshi", "azuro")


def value_hedge(v, stake: float, lock_pct: float = VALUE_LOCK_PCT) -> dict | None:
    """Red de seguridad de una apuesta de valor: qué comprar en Polymarket, cuántas shares y a qué precio máximo."""
    comp = frozenset(market_outcomes(v.market)) - v.quote.covers
    cands = [q for q in v.event.quotes_for(v.market) if q.source == POLYMARKET and q.covers == comp and q.token_id]
    if not cands:
        return None
    q = max(cands, key=lambda q: q.odds)
    odds = v.quote.odds
    shares = stake * odds                               # mismo cobro que la apuesta de la casa
    limit = math.floor((1 / (1 + lock_pct / 100) - 1 / odds) * 100 + 1e-9) / 100
    ask = q.asks[0].price if q.asks else None
    if ask is not None:
        limit = min(limit, round(ask - TICK, 2))        # una orden límite que espera nunca cruza el libro
    if limit < TICK:
        return None
    return {"quote": q, "token": q.token_id, "label": q.label, "shares": shares, "limit": limit, "ask": ask}


def book_ref(q) -> str:
    return json.dumps([q.source, q.ref or q.url, q.market, q.label])   # las casas con API se identifican por ref


class MakerSimulator:
    def __init__(self, db_path: Path, stake: float = 100.0, cloudbet_key: str = "") -> None:
        self.cloudbet_key = cloudbet_key
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(maker_sim)")}
        for col, decl in MIGRATIONS.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE maker_sim ADD COLUMN {col} {decl}")
        self.conn.commit()
        self.stake = stake

    def step(self, opps: list[Opportunity], poly_quotes: dict, eligible, book_quotes: dict | None = None,
             track: str = "manual", book_sources: tuple = ("1xbet", "onexbet"), modes: tuple = (BOOK_FIRST, POLY_FIRST)) -> None:
        """`poly_quotes`: token -> Quote de Polymarket de este ciclo (con asks y mejor bid).
        `book_quotes`: book_ref -> Quote actual de la otra casa (para la prueba poly_first)."""
        now = time.time()
        prefix = "" if track == "manual" else f"{track}#"
        for o in opps:
            srcs = [l.quote.source for l in o.legs]
            if len(o.legs) != 2 or POLYMARKET not in srcs or not any(s in book_sources for s in srcs):
                continue
            if not eligible(o):
                continue
            for mode in modes:
                key = prefix + (o.key if mode == BOOK_FIRST else f"{o.key}#pf")
                if not self.conn.execute("SELECT 1 FROM maker_sim WHERE key=?", (key,)).fetchone():
                    self._open(o, key, mode, now, track)
        self._advance(poly_quotes, book_quotes or {}, now)
        self.conn.commit()

    def step_value(self, values, golden) -> None:
        """Abre la prueba "valor con red de seguridad" para cada apuesta oro nueva (el avance va en step)."""
        now = time.time()
        for v in values:
            st = v.event.anchor.start
            hrs = (st.timestamp() - now) / 3600 if st else None
            if v.event.live or not golden(False, v.ev_pct, v.quote.odds, hrs):
                continue
            key = "vn#" + v.key
            if self.conn.execute("SELECT 1 FROM maker_sim WHERE key=?", (key,)).fetchone():
                continue
            h = value_hedge(v, self.stake)
            if not h:
                continue
            paper_key = f"value#{v.event.key}#{v.market}#{''.join(sorted(v.quote.covers))}"
            self.conn.execute(
                "INSERT INTO maker_sim (key, created_ts, title, market, start_ts, book_payout, book_stake, token, shares, "
                "limit_price, status, taker_net_pct, mode, book_ref, book_odds, value_key) "
                "VALUES (?,?,?,?,?,?,?,?,?,?, 'waiting', ?,?,?,?,?)",
                (key, now, v.event.anchor.title, f"{market_label(v.market)} · {v.quote.label} → red: {h['label']}",
                 st.timestamp(), round(h["shares"], 4), self.stake, h["token"], round(h["shares"], 4), h["limit"],
                 round(v.ev_pct, 3), VALUE_NET, paper_key, v.quote.odds, v.key))
        self.conn.commit()

    def _open(self, o: Opportunity, key: str, mode: str, now: float, track: str = "manual") -> None:
        target = min(self.stake, o.max_total_stake) if o.max_total_stake else self.stake
        legs = size_legs([l.quote for l in o.legs], round(target, 2))
        if not legs:
            return
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
            return
        start = o.event.anchor.start.timestamp() if o.event.anchor.start else None
        self.conn.execute(
            "INSERT INTO maker_sim (key, created_ts, title, market, start_ts, book_payout, book_stake, token, shares, "
            "taker_cost, limit_price, status, taker_net_pct, mode, book_ref, book_odds, track) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'waiting', ?,?,?,?,?)",
            (key, now, o.event.anchor.title, market_label(o.market), start, round(book_payout, 4),
             round(book.stake, 2), q.token_id, round(shares, 4), round(poly.stake, 4), limit, round(taker_net, 3),
             mode, book_ref(book.quote), book.odds, track))

    def _advance(self, poly_quotes: dict, book_quotes: dict, now: float) -> None:
        rows = self.conn.execute("SELECT id, created_ts, start_ts, book_payout, book_stake, token, shares, limit_price, "
                                 "mode, book_ref, COALESCE(track,'manual') FROM maker_sim WHERE status='waiting'").fetchall()
        for op_id, created, start, payout, bstake, token, shares, limit, mode, ref, track in rows:
            q = poly_quotes.get(token)
            deadline = min(created + MAX_WAIT_S, (start or created + MAX_WAIT_S) - 600)
            filled = bool(q and q.asks and q.asks[0].price <= limit + 1e-9)
            if mode == VALUE_NET:
                if filled:  # el lado contrario bajó hasta nuestra orden: la apuesta de valor quedó asegurada
                    cost = shares * limit
                    gain = min(payout, shares) - (bstake + cost)
                    self.conn.execute("UPDATE maker_sim SET status='locked', done_ts=?, poly_cost=?, net_pct=?, pnl=? "
                                      "WHERE id=?", (now, round(cost, 4), round(100 * gain / (bstake + cost), 3),
                                                     round(gain, 2), op_id))
                elif start and now >= start - 600:  # el partido empieza: se cancela y queda la apuesta de valor
                    self.conn.execute("UPDATE maker_sim SET status='kept', done_ts=? WHERE id=?", (now, op_id))
                continue
            if mode == POLY_FIRST and track == "auto":
                self._fill_auto(op_id, q, filled, now >= deadline, shares, limit, now)
                continue
            if mode == POLY_FIRST:
                self._advance_poly_first(op_id, q, filled, now >= deadline, shares, limit, book_quotes.get(ref), now)
                continue
            if filled:
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
            self.conn.execute("UPDATE maker_sim SET status=?, done_ts=?, poly_cost=?, net_pct=?, pnl=? WHERE id=?",
                              (status, now, round(cost, 4), round(net, 3), round(min(payout, shares) - total, 2), op_id))

    def _fill_auto(self, op_id, q, filled, expired, shares, limit, now) -> None:
        """La límite de Polymarket se llenó (o venció). El resto lo decide finish_auto con el precio real de la otra casa."""
        if not filled:
            if expired:
                self.conn.execute("UPDATE maker_sim SET status='cancelled', done_ts=?, net_pct=0, pnl=0 WHERE id=?",
                                  (now, op_id))
            return
        cost = shares * limit
        alt = None
        if q and q.bid:   # deshacer: vender al mejor comprador pagando comisión de taker
            alt = shares * (q.bid - poly_fee_per_share(q.bid, q.fee_rate, q.fee_exponent)) - cost
        self.conn.execute("UPDATE maker_sim SET status='filling', done_ts=?, poly_cost=?, alt_pnl=? WHERE id=?",
                          (now, round(cost, 4), alt, op_id))

    async def finish_auto(self, client) -> None:
        """Con la límite ya llena, le pide a la otra casa SU precio de ahora y cubre (o deshace) como haría el bot."""
        from .autovivo import fresh_odds
        rows = self.conn.execute("SELECT id, shares, poly_cost, book_stake, book_ref, alt_pnl, done_ts FROM maker_sim "
                                 "WHERE status='filling' ORDER BY done_ts LIMIT 6").fetchall()
        fetched = []
        for op_id, shares, cost, bstake, ref, alt, done in rows:      # primero las consultas, después las escrituras
            src, bref, _m, _l = json.loads(ref)
            fo = await fresh_odds(client, {"source": src, "stake": bstake, "ref": bref}, self.cloudbet_key)
            fetched.append((op_id, shares, cost, alt, done, fo))
        now = time.time()
        for op_id, shares, cost, alt, done, fo in fetched:
            options = []
            if fo:
                stake_b = shares / fo                                 # mismo cobro en los dos resultados
                options.append(("covered", shares - (cost + stake_b), cost + stake_b))
            if alt is not None:
                options.append(("exposed", alt, cost))
            if not options:
                if now - (done or now) < 180:
                    continue                                          # sin datos: se reintenta en el próximo ciclo
                options = [("exposed", -0.05 * cost, cost)]           # sin datos tras 3 min: pérdida supuesta del 5 %
            status, pnl, base = max(options, key=lambda x: x[1])
            if status == "covered" and pnl < 0:
                status = "exposed"
            self.conn.execute("UPDATE maker_sim SET status=?, done_ts=?, net_pct=?, pnl=?, book_odds_fill=? WHERE id=?",
                              (status, now, round(100 * pnl / base, 3), round(pnl, 2), fo, op_id))
        self.conn.commit()

    def _advance_poly_first(self, op_id, q, filled, expired, shares, limit, bq, now) -> None:
        if not filled:
            if expired:  # nunca se llenó: se cancela, no se arriesgó nada
                self.conn.execute("UPDATE maker_sim SET status='cancelled', done_ts=?, net_pct=0, pnl=0 WHERE id=?",
                                  (now, op_id))
            return
        cost = shares * limit
        odds_now = bq.odds if bq and bq.age(now) <= MAX_BOOK_AGE_S else None
        options = []
        if odds_now:
            stake_b = shares / odds_now                       # mismo cobro en los dos resultados
            options.append(("covered", shares - (cost + stake_b), cost + stake_b))
        if q and q.bid:
            # deshacer: vender al mejor comprador, pagando comisión de taker
            proceeds = shares * (q.bid - poly_fee_per_share(q.bid, q.fee_rate, q.fee_exponent))
            options.append(("exposed", proceeds - cost, cost))
        if not options:
            return  # sin datos este ciclo: se decide en el próximo
        status, pnl, base = max(options, key=lambda x: x[1])
        if status == "covered" and pnl < 0:
            status = "exposed"   # apostar igual a la cuota nueva, pero ya con pérdida
        self.conn.execute("UPDATE maker_sim SET status=?, done_ts=?, poly_cost=?, net_pct=?, pnl=?, book_odds_fill=? "
                          "WHERE id=?", (status, now, round(cost, 4), round(100 * pnl / base, 3), round(pnl, 2),
                                         odds_now, op_id))


def _mode_summary(conn, mode: str, track: str = "manual") -> dict:
    rows = [dict(zip(("created_ts", "title", "market", "status", "limit_price", "net_pct", "taker_net_pct",
                      "book_odds", "book_odds_fill", "pnl", "book_ref"), r))
            for r in conn.execute("SELECT created_ts, title, market, status, limit_price, net_pct, taker_net_pct, "
                                  "book_odds, book_odds_fill, pnl, book_ref FROM maker_sim "
                                  "WHERE COALESCE(mode,'book_first')=? AND COALESCE(track,'manual')=? "
                                  "ORDER BY created_ts DESC LIMIT 40", (mode, track))]
    agg = conn.execute(
        "SELECT COUNT(*), SUM(status IN ('filled','covered')), SUM(status='hedged'), SUM(status='exposed'), "
        "SUM(status='cancelled'), AVG(CASE WHEN status!='cancelled' THEN net_pct END), "
        "AVG(CASE WHEN status!='cancelled' THEN taker_net_pct END), SUM(pnl) "
        "FROM maker_sim WHERE status NOT IN ('waiting','filling') AND COALESCE(mode,'book_first')=? "
        "AND COALESCE(track,'manual')=?", (mode, track)).fetchone()
    total, filled, hedged, exposed, cancelled, net, taker, pnl = agg
    return {"total": total or 0, "filled": filled or 0, "hedged": hedged or 0, "exposed": exposed or 0,
            "cancelled": cancelled or 0, "pnl": round(pnl or 0, 2),
            "avg_net": round(net, 3) if net is not None else None,
            "avg_taker": round(taker, 3) if taker is not None else None, "rows": rows}


def auto_summary(conn) -> dict:
    """Maker con las casas automáticas: por casa que acompaña a Polymarket, más el total."""
    base = _mode_summary(conn, POLY_FIRST, "auto")
    by_house: dict[str, dict] = {}
    for src, ref, status, net, taker, pnl in conn.execute(
            "SELECT json_extract(book_ref,'$[0]'), book_ref, status, net_pct, taker_net_pct, pnl FROM maker_sim "
            "WHERE track='auto' AND status NOT IN ('waiting','filling')"):
        h = by_house.setdefault(src, {"house": src, "total": 0, "filled": 0, "exposed": 0, "cancelled": 0, "pnl": 0.0})
        h["total"] += 1
        h["filled"] += status == "covered"
        h["exposed"] += status == "exposed"
        h["cancelled"] += status == "cancelled"
        h["pnl"] += pnl or 0
    base["by_house"] = sorted(({**h, "pnl": round(h["pnl"], 2)} for h in by_house.values()), key=lambda h: -h["total"])
    return base


def summary(conn) -> dict:
    out = {}
    for mode in (BOOK_FIRST, POLY_FIRST):
        out[mode] = _mode_summary(conn, mode)
    out[VALUE_NET] = _value_net_summary(conn)
    # compatibilidad: el panel viejo lee las claves de book_first en la raíz
    return {**out[BOOK_FIRST], **out}


def _value_net_summary(conn) -> dict:
    """Valor con red: aseguradas (ganancia fija) + las que quedaron como valor (resultado real y CLV)."""
    rows = []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    joined = {"paper_ops", "value_bets"} <= tables
    sql = ("SELECT m.created_ts, m.title, m.market, m.status, m.limit_price, m.book_odds, m.taker_net_pct, m.net_pct, "
           "m.pnl, m.book_stake, " + ("p.status, p.pnl, p.total_stake, vb.clv_pct FROM maker_sim m "
                                       "LEFT JOIN paper_ops p ON p.key = m.book_ref "
                                       "LEFT JOIN value_bets vb ON vb.key = m.value_key " if joined else
                                       "NULL, NULL, NULL, NULL FROM maker_sim m ") +
           "WHERE m.mode='value_net' ORDER BY m.created_ts DESC")
    for r in conn.execute(sql):
        (ts, title, market, status, limit, odds, ev, net, pnl, stake, v_status, v_pnl, v_stake, clv) = r
        value_pnl = v_pnl * stake / v_stake if v_pnl is not None and v_stake else None  # escalado al mismo monto
        rows.append({"created_ts": ts, "title": title, "market": market, "status": status, "limit_price": limit,
                     "odds": odds, "ev": ev, "net_pct": net, "pnl": pnl, "stake": stake, "clv": clv,
                     "value_status": v_status, "value_pnl": None if value_pnl is None else round(value_pnl, 2)})
    locked = [r for r in rows if r["status"] == "locked"]
    kept = [r for r in rows if r["status"] == "kept"]
    done = locked + kept
    settled = locked + [r for r in kept if r["value_status"] in ("won", "lost", "void")]
    pnl = sum(r["pnl"] for r in locked) + sum(r["value_pnl"] or 0 for r in kept)
    staked = sum(r["stake"] for r in settled)
    pure = [r for r in done if r["value_status"] in ("won", "lost", "void")]  # mismo set sin red, para comparar
    pure_pnl = sum(r["value_pnl"] or 0 for r in pure)
    clvs = [r["clv"] for r in kept if r["clv"] is not None]
    return {"total": len(done), "locked": len(locked), "kept": len(kept), "waiting": len(rows) - len(done),
            "fill_rate": round(100 * len(locked) / len(done), 1) if done else None,
            "avg_lock": round(sum(r["net_pct"] for r in locked) / len(locked), 2) if locked else None,
            "kept_clv": round(sum(clvs) / len(clvs), 2) if clvs else None,
            "pnl": round(pnl, 2), "roi": round(100 * pnl / staked, 2) if staked else None,
            "pure_roi": round(100 * pure_pnl / sum(r["stake"] for r in pure), 2) if pure else None,
            "rows": rows[:60]}
