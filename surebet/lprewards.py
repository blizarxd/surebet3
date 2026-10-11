"""
Recompensas de liquidez de Polymarket, en SIMULACIÓN (nunca envía órdenes). Dos versiones en paralelo.

Polymarket reparte cada día una recompensa por mercado (rewards_daily_rate, en USDC) entre quienes dejan órdenes
límite cerca del precio medio. Cada minuto puntúa cada orden con ((v - s) / v)^2 × tamaño (v = spread máximo del
mercado, s = distancia al medio) si la orden tiene al menos min_size shares; un lado solo vale un tercio (c = 3)
salvo que haya órdenes en los dos lados. Lo que te toca = tu puntaje / el de todos.

El bot simulado, en partidos que todavía no empiezan:
  1. Elige mercados con buena recompensa por dólar puesto (según la competencia real del libro).
  2. Pone COMPRA del SÍ a ~1¢ por debajo del medio y COMPRA del NO a ~1¢ por debajo de su medio (= vende el SÍ
     ~1¢ por encima). Si el precio se mueve, las corre (sin cruzar el libro).
  3. Cada minuto lee el libro: suma su parte de la recompensa y mira si alguien le "llenó" una orden.
  4. Si se llenan las dos: SÍ + NO = $1 seguro → ganancia fija. Si se llena una sola, deja de cotizar ese lado;
     al cerrar vende lo que tenga al mejor comprador (ahí se ve la pérdida por "selección adversa").

Versiones (VARIANTS):
  - base: cierra 2 min antes del partido, sin más protecciones.
  - protegida: retira todo 75 min antes (alineaciones / inactivos), se pausa 10 min si el precio salta ≥ 2¢ en un
    minuto, si le llenan un solo lado lo cubre al instante comprando el otro (pérdida chica y fija en vez de quedar
    expuesta), tope de pérdida diaria de $20 y sin mercados de jugadores (yardas, puntos...).
Resultado por día: recompensas − pérdidas por llenadas, para cada versión.

Supuestos conservadores: toda la liquidez del libro cuenta como competencia, y la de los rivales se toma como el lado
más grande. Optimista: solo se mira el libro una vez por minuto y no hay cola de órdenes.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import httpx

from .pricing import poly_fee_per_share

CLOB = "https://clob.polymarket.com"
C_SINGLE = 3.0
MID_RANGE = (0.15, 0.85)
REFRESH_MARKETS_S = 20 * 60
STEP_S = 60
MIN_DAILY_RATE = 10.0
MAX_MARKETS = 10
MAX_PER_MARKET = 1000.0   # si el mercado exige un tamaño mínimo grande (NFL: 1000 shares), hasta este monto
HORIZON_S = 24 * 3600
TAKER_FEE = 0.05          # comisión taker de deportes en Polymarket (para la cobertura al instante)
# Solo partidos (fútbol, básquet, esports, tenis...): "A vs B", "O/U", "Spread", "win on fecha".
MATCH_RE = re.compile(r"\bvs\.?\s|\bo/u\b|^spread:|\bboth teams\b|\bdraw\b|\bwin on \d{4}-\d{2}-\d{2}", re.I)
# Mercados de un jugador: una lesión o una noticia los mueve de golpe (puro riesgo de información)
PROP_RE = re.compile(r"yards|receptions|touchdown|rebounds|assists|strikeouts|passing|rushing|receiving|"
                     r"points o/u|: points|shots on|goals scored by", re.I)

VARIANTS = {
    "base": {"close_before_s": 120, "vol_pause_c": None, "pause_s": 0, "hedge": False, "daily_stop": None,
             "no_props": False, "label": "Base (cierra 2 min antes)"},
    "protegida": {"close_before_s": 75 * 60, "vol_pause_c": 2.0, "pause_s": 600, "hedge": True, "daily_stop": 20.0,
                  "no_props": True, "label": "Protegida (75 min antes, pausa, cobertura, tope diario)"},
}


def is_match(question: str) -> bool:
    return bool(MATCH_RE.search(question or ""))


def is_prop(question: str) -> bool:
    return bool(PROP_RE.search(question or ""))


SCHEMA = """
CREATE TABLE IF NOT EXISTS lp_markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT, question TEXT, slug TEXT, token_yes TEXT,
    start_ts REAL, rate REAL, max_spread REAL, min_size REAL, tick REAL, size REAL,
    opened_ts REAL, status TEXT,            -- active | closed
    yes_px REAL, no_px REAL,                -- precio de nuestras compras (SÍ y NO)
    yes_fill_px REAL, yes_fill_ts REAL, no_fill_px REAL, no_fill_ts REAL,
    requotes INTEGER DEFAULT 0, minutes INTEGER DEFAULT 0, last_step REAL,
    last_share REAL, last_mid REAL,
    reward_usd REAL DEFAULT 0, inv_pnl_usd REAL,
    closed_ts REAL, close_reason TEXT
);
CREATE TABLE IF NOT EXISTS lp_daily (
    day TEXT PRIMARY KEY, reward_usd REAL DEFAULT 0, inv_pnl_usd REAL DEFAULT 0, fills INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lp_daily_v (
    day TEXT, variant TEXT, reward_usd REAL DEFAULT 0, inv_pnl_usd REAL DEFAULT 0, fills INTEGER DEFAULT 0,
    PRIMARY KEY (day, variant)
);
CREATE TABLE IF NOT EXISTS lp_meta (k TEXT PRIMARY KEY, v TEXT);
"""
MIGRATIONS = {"variant": "TEXT DEFAULT 'base'", "paused_until": "REAL", "pauses": "INTEGER DEFAULT 0"}

_CAND: dict = {"ts": 0.0, "list": []}     # candidatos compartidos por las dos versiones
_CAND_LOCK = asyncio.Lock()


def score(price: float, mid: float, v_cents: float) -> float:
    """Puntaje por share de una orden a `price` (0 si está más lejos que el spread máximo)."""
    s = abs(price - mid) * 100
    return ((v_cents - s) / v_cents) ** 2 if s < v_cents else 0.0


def q_min(q1: float, q2: float, mid: float) -> float:
    if MID_RANGE[0] - 0.05 <= mid <= MID_RANGE[1] + 0.05:  # entre 0,10 y 0,90 un lado solo vale 1/3
        return max(min(q1, q2), max(q1, q2) / C_SINGLE)
    return min(q1, q2)


def book_sides(book: dict) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    bids = sorted(((float(x["price"]), float(x["size"])) for x in book.get("bids") or []), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in book.get("asks") or [])
    return bids, asks


def competition(bids, asks, mid: float, v: float) -> float:
    """Puntaje de los demás (conservador: el lado más grande del libro)."""
    q1 = sum(score(p, mid, v) * s for p, s in bids)
    q2 = sum(score(p, mid, v) * s for p, s in asks)
    return max(q1, q2)


def quote_prices(mid: float, best_bid: float, best_ask: float, v: float, tick: float) -> tuple[float, float]:
    """(precio de compra del SÍ, precio de compra del NO) a ~1¢ del medio, sin cruzar el libro."""
    d = min(0.01, v / 200)
    yes = math.floor((mid - d) / tick + 1e-9) * tick
    yes = min(yes, best_ask - tick)
    sell = math.ceil((mid + d) / tick - 1e-9) * tick          # vender SÍ = comprar NO a 1 - sell
    sell = max(sell, best_bid + tick)
    return round(yes, 4), round(1 - sell, 4)


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00").replace(" ", "T")).timestamp()
    except ValueError:
        return None


class RewardsSimulator:
    def __init__(self, db_path: Path, capital: float | None = None, per_market: float | None = None,
                 variant: str = "base") -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(lp_markets)")}
        for col, decl in MIGRATIONS.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE lp_markets ADD COLUMN {col} {decl}")
        if not self.conn.execute("SELECT 1 FROM lp_daily_v LIMIT 1").fetchone():   # días viejos = versión base
            self.conn.execute("INSERT OR IGNORE INTO lp_daily_v SELECT day, 'base', reward_usd, inv_pnl_usd, fills "
                              "FROM lp_daily")
        self.variant, self.rules = variant, VARIANTS[variant]
        self.capital = capital if capital is not None else float(os.getenv("LP_CAPITAL", "2000"))
        self.per_market = per_market if per_market is not None else float(os.getenv("LP_PER_MARKET", "250"))
        started_key = "started" if variant == "base" else f"started_{variant}"
        self.conn.execute("INSERT OR IGNORE INTO lp_meta VALUES (?, ?)", (started_key, str(time.time())))
        self.conn.execute("INSERT OR REPLACE INTO lp_meta VALUES ('capital', ?)", (str(self.capital),))
        self.conn.commit()
        self.status = "iniciando"

    # ------------------------------------------------------------------ bucle
    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "surebet3/lp-sim"}) as client:
            while True:
                t0 = time.time()
                try:
                    await self.step(client)
                except Exception as e:   # nunca tumba al escáner
                    self.status = f"error: {e!r}"[:160]
                await asyncio.sleep(max(5.0, STEP_S - (time.time() - t0)))

    async def step(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        async with _CAND_LOCK:
            if now - _CAND["ts"] > REFRESH_MARKETS_S:
                _CAND["list"], _CAND["ts"] = await self._load_candidates(client, now), now
        await self._advance(client, now)
        await self._open_new(client, now)
        active = self.conn.execute("SELECT COUNT(*) FROM lp_markets WHERE status='active' AND variant=?",
                                   (self.variant,)).fetchone()[0]
        self.status = f"ok · {active} mercados con órdenes · {len(_CAND['list'])} candidatos"

    async def _get(self, client, path: str, **params):
        r = await client.get(f"{CLOB}{path}", params=params or None)
        r.raise_for_status()
        return r.json()

    async def _load_candidates(self, client, now: float) -> list[dict]:
        """Mercados de partidos con recompensa, que empiezan entre 30 min y 24 h."""
        out, cursor = [], ""
        for _ in range(80):
            d = await self._get(client, "/sampling-markets", **({"next_cursor": cursor} if cursor else {}))
            for m in d.get("data") or []:
                start = _ts(m.get("game_start_time"))
                rw = m.get("rewards") or {}
                rate = sum(float(x.get("rewards_daily_rate") or 0) for x in rw.get("rates") or [])
                toks = [t.get("token_id") for t in m.get("tokens") or []]
                if (not start or not 1800 <= start - now <= HORIZON_S or rate < MIN_DAILY_RATE or len(toks) != 2
                        or not is_match(m.get("question") or "")
                        or not m.get("accepting_orders") or m.get("closed") or not rw.get("max_spread")):
                    continue
                out.append({"condition_id": m.get("condition_id"), "question": m.get("question") or "",
                            "slug": m.get("market_slug") or "", "token_yes": toks[0], "start_ts": start, "rate": rate,
                            "max_spread": float(rw["max_spread"]), "min_size": float(rw.get("min_size") or 0),
                            "tick": float(m.get("minimum_tick_size") or 0.01)})
            cursor = d.get("next_cursor") or ""
            if not cursor or cursor == "LTE=":
                break
            await asyncio.sleep(0.1)
        out.sort(key=lambda c: c["rate"], reverse=True)
        return out[:60]

    def _in_use(self) -> float:
        rows = self.conn.execute("SELECT size, yes_px, no_px, yes_fill_px, no_fill_px FROM lp_markets "
                                 "WHERE status='active' AND variant=?", (self.variant,)).fetchall()
        return sum(size * ((yf or yp) + (nf or np_)) for size, yp, np_, yf, nf in rows)

    def _stopped_today(self) -> bool:
        stop = self.rules["daily_stop"]
        if not stop:
            return False
        r = self.conn.execute("SELECT inv_pnl_usd FROM lp_daily_v WHERE day=? AND variant=?",
                              (time.strftime("%Y-%m-%d"), self.variant)).fetchone()
        return bool(r and r[0] is not None and r[0] <= -stop)

    def _daily(self, day: str, reward: float = 0.0, inv: float = 0.0, fills: int = 0) -> None:
        self.conn.execute(
            "INSERT INTO lp_daily_v (day, variant, reward_usd, inv_pnl_usd, fills) VALUES (?,?,?,?,?) "
            "ON CONFLICT(day, variant) DO UPDATE SET reward_usd=reward_usd+excluded.reward_usd, "
            "inv_pnl_usd=inv_pnl_usd+excluded.inv_pnl_usd, fills=fills+excluded.fills",
            (day, self.variant, reward, inv, fills))

    async def _open_new(self, client, now: float) -> None:
        if self._stopped_today():
            return
        active = {r[0] for r in self.conn.execute("SELECT condition_id FROM lp_markets WHERE status='active' "
                                                  "AND variant=?", (self.variant,))}
        done = {r[0] for r in self.conn.execute("SELECT condition_id FROM lp_markets WHERE variant=?", (self.variant,))}
        if len(active) >= MAX_MARKETS:
            return
        free = self.capital - self._in_use()
        min_lead = self.rules["close_before_s"] + 1800   # que quede al menos media hora cotizando
        scored = []
        for c in _CAND["list"]:
            if c["condition_id"] in done or c["start_ts"] - now < min_lead:
                continue
            if self.rules["no_props"] and is_prop(c["question"]):
                continue
            try:
                book = await self._get(client, "/book", token_id=c["token_yes"])
            except (httpx.HTTPError, ValueError):
                continue
            bids, asks = book_sides(book)
            if not bids or not asks:
                continue
            mid = (bids[0][0] + asks[0][0]) / 2
            if not MID_RANGE[0] <= mid <= MID_RANGE[1] or (asks[0][0] - bids[0][0]) * 100 > c["max_spread"]:
                continue
            yes_px, no_px = quote_prices(mid, bids[0][0], asks[0][0], c["max_spread"], c["tick"])
            size = max(math.floor(self.per_market / (yes_px + no_px)), math.ceil(c["min_size"]))
            if size < 1 or yes_px <= 0 or no_px <= 0 or size * (yes_px + no_px) > MAX_PER_MARKET:
                continue
            ours = q_min(score(yes_px, mid, c["max_spread"]) * size, score(1 - no_px, mid, c["max_spread"]) * size, mid)
            share = ours / (competition(bids, asks, mid, c["max_spread"]) + ours) if ours else 0.0
            per_dollar = c["rate"] * share / (size * (yes_px + no_px))
            scored.append((per_dollar, c, mid, yes_px, no_px, size, share))
            await asyncio.sleep(0.1)
            if len(scored) >= 25:
                break
        scored.sort(key=lambda x: x[0], reverse=True)
        for per_dollar, c, mid, yes_px, no_px, size, share in scored:
            need = size * (yes_px + no_px)
            if len(active) >= MAX_MARKETS or need > free or per_dollar <= 0:
                continue
            self.conn.execute(
                "INSERT INTO lp_markets (condition_id, question, slug, token_yes, start_ts, rate, max_spread, min_size, "
                "tick, size, opened_ts, status, yes_px, no_px, last_step, last_share, last_mid, variant) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'active', ?,?,?,?,?,?)",
                (c["condition_id"], c["question"], c["slug"], c["token_yes"], c["start_ts"], c["rate"], c["max_spread"],
                 c["min_size"], c["tick"], size, now, yes_px, no_px, now, round(share, 5), mid, self.variant))
            active.add(c["condition_id"])
            free -= need
        self.conn.commit()

    def _close(self, mid_id: int, now: float, day: str, pnl: float, reason: str) -> None:
        self.conn.execute("UPDATE lp_markets SET status='closed', closed_ts=?, close_reason=?, inv_pnl_usd=? WHERE id=?",
                          (now, reason, round(pnl, 4), mid_id))
        self._daily(day, inv=pnl)

    async def _advance(self, client, now: float) -> None:
        rows = self.conn.execute(
            "SELECT id, token_yes, start_ts, rate, max_spread, min_size, tick, size, yes_px, no_px, yes_fill_px, "
            "no_fill_px, last_step, last_mid, paused_until FROM lp_markets WHERE status='active' AND variant=?",
            (self.variant,)).fetchall()
        day = time.strftime("%Y-%m-%d")
        stop_all = self._stopped_today()
        for (mid_id, token, start, rate, v, min_size, tick, size, yes_px, no_px, yes_fill, no_fill, last, last_mid,
             paused_until) in rows:
            try:
                book = await self._get(client, "/book", token_id=token)
            except (httpx.HTTPError, ValueError):
                continue
            bids, asks = book_sides(book)
            if not bids or not asks:
                continue
            best_bid, best_ask = bids[0][0], asks[0][0]
            mid = (best_bid + best_ask) / 2
            minutes = max(0.0, min(now - last, 5 * STEP_S)) / 60
            # Freno por volatilidad: si el precio saltó, se retiran las órdenes un rato (sin recompensa ni llenadas)
            pause_c = self.rules["vol_pause_c"]
            jumped = pause_c and last_mid and abs(mid - last_mid) * 100 >= pause_c
            paused = bool(paused_until and now < paused_until)
            if jumped and not paused and (yes_fill is None or no_fill is None):
                paused_until, paused = now + self.rules["pause_s"], True
                self.conn.execute("UPDATE lp_markets SET pauses=pauses+1 WHERE id=?", (mid_id,))
            fills = 0
            if not paused:
                # ¿Nos llenaron? (alguien vendió hasta nuestra compra del SÍ / compró hasta nuestra venta del SÍ)
                if yes_fill is None and best_ask <= yes_px + 1e-9:
                    yes_fill, fills = yes_px, fills + 1
                    self.conn.execute("UPDATE lp_markets SET yes_fill_px=?, yes_fill_ts=? WHERE id=?",
                                      (yes_fill, now, mid_id))
                if no_fill is None and best_bid >= 1 - no_px - 1e-9:
                    no_fill, fills = no_px, fills + 1
                    self.conn.execute("UPDATE lp_markets SET no_fill_px=?, no_fill_ts=? WHERE id=?",
                                      (no_fill, now, mid_id))
            # Recompensa de los minutos con las órdenes puestas (en pausa no hay órdenes, no hay recompensa)
            share = 0.0
            if not paused:
                q1 = score(yes_px, mid, v) * size if yes_fill is None else 0.0
                q2 = score(1 - no_px, mid, v) * size if no_fill is None else 0.0
                ours = q_min(q1, q2, mid) if size >= min_size else 0.0
                share = ours / (competition(bids, asks, mid, v) + ours) if ours else 0.0
            reward = rate / 1440 * share * minutes
            # Correr las órdenes que quedaron lejos (fuera del spread que puntúa) o que cruzarían el libro
            new_yes, new_no = quote_prices(mid, best_bid, best_ask, v, tick)
            requote = 0
            if yes_fill is None and (paused or score(yes_px, mid, v) == 0 or yes_px >= best_ask):
                yes_px, requote = new_yes, 1
            if no_fill is None and (paused or score(1 - no_px, mid, v) == 0 or 1 - no_px <= best_bid):
                no_px, requote = new_no, 1
            self.conn.execute(
                "UPDATE lp_markets SET reward_usd=reward_usd+?, minutes=minutes+?, last_step=?, last_share=?, last_mid=?, "
                "yes_px=?, no_px=?, requotes=requotes+?, paused_until=? WHERE id=?",
                (reward, round(minutes) if not paused else 0, now, round(share, 5), mid, yes_px, no_px, requote,
                 paused_until, mid_id))
            self._daily(day, reward=reward, fills=fills)
            both = yes_fill is not None and no_fill is not None
            one = (yes_fill is not None) != (no_fill is not None)
            if both:     # SÍ + NO = $1 seguro al resolverse
                self._close(mid_id, now, day, size * (1 - yes_fill - no_fill), "se llenaron las dos: ganancia fija")
            elif one and self.rules["hedge"]:
                # Cobertura al instante: se compra el otro lado al precio publicado (con comisión) → pérdida chica fija
                if yes_fill is not None:
                    ask_no = 1 - best_bid
                    cost = ask_no + poly_fee_per_share(ask_no, TAKER_FEE)
                    pnl = size * (1 - yes_fill - cost)
                else:
                    cost = best_ask + poly_fee_per_share(best_ask, TAKER_FEE)
                    pnl = size * (1 - no_fill - cost)
                self._close(mid_id, now, day, pnl, "le llenaron un lado: cubierto al instante comprando el otro")
            elif stop_all or now >= start - self.rules["close_before_s"]:
                why = "tope de pérdida diaria" if stop_all else "cierre antes del partido"
                if yes_fill is not None:   # quedó con SÍ: se vende al mejor comprador
                    self._close(mid_id, now, day, size * (best_bid - yes_fill), f"{why}: vendió el SÍ que le llenaron")
                elif no_fill is not None:
                    self._close(mid_id, now, day, size * ((1 - best_ask) - no_fill), f"{why}: vendió el NO que le llenaron")
                else:
                    self._close(mid_id, now, day, 0.0, f"{why}: no le llenaron nada")
            await asyncio.sleep(0.05)
        self.conn.commit()


def summary_variant(conn, variant: str) -> dict:
    meta = dict(conn.execute("SELECT k, v FROM lp_meta").fetchall())
    daily = [dict(zip(("day", "reward", "inv_pnl", "fills"), r)) for r in conn.execute(
        "SELECT day, reward_usd, inv_pnl_usd, fills FROM lp_daily_v WHERE variant=? ORDER BY day", (variant,))]
    cols = ("question", "slug", "start_ts", "rate", "max_spread", "size", "opened_ts", "status", "yes_px", "no_px",
            "yes_fill_px", "no_fill_px", "requotes", "minutes", "last_share", "last_mid", "reward_usd",
            "inv_pnl_usd", "closed_ts", "close_reason", "pauses")
    active = [dict(zip(cols, r)) for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM lp_markets WHERE status='active' AND variant=? ORDER BY start_ts", (variant,))]
    closed = [dict(zip(cols, r)) for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM lp_markets WHERE status='closed' AND variant=? ORDER BY closed_ts DESC LIMIT 60",
        (variant,))]
    tot = conn.execute("SELECT COUNT(*), COALESCE(SUM(reward_usd),0), COALESCE(SUM(inv_pnl_usd),0), "
                       "COALESCE(SUM(yes_fill_px IS NOT NULL) + SUM(no_fill_px IS NOT NULL),0), "
                       "COALESCE(SUM(yes_fill_px IS NOT NULL AND no_fill_px IS NOT NULL),0), COALESCE(SUM(pauses),0) "
                       "FROM lp_markets WHERE variant=?", (variant,)).fetchone()
    started = float(meta.get("started" if variant == "base" else f"started_{variant}", time.time()))
    days = max((time.time() - started) / 86400, 1e-9)
    n, reward, inv, fills, pairs, pauses = tot
    net = reward + inv
    in_use = sum(m["size"] * ((m["yes_fill_px"] or m["yes_px"]) + (m["no_fill_px"] or m["no_px"])) for m in active)
    for m in active + closed:
        m["category"] = "partido" if is_match(m["question"]) else "otro (clima, etc.)"
        m["variant"] = variant
    for d in daily:
        d["net"] = round(d["reward"] + d["inv_pnl"], 2)
        d["reward"], d["inv_pnl"] = round(d["reward"], 2), round(d["inv_pnl"], 2)
    return {"variant": variant, "label": VARIANTS[variant]["label"], "capital": float(meta.get("capital", 2000)),
            "started": started, "days": round(days, 2), "markets": n, "reward": round(reward, 2),
            "inv_pnl": round(inv, 2), "net": round(net, 2),
            # antes de 1 día completo la proyección diaria es ruido
            "per_day": round(net / days, 2) if days >= 1 else None,
            "fills": fills, "pairs": pairs, "pauses": pauses, "in_use": round(in_use, 2),
            "daily": daily, "active": active, "closed": closed}


def summary(conn) -> dict | None:
    try:
        return {v: summary_variant(conn, v) for v in VARIANTS}
    except sqlite3.OperationalError:
        return None
