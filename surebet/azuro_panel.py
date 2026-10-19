"""
Pestaña "🟦 Azuro" del panel: todo lo que se mide del valor en Azuro, a $30 por pick (lo decidido para el automático).

  - Valor simple (sin red): la cuenta de valor oro (paper_ops track 'valor') con las apuestas de Azuro, recalculadas a
    $30 fijos: día por día (hora de Colombia), acumulado, esperado por CLV y pick por pick.
  - Con red vs sin red: los mismos picks de "valor + red" (net_picks) en Azuro, a $30.
  - ¿Qué limita la cantidad de picks?: CLV de todas las señales de valor de Azuro por EV, cuota y horas antes del inicio.
CS2 y Dota2 se muestran (observación) pero no cuentan en "lo que haría el bot".
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

STAKE = 30.0
COL = timezone(timedelta(hours=-5))          # Colombia, sin horario de verano
OBSERVED = ("esports:cs2", "esports:dota2")
RARE_CLV = 15.0                              # CLV más allá de ±15 %: casi siempre un dato raro (cuenta con la mediana)
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _day(ts: float) -> str:
    d = datetime.fromtimestamp(ts, COL)
    return f"{DIAS[d.weekday()]} {d.day:02d}/{d.month:02d}"


def _bucket(x: float, edges: list[float]) -> str:
    for lo, hi in zip(edges, edges[1:]):
        if lo <= x < hi:
            return f"{lo:g}–{hi:g}"
    return f"{edges[-1]:g}+" if x >= edges[-1] else f"<{edges[0]:g}"


def _clv_stats(v: list[float]) -> dict:
    v = sorted(v)
    if not v:
        return {"n": 0, "median": None, "mean": None, "pos": None}
    trimmed = [x for x in v if abs(x) <= RARE_CLV]
    return {"n": len(v), "median": round(v[len(v) // 2], 2), "mean": round(statistics.mean(trimmed), 2) if trimmed else None,
            "pos": round(100 * sum(x > 0 for x in v) / len(v))}


def summary(conn) -> dict | None:
    try:
        clv = {k: c for k, c in conn.execute("SELECT key, clv_pct FROM value_bets WHERE clv_pct IS NOT NULL")}
        ops = conn.execute("SELECT event_key, market, market_name, legs_json, status, sport, created_ts, start_ts, title, "
                           "edge_pct FROM paper_ops WHERE track='valor' ORDER BY created_ts").fetchall()
    except sqlite3.OperationalError:
        return None
    picks = []
    for ek, mk, mname, legs, status, sport, ts, start, title, ev in ops:
        leg = (json.loads(legs) or [{}])[0]
        if leg.get("source") != "azuro":
            continue
        odds = leg.get("odds") or 0
        picks.append({"ts": ts, "start": start, "title": title, "sport": sport or "?", "market": mname or mk,
                      "label": leg.get("label"), "odds": odds, "ev": ev, "status": status,
                      "clv": clv.get(f"{ek}#{mk}#azuro:{''.join(leg.get('covers') or [])}"),
                      "bot": (sport or "") not in OBSERVED,
                      "pnl": None if status == "open" else {"won": STAKE * (odds - 1), "lost": -STAKE, "void": 0.0}[status]})
    med = _clv_stats([p["clv"] for p in picks if p["clv"] is not None and p["bot"]])["median"] or 0.0
    acc = {True: 0.0, False: 0.0}
    for p in picks:
        c = p["clv"]
        p["exp"] = None if c is None else round(STAKE * (c if abs(c) <= RARE_CLV else med) / 100, 2)
        for only_bot in (True, False):
            if (p["bot"] or not only_bot) and p["pnl"] is not None:
                acc[only_bot] += p["pnl"]
        p["acc"] = round(acc[True], 2) if p["bot"] else None
        p["pnl"] = None if p["pnl"] is None else round(p["pnl"], 2)

    def days(only_bot: bool) -> list[dict]:
        out: dict[str, dict] = {}
        run = 0.0
        for p in picks:
            if only_bot and not p["bot"]:
                continue
            d = out.setdefault(_day(p["ts"]), {"day": _day(p["ts"]), "n": 0, "won": 0, "lost": 0, "void": 0, "open": 0,
                                               "pnl": 0.0, "exp": 0.0, "low": None, "high": None})
            d["n"] += 1
            d[p["status"]] += 1
            d["exp"] += p["exp"] or 0
            if p["pnl"] is not None:
                d["pnl"] += p["pnl"]
                run += p["pnl"]
                d["low"] = run if d["low"] is None else min(d["low"], run)
                d["high"] = run if d["high"] is None else max(d["high"], run)
            d["acc"] = run
        return [{**d, "pnl": round(d["pnl"], 2), "exp": round(d["exp"], 2), "acc": round(d["acc"], 2),
                 "staked": d["n"] * STAKE, "low": None if d["low"] is None else round(d["low"], 2),
                 "high": None if d["high"] is None else round(d["high"], 2)} for d in out.values()]

    bot = [p for p in picks if p["bot"]]
    closed = [p for p in bot if p["pnl"] is not None]
    t0 = picks[0]["ts"] if picks else None
    n_days = max(((picks[-1]["ts"] - t0) / 86400) if picks else 0.0, 0.5)

    # Con red vs sin red: los mismos picks de valor + red en Azuro, a $30 (escala lineal: la red cubre el cobro)
    try:
        nets = conn.execute("SELECT created_ts, title, sport, status, stake, odds, limit_price, pnl, pnl_no_net, p_close "
                            "FROM net_picks WHERE house='azuro' AND COALESCE(skipped,0)=0 AND pnl IS NOT NULL "
                            "AND pnl_no_net IS NOT NULL AND result NOT IN ('pendiente','sin datos')").fetchall()
    except sqlite3.OperationalError:
        nets = []
    red = {"n": 0, "locked": 0, "real_net": 0.0, "real_no": 0.0, "exp_net": 0.0, "exp_no": 0.0, "net_cost": 0.0}
    for ts, title, sport, status, stake, odds, lim, pnl, no_net, p_close in nets:
        if (sport or "") in OBSERVED or not stake:
            continue
        k = STAKE / stake
        exp_no = STAKE * (odds * (p_close or 0) - 1)
        red["n"] += 1
        red["locked"] += status == "asegurada"
        red["real_net"] += pnl * k
        red["real_no"] += no_net * k
        red["exp_net"] += pnl * k if status == "asegurada" else exp_no
        red["exp_no"] += exp_no
        red["net_cost"] += STAKE * odds * (lim or 0)
    red = {**{k: round(v, 2) for k, v in red.items()},
           "net_avg": round(red["net_cost"] / red["n"], 2) if red["n"] else None}

    # Señales de valor de Azuro (todas las que tuvieron EV ≥ 0) y su CLV: qué pasa si se afloja el filtro
    try:
        sig = [r for r in conn.execute("SELECT sport, ev_pct, odds, start_ts, first_seen, clv_pct FROM value_bets "
                                       "WHERE source='azuro' AND clv_pct IS NOT NULL AND start_ts > first_seen")
               if (r[0] or "") not in OBSERVED]
    except sqlite3.OperationalError:
        sig = []

    def by(f, edges) -> list[dict]:
        g: dict[str, list] = {}
        for r in sig:
            g.setdefault(_bucket(f(r), edges), []).append(r[5])
        order = {_bucket(e, edges): i for i, e in enumerate(edges)}
        return [{"name": k, **_clv_stats(v)} for k, v in sorted(g.items(), key=lambda kv: order.get(kv[0], 99))]

    bot_clv = _clv_stats([p["clv"] for p in bot if p["clv"] is not None])
    return {
        "stake": STAKE, "since": t0, "days": round(n_days, 1), "picks": len(picks), "bot_picks": len(bot),
        "per_day": round(len(bot) / n_days, 1), "clv": bot_clv,
        "pnl": round(sum(p["pnl"] for p in closed), 2), "exp": round(sum(p["exp"] or 0 for p in bot), 2),
        "won": sum(p["status"] == "won" for p in bot), "lost": sum(p["status"] == "lost" for p in bot),
        "open": sum(p["status"] == "open" for p in bot),
        "days_bot": days(True), "days_all": days(False), "red": red,
        "limits": {"ev": by(lambda r: r[1], [0, 1, 1.5, 2, 3, 5, 7, 10]),
                   "odds": by(lambda r: r[2], [1.2, 1.4, 1.6, 2, 3, 4, 6]),
                   "hours": by(lambda r: (r[3] - r[4]) / 3600, [0, 1, 3, 6, 12, 24])},
        "rows": list(reversed(picks))[:200],
    }
