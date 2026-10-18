"""
Backtest y sombra de "cubrir en vivo" (objetivo 5): ¿esperar al minuto 15 del partido para cubrir da más plata que
asegurar antes del partido?

Para cada pick de "valor + red" (net_picks: valor oro de 1xBet, SX, Azuro…) cuyo partido ya pasó el minuto 15 se
guarda, con el historial de Polymarket minuto a minuto:
  p_pre: probabilidad de lo apostado justo antes del inicio (el cierre)
  p15:   la misma al minuto 15 (15 min después de la hora de inicio; en fútbol el saque casi siempre es puntual)
y el resultado final sale de net_picks / paper_ops. Sobre los MISMOS picks se comparan:
  A. sin red (solo valor)                 B. red antes del partido (+1 %, la actual: orden límite maker)
  C. cubrir al cierre (taker)             D. cubrir al minuto 15 siempre (taker)
  E. al minuto 15 solo si vamos ganando (lo apostado subió ≥ JUMP: gol a favor); si no, se deja correr
  F. la red actual y, si no se llenó, E   G. la red actual y, si no se llenó, cubrir al minuto 15 siempre
Cubrir a probabilidad p = comprar el lado contrario (las S·O shares que igualan el cobro de la casa) a 1 − p más medio
spread (SLIP) y la comisión taker de Polymarket deportes: deja fijo S·O·(1 − costo) − S salga lo que salga.
Dos columnas: "real" (lo que pasó, con suerte) y "esperado" (sin suerte: lo que queda sin cubrir vale S·(O·p − 1) a la
probabilidad del último momento en que se decidió). Con unos cientos de picks, el esperado es el que manda.
Nunca ejecuta nada: solo mide.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import httpx

from .nettrack import price_path

MIN15 = 15 * 60
BEFORE_S, AFTER_S = 30 * 60, 20 * 60     # ventana de historial: 30 min antes del inicio → minuto 20
NEAR_S = 5 * 60                          # el punto del minuto 15 puede estar hasta 5 min corrido (minutos sin operaciones)
SLIP = 0.01                              # medio spread al cubrir en vivo como taker
FEE_RATE = 0.05                          # comisión taker de Polymarket en deportes (ver pricing.py)
JUMP = 0.08                              # "vamos ganando": lo apostado subió 8 puntos o más (en fútbol, un gol)
PER_RUN, MAX_TRIES, EVERY_S = 20, 3, 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_hedge (
    key TEXT PRIMARY KEY,       -- net_picks.key
    p_pre REAL,                 -- probabilidad de lo apostado al inicio (cierre)
    p15 REAL,                   -- la misma al minuto 15
    status TEXT,                -- ok | pendiente | sin datos
    tries INTEGER DEFAULT 0,
    checked_ts REAL
);
"""


def hedge_cost(q: float) -> float:
    """Costo por share del lado contrario comprado como taker a precio q (medio spread + comisión)."""
    q = min(max(q, 0.0), 1.0)
    return min(1.0, q + SLIP + FEE_RATE * q * (1 - q))


def lock(stake: float, odds: float, p: float) -> float:
    """Ganancia fija al cubrir cuando lo apostado vale p: S·O shares del contrario a (1 − p) con costos."""
    return stake * odds * (1 - hedge_cost(1 - p)) - stake


def points(path: list[tuple[float, float]], start: float) -> tuple[float | None, float | None]:
    """(p al inicio, p al minuto 15) de un camino [(ts, p)]."""
    pre = [p for t, p in path if t <= start]
    near = [(abs(t - (start + MIN15)), p) for t, p in path if abs(t - (start + MIN15)) <= NEAR_S]
    return (pre[-1] if pre else None), (min(near)[1] if near else None)


