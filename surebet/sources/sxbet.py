"""
SX Bet: exchange cripto P2P en USDC. Su API es pública y gratis para LEER mercados y libros
(docs.sx.bet); para apostar hace falta cuenta y wallet, y el bot puede operar por API.

  - GET /markets/active?sportIds=..&type=..&pageSize=100&paginationKey=..   (paginado con nextKey)
  - GET /orderbook-v3/snapshot?marketHash=..&showTakerPerspective=true    (libro de UN mercado, sin key)
    Con showTakerPerspective, outcomeOne es lo que consigue quien APUESTA al resultado uno:
    percentageOdds = probabilidad implícita x 1e20, size = monto del apostador (USDC, 6 decimales).

Comisión: 1 % de la ganancia neta, solo en apuestas ganadas (quien toma la cuota). Se modela como
un precio efectivo p / (0,99 + 0,01·p) por "share" que paga $1 neto, así el resto del escáner
recorre el libro igual que el de Polymarket.

Mercados que se leen:
  1    1X2 de fútbol: un mercado SÍ/NO por resultado ("Fulham" / "Not Fulham", "Tie" / "Not tie")
  52   ganador sin empate (tenis, esports)
  226  ganador con prórroga (básquet, NHL, MLB, NFL), igual que el ganador de Polymarket
  2    más/menos (solo fútbol, líneas .5: sin devolución)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from ..matching import MatchConfig, match_events
from ..models import AWAY, DRAW, HOME, OVER, THREE_WAY, UNDER, BookLevel, Quote, SourceEvent
from ..sports import esports_family
from .base import Source

API = "https://api.sx.bet"
SCALE = 10 ** 20
TAKER_FEE = 0.01           # 1 % de la ganancia, solo si la apuesta gana
MIN_STAKE = 1.0            # orden mínima: 1 USDC
MAX_LEVELS = 15
SPORT_FAMILY = {1: "basketball", 2: "hockey", 3: "baseball", 5: "soccer", 6: "tennis", 7: "mma",
                8: "americanfootball", 9: "esports"}
# Ganador que equivale al de Polymarket en cada deporte
WINNER_TYPE = {"basketball": 226, "hockey": 226, "baseball": 226, "americanfootball": 226,
               "tennis": 52, "esports": 52, "mma": 52}
TOTAL_LINES = {2.5}  # fútbol: la línea principal (la que más tiene Polymarket); menos libros que pedir
MARKETS_REFRESH_S = 180
SNAPSHOTS_PER_PASS = 15     # libros pedidos por pasada (no hay límite publicado, pero sin abusar)
REQUEST_GAP_S = 0.05


def effective_price(p: float) -> float:
    """Precio por $1 de cobro NETO de comisión: p / (0,99 + 0,01·p)."""
    return p / (1 - TAKER_FEE + TAKER_FEE * p)


def book_levels(levels: list[dict]) -> list[BookLevel]:
    """Niveles del libro (perspectiva del apostador) a BookLevel con la comisión incluida."""
    out = []
    for x in levels or []:
        try:
            p = int(x["percentageOdds"]) / SCALE
            stake = int(x["size"]) / 1e6
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < p < 1 and stake > 0:
            pe = effective_price(p)
            out.append(BookLevel(pe, stake / pe))
    out.sort(key=lambda l: l.price)
    return out[:MAX_LEVELS]


def family(m: dict) -> str | None:
    fam = SPORT_FAMILY.get(m.get("sportId"))
    if fam == "esports":
        fam = esports_family(name=m.get("leagueLabel", ""))
    return fam


def market_sides(m: dict) -> tuple[str, frozenset, str, frozenset, str] | None:
    """(mercado, cubre lado uno, etiqueta uno, cubre lado dos, etiqueta dos) o None si no se usa."""
    fam = family(m) or ""
    t, one, two = m.get("type"), m.get("outcomeOneName", ""), m.get("outcomeTwoName", "")
    home, away = m.get("teamOneName", ""), m.get("teamTwoName", "")
    if fam == "soccer" and t == 1:
        res = {home: HOME, away: AWAY}.get(one) or (DRAW if one.lower() == "tie" else None)
        if res is None:
            return None
        name = {HOME: home, AWAY: away, DRAW: "Empate"}[res]
        return "1x2", frozenset({res}), f"{name} (SI)", THREE_WAY - {res}, f"{name} (NO)"
    if fam == "soccer" and t == 2:
        line = m.get("line")
        if line is None or float(line) not in TOTAL_LINES:
            return None
        return f"ou_{float(line):g}", frozenset({OVER}), f"Más {float(line):g}", frozenset({UNDER}), f"Menos {float(line):g}"
    if fam and WINNER_TYPE.get(fam.split(":")[0]) == t:
        if one == home and two == away:
            return "ml", frozenset({HOME}), home, frozenset({AWAY}), away
        if one == away and two == home:
            return "ml", frozenset({AWAY}), one, frozenset({HOME}), two
    return None


def build_quotes(m: dict, book: dict, ts: float) -> list[Quote]:
    sides = market_sides(m)
    if sides is None:
        return []
    market, cov1, lab1, cov2, lab2 = sides
    url = "https://sx.bet"
    out = []
    for side, levels, covers, label in (("1", book.get("outcomeOne"), cov1, lab1), ("2", book.get("outcomeTwo"), cov2, lab2)):
        lv = book_levels(levels)
        if not lv:
            continue
        out.append(Quote(source=SXBetSource.name, feed=SXBetSource.name, covers=covers, odds=1 / lv[0].price,
                         label=label, url=url, ts=ts, max_stake=sum(l.price * l.size for l in lv),
                         min_stake=MIN_STAKE, asks=tuple(lv), market=market, ref=f"{m.get('marketHash', '')}|{side}"))
    return out


def slim_market(m: dict) -> dict:
    keys = ("marketHash", "type", "outcomeOneName", "outcomeTwoName", "teamOneName", "teamTwoName", "line",
            "gameTime", "sportXeventId", "sportId", "leagueLabel", "status")
    return {k: m[k] for k in keys if k in m}


class SXBetSource(Source):
    name = "sxbet"

    def __init__(self, sports: list[int] | None = None, horizon_h: float = 24, concurrency: int = 4,
                 match_cfg: MatchConfig | None = None) -> None:
        super().__init__()
        self.sports = sports or list(SPORT_FAMILY)
        self.horizon_h = horizon_h
        self.match_cfg = match_cfg or MatchConfig()
        self._sem = asyncio.Semaphore(concurrency)
        self._markets: dict[str, dict] = {}            # marketHash -> mercado (campos usados)
        self._markets_ts = 0.0
        self._books: dict[str, tuple[float, dict]] = {}  # marketHash -> (ts, libro del apostador)
        self._wanted: set[str] = set()                 # mercados de partidos emparejados con Polymarket
        self._targets: list[SourceEvent] = []
        self._task: asyncio.Task | None = None
        self._events_cache: list[SourceEvent] = []
        self._dirty = True
        self.requests = 0
        self._errors = 0

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> dict | None:
        async with self._sem:
            try:
                r = await client.get(f"{API}{path}", params=params, timeout=15)
                self.requests += 1
            except httpx.HTTPError:
                self._errors += 1
                return None
            finally:
                await asyncio.sleep(REQUEST_GAP_S)
        if r.status_code != 200:
            self._errors += 1
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        return data.get("data") if isinstance(data, dict) and data.get("status") == "success" else None

    async def fetch(self, client: httpx.AsyncClient, targets: list[SourceEvent] | None = None) -> list[SourceEvent]:
        self._targets = targets or []
        if self._task is None or self._task.done():
            if self._task and self._task.exception():
                self.status = f"error: {self._task.exception()!r}"[:160]
            self._task = asyncio.create_task(self._refresh(client))
        return self._build_events()

    async def _refresh(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        fams = {t.sport.split(":")[0] for t in self._targets}
        sports = [s for s in self.sports if SPORT_FAMILY[s] in fams]
        if now - self._markets_ts > MARKETS_REFRESH_S and sports:
            await self._load_markets(client, sports)
            self._match()
        due = []
        for h in self._wanted:
            m = self._markets.get(h)
            if not m:
                continue
            to_start = m.get("gameTime", now) - now
            ttl = 30 if to_start <= 0 else 60 if to_start < 3600 else 300 if to_start < 6 * 3600 else 900
            fetched = self._books.get(h, (0.0, {}))[0]
            if now - fetched >= ttl:
                due.append((fetched, h))
        due = [h for _, h in sorted(due)[:SNAPSHOTS_PER_PASS]]
        if not due:
            return
        results = await asyncio.gather(*(self._get(client, "/orderbook-v3/snapshot", marketHash=h,
                                                   showTakerPerspective="true") for h in due))
        ts = time.time()
        for h, book in zip(due, results):
            if isinstance(book, dict):
                self._books[h] = (ts, {"outcomeOne": book.get("outcomeOne") or [],
                                       "outcomeTwo": book.get("outcomeTwo") or []})
        self._books = {h: v for h, v in self._books.items() if h in self._wanted}
        self._dirty = True
        self.status = (f"{len(self._markets)} mercados | {len({self._markets[h]['sportXeventId'] for h in self._wanted if h in self._markets})} "
                       f"partidos emparejados ({len(self._wanted)} mercados) | llamadas {self.requests}"
                       + (f" | errores {self._errors}" if self._errors else ""))

    async def _load_markets(self, client: httpx.AsyncClient, sports: list[int]) -> None:
        horizon = time.time() + self.horizon_h * 3600
        markets: dict[str, dict] = {}
        key = None
        for _ in range(60):
            params = {"sportIds": ",".join(map(str, sports)), "type": "1,2,52,226", "pageSize": 100}
            if key:
                params["paginationKey"] = key
            data = await self._get(client, "/markets/active", **params)
            if data is None:
                return  # se reintenta en la próxima pasada; se conservan los mercados anteriores
            for m in data.get("markets") or []:
                if m.get("status") == "ACTIVE" and m.get("gameTime", 0) <= horizon and market_sides(m):
                    markets[m["marketHash"]] = slim_market(m)
            key = data.get("nextKey")
            if not key:
                break
        self._markets, self._markets_ts = markets, time.time()

    def resolution_for(self, event_id: str, market: str) -> dict[str, tuple[str, str]]:
        """{resultado: ("sx:<marketHash>", lado que paga si ocurre)} para liquidar con el resultado de SX."""
        out: dict[str, tuple[str, str]] = {}
        for h, m in self._markets.items():
            if m.get("sportXeventId") != event_id:
                continue
            sides = market_sides(m)
            if not sides or sides[0] != market:
                continue
            _mk, cov1, _l1, cov2, _l2 = sides
            if len(cov1) == 1:
                out[next(iter(cov1))] = (f"sx:{h}", "1")
            if len(cov2) == 1:
                out[next(iter(cov2))] = (f"sx:{h}", "2")
        return out

    def _bare_events(self) -> dict[str, SourceEvent]:
        out: dict[str, SourceEvent] = {}
        for m in self._markets.values():
            eid = m["sportXeventId"]
            if eid not in out:
                fam = family(m) or ""
                start = datetime.fromtimestamp(m["gameTime"], timezone.utc) if m.get("gameTime") else None
                out[eid] = SourceEvent(feed=self.name, event_id=eid, sport=fam, home=m.get("teamOneName", ""),
                                       away=m.get("teamTwoName", ""), start=start, three_way=fam == "soccer",
                                       live=bool(start and start.timestamp() <= time.time()),
                                       league=m.get("leagueLabel", ""), url="https://sx.bet")
        return out

    def _match(self) -> None:
        """Solo se piden libros de partidos que también están en Polymarket o en Cloudbet."""
        bare = self._bare_events()
        clusters = match_events({"polymarket": self._targets, self.name: list(bare.values())}, self.match_cfg)
        ids = {ev.event_id for c in clusters if self.name in c.feeds and len(c.feeds) >= 2
               for ev, _ in c.members if ev.feed == self.name}
        self._wanted = {h for h, m in self._markets.items() if m["sportXeventId"] in ids}

    def _build_events(self) -> list[SourceEvent]:
        if not self._dirty:
            return self._events_cache
        events = self._bare_events()
        for h, (ts, book) in self._books.items():
            m = self._markets.get(h)
            if m and m["sportXeventId"] in events:
                events[m["sportXeventId"]].quotes.extend(build_quotes(m, book, ts))
        self._events_cache = [e for e in events.values() if e.quotes]
        self._dirty = False
        return self._events_cache
