"""
Kalshi: exchange de contratos de eventos (regulado por la CFTC) con API pública para LEER sin cuenta
(docs.kalshi.com). Para operar hace falta cuenta y clave de API; el bot podría mandar órdenes por API.

  - GET /events?status=open&with_nested_markets=true&limit=200&cursor=..   todos los eventos abiertos (~65 págs.)
  - GET /markets/orderbooks?tickers=A&tickers=B..                          libros de varios mercados a la vez
    El libro trae solo compradores de SÍ y de NO ([precio, contratos]). Comprar SÍ = cruzar con el mejor
    comprador de NO: ask del SÍ = 1 − bid del NO (y al revés).

Cada resultado de un partido es un mercado SÍ/NO (fútbol: local, visitante y empate a 90'; el resto: los dos
equipos, con prórroga). SÍ cubre ese resultado y NO cubre los demás, igual que Polymarket.

Comisión (quien toma): 0,07 × contratos × p × (1 − p), redondeada al centavo por orden: se modela como la de
Polymarket (fee_rate 0,07). Hora: Kalshi publica el FINAL esperado; en deportes de equipo es inicio + 3 h.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from ..matching import MatchConfig, match_events
from ..models import AWAY, DRAW, HOME, THREE_WAY, BookLevel, Quote, SourceEvent
from ..pricing import book_capacity, poly_odds
from ..sports import esports_family
from .base import Source, parse_iso
from .kalshi_teams import TEAMS

API = "https://api.elections.kalshi.com/trade-api/v2"
NAME = "kalshi"
FEE_RATE = 0.07
MIN_STAKE = 1.0
MAX_LEVELS = 15
TEAM_SPORTS = {"soccer", "basketball", "hockey", "baseball", "americanfootball"}
END_OFFSET = timedelta(hours=3)          # final esperado − inicio en deportes de equipo
DISCOVERY_S = 600
BOOKS_PER_CALL = 20
CALLS_PER_PASS = 10
REQUEST_GAP_S = 0.1
MIN_PASS_S = 15.0         # una pasada como mucho cada 15 s (sin martillar la API)
DATE_RE = re.compile(r"^\d{2}[A-Z]{3}\d{2}")

# Serie (sin "KX") -> familia. Solo ganador del partido: se excluyen hándicaps, totales, sets, dobles...
FAMILY_PATTERNS = [
    (r"^(NFL|NCAAF|CFL|UFL)GAME$", "americanfootball"),
    (r"^(MLB|KBO|NPB|LMB|CPBL)GAME$", "baseball"),
    (r"^(NHL|KHL|AHL|SHL|DEL|LIIGA|ELH|IIHF|NLA|CHL)GAME$", "hockey"),
    (r"^(NBA|WNBA|NCAAMB|NCAAB|NCAAWB|EUROLEAGUE|EUROCUP|ACB|FIBA|BBL|BSL|CBA|KBL|NBL|ABA|LNB|BNXT|BBSERIEA|"
     r"BAL|BIG3|GBL|LBL|KML|BBINTL)GAME$", "basketball"),
    (r"^(ATP|WTA|ITF|ITFW|CHALLENGER|ATPCHALLENGER|DAVISCUP|BJK)MATCH$", "tennis"),
    (r"^(UFC|PFL|MMA)FIGHT$", "mma"),
    (r"^BOXING(FIGHT|MATCH)$", "boxing"),
    (r"^DARTSMATCH$", "darts"),
    (r"^(CS2|CSGO)GAME$", "esports:cs2"), (r"^LOLGAME$", "esports:lol"), (r"^DOTA2GAME$", "esports:dota2"),
    (r"^VALORANTGAME$", "esports:valorant"), (r"^R6GAME$", "esports:r6"),
]


def series_family(series: str) -> str | None:
    s = series[2:] if series.startswith("KX") else series
    for pattern, fam in FAMILY_PATTERNS:
        if re.match(pattern, s):
            return fam
    if "ESPORT" in s:
        return esports_family(name=s)
    return None


def _code(ticker: str) -> str:
    return ticker.rsplit("-", 1)[-1]


def _ordered(event: dict, markets: list[dict]) -> list[dict]:
    """Equipos en el orden del ticker del evento (KXNBAGAME-26OCT12BKNWAS: primero BKN, después WAS)."""
    tail = DATE_RE.sub("", event["event_ticker"].split("-", 1)[-1])
    def pos(m):
        i = tail.find(_code(m["ticker"]))
        return i if i >= 0 else 99
    return sorted(markets, key=pos)


def parse_event(e: dict, now: float, horizon_h: float) -> dict | None:
    """Evento de partido de Kalshi -> dict compacto (o None si no es un ganador de partido que sirva)."""
    if e.get("category") != "Sports" or (e.get("product_metadata") or {}).get("competition_scope") != "Game":
        return None
    markets = [m for m in e.get("markets") or [] if m.get("status") == "active"]
    ties = [m for m in markets if _code(m["ticker"]) == "TIE" or (m.get("yes_sub_title") or "").lower() == "tie"]
    teams = [m for m in markets if m not in ties]
    if len(teams) != 2 or len(ties) > 1:
        return None
    fam = "soccer" if ties else series_family(e.get("series_ticker", ""))
    if fam is None or (fam == "soccer" and not ties):
        return None
    when = parse_iso(teams[0].get("occurrence_datetime") or teams[0].get("expected_expiration_time"))
    if when is None:
        return None
    start = when - END_OFFSET if fam.split(":")[0] in TEAM_SPORTS else when
    if not now < start.timestamp() <= now + horizon_h * 3600:
        return None                           # solo prepartido dentro del horizonte
    names = TEAMS.get(e.get("series_ticker", ""), {})
    home, away = _ordered(e, teams)
    outs = [(home, HOME), (away, AWAY)] + [(t, DRAW) for t in ties]
    return {
        "id": e["event_ticker"], "series": e.get("series_ticker", ""), "sport": fam, "start": start,
        "home": names.get(_code(home["ticker"]), home.get("yes_sub_title") or ""),
        "away": names.get(_code(away["ticker"]), away.get("yes_sub_title") or ""),
        "three_way": bool(ties), "league": (e.get("product_metadata") or {}).get("competition", ""),
        "markets": [{"ticker": m["ticker"], "outcome": o} for m, o in outs],
    }


def asks_from_bids(bids: list) -> list[BookLevel]:
    """Compradores del lado contrario -> escalera de compra de este lado (precio = 1 − bid)."""
    out = []
    for row in bids or []:
        try:
            p, q = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 < p < 1 and q > 0:
            out.append(BookLevel(round(1 - p, 4), q))
    out.sort(key=lambda l: l.price)
    return out[:MAX_LEVELS]


def build_quotes(ev: dict, books: dict[str, tuple[float, dict]]) -> list[Quote]:
    market = "1x2" if ev["three_way"] else "ml"
    names = {HOME: ev["home"], AWAY: ev["away"], DRAW: "Empate"}
    everything = THREE_WAY if ev["three_way"] else frozenset({HOME, AWAY})
    url = f"https://kalshi.com/markets/{ev['series'].lower()}"
    out = []
    for m in ev["markets"]:
        ts, book = books.get(m["ticker"], (0.0, None))
        if not book:
            continue
        o = m["outcome"]
        for side, bids, covers, label in (
                ("yes", book.get("no_dollars"), frozenset({o}), f"{names[o]} (SI)"),
                ("no", book.get("yes_dollars"), everything - {o}, f"{names[o]} (NO)")):
            lv = asks_from_bids(bids)
            if not lv:
                continue
            out.append(Quote(source=NAME, feed=NAME, covers=covers, odds=poly_odds(lv[0].price, FEE_RATE),
                             label=label, url=url, ts=ts, max_stake=book_capacity(tuple(lv), FEE_RATE),
                             min_stake=MIN_STAKE, asks=tuple(lv), fee_rate=FEE_RATE, market=market,
                             ref=f"{m['ticker']}|{side}"))
    return out


class KalshiSource(Source):
    name = NAME

    def __init__(self, horizon_h: float = 24, match_cfg: MatchConfig | None = None) -> None:
        super().__init__()
        self.horizon_h = horizon_h
        self.match_cfg = match_cfg or MatchConfig()
        self._events: dict[str, dict] = {}                 # event_ticker -> evento compacto
        self._events_ts = 0.0
        self._books: dict[str, tuple[float, dict]] = {}    # market ticker -> (ts, libro)
        self._wanted: set[str] = set()                     # eventos emparejados con otra fuente
        self._targets: list[SourceEvent] = []
        self._task: asyncio.Task | None = None
        self._cache: list[SourceEvent] = []
        self._dirty = True
        self.requests = 0
        self._errors = 0
        self._last_err = ""
        self._pass_ts = 0.0

    async def _get(self, client: httpx.AsyncClient, path: str, params=None) -> dict | None:
        try:
            r = await client.get(f"{API}{path}", params=params, timeout=20)
            self.requests += 1
        except httpx.HTTPError as e:
            self._errors += 1
            self._last_err = f"{path.split('?')[0]}: {type(e).__name__}"
            return None
        finally:
            await asyncio.sleep(REQUEST_GAP_S)
        if r.status_code != 200:
            self._errors += 1
            self._last_err = f"{path.split('?')[0]}: HTTP {r.status_code} {r.text[:60]}"
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def fetch(self, client: httpx.AsyncClient, targets: list[SourceEvent] | None = None) -> list[SourceEvent]:
        self._targets = targets or []
        if (self._task is None or self._task.done()) and time.time() - self._pass_ts >= MIN_PASS_S:
            if self._task and self._task.exception():
                self.status = f"error: {self._task.exception()!r}"[:160]
            self._pass_ts = time.time()
            self._task = asyncio.create_task(self._refresh(client))
        return self._build()

    async def _refresh(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        if now - self._events_ts > DISCOVERY_S:
            await self._discover(client)
        if self._targets:
            self._match()
        due = []
        for eid in self._wanted:
            ev = self._events.get(eid)
            if not ev:
                continue
            to_start = ev["start"].timestamp() - now
            ttl = 60 if to_start < 3600 else 180 if to_start < 6 * 3600 else 600
            for m in ev["markets"]:
                fetched = self._books.get(m["ticker"], (0.0, None))[0]
                if now - fetched >= ttl:
                    due.append((fetched, m["ticker"]))
        due = [t for _, t in sorted(due)[:BOOKS_PER_CALL * CALLS_PER_PASS]]
        for i in range(0, len(due), BOOKS_PER_CALL):
            chunk = due[i:i + BOOKS_PER_CALL]
            data = await self._get(client, "/markets/orderbooks", [("tickers", t) for t in chunk])
            ts = time.time()
            for ob in (data or {}).get("orderbooks") or []:
                book = ob.get("orderbook_fp") or {}
                self._books[ob.get("ticker", "")] = (ts, {"yes_dollars": book.get("yes_dollars") or [],
                                                         "no_dollars": book.get("no_dollars") or []})
        keep = {m["ticker"] for eid in self._wanted if eid in self._events for m in self._events[eid]["markets"]}
        self._books = {t: v for t, v in self._books.items() if t in keep}
        self._dirty = True
        self.status = (f"{len(self._events)} partidos | {len(self._wanted)} emparejados | "
                       f"libros {len(self._books)} | llamadas {self.requests}"
                       + (f" | errores {self._errors} (último: {self._last_err})" if self._errors else ""))

    async def _discover(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        events: dict[str, dict] = {}
        cursor = None
        for _ in range(120):
            params = {"status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = await self._get(client, "/events", params)
            if data is None:
                return            # se reintenta en la próxima pasada; se conservan los eventos anteriores
            for e in data.get("events") or []:
                ev = parse_event(e, now, self.horizon_h)
                if ev:
                    events[ev["id"]] = ev
            cursor = data.get("cursor")
            if not cursor:
                break
        self._events, self._events_ts = events, time.time()

    def _bare(self) -> dict[str, SourceEvent]:
        now = time.time()
        return {eid: SourceEvent(feed=self.name, event_id=eid, sport=ev["sport"], home=ev["home"], away=ev["away"],
                                 start=ev["start"], three_way=ev["three_way"], live=ev["start"].timestamp() <= now,
                                 league=ev["league"], url=f"https://kalshi.com/markets/{ev['series'].lower()}")
                for eid, ev in self._events.items()}

    def _match(self) -> None:
        """Solo se piden libros de partidos que también están en otra fuente (Polymarket, SX, Cloudbet)."""
        bare = self._bare()
        clusters = match_events({"otros": self._targets, self.name: list(bare.values())}, self.match_cfg)
        self._wanted = {ev.event_id for c in clusters if self.name in c.feeds and len(c.feeds) >= 2
                        for ev, _ in c.members if ev.feed == self.name}

    def resolution_for(self, event_id: str, market: str) -> dict[str, tuple[str, str]]:
        """{resultado: ("kalshi:<ticker>", "yes")} para liquidar con el resultado oficial de Kalshi."""
        ev = self._events.get(event_id)
        if not ev or market != ("1x2" if ev["three_way"] else "ml"):
            return {}
        return {m["outcome"]: (f"kalshi:{m['ticker']}", "yes") for m in ev["markets"]}

    def _build(self) -> list[SourceEvent]:
        if not self._dirty:
            return self._cache
        events = self._bare()
        out = []
        for eid in self._wanted:
            ev, se = self._events.get(eid), events.get(eid)
            if ev and se:
                se.quotes = build_quotes(ev, self._books)
                if se.quotes:
                    out.append(se)
        self._cache, self._dirty = out, False
        return out