class LiveHedgeTracker:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.status = "iniciando"

    async def run(self) -> None:
        """Segundo plano: completa los picks de a tandas chicas (suave con Polymarket). Nunca tumba al escáner."""
        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                try:
                    n = await self.update(client)
                    self.status = f"ok · {n} picks revisados"
                except Exception as e:
                    self.status = f"error: {e!r}"[:160]
                await asyncio.sleep(EVERY_S)

    async def update(self, client: httpx.AsyncClient, now: float | None = None) -> int:
        now = now or time.time()
        try:
            rows = self.conn.execute(
                "SELECT n.key, n.start_ts, n.tokens, n.covers, COALESCE(h.tries, 0) FROM net_picks n "
                "LEFT JOIN live_hedge h ON h.key = n.key WHERE n.status != 'sin datos' AND n.start_ts IS NOT NULL "
                "AND n.start_ts + ? < ? AND (h.key IS NULL OR (h.status = 'pendiente' AND h.checked_ts < ?)) "
                "ORDER BY n.start_ts LIMIT ?", (AFTER_S + 120, now, now - 600, PER_RUN)).fetchall()
        except sqlite3.OperationalError:   # todavía no existe net_picks
            return 0
        done = []   # se escribe al final: nunca dejar la base tomada mientras se consulta a Polymarket
        for key, start, tokens, covers, tries in rows:
            path = await price_path(client, json.loads(tokens or "{}"), set(json.loads(covers or "[]")),
                                    start - BEFORE_S, start + AFTER_S)
            pre, p15 = points(path, start)
            if pre is not None and p15 is not None:
                done.append((key, pre, p15, "ok", tries + 1, now))
            else:
                done.append((key, None, None, "sin datos" if tries + 1 >= MAX_TRIES else "pendiente", tries + 1, now))
        self.conn.executemany("INSERT OR REPLACE INTO live_hedge (key, p_pre, p15, status, tries, checked_ts) "
                              "VALUES (?,?,?,?,?,?)", done)
        self.conn.commit()
        return len(rows)


STRATEGIES = [
    ("A", "Sin red (solo valor)"),
    ("B", "Red antes del partido +1 % (la actual)"),
    ("C", "Cubrir al cierre (taker, justo antes del inicio)"),
    ("D", "Esperar y cubrir al minuto 15 siempre"),
    ("E", "Minuto 15 solo si vamos ganando (si no, dejar correr)"),
    ("F", "Red actual y, si no se llenó, minuto 15 si vamos ganando"),
    ("G", "Red actual y, si no se llenó, minuto 15 siempre"),
]


def evaluate(rows: list[dict]) -> list[dict]:
    """Cada estrategia sobre los mismos picks: (real, esperado, cubiertas). rows: p_pre, p15, stake, odds, red_locked,
    red_pnl (resultado de la red actual) y no_net (resultado real sin red)."""
    out = {k: {"id": k, "name": n, "picks": 0, "hedged": 0, "real": 0.0, "exp": 0.0, "staked": 0.0, "worst": 0.0}
           for k, n in STRATEGIES}

    def add(k: str, real: float, exp: float, hedged: bool, stake: float) -> None:
        s = out[k]
        s["picks"] += 1
        s["hedged"] += hedged
        s["real"] += real
        s["exp"] += exp
        s["staked"] += stake
        s["worst"] = min(s["worst"], real)

    for r in rows:
        S, O, pre, p15 = r["stake"], r["odds"], r["p_pre"], r["p15"]
        up = p15 - pre >= JUMP
        loose_exp15 = S * (O * p15 - 1)           # sin cubrir, valuado con lo que se sabe al minuto 15
        add("A", r["no_net"], S * (O * pre - 1), False, S)
        red_exp = r["red_pnl"] if r["red_locked"] else S * (O * pre - 1)
        add("B", r["red_pnl"], red_exp, r["red_locked"], S)
        add("C", lock(S, O, pre), lock(S, O, pre), True, S)
        add("D", lock(S, O, p15), lock(S, O, p15), True, S)
        e = (lock(S, O, p15),) * 2 if up else (r["no_net"], loose_exp15)
        add("E", *e, up, S)
        if r["red_locked"]:
            add("F", r["red_pnl"], r["red_pnl"], True, S)
            add("G", r["red_pnl"], r["red_pnl"], True, S)
        else:
            add("F", *e, up, S)
            add("G", lock(S, O, p15), lock(S, O, p15), True, S)
    return [{**s, "real": round(s["real"], 2), "exp": round(s["exp"], 2), "worst": round(s["worst"], 2),
             "real_roi": round(100 * s["real"] / s["staked"], 2) if s["staked"] else None,
             "exp_roi": round(100 * s["exp"] / s["staked"], 2) if s["staked"] else None,
             "hedge_rate": round(100 * s["hedged"] / s["picks"], 1) if s["picks"] else None}
            for s in out.values()]


