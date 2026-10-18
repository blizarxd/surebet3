"""
Valor automático en SX Bet, EN SOMBRA (nunca envía nada): ¿el oro de SX es real o es cuota vieja?

El escáner refresca los libros de SX cada 5 min para partidos a 1–6 h y la apuesta de valor usa solo el mejor nivel;
el automático de surebets vio que al releer el libro la cuota de SX salía ~5 % más baja (15 de 20 intentos). Esta
sombra hace lo que haría el bot de verdad con cada señal de valor ORO (de cualquier casa):
  1. busca la misma selección en SX (mismo mercado y lo que cubre); si no está → "sin SX" (queda el aviso manual);
  2. pasados LATENCY_S pide el libro de SX en ese momento y calcula la cuota real al monto (Kelly/4, mínimo
     VALUE_MIN_TOTAL entre casa y red, como valor + red), con la comisión de SX;
  3. si con esa cuota el EV contra Polymarket sigue ≥ MIN_EV → "llenada" (el bot apostaría solo en SX);
     si no → "rechazada" con el motivo, y la señal queda para 1xBet a mano (si era de 1xBet) o se pierde.
De las llenadas se mide el CLV con la cuota REAL (contra el cierre de Polymarket) y el resultado del partido.
También se guarda cuánto aguantaba el libro de SX con EV ≥ MIN_EV (para saber hasta qué monto se puede crecer).
La red de Polymarket iría igual que hoy (orden límite del lado contrario): su efecto ya se mide en valor + red.
Aprobado para probar en real si: ≥ 100 llenadas, se llena ≥ 50 % de las señales con SX y CLV real ≥ +1,5 %.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import httpx

from .nettrack import price_path
from .pricing import fill_detail
from .sources.sxbet import API as SX_API, book_levels
from .staking import net_limit, value_stake

LATENCY_S = 5.0
MIN_EV = 2.0
EXPIRE_S = 120.0
PER_VERIFY = 8
SETTLE_EVERY_S = 300.0
EARLY_PRICE, EARLY_AFTER_S = 0.997, 2 * 3600     # partido decidido: como en paper.py
CLOB = "https://clob.polymarket.com"
MANUAL = {"1xbet", "onexbet"}
APPROVE = {"fills": 100, "fill_rate": 50.0, "clv": 1.5}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sx_shadow (
    key TEXT PRIMARY KEY,       -- partido#mercado#lo que cubre (una fila por selección)
    created_ts REAL, start_ts REAL, title TEXT, sport TEXT, market TEXT, label TEXT, covers TEXT,
    signal_source TEXT, signal_odds REAL, signal_ev REAL,   -- la señal oro que la disparó (casa, cuota, EV)
    fair_prob REAL,             -- probabilidad justa de Polymarket al detectar
    sx_ref TEXT,                -- marketHash|lado
    sx_seen_odds REAL,          -- cuota de SX que tenía el escáner
    sx_seen_age_s REAL,         -- antigüedad de ese libro de SX al detectar
    stake REAL,
    status TEXT,                -- pendiente | llenada | rechazada | sin SX
    reason TEXT,
    checked_ts REAL,
    fill_odds REAL,             -- cuota real al monto, leída en SX al ejecutar (neta de comisión)
    ev_real REAL,               -- EV con esa cuota contra Polymarket
    capacity REAL,              -- USDC que aguantaba el libro con EV ≥ MIN_EV
    tokens TEXT, conds TEXT,    -- Polymarket {resultado: token / condition_id} (CLV y resultado)
    p_close REAL, clv_real REAL,
    result TEXT,                -- pendiente | ganada | perdida | anulada
    pnl REAL,                   -- sin red
    last_check REAL
);
"""


def ev_at(odds: float, fair: float) -> float:
    return 100 * (odds * fair - 1)


def capacity(levels, fair: float, min_ev: float = MIN_EV) -> float:
    """USDC que se pueden apostar en SX con EV ≥ min_ev (niveles de mejor a peor cuota)."""
    return sum(l.price * l.size for l in sorted(levels, key=lambda l: l.price) if ev_at(1 / l.price, fair) >= min_ev)


