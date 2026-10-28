"""
Pestaña "📊 Dashboard valor manual" del panel: el valor oro de 1xBet (semi-manual) a $30 fijos por apuesta, con todo lo que se
puede filtrar y desglosar (día, franja horaria de Venezuela (UTC−4), deporte, mercado, cuota, EV, horas antes del partido, tus botones ✅/❌).
Sale de la cuenta de valor en papel (paper_ops track 'valor'), así que se actualiza solo cuando el liquidador cierra cada partido.
Cada apuesta es un dict con claves cortas (el panel lo recibe comprimido):
  d día · m minuto del día (Venezuela, UTC−4) · ts hora de la señal · st inicio del partido · t partido · l selección · o cuota · ev EV %
  c CLV % (None si aún no hay cierre) · sp deporte · mk mercado · s g/p/a/o (ganada, perdida, anulada, abierta) · p ganancia a $30
  (None si abierta) · e esperado sin suerte a $30 · u link al partido en 1xBet · y tu botón (puesta / no estaba / avisada / …)
  · mid n.º de la señal (para /puse y /noestaba en Telegram) · tr nivel (oro | amplio = EV 1,5–2 %) · lt tamaño del lote (0 = suelta) · n n.º de pick en el mismo partido · h casa donde la pusiste (o donde no estaba) · po cuota a la que dijiste que la pusiste · mn cuota mínima del aviso · fs/ls primera y última vez que el escáner vio la señal
  como valor (la duración de la cuota: mientras ls siga reciente, la señal está vigente y se puede apostar) · sy símbolo de la apuesta (over/under, W1/W2, X, 1X/X2/12) · lv marcador en vivo · fin marcador final de las cerradas («0-1»; «últ. …» si no se vio terminar)
  («1-0 · 32'», solo en abiertas cuyo partido ya empezó y con dato de hace menos de 6 min).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from .livescore import FRESH_S, final_score, game_id
from .manualvalor import NO_ALERT_SPORTS
from .safety import OBSERVE_SPORTS

STAKE = 30.0
VET = timezone(timedelta(hours=-4))          # Venezuela (UTC−4), sin horario de verano
SOURCES = ("1xbet", "onexbet")
RARE_CLV = 15.0              # CLV más allá de ±15 %: casi siempre un dato raro (cuenta con la mediana)
STATUS = {"won": "g", "lost": "p", "void": "a", "open": "o"}


def book_flipped(title: str, url: str) -> bool:
    """¿1xBet lista los equipos al revés que el título (orden de Polymarket)? En EE. UU. Polymarket pone primero al visitante
    y 1xBet al local: «Heat vs Hawks» allá es «Atlanta Hawks – Miami Heat». Se compara el orden en el link del partido."""
    if " vs " not in (title or ""):
        return False
    m = re.search(r"/\d+-([^/?#]+)$", url or "")
    if not m:
        return False
    tail = m.group(1)

    def pos(name: str) -> int:
        ascii_ = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
        for w in reversed([w for w in re.findall(r"[a-z0-9]+", ascii_) if len(w) > 2]):   # el apodo (última palabra) primero
            if w in tail:
                return tail.find(w)
        return -1
    home, away = title.split(" vs ", 1)
    ph, pa = pos(home), pos(away)
    return ph >= 0 and pa >= 0 and pa < ph


def symbol(market: str, covers: list[str], flip: bool = False) -> str:
    """Símbolo corto de la apuesta (debajo de la selección): over/under, W1/W2, X, 1X/X2/12; «· 1T» si es del primer tiempo.
    Con flip, W1/W2 (y 1X/X2) se cuentan en el orden de 1xBet, que es donde se apuesta."""
    cov = set(covers or [])
    if flip:
        cov = {{"H": "A", "A": "H"}.get(x, x) for x in cov}
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
    try:                                   # columnas que falten en bases viejas salen como NULL
        have = {r[1] for r in conn.execute("PRAGMA table_info(manual_value)")}
        cols = ["status", "placed_odds", "min_odds", "house", "id", "wrong_pick", "wrong_stake", "wrong_result"]
        sel = ", ".join(c if c in have else f"NULL AS {c}" for c in cols)
        mv = {r[0]: r[1:] for r in conn.execute(f"SELECT key, {sel} FROM manual_value")} if have else {}
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
    try:       # marcador en vivo de las abiertas y marcador final de las cerradas (livescore.py)
        live = {g: (ts_, txt, e) for g, ts_, txt, e in conn.execute("SELECT game_id, ts, text, COALESCE(ended, 0) FROM live_scores")}
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
        y, po, mn, hs, mid, wp, ws, wr = mv.get(key, (None,) * 8)
        flip = leg["flip"] if "flip" in leg else book_flipped(title, leg.get("url") or "")
        if flip and " vs " in (title or ""):
            a, b = title.split(" vs ", 1)
            title = f"{b} vs {a}"             # el partido tal como lo ve el usuario en 1xBet (local primero)
        picks.append({"d": t.strftime("%Y-%m-%d"), "m": t.hour * 60 + t.minute, "ts": round(ts), "st": round(start) if start else None,
                      "t": (title or "")[:34], "l": (leg.get("label") or "")[:22], "o": round(odds, 3), "ev": round(ev or 0, 2),
                      "c": None if c is None else round(c, 2), "sp": sport or "?", "mk": mname or mk, "s": STATUS[status],
                      "p": None if pnl is None else round(pnl, 2), "e": round(exp, 2), "u": leg.get("url") or "",
                      "y": y, "po": po, "mn": mn, "h": (hs or "1xBet") if y in ("puesta", "no estaba", "mal jugada") else None,
                      "fs": round(fs) if fs else None, "ls": round(ls) if ls else None,
                      "mid": mid, "lt": lot.get(key, 0), "n": ordinal.get(key, 1), "tr": "amplio" if track == "amplio" else "oro",
                      "sy": symbol(mk, leg.get("covers"), flip)})
        if y == "mal jugada":                 # jugaste otra apuesta: qué, con cuánto y cómo salió (lo marcas tú)
            picks[-1].update({"wp": wp, "ws": ws, "wr": wr or "pendiente"})
        if mid is None:
            picks[-1]["k"] = key                 # sin fila en manual_value (CS2, etc.): el panel la crea al marcarla
        sc = live.get(game_id(leg.get("url") or "") or "")
        if sc and status == "open" and not sc[2] and sc[0] > time.time() - FRESH_S:
            picks[-1]["lv"] = sc[1]
        elif sc and (status in ("won", "lost") or (status == "open" and sc[2])):   # cerrada, o terminó y falta liquidarla
            picks[-1]["fin"] = final_score(sc[1]) if sc[2] else "últ. " + sc[1]
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
    try:
        hrs = (conn.execute("SELECT v FROM manual_value_meta WHERE k='hours'").fetchone() or [None])[0]
        hours = [int(x) for x in hrs.split("-")] if hrs else None
    except (sqlite3.OperationalError, ValueError):
        hours = None
    return {"stake": STAKE, "picks": picks, "last_ts": picks[-1]["ts"], "house": active or "1xBet", "domains": domains,
            "hours": hours or [12, 20],
            "work": {"observe": list(OBSERVE_SPORTS), "no_alert": list(NO_ALERT_SPORTS)}}
