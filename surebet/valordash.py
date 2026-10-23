"""
Pestaña "📊 Dashboard valor manual" del panel: el valor oro de 1xBet (semi-manual) a $30 fijos por apuesta, con todo lo que se
puede filtrar y desglosar (día, franja horaria de Colombia, deporte, mercado, cuota, EV, horas antes del partido, tus botones ✅/❌).
Sale de la cuenta de valor en papel (paper_ops track 'valor'), así que se actualiza solo cuando el liquidador cierra cada partido.
Cada apuesta es un dict con claves cortas (el panel lo recibe comprimido):
  d día · m minuto del día (Colombia) · ts hora de la señal · st inicio del partido · t partido · l selección · o cuota · ev EV %
  c CLV % (None si aún no hay cierre) · sp deporte · mk mercado · s g/p/a/o (ganada, perdida, anulada, abierta) · p ganancia a $30
  (None si abierta) · e esperado sin suerte a $30 · u link al partido en 1xBet · y tu botón (puesta / no estaba / avisada / …)
  · h casa donde la pusiste (o donde no estaba) · po cuota a la que dijiste que la pusiste · mn cuota mínima del aviso · fs/ls primera y última vez que el escáner vio la señal
  como valor (la duración de la cuota: mientras ls siga reciente, la señal está vigente y se puede apostar).
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
        vb = {k: (c, fs, ls) for k, c, fs, ls in conn.execute("SELECT key, clv_pct, first_seen, last_seen FROM value_bets")}
        ops = conn.execute("SELECT key, event_key, market, market_name, legs_json, status, created_ts, start_ts, title, sport, "
                           "edge_pct FROM paper_ops WHERE track='valor' ORDER BY created_ts").fetchall()
    except sqlite3.OperationalError:
        return None
    try:
        mv = {k: (s, po, mn, h) for k, s, po, mn, h in conn.execute("SELECT key, status, placed_odds, min_odds, house FROM manual_value")}
    except sqlite3.OperationalError:       # base anterior a la columna «house»
        try:
            mv = {k: (s, po, mn, None) for k, s, po, mn in conn.execute("SELECT key, status, placed_odds, min_odds FROM manual_value")}
        except sqlite3.OperationalError:
            mv = {}
    raw = []
    for key, ek, mk, mname, legs, status, ts, start, title, sport, ev in ops:
        leg = (json.loads(legs) or [{}])[0]
        if leg.get("source") not in SOURCES or status not in STATUS:
            continue
        raw.append((key, ek, mk, mname, leg, status, ts, start, title, sport, ev,
                    vb.get(f"{ek}#{mk}#{leg['source']}:{''.join(leg.get('covers') or [])}", (None, None, None))))
    vals = sorted(r[11][0] for r in raw if r[11][0] is not None)
    med = vals[len(vals) // 2] if vals else 0.0
    picks = []
    for key, ek, mk, mname, leg, status, ts, start, title, sport, ev, (c, fs, ls) in raw:
        t = datetime.fromtimestamp(ts, COL)
        odds = float(leg.get("odds") or 0)
        pnl = None if status == "open" else {"won": STAKE * (odds - 1), "lost": -STAKE, "void": 0.0}[status]
        exp = 0.0 if c is None else STAKE * (c if abs(c) <= RARE_CLV else med) / 100
        y, po, mn, hs = mv.get(key, (None, None, None, None))
        picks.append({"d": t.strftime("%Y-%m-%d"), "m": t.hour * 60 + t.minute, "ts": round(ts), "st": round(start) if start else None,
                      "t": (title or "")[:34], "l": (leg.get("label") or "")[:22], "o": round(odds, 3), "ev": round(ev or 0, 2),
                      "c": None if c is None else round(c, 2), "sp": sport or "?", "mk": mname or mk, "s": STATUS[status],
                      "p": None if pnl is None else round(pnl, 2), "e": round(exp, 2), "u": leg.get("url") or "",
                      "y": y, "po": po, "mn": mn, "h": (hs or "1xBet") if y in ("puesta", "no estaba") else None,
                      "fs": round(fs) if fs else None, "ls": round(ls) if ls else None})
    if not picks:
        return None
    try:
        active = (conn.execute("SELECT v FROM manual_value_meta WHERE k='house'").fetchone() or [None])[0]
    except sqlite3.OperationalError:
        active = None
    try:
        domains = {k[7:]: v for k, v in conn.execute("SELECT k, v FROM manual_value_meta WHERE k LIKE 'domain:%'") if v}
    except sqlite3.OperationalError:
        domains = {}
    return {"stake": STAKE, "picks": picks, "last_ts": picks[-1]["ts"], "house": active or "1xBet", "domains": domains}
