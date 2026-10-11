"""
"Valor + red" en papel: pick por pick, cada apuesta de valor oro y si su red de Polymarket la convirtió en surebet.

Para cada apuesta de valor "oro" anotada (prepartido, EV 2–7 %, cuota 1.6–4, empieza en ≤ 6 h):
  1. Se apuesta en 1xBet a la cuota anotada, con monto Kelly/4 (tope 3 %) sobre un capital de $1000.
  2. Se deja una red: orden límite del lado contrario en Polymarket al precio que asegura LOCK_PCT
     (sin comisión). Precio límite = 1/(1+lock) − 1/cuota, redondeado al centavo hacia abajo.
  3. Con el historial de precios de Polymarket minuto a minuto (desde la entrada hasta 10 min antes del
     partido) se ve si el precio del lado contrario bajó hasta la red, con medio centavo de margen
     (alguien tiene que vender a ese precio):
       - si bajó → "asegurada" a esa hora: ganancia fija, salga lo que salga;
       - si no → "quedó como valor": se cancela la red y cuenta el resultado real de la apuesta.
Además se prueban redes "a X centavos": orden límite 1, 2, 3, 4 y 5 ¢ por debajo del precio que tenía el lado
contrario al entrar (más abajo = se llena menos pero asegura más). Cada nivel se compara con no usar red.
Si Polymarket no devuelve historial se reintenta; si nunca lo hay, el pick queda "sin datos" y no cuenta.

Mismo método para el pasado (backtest con lo que ya estaba anotado) y para lo nuevo (seguimiento cada pocos
minutos), así los números son comparables. Lo que NO modela: la cola de órdenes de Polymarket (es optimista).
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from pathlib import Path

import httpx

from .risk import risk_stats
from .safety import golden_value
from .staking import value_stake

CLOB = "https://clob.polymarket.com"
LOCK_PCT = 1.0
CANCEL_BEFORE_S = 600
CROSS = 0.005           # el precio tiene que pasar medio centavo nuestra red para contarla llenada
CHECK_EVERY_S = 300     # cada cuánto se revisa una red que sigue esperando
PICKS_PER_RUN = 25      # para no golpear a Polymarket: tandas chicas
LEVELS_C = (1, 2, 3, 4, 5)  # redes a X centavos bajo el precio de entrada del lado contrario
MAX_TRIES = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS net_picks (
    key TEXT PRIMARY KEY,       -- la apuesta de valor medida (paper_ops.key)
    source TEXT,                -- backtest (ya estaba anotada al activar) | vivo
    created_ts REAL, start_ts REAL, title TEXT, sport TEXT, market TEXT, selection TEXT, house TEXT,
    odds REAL, ev_pct REAL, stake REAL,
    net_label TEXT,             -- qué compra la red en Polymarket
    limit_price REAL,
    tokens TEXT,                -- {resultado: token} para el historial de precios
    covers TEXT,
    status TEXT,                -- valor (red esperando) | asegurada | quedó como valor
    lock_ts REAL,               -- cuándo se llenó la red
    best_gap REAL,              -- lo más cerca que estuvo el precio de la red (en ¢; negativo = la cruzó)
    gap_now REAL,               -- distancia actual (en ¢)
    last_check REAL,
    result TEXT,                -- pendiente | asegurada | ganada | perdida | anulada
    pnl REAL,                   -- con red
    pnl_no_net REAL             -- la misma apuesta sin red
);
CREATE TABLE IF NOT EXISTS net_meta (k TEXT PRIMARY KEY, v TEXT);
"""
MIGRATIONS = {"p_entry": "REAL", "p_close": "REAL", "levels": "TEXT", "tries": "INTEGER DEFAULT 0"}


def limit_for(odds: float, lock_pct: float = LOCK_PCT) -> float:
    return math.floor((1 / (1 + lock_pct / 100) - 1 / odds) * 100 + 1e-9) / 100


def net_label(market: str, covers: list[str], selection: str, title: str) -> str:
    if market.startswith("ou"):
        return selection.replace("Más", "Menos") if "Más" in selection else selection.replace("Menos", "Más")
    if market.startswith("ml") and " vs " in title:
        home, away = title.split(" vs ", 1)
        return away if covers == ["H"] else home
    return f"NO «{selection}»"