def fill_odds(levels, stake: float) -> float | None:
    fd = fill_detail(tuple(sorted(levels, key=lambda l: l.price)), stake, 0.0)
    return fd["shares"] / fd["spent"] if fd["spent"] >= stake * 0.999 else None


class SXShadow:
    def __init__(self, db_path: Path, bankroll: float = 1000.0, min_total: float = 30.0) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.bankroll, self.min_total = bankroll, min_total
        self._last_settle = 0.0

    # ------------------------------------------------------------------ cada ciclo
    def step(self, values, is_gold) -> int:
        """Anota cada selección con señal oro nueva (la de mejor EV del ciclo) y su par en SX si existe."""
        now = time.time()
        rows = []
        seen: set[str] = set()
        for v in sorted(values, key=lambda v: v.ev_pct, reverse=True):
            st = v.event.anchor.start
            if st is None or not is_gold(v.event.live, v.ev_pct, v.quote.odds, (st.timestamp() - now) / 3600):
                continue
            covers = "".join(sorted(v.quote.covers))
            key = f"{v.event.key}#{v.market}#{covers}"
            if key in seen or self.conn.execute("SELECT 1 FROM sx_shadow WHERE key=?", (key,)).fetchone():
                continue
            seen.add(key)
            sx = next((q for q in v.event.quotes_for(v.market)
                       if q.source == "sxbet" and q.covers == v.quote.covers and q.ref), None)
            stake = None
            if sx:
                ev_sx = max(ev_at(sx.odds, v.fair_prob), MIN_EV)
                stake = round(value_stake(sx.odds, ev_sx, self.bankroll, min_total=self.min_total,
                                          limit=net_limit(sx.odds)), 2)
            rows.append((key, now, st.timestamp(), v.event.anchor.title, v.event.anchor.sport, v.market, v.quote.label,
                         covers, v.quote.source, round(v.quote.odds, 4), round(v.ev_pct, 3), round(v.fair_prob, 5),
                         sx.ref if sx else None, round(sx.odds, 4) if sx else None,
                         round(now - sx.ts, 1) if sx else None, stake,
                         "pendiente" if sx else "sin SX",
                         None if sx else "SX no tiene esta selección: queda el aviso manual",
                         json.dumps(v.poly_tokens), json.dumps(v.poly_conditions)))
        self.conn.executemany(
            "INSERT OR IGNORE INTO sx_shadow (key, created_ts, start_ts, title, sport, market, label, covers, signal_source, "
            "signal_odds, signal_ev, fair_prob, sx_ref, sx_seen_odds, sx_seen_age_s, stake, status, reason, tokens, conds) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.execute("UPDATE sx_shadow SET status='rechazada', reason='sin revisar a tiempo' "
                          "WHERE status='pendiente' AND created_ts < ?", (now - EXPIRE_S,))
        self.conn.commit()
        return len(rows)

    async def verify(self, client: httpx.AsyncClient) -> int:
        """Pasada la latencia, el libro de SX en ese momento: cuota real al monto, EV y cuánto aguanta."""
        now = time.time()
        rows = self.conn.execute(
            "SELECT key, sx_ref, stake, fair_prob, signal_source FROM sx_shadow WHERE status='pendiente' "
            "AND created_ts <= ? ORDER BY created_ts LIMIT ?", (now - LATENCY_S, PER_VERIFY)).fetchall()
        fetched = []
        for key, ref, stake, fair, signal in rows:      # primero todas las consultas, después las escrituras
            market_hash, _, side = ref.partition("|")
            try:
                r = await client.get(f"{SX_API}/orderbook-v3/snapshot", timeout=8,
                                     params={"marketHash": market_hash, "showTakerPerspective": "true"})
                data = (r.json() or {}).get("data") or {} if r.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                data = None
            fetched.append((key, side, stake, fair, signal, data))
        updates = []
        for key, side, stake, fair, signal, data in fetched:
            manual = " · queda el aviso manual de 1xBet" if signal in MANUAL else ""
            if data is None:
                updates.append(("rechazada", "no se pudo leer el libro de SX" + manual, None, None, None, None, now, key))
                continue
            levels = book_levels(data.get("outcomeOne" if side == "1" else "outcomeTwo") or [])
            odds = fill_odds(levels, stake)
            cap = round(capacity(levels, fair), 2)
            if odds is None:
                updates.append(("rechazada", f"el libro de SX no llega a ${stake:,.2f}" + manual, None, None, cap, None,
                                now, key))
                continue
            ev = ev_at(odds, fair)
            if ev < MIN_EV:
                updates.append(("rechazada", f"cuota real {odds:.3f}: EV {ev:+.2f} % < {MIN_EV:g} %" + manual,
                                round(odds, 4), round(ev, 3), cap, None, now, key))
                continue
            updates.append(("llenada", f"SX aceptaba ${stake:,.2f} a {odds:.3f} (EV {ev:+.2f} %)", round(odds, 4),
                            round(ev, 3), cap, "pendiente", now, key))
        self.conn.executemany("UPDATE sx_shadow SET status=?, reason=?, fill_odds=?, ev_real=?, capacity=?, result=?, "
                              "checked_ts=? WHERE key=?", updates)
        self.conn.commit()
        return len(rows)

    async def settle(self, client: httpx.AsyncClient, every_s: float = SETTLE_EVERY_S) -> int:
        """CLV con la cuota real (cierre de Polymarket) y resultado del partido de las llenadas."""
        now = time.time()
        if now - self._last_settle < every_s:
            return 0
        self._last_settle = now
        rows = self.conn.execute(
            "SELECT key, start_ts, covers, fill_odds, stake, tokens, conds, p_close FROM sx_shadow WHERE status='llenada' "
            "AND result='pendiente' AND start_ts < ? AND (last_check IS NULL OR last_check < ?) "
            "ORDER BY COALESCE(last_check, 0) LIMIT 30", (now - 120, now - 600)).fetchall()
        markets: dict[str, dict | None] = {}
        prepared = []
        for key, start, covers, odds, stake, tokens_json, conds_json, p_close in rows:
            tokens, conds = json.loads(tokens_json or "{}"), json.loads(conds_json or "{}")
            if p_close is None:
                path = await price_path(client, tokens, set(covers), start - 3 * 3600, start)
                p_close = path[-1][1] if path else None
            if now - start >= 45 * 60:
                for cid in set(conds.values()):
                    if cid and cid not in markets:
                        try:
                            r = await client.get(f"{CLOB}/markets/{cid}", timeout=15)
                            markets[cid] = r.json() if r.status_code == 200 else None
                        except (httpx.HTTPError, ValueError):
                            markets[cid] = None
            prepared.append((key, start, covers, odds, stake, tokens, conds, p_close))
        for key, start, covers, odds, stake, tokens, conds, p_close in prepared:
            outcome, void, decided = _outcome(tokens, conds, markets, now - start >= EARLY_AFTER_S)
            result = pnl = None
            if decided:
                result = "anulada" if void else ("ganada" if outcome in covers else "perdida")
                pnl = 0.0 if void else round(stake * (odds - 1) if outcome in covers else -stake, 2)
            self.conn.execute(
                "UPDATE sx_shadow SET p_close=?, clv_real=?, result=COALESCE(?, result), pnl=COALESCE(?, pnl), "
                "last_check=? WHERE key=?",
                (p_close, round(100 * (odds * p_close - 1), 3) if p_close else None, result, pnl, now, key))
        self.conn.commit()
        return len(rows)


