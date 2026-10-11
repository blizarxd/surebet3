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

from .safety import golden_value

CLOB = "https://clob.polymarket.com"
LOCK_PCT = 1.0
CANCEL_BEFORE_S = 600
CROSS = 0.005           # el precio tiene que pasar medio centavo nuestra red para contarla llenada
CHECK_EVERY_S = 300     # cada cuánto se revisa una red que sigue esperando
PICKS_PER_RUN = 25      # para no golpear a Polymarket: tandas chicas

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
    def __init__(self, db_path: Path, bankroll: float = 1000.0, lock_pct: float = LOCK_PCT) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.bankroll, self.lock_pct = bankroll, lock_pct
        self.conn.execute("INSERT OR IGNORE INTO net_meta VALUES ('activated', ?)", (str(time.time()),))
        self.conn.execute("INSERT OR REPLACE INTO net_meta VALUES ('bankroll', ?)", (str(bankroll),))
        self.conn.commit()
        self.activated = float(self.conn.execute("SELECT v FROM net_meta WHERE k='activated'").fetchone()[0])
        self.status = "iniciando"

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
                 leg.get("label"), leg.get("source"), odds, ev, kelly_stake(odds, ev, self.bankroll),
                 net_label(market, covers, leg.get("label", ""), title), lim, json.dumps(tokens), json.dumps(covers)))
        self.conn.commit()

    async def update(self, client: httpx.AsyncClient) -> int:
        self._sync_picks()
        now = time.time()
        rows = self.conn.execute(
            "SELECT key, created_ts, start_ts, odds, limit_price, tokens, covers, best_gap FROM net_picks "
            "WHERE status='valor' AND (last_check IS NULL OR last_check < ?) ORDER BY COALESCE(last_check, 0) LIMIT ?",
            (now - CHECK_EVERY_S, PICKS_PER_RUN)).fetchall()
        updates = []  # se escriben al final: no dejar la base tomada mientras se consulta a Polymarket
        for key, created, start, odds, lim, tokens_json, covers_json, best_gap in rows:
            end = min(now, start - CANCEL_BEFORE_S)
            path = await self._path(client, json.loads(tokens_json), set(json.loads(covers_json)), created, end)
            status, lock_ts, gap_now = "valor", None, None
            for t, pa in path:
                gap = 100 * ((1 - pa) + CROSS - lim)          # ¢ que le faltan al precio para llenar la red
                best_gap = gap if best_gap is None else min(best_gap, gap)
                gap_now = gap
                if gap <= 0:
                    status, lock_ts = "asegurada", t
                    break
            if status == "valor" and now >= start - CANCEL_BEFORE_S:
                status = "quedó como valor"
            updates.append((status, lock_ts, best_gap, gap_now, now, key))
        self.conn.executemany("UPDATE net_picks SET status=?, lock_ts=?, best_gap=?, gap_now=?, last_check=? WHERE key=?",
                              updates)
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
                pts = r.json().get("history") or [] if r.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                return []
            # Polymarket a veces devuelve puntos fuera del rango pedido: se filtran
            hs[out] = {int(p["t"]) // 60: float(p["p"]) for p in pts if a <= p["t"] <= b}
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


def summary(conn) -> dict | None:
    try:
        meta = dict(conn.execute("SELECT k, v FROM net_meta").fetchall())
        cols = ("key", "source", "created_ts", "start_ts", "title", "sport", "market", "selection", "house", "odds",
                "ev_pct", "stake", "net_label", "limit_price", "status", "lock_ts", "best_gap", "gap_now", "result",
                "pnl", "pnl_no_net")
        rows = [dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {', '.join(cols)} FROM net_picks ORDER BY created_ts DESC")]
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
    return {"activated": float(meta.get("activated", 0)), "bankroll": bankroll,
            "equity": round(bankroll + all_["pnl"], 2), "all": all_,
            "backtest": block([r for r in rows if r["source"] == "backtest"]),
            "live": block([r for r in rows if r["source"] == "vivo"]),
            "curve": curve, "rows": rows[:400]}
