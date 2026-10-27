"""
Pestaña "📊 Dashboard valor manual" del panel: el valor oro de 1xBet (semi-manual) a $30 fijos por apuesta, con todo lo que se
puede filtrar y desglosar (día, franja horaria de Venezuela (UTC−4), deporte, mercado, cuota, EV, horas antes del partido, tus botones ✅/❌).
Sale de la cuenta de valor en papel (paper_ops track 'valor'), así que se actualiza solo cuando el liquidador cierra cada partido.
Cada apuesta es un dict con claves cortas (el panel lo recibe comprimido):
  d día · m minuto del día (Venezuela, UTC−4) · ts hora de la señal · st inicio del partido · t partido · l selección · o cuota · ev EV %
  c CLV % (None si aún no hay cierre) · sp deporte · mk mercado · s g/p/a/o (ganada, perdida, anulada, abierta) · p ganancia a $30
  (None si abierta) · e esperado sin suerte a $30 · u link al partido en 1xBet · y tu botón (puesta / no estaba / avisada / …)
  · mid n.º de la señal (para /puse y /noestaba en Telegram) · tr nivel (oro | amplio = EV 1,5–2 %) · lt tamaño del lote (0 = suelta) · n n.º de pick en el mismo partido · h casa donde la pusiste (o donde no estaba) · po cuota a la que dijiste que la pusiste · mn cuota mínima del aviso · fs/ls primera y última vez que el escáner vio la señal
  como valor (la duración de la cuota: mientras ls siga reciente, la señal está vigente y se puede apostar) · sy símbolo de la apuesta (over/under, W1/W2, X, 1X/X2/12) · lv marcador en vivo
  («1-0 · 32'», solo en abiertas cuyo partido ya empezó y con dato de hace menos de 6 min).
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from .livescore import FRESH_S, game_id

STAKE = 30.0
VET = timezone(timedelta(hours=-4))          # Venezuela (UTC−4), sin horario de verano
SOURCES = ("1xbet", "onexbet")
RARE_CLV = 15.0              # CLV más allá de ±15 %: casi siempre un dato raro (cuenta con la mediana)
STATUS = {"won": "g", "lost": "p", "void": "a", "open": "o"}


def symbol(market: str, covers: list[str]) -> str:
    """Símbolo corto de la apuesta (debajo de la selección): over/under, W1/W2, X, 1X/X2/12; «· 1T» si es del primer tiempo."""
    cov = set(covers or [])
    if cov in ({"O"}, {"U"}):
        s = "over" if cov == {"O"} else "under"
    else:
        s = {frozenset("H"): "W1", frozenset("A"): "W2", frozenset("D"): "X", frozenset("DH"): "1X",
             frozenset("AD"): "X2", frozenset("AH"): "12"}.get(frozenset(cov), "")
    return f"{s} · 1T" if s and "_h1" in (market or "") else s


def summary(conn) -> dict | None:
    try:
        vb = {k: (c, fs, ls) for k, c, fs, ls in conn.execute("SELECT key, clv_pct, first_seen, last_seen FROM value_bets")}
        ops = conn.execute("SELECT key, event_key, market, market_name, legs_json, status, created_ts, start_ts, title, sport, "
                           "edge_pct, track FROM paper_ops WHERE track IN ('valor','amplio') ORDER BY created_ts").fetchall()
    except sqlite3.OperationalError:
        return None
    try:
        mv = {k: (s, po, mn, h, i) for k, s, po, mn, h, i in conn.execute("SELECT key, status, placed_odds, min_odds, house, id FROM manual_value")}
    except sqlite3.OperationalError:       # base anterior a la columna «house»
        try:
            mv = {k: (s, po, mn, None, i) for k, s, po, mn, i in conn.execute("SELECT key, status, placed_odds, min_odds, id FROM manual_value")}
        except sqlite3.OperationalError:
            mv = {}
    raw = []
    for key, ek, mk, mname, legs, status, ts, start, title, sport, ev, track in ops:
        leg = (json.loads(legs) or [{}])[0]
        if leg.get("source") not in SOURCES or status not in STATUS:
            continue
        raw.append((key, ek, mk, mname, leg, status, ts, start, title, sport, ev,
                    vb.get(f"{ek}#{mk}#{leg['source']}:{''.join(leg.get('covers') or [])}", (None, None, None)), track))
    # lote: 3 o más señales nacidas en 60 s; n.º de pick dentro del mismo partido (el aviso se corta en MAX_PER_GAME)
    lot, i = {}, 0
    while i < len(raw):
        j = i
        while j + 1 < len(raw) and raw[j + 1][6] - raw[j][6] <= 60:
            j += 1
        if j - i + 1 >= 3:
            for k in range(i, j + 1):
                lot[raw[k][0]] = j - i + 1
        i = j + 1
    nth: dict[str, int] = {}
    ordinal: dict[str, int] = {}
    for r in raw:
        nth[r[1]] = nth.get(r[1], 0) + 1
        ordinal[r[0]] = nth[r[1]]
    try:       # marcador en vivo de las abiertas (livescore.py): solo si es reciente
        live = {g: t for g, ts_, t in conn.execute("SELECT game_id, ts, text FROM live_scores WHERE ts > ?", (time.time() - FRESH_S,))}
    except sqlite3.OperationalError:
        live = {}
    vals = sorted(r[11][0] for r in raw if r[11][0] is not None)
    med = vals[len(vals) // 2] if vals else 0.0
    picks = []
    for key, ek, mk, mname, leg, status, ts, start, title, sport, ev, (c, fs, ls), track in raw:
        t = datetime.fromtimestamp(ts, VET)
        odds = float(leg.get("odds") or 0)
        pnl = None if status == "open" else {"won": STAKE * (odds - 1), "lost": -STAKE, "void": 0.0}[status]
        exp = 0.0 if c is None else STAKE * (c if abs(c) <= RARE_CLV else med) / 100
        y, po, mn, hs, mid = mv.get(key, (None, None, None, None, None))
        picks.append({"d": t.strftime("%Y-%m-%d"), "m": t.hour * 60 + t.minute, "ts": round(ts), "st": round(start) if start else None,
                      "t": (title or "")[:34], "l": (leg.get("label") or "")[:22], "o": round(odds, 3), "ev": round(ev or 0, 2),
                      "c": None if c is None else round(c, 2), "sp": sport or "?", "mk": mname or mk, "s": STATUS[status],
                      "p": None if pnl is None else round(pnl, 2), "e": round(exp, 2), "u": leg.get("url") or "",
                      "y": y, "po": po, "mn": mn, "h": (hs or "1xBet") if y in ("puesta", "no estaba") else None,
                      "fs": round(fs) if fs else None, "ls": round(ls) if ls else None,
                      "mid": mid, "lt": lot.get(key, 0), "n": ordinal.get(key, 1), "tr": "amplio" if track == "amplio" else "oro",
                      "sy": symbol(mk, leg.get("covers"))})
        if status == "open" and (lv := live.get(game_id(leg.get("url") or "") or "")):
            picks[-1]["lv"] = lv
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