def _outcome(tokens: dict, conds: dict, markets: dict, early: bool) -> tuple[str | None, bool, bool]:
    """(resultado ganador, anulado, decidido) con los mercados de Polymarket (como paper.settle)."""
    outcome, void, all_closed = None, False, bool(conds)
    for out, cid in conds.items():
        m = markets.get(cid)
        tok = str(tokens.get(out))
        if m and not m.get("closed") and early:
            px = next((float(t.get("price") or 0) for t in m.get("tokens") or [] if str(t.get("token_id")) == tok), 0.0)
            if px >= EARLY_PRICE:
                return out, False, True
        if not m or not m.get("closed"):
            all_closed = False
            continue
        toks = m.get("tokens") or []
        if any(str(t.get("token_id")) == tok and t.get("winner") for t in toks):
            outcome = out
        elif not any(t.get("winner") for t in toks):
            void = True
    return outcome, void and outcome is None, outcome is not None or (all_closed and void)


def summary(conn) -> dict | None:
    try:
        cols = ("key", "created_ts", "start_ts", "title", "sport", "market", "label", "signal_source", "signal_odds",
                "signal_ev", "fair_prob", "sx_seen_odds", "sx_seen_age_s", "stake", "status", "reason", "fill_odds",
                "ev_real", "capacity", "clv_real", "result", "pnl")
        rows = [dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {', '.join(cols)} FROM sx_shadow ORDER BY created_ts DESC")]
    except sqlite3.OperationalError:
        return None
    done = [r for r in rows if r["status"] != "pendiente"]
    with_sx = [r for r in done if r["status"] != "sin SX"]
    fills = [r for r in with_sx if r["status"] == "llenada"]
    clvs = sorted(r["clv_real"] for r in fills if r["clv_real"] is not None)
    settled = [r for r in fills if r["result"] in ("ganada", "perdida", "anulada")]
    drops = [100 * (1 - r["fill_odds"] / r["sx_seen_odds"]) for r in with_sx if r["fill_odds"] and r["sx_seen_odds"]]
    ages = sorted(r["sx_seen_age_s"] for r in with_sx if r["sx_seen_age_s"] is not None)
    caps = sorted(r["capacity"] for r in fills if r["capacity"] is not None)
    fill_rate = round(100 * len(fills) / len(with_sx), 1) if with_sx else None
    clv = round(sum(clvs) / len(clvs), 2) if clvs else None
    reasons: dict[str, int] = {}
    for r in with_sx:
        if r["status"] == "rechazada":
            k = (r["reason"] or "").split(":")[0].split(" · ")[0]
            k = "cuota real con EV menor al mínimo" if k.startswith("cuota real") else k
            k = "el libro no llega al monto" if k.startswith("el libro de SX no llega") else k
            reasons[k] = reasons.get(k, 0) + 1

    def by(field: str) -> list[dict]:
        out: dict[str, dict] = {}
        for r in with_sx:
            g = out.setdefault(r[field] or "?", {"name": r[field] or "?", "n": 0, "fills": 0, "clv": []})
            g["n"] += 1
            g["fills"] += r["status"] == "llenada"
            if r["status"] == "llenada" and r["clv_real"] is not None:
                g["clv"].append(r["clv_real"])
        return [{"name": g["name"], "n": g["n"], "fills": g["fills"], "fill_rate": round(100 * g["fills"] / g["n"], 1),
                 "clv": round(sum(g["clv"]) / len(g["clv"]), 2) if g["clv"] else None, "clv_n": len(g["clv"])}
                for g in sorted(out.values(), key=lambda g: -g["n"])]

    staked = sum(r["stake"] for r in settled)
    ok = (len(fills) >= APPROVE["fills"] and fill_rate is not None and fill_rate >= APPROVE["fill_rate"]
          and clv is not None and clv >= APPROVE["clv"])
    return {
        "signals": len(done), "with_sx": len(with_sx), "no_sx": len(done) - len(with_sx), "fills": len(fills),
        "pending": len(rows) - len(done), "fill_rate": fill_rate,
        "ev_seen": round(sum(ev_at(r["sx_seen_odds"], r["fair_prob"]) for r in with_sx) / len(with_sx), 2) if with_sx else None,
        "ev_real": round(sum(r["ev_real"] for r in fills) / len(fills), 2) if fills else None,
        "drop_pct": round(sorted(drops)[len(drops) // 2], 2) if drops else None,
        "age_med_s": ages[len(ages) // 2] if ages else None,
        "cap_med": caps[len(caps) // 2] if caps else None,
        "clv": clv, "clv_med": clvs[len(clvs) // 2] if clvs else None, "clv_n": len(clvs),
        "clv_pos": round(100 * sum(c > 0 for c in clvs) / len(clvs)) if clvs else None,
        "settled": len(settled), "pnl": round(sum(r["pnl"] or 0 for r in settled), 2),
        "roi": round(100 * sum(r["pnl"] or 0 for r in settled) / staked, 2) if staked else None,
        "reasons": sorted(({"reason": k, "n": v} for k, v in reasons.items()), key=lambda x: -x["n"]),
        "by_signal": by("signal_source"), "by_sport": by("sport"),
        "approve": {**APPROVE, "ok": ok}, "rows": rows[:150],
    }