def kelly_stake(odds: float, ev_pct: float, bankroll: float, fraction: float = 0.25, cap: float = 0.03) -> float:
    k = max(0.0, (ev_pct / 100) / (odds - 1)) if odds > 1 else 0.0
    return round(min(bankroll * k * fraction, bankroll * cap), 2)


class NetTracker:
    def __init__(self, db_path: Path, bankroll: float = 1000.0, lock_pct: float = LOCK_PCT,
                 min_total: float = 0.0) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(net_picks)")}
        for col, decl in MIGRATIONS.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE net_picks ADD COLUMN {col} {decl}")
        self.bankroll, self.lock_pct, self.min_total = bankroll, lock_pct, min_total
        self.conn.execute("INSERT OR IGNORE INTO net_meta VALUES ('activated', ?)", (str(time.time()),))
        self.conn.execute("INSERT OR REPLACE INTO net_meta VALUES ('bankroll', ?)", (str(bankroll),))
        self.conn.commit()
        self.activated = float(self.conn.execute("SELECT v FROM net_meta WHERE k='activated'").fetchone()[0])
        # Si cambió la regla de montos, se recalculan TODOS los picks (también el backtest) con la nueva
        prev = self.conn.execute("SELECT v FROM net_meta WHERE k='min_total'").fetchone()
        if prev is None or float(prev[0]) != min_total:
            for key, odds, ev, lim in self.conn.execute("SELECT key, odds, ev_pct, limit_price FROM net_picks").fetchall():
                self.conn.execute("UPDATE net_picks SET stake=?, pnl=NULL, pnl_no_net=NULL, "
                                  "result=CASE WHEN result='sin datos' THEN result ELSE 'pendiente' END WHERE key=?",
                                  (self._stake(odds, ev, lim), key))
            self.conn.execute("INSERT OR REPLACE INTO net_meta VALUES ('min_total', ?)", (str(min_total),))
            self.conn.commit()
        self.status = "iniciando"

    def _stake(self, odds: float, ev: float, limit: float) -> float:
        return value_stake(odds, ev, self.bankroll, min_total=self.min_total, limit=limit)

    async def run(self) -> None:
        """Segundo plano: incorpora picks nuevos, revisa redes y cierra resultados. Nunca tumba al escáner."""
        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                try:
                    n = await self.update(client)
                    self.status = f"ok · {n} picks revisados"
                except Exception as e:
                    self.status = f"error: {e!r}"[:160]
                await asyncio.sleep(60)

    # ------------------------------------------------------------------ picks
    def _sync_picks(self) -> None:
        try:
            ops = self.conn.execute(
                "SELECT key, created_ts, start_ts, title, sport, market, market_name, edge_pct, live, legs_json, resolve_json "
                "FROM paper_ops WHERE kind='value' AND track='medicion' AND live=0 AND start_ts IS NOT NULL "
                "AND key NOT IN (SELECT key FROM net_picks)").fetchall()
        except sqlite3.OperationalError:
            return
        for key, created, start, title, sport, market, mname, ev, live, legs_json, resolve_json in ops:
            leg = (json.loads(legs_json) or [{}])[0]
            odds = leg.get("odds") or 0
            if not golden_value(bool(live) or created >= start, ev, odds, (start - created) / 3600):
                continue
            lim = limit_for(odds, self.lock_pct)
            tokens = {o: t[1] for o, t in json.loads(resolve_json or "{}").items()}
            if lim < 0.01 or not tokens:
                continue
            covers = leg.get("covers") or []
            self.conn.execute(
                "INSERT OR IGNORE INTO net_picks (key, source, created_ts, start_ts, title, sport, market, selection, house, "
                "odds, ev_pct, stake, net_label, limit_price, tokens, covers, status, result) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'valor', 'pendiente')",
                (key, "backtest" if created < self.activated else "vivo", created, start, title, sport, mname or market,
                 leg.get("label"), leg.get("source"), odds, ev, self._stake(odds, ev, lim),
                 net_label(market, covers, leg.get("label", ""), title), lim, json.dumps(tokens), json.dumps(covers)))
        self.conn.commit()

    async def update(self, client: httpx.AsyncClient) -> int:
        self._sync_picks()
        now = time.time()
        # Las que esperan, y las ya decididas sin niveles calculados (se recalculan enteras con el mismo método)
        rows = self.conn.execute(
            "SELECT key, created_ts, start_ts, odds, limit_price, tokens, covers, tries FROM net_picks "
            "WHERE (status='valor' OR levels IS NULL) AND status!='sin datos' AND (last_check IS NULL OR last_check < ?) "
            "ORDER BY COALESCE(last_check, 0) LIMIT ?", (now - CHECK_EVERY_S, PICKS_PER_RUN)).fetchall()
        updates, retries, gone = [], [], []  # se escriben al final: no dejar la base tomada mientras se consulta
        for key, created, start, odds, lim, tokens_json, covers_json, tries in rows:
            end = min(now, start - CANCEL_BEFORE_S)
            path = await self._path(client, json.loads(tokens_json), set(json.loads(covers_json)), created, end)
            if not path:
                if (tries or 0) + 1 >= MAX_TRIES and now >= start - CANCEL_BEFORE_S:
                    gone.append((now, key))           # nunca hubo historial: no cuenta (no se asume nada)
                else:
                    retries.append((now, key))
                continue
            status, lock_ts, gap_now, best_gap = "valor", None, None, None
            for t, pa in path:
                gap = 100 * ((1 - pa) + CROSS - lim)          # ¢ que le faltan al precio para llenar la red
                best_gap = gap if best_gap is None else min(best_gap, gap)
                gap_now = gap
                if gap <= 0 and lock_ts is None:
                    status, lock_ts = "asegurada", t
            if status == "valor" and now >= start - CANCEL_BEFORE_S:
                status = "quedó como valor"
            comp0 = 1 - path[0][1]
            levels = {}
            for k in LEVELS_C:
                lk = math.floor((comp0 - k / 100) * 100 + 1e-9) / 100
                hit = next((t for t, pa in path[1:] if (1 - pa) + CROSS <= lk), None) if lk >= 0.01 else None
                levels[str(k)] = {"limit": lk, "ts": hit}
            updates.append((status, lock_ts, best_gap, gap_now, now, path[0][1], path[-1][1], json.dumps(levels), key))
        self.conn.executemany(
            "UPDATE net_picks SET status=?, lock_ts=?, best_gap=?, gap_now=?, last_check=?, p_entry=?, p_close=?, levels=?, "
            "result='pendiente', pnl=NULL, pnl_no_net=NULL WHERE key=?", updates)
        self.conn.executemany("UPDATE net_picks SET last_check=?, tries=COALESCE(tries,0)+1 WHERE key=?", retries)
        self.conn.executemany("UPDATE net_picks SET status='sin datos', result='sin datos', pnl=NULL, last_check=? "
                              "WHERE key=?", gone)
        self._settle()
        self.conn.commit()
        return len(rows)

    async def _path(self, client, tokens: dict, covers: set, a: float, b: float) -> list[tuple[float, float]]:
        """Probabilidad de lo apostado minuto a minuto según Polymarket (normalizada entre los resultados)."""
        if b <= a:
            return []
        hs = {}
        for out, tok in tokens.items():
            try:
                r = await client.get(f"{CLOB}/prices-history", params={
                    "market": tok, "startTs": int(a), "endTs": int(b), "fidelity": 1})
                if r.status_code == 429:
                    await asyncio.sleep(3)
                    r = await client.get(f"{CLOB}/prices-history", params={
                        "market": tok, "startTs": int(a), "endTs": int(b), "fidelity": 1})
                pts = r.json().get("history") or [] if r.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                return []
            # Polymarket a veces devuelve puntos fuera del rango pedido: se filtran
            hs[out] = {int(p["t"]) // 60: float(p["p"]) for p in pts if a <= p["t"] <= b}
            await asyncio.sleep(0.25)  # suave con Polymarket (sin pausa a veces devuelve vacío)
        if not hs or not all(hs.values()):
            return []
        mins = sorted(set.intersection(*(set(h) for h in hs.values())))
        out = []
        for m in mins:
            tot = sum(h[m] for h in hs.values())
            if tot > 0:
                out.append((m * 60.0, sum(hs[o][m] for o in covers if o in hs) / tot))
        return out

    def _settle(self) -> None:
        """Resultado de cada pick: asegurada = ganancia fija; sin red = resultado real de la apuesta (paper_ops)."""
        rows = self.conn.execute(
            "SELECT n.key, n.status, n.odds, n.stake, n.limit_price, p.status FROM net_picks n "
            "JOIN paper_ops p ON p.key = n.key WHERE n.result='pendiente'").fetchall()
        for key, status, odds, stake, lim, pstatus in rows:
            no_net = {"won": stake * (odds - 1), "lost": -stake, "void": 0.0}.get(pstatus)
            if status == "asegurada":
                shares = stake * odds
                gain = shares - stake - shares * lim
                self.conn.execute("UPDATE net_picks SET result='asegurada', pnl=?, pnl_no_net=? WHERE key=?",
                                  (round(gain, 2), None if no_net is None else round(no_net, 2), key))
            elif status == "quedó como valor" and no_net is not None:
                result = {"won": "ganada", "lost": "perdida", "void": "anulada"}[pstatus]
                self.conn.execute("UPDATE net_picks SET result=?, pnl=?, pnl_no_net=? WHERE key=?",
                                  (result, round(no_net, 2), round(no_net, 2), key))
        # las aseguradas guardan también el "sin red" cuando el partido termina (para comparar)
        self.conn.execute(
            "UPDATE net_picks SET pnl_no_net = (SELECT CASE p.status WHEN 'won' THEN net_picks.stake*(net_picks.odds-1) "
            "WHEN 'lost' THEN -net_picks.stake WHEN 'void' THEN 0 END FROM paper_ops p WHERE p.key = net_picks.key) "
            "WHERE result='asegurada' AND pnl_no_net IS NULL")


def _lock_gain(stake: float, odds: float, limit: float) -> tuple[float, float]:
    """(ganancia fija, costo en Polymarket) de una red llenada a `limit` cubriendo el cobro de la apuesta."""
    shares = stake * odds
    return shares - stake - shares * limit, shares * limit


def by_level(rows: list[dict]) -> list[dict]:
    """Cada forma de poner la red sobre los MISMOS picks decididos: se llena %, asegura %, resultado real y esperado."""
    decided = [r for r in rows if r["status"] in ("asegurada", "quedó como valor") and r["levels"] is not None]
    out = []

    def add(name: str, fill) -> None:
        fills = real = exp = staked = staked_real = 0.0
        locks = []
        for r in decided:
            stake, odds = r["stake"], r["odds"]
            lim = fill(r)
            exp_value = stake * (odds * (r["p_close"] or 0) - 1)        # valor esperado al cierre (sin suerte)
            staked += stake
            if lim is not None:
                gain, cost = _lock_gain(stake, odds, lim)
                fills += 1
                locks.append(100 * gain / (stake + cost))
                exp += gain
                real += gain
                staked_real += stake
            else:
                exp += exp_value
                if r["pnl_no_net"] is not None:
                    real += r["pnl_no_net"]
                    staked_real += stake
        n = len(decided)
        out.append({"name": name, "picks": n, "fills": int(fills),
                    "fill_rate": round(100 * fills / n, 1) if n else None,
                    "avg_lock": round(sum(locks) / len(locks), 2) if locks else None,
                    "real": round(real, 2), "real_roi": round(100 * real / staked_real, 2) if staked_real else None,
                    "exp": round(exp, 2), "exp_roi": round(100 * exp / staked, 2) if staked else None})

    add("Sin red (solo valor)", lambda r: None)
    add("Red +1 % (actual)", lambda r: r["limit_price"] if r["status"] == "asegurada" else None)
    for k in LEVELS_C:
        add(f"Red a {k}¢ bajo la entrada",
            lambda r, k=k: r["levels"][str(k)]["limit"] if r["levels"][str(k)]["ts"] else None)
    return out


def summary(conn) -> dict | None:
    try:
        meta = dict(conn.execute("SELECT k, v FROM net_meta").fetchall())
        cols = ("key", "source", "created_ts", "start_ts", "title", "sport", "market", "selection", "house", "odds",
                "ev_pct", "stake", "net_label", "limit_price", "status", "lock_ts", "best_gap", "gap_now", "result",
                "pnl", "pnl_no_net", "p_entry", "p_close", "levels")
        rows = [dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {', '.join(cols)} FROM net_picks WHERE status!='sin datos' ORDER BY created_ts DESC")]
        no_data = conn.execute("SELECT COUNT(*) FROM net_picks WHERE status='sin datos'").fetchone()[0]
    except sqlite3.OperationalError:
        return None
    bankroll = float(meta.get("bankroll", 1000))

    def block(rs) -> dict:
        done = [r for r in rs if r["result"] != "pendiente" and r["pnl"] is not None]
        locked = [r for r in rs if r["status"] == "asegurada"]
        decided = [r for r in rs if r["status"] in ("asegurada", "quedó como valor")]
        both = [r for r in done if r["pnl_no_net"] is not None]
        staked = sum(r["stake"] for r in done)
        pnl = sum(r["pnl"] for r in done)
        return {"picks": len(rs), "decided": len(decided), "locked": len(locked),
                "lock_rate": round(100 * len(locked) / len(decided), 1) if decided else None,
                "in_value": sum(r["status"] == "valor" for r in rs),
                "settled": len(done), "won": sum(r["result"] == "ganada" for r in done),
                "lost": sum(r["result"] == "perdida" for r in done),
                "pnl": round(pnl, 2), "roi": round(100 * pnl / staked, 2) if staked else None,
                "pnl_no_net": round(sum(r["pnl_no_net"] for r in both), 2),
                "roi_no_net": round(100 * sum(r["pnl_no_net"] for r in both) / sum(r["stake"] for r in both), 2) if both else None,
                "pnl_same": round(sum(r["pnl"] for r in both), 2),
                "locked_pnl": round(sum(r["pnl"] or 0 for r in locked), 2),
                "in_play": round(sum(r["stake"] for r in rs if r["result"] == "pendiente"), 2)}

    all_ = block(rows)
    curve, eq = [], bankroll
    for r in sorted((r for r in rows if r["result"] != "pendiente" and r["pnl"] is not None),
                    key=lambda r: r["start_ts"] or 0):
        eq += r["pnl"]
        curve.append([(r["start_ts"] or 0) * 1000, round(eq, 2)])
    for r in rows:
        r["levels"] = json.loads(r["levels"]) if r["levels"] else None
    # Riesgo con y sin red. El partido se da por liquidado ~2 h después del inicio. Con red, el dinero de la
    # orden en Polymarket también queda inmovilizado: hasta 10 min antes del partido si no se llenó, o hasta
    # el final si se llenó.
    with_net, no_net = [], []
    for r in rows:
        end = (r["start_ts"] or r["created_ts"]) + 2 * 3600
        done = r["result"] not in ("pendiente", None)
        outcome = {"ganada": "won", "perdida": "lost", "anulada": "void", "asegurada": "won"}.get(r["result"])
        reserve = r["stake"] * r["odds"] * r["limit_price"]
        poly_end = end if r["status"] == "asegurada" else (r["start_ts"] or r["created_ts"]) - CANCEL_BEFORE_S
        with_net.append({"open_ts": r["created_ts"], "close_ts": end if done else None, "capital": r["stake"],
                         "pnl": r["pnl"] if done else None, "outcome": outcome})
        with_net.append({"open_ts": r["created_ts"], "close_ts": poly_end if r["status"] != "valor" else None,
                         "capital": reserve, "pnl": 0.0 if r["status"] != "valor" else None, "outcome": None})
        nn = r["pnl_no_net"]
        no_net.append({"open_ts": r["created_ts"], "close_ts": end if nn is not None else None, "capital": r["stake"],
                       "pnl": nn, "outcome": None if nn is None else ("won" if nn > 0 else "lost" if nn < 0 else "void")})
    return {"activated": float(meta.get("activated", 0)), "bankroll": bankroll, "no_data": no_data,
            "by_level": by_level(rows),
            "risk": risk_stats(with_net, bankroll), "risk_no_net": risk_stats(no_net, bankroll),
            "equity": round(bankroll + all_["pnl"], 2), "all": all_,
            "backtest": block([r for r in rows if r["source"] == "backtest"]),
            "live": block([r for r in rows if r["source"] == "vivo"]),
            "curve": curve, "rows": rows[:400]}