def _block(rows: list[dict]) -> dict:
    moves = [r["p15"] - r["p_pre"] for r in rows]
    strat = evaluate(rows)
    by = {s["id"]: s for s in strat}
    verdict = None
    if rows:
        now_best = max((by["B"], by["C"]), key=lambda s: s["exp"])
        wait_best = max((by[k] for k in "DEFG"), key=lambda s: s["exp"])
        diff = wait_best["exp"] - now_best["exp"]
        verdict = (f"Esperar al minuto 15 (mejor variante: {wait_best['id']}) da {'+' if diff >= 0 else '−'}${abs(diff):,.2f} "
                   f"esperados vs asegurar antes del partido ({now_best['id']}) en {len(rows)} picks: "
                   + ("MÁS plata." if diff > 0 else "MENOS plata: conviene seguir asegurando antes."))
    return {"picks": len(rows), "strategies": strat, "verdict": verdict,
            "avg_move": round(100 * sum(moves) / len(moves), 2) if moves else None,
            "up": sum(m >= JUMP for m in moves), "down": sum(m <= -JUMP for m in moves)}


def summary(conn) -> dict | None:
    try:
        counts = dict(conn.execute("SELECT status, COUNT(*) FROM live_hedge GROUP BY status").fetchall())
        cols = ("p_pre", "p15", "stake", "odds", "status", "pnl", "pnl_no_net", "sport", "title", "start_ts", "house")
        rows = [dict(zip(cols, r)) for r in conn.execute(
            "SELECT h.p_pre, h.p15, n.stake, n.odds, n.status, n.pnl, n.pnl_no_net, n.sport, n.title, n.start_ts, n.house "
            "FROM live_hedge h JOIN net_picks n ON n.key = h.key WHERE h.status = 'ok' AND COALESCE(n.skipped, 0) = 0 "
            "AND n.result NOT IN ('pendiente', 'sin datos') AND n.pnl IS NOT NULL AND n.pnl_no_net IS NOT NULL")]
    except sqlite3.OperationalError:
        return None
    for r in rows:
        r.update(red_locked=r["status"] == "asegurada", red_pnl=r["pnl"], no_net=r["pnl_no_net"])
    soccer = [r for r in rows if (r["sport"] or "").startswith("soccer")]
    recent = sorted(rows, key=lambda r: -(r["start_ts"] or 0))[:60]
    return {"counts": counts, "jump": JUMP, "slip": SLIP, "all": _block(rows), "soccer": _block(soccer),
            "rows": [{"title": r["title"], "house": r["house"], "start_ts": r["start_ts"], "odds": r["odds"],
                      "stake": r["stake"], "p_pre": r["p_pre"], "p15": r["p15"], "red": r["status"],
                      "no_net": r["no_net"], "red_pnl": r["red_pnl"],
                      "lock_pre": round(lock(r["stake"], r["odds"], r["p_pre"]), 2),
                      "lock15": round(lock(r["stake"], r["odds"], r["p15"]), 2)} for r in recent]}
