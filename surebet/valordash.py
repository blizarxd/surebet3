"""
Pestaña "📊 Dashboard valor" del panel: el valor oro de 1xBet (semi-manual) a $30 fijos por apuesta, día por día y por franja
horaria (hora de Colombia). Sale de la cuenta de valor en papel (paper_ops track 'valor'), así que se actualiza solo cuando
el liquidador cierra cada partido. Cada apuesta: [día, minuto del día, ganancia a $30 o None si sigue abierta, esperado por CLV,
partido, selección, cuota, estado].
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

STAKE = 30.0
COL = timezone(timedelta(hours=-5))
SOURCES = ("1xbet", "onexbet")
RARE_CLV = 15.0              # CLV más allá de ±15 %: casi siempre un dato raro (cuenta con la mediana)
STATUS = {"won": "g", "lost": "p", "void": "a", "open": "o"}


def summary(conn) -> dict | None:
    try:
        clv = {k: c for k, c in conn.execute("SELECT key, clv_pct FROM value_bets WHERE clv_pct IS NOT NULL")}
        ops = conn.execute("SELECT event_key, market, legs_json, status, created_ts, title FROM paper_ops "
                           "WHERE track='valor' ORDER BY created_ts").fetchall()
    except sqlite3.OperationalError:
        return None
    raw = []
    for ek, mk, legs, status, ts, title in ops:
        leg = (json.loads(legs) or [{}])[0]
        if leg.get("source") not in SOURCES or status not in STATUS:
            continue
        odds = float(leg.get("odds") or 0)
        c = clv.get(f"{ek}#{mk}#{leg['source']}:{''.join(leg.get('covers') or [])}")
        raw.append((ts, title, leg.get("label") or "", odds, status, c))
    vals = sorted(r[5] for r in raw if r[5] is not None)
    med = vals[len(vals) // 2] if vals else 0.0
    picks = []
    for ts, title, label, odds, status, c in raw:
        t = datetime.fromtimestamp(ts, COL)
        pnl = None if status == "open" else {"won": STAKE * (odds - 1), "lost": -STAKE, "void": 0.0}[status]
        exp = 0.0 if c is None else STAKE * (c if abs(c) <= RARE_CLV else med) / 100
        picks.append([t.strftime("%Y-%m-%d"), t.hour * 60 + t.minute, None if pnl is None else round(pnl, 2), round(exp, 2),
                      (title or "")[:30], label[:20], round(odds, 3), STATUS[status]])
    if not picks:
        return None
    return {"stake": STAKE, "picks": picks, "last_ts": raw[-1][0]}
