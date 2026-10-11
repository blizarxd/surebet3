"""
Polymarket: partidos (Gamma API) + order books (CLOB API).

Estructura de los mercados "moneyline" según el deporte:
  - 2 vías (NBA, tenis, esports...): UN mercado con outcomes = [equipo1, equipo2].
    Cada token paga si gana ese equipo.
  - 1X2 (fútbol): TRES mercados Sí/No: "¿Gana local?", "¿Empate?", "¿Gana visitante?".
    El token SÍ cubre ese resultado; el token NO cubre los otros dos.

Precios: siempre el ASK del libro (lo que realmente pagas), con la comisión taker de
`feeSchedule` aplicada y la escalera completa para calcular cuotas según el monto.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from ..matching import name_score
from ..models import AWAY, DRAW, HOME, OVER, THREE_WAY, UNDER, BookLevel, Quote, SourceEvent
from ..pricing import book_capacity, poly_cost_per_share, poly_odds
from ..sports import POLY_GENERIC_TAGS, esports_family, poly_family_from_tags
from .base import Source, parse_iso

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
GAMES_TAG = 100639
DEFAULT_SPORTS_FEE = {"rate": 0.05, "exponent": 1}
MAX_FAIR_SPREAD = 0.03
# Mercados que se leen además del ganador (fútbol): resultado y totales del partido y del 1er tiempo
EXTRA_TYPES = {"soccer_halftime_result", "totals", "first_half_totals"}
WANTED_TYPES = {"moneyline"} | EXTRA_TYPES
SLICE_H = 3             # tramos de 3 h de partidos, descargados en paralelo
NEAR_H = 6              # tramos que empiezan en menos de 6 h se refrescan cada discovery_interval_s
FAR_REFRESH_S = 900     # los más lejanos, cada 15 min  # con spreads mayores el precio medio no es una referencia fiable
SLICE_CONCURRENCY = 1   # tramos a la vez: más paralelismo sube el pico de memoria al arrancar
PAGE_LIMIT = 50          # eventos por página de Gamma (páginas más chicas = menos memoria de golpe)
BOOK_CACHE_MAX_AGE_S = 3600  # libros de tokens que ya no se piden (partidos terminados) se olvidan
# Campos de Gamma que se usan de verdad: el evento crudo trae descripciones, imágenes y decenas de
# mercados que no se comparan, y guardarlo entero hacía pesar al escáner cientos de MB.
EVENT_KEYS = ("id", "title", "slug", "gameId", "startTime", "live", "ended", "score", "period", "seriesSlug")
MARKET_KEYS = ("sportsMarketType", "closed", "acceptingOrders", "enableOrderBook", "outcomes", "clobTokenIds",
               "groupItemTitle", "question", "gameStartTime", "feesEnabled", "feeSchedule", "secondsDelay",
               "orderMinSize", "conditionId", "line")


@dataclass
class _Outcome:
    token_id: str
    covers: frozenset[str]
    label: str
    fee_rate: float
    fee_exponent: float
    delay_s: float
    min_shares: float
    market: str = ""
    condition_id: str = ""


@dataclass
class _ParsedEvent:
    event: SourceEvent
    outcomes: list[_Outcome] = field(default_factory=list)


def slim_event(e: dict) -> dict:
    """Solo lo que se usa de un evento de Gamma (ver EVENT_KEYS / MARKET_KEYS)."""
    out = {k: e[k] for k in EVENT_KEYS if k in e}
    out["series"] = [{"id": x.get("id")} for x in e.get("series") or [] if isinstance(x, dict)]
    out["tags"] = [{"id": t.get("id")} for t in e.get("tags") or [] if isinstance(t, dict)]
    out["markets"] = [{k: m[k] for k in MARKET_KEYS if k in m} for m in e.get("markets") or []
                      if m.get("sportsMarketType") in WANTED_TYPES]
    return out


def _jl(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return []


def _fee(m: dict) -> tuple[float, float]:
    if not m.get("feesEnabled"):
        return 0.0, 1.0
    sched = m.get("feeSchedule") or DEFAULT_SPORTS_FEE
    return float(sched.get("rate", 0.05)), float(sched.get("exponent", 1))


def _common(m: dict, market: str) -> dict:
    rate, exp = _fee(m)
    return dict(fee_rate=rate, fee_exponent=exp, delay_s=float(m.get("secondsDelay") or 0),
                min_shares=float(m.get("orderMinSize") or 0), market=market,
                condition_id=str(m.get("conditionId") or ""))


def _tradeable(m: dict) -> bool:
    return not m.get("closed") and bool(m.get("acceptingOrders")) and m.get("enableOrderBook", True)


def parse_extra_markets(e: dict, home: str, away: str) -> list[_Outcome]:
    """
    Mercados extra de fútbol de un evento hermano (mismo partido):
      - "Halftime Result": 3 mercados SÍ/NO (local, empate, visitante) del primer tiempo -> 1x2_h1
      - "totals" / "first_half_totals": Over/Under por línea (solo líneas .5: sin devolución)
    """
    out: list[_Outcome] = []
    markets = [m for m in e.get("markets") or [] if m.get("sportsMarketType") in EXTRA_TYPES and _tradeable(m)]
    ht = [m for m in markets if m.get("sportsMarketType") == "soccer_halftime_result"]
    if len(ht) == 3:
        sides: dict[str, dict] = {}
        for m in ht:
            title = m.get("groupItemTitle", "")
            if "draw" in title.lower():
                sides[DRAW] = m
            elif name_score(title, home) >= name_score(title, away):
                sides[HOME] = m
            else:
                sides[AWAY] = m
        if set(sides) == {HOME, DRAW, AWAY}:
            names = {HOME: home, DRAW: "Empate", AWAY: away}
            for res, m in sides.items():
                outs, tokens = _jl(m.get("outcomes")), _jl(m.get("clobTokenIds"))
                if [n.lower() for n in outs] != ["yes", "no"] or len(tokens) != 2:
                    break
                common = _common(m, "1x2_h1")
                out.append(_Outcome(tokens[0], frozenset({res}), f"1T {names[res]} (SI)", **common))
                out.append(_Outcome(tokens[1], THREE_WAY - {res}, f"1T {names[res]} (NO)", **common))
    for m in markets:
        kind = m.get("sportsMarketType")
        if kind not in ("totals", "first_half_totals"):
            continue
        line = m.get("line")
        outs, tokens = _jl(m.get("outcomes")), _jl(m.get("clobTokenIds"))
        if line is None or float(line) % 1 != 0.5 or [n.lower() for n in outs] != ["over", "under"] or len(tokens) != 2:
            continue
        half = kind == "first_half_totals"
        market = f"ou_h1_{float(line):g}" if half else f"ou_{float(line):g}"
        tag = "1T " if half else ""
        common = _common(m, market)
        out.append(_Outcome(tokens[0], frozenset({OVER}), f"Más {tag}{float(line):g}", **common))
        out.append(_Outcome(tokens[1], frozenset({UNDER}), f"Menos {tag}{float(line):g}", **common))
    return out


def _title_teams(title: str) -> tuple[str, str] | None:
    base = title.split(" - ")[0]
    base = re.sub(r"^[^:]{2,40}:\s*", "", base)  # "Counter-Strike: A vs B" -> "A vs B"
    base = re.sub(r"\s*\(BO\d\)\s*$", "", base, flags=re.I)
    parts = re.split(r"\s+vs\.?\s+", base, maxsplit=1, flags=re.I)
    if len(parts) == 2 and all(p.strip() for p in parts):
        return parts[0].strip(), parts[1].strip()
    return None


class PolymarketSource(Source):
    name = "polymarket"

    def __init__(self, mode: str = "all", horizon_h: float = 6.0,
                 discovery_interval_s: float = 60.0, max_levels: int = 15,
                 max_pages: int = 30) -> None:
        super().__init__()
        self.mode = mode
        self.horizon_h = horizon_h
        self.discovery_interval_s = discovery_interval_s
        self.max_levels = max_levels
        self.max_pages = max_pages
        self._raw_events: list[dict] = []
        self._parsed: dict[str, _ParsedEvent] = {}
        self._last_discovery = 0.0
        self._parsed_at = -1.0  # descubrimiento ya parseado (no se re-parsea cada ciclo si no hubo uno nuevo)
        self._family_by_series: dict[str, str] = {}
        self._slices: dict[float, tuple[float, list[dict]]] = {}  # inicio del tramo -> (descargado, eventos)
        self._book_cache: dict[str, tuple[float, list[BookLevel], float | None]] = {}
        # event_id -> mercado -> resultado -> (condition_id, token que paga si ocurre)
        self.resolution: dict[str, dict[str, dict[str, tuple[str, str]]]] = {}

    # ------------------------------------------------------------------ discovery
    async def _load_sports(self, client: httpx.AsyncClient) -> None:
        r = await client.get(f"{GAMMA}/sports")
        r.raise_for_status()
        for s in r.json():
            tags = [t.strip() for t in str(s.get("tags", "")).split(",") if t.strip()]
            fam = poly_family_from_tags([t for t in tags if t not in POLY_GENERIC_TAGS], s.get("sport", ""))
            if s.get("series"):
                self._family_by_series[str(s["series"])] = fam

    async def _paged_events(self, client: httpx.AsyncClient, params: dict) -> list[dict]:
        out: list[dict] = []
        cursor = None
        for _ in range(self.max_pages):
            p = dict(params, tag_id=GAMES_TAG, closed="false", limit=PAGE_LIMIT)
            if cursor:
                p["after_cursor"] = cursor
            r = await client.get(f"{GAMMA}/events/keyset", params=p)
            r.raise_for_status()
            data = r.json()
            out.extend(slim_event(e) for e in data.get("events") or [])
            del r
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return out

    async def _discover(self, client: httpx.AsyncClient) -> None:
        if not self._family_by_series:
            await self._load_sports(client)
        if self.mode == "live":
            events = await self._paged_events(client, {"live": "true"})
        else:
            events = await self._windowed_events(client)
        # Eventos con mercados que sabemos comparar (ganador, 1er tiempo, totales)
        self._raw_events = [
            e for e in events
            if not e.get("ended") and any(m.get("sportsMarketType") in WANTED_TYPES for m in e.get("markets") or [])
        ]
        self._last_discovery = time.time()

    async def _windowed_events(self, client: httpx.AsyncClient) -> list[dict]:
        """
        24 h de partidos son ~2.700 eventos (27 páginas en serie, ~25 s). Se parte la ventana
        en tramos de SLICE_H horas que se piden en paralelo; los tramos lejanos se refrescan
        con menos frecuencia (sus cuotas casi no cambian y no hacen falta cada minuto).
        """
        now = time.time()
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        step = SLICE_H * 3600
        first = int((now - 4 * 3600) // step) * step
        wanted = [first + i * step for i in range(int((self.horizon_h + 4) * 3600 // step) + 2)]
        wanted = [w for w in wanted if w <= now + self.horizon_h * 3600]

        sem = asyncio.Semaphore(SLICE_CONCURRENCY)

        async def load(start: float) -> tuple[float, list[dict]]:
            params = {
                "start_time_min": datetime.fromtimestamp(start, timezone.utc).strftime(fmt),
                "start_time_max": datetime.fromtimestamp(start + step - 1, timezone.utc).strftime(fmt),
            }
            async with sem:
                return start, await self._paged_events(client, params)

        stale = []
        for w in wanted:
            ttl = self.discovery_interval_s if w < now + NEAR_H * 3600 else FAR_REFRESH_S
            fetched = self._slices.get(w)
            if fetched is None or now - fetched[0] > ttl:
                stale.append(w)
        for res in await asyncio.gather(*(load(w) for w in stale), return_exceptions=True):
            if not isinstance(res, BaseException):
                self._slices[res[0]] = (now, res[1])
        self._slices = {w: v for w, v in self._slices.items() if w in wanted}
        seen: dict[str, dict] = {}
        for _, evs in self._slices.values():
            for e in evs:
                seen[str(e.get("id"))] = e
        return list(seen.values())

    # ------------------------------------------------------------------ parsing
    def _family(self, e: dict) -> str:
        for s in e.get("series") or []:
            fam = self._family_by_series.get(str(s.get("id")))
            if fam:
                return fam
        tag_ids = [str(t.get("id")) for t in e.get("tags") or [] if isinstance(t, dict)]
        fam = poly_family_from_tags([t for t in tag_ids if t not in POLY_GENERIC_TAGS], e.get("seriesSlug", ""))
        if fam.startswith("esports"):
            fam = esports_family(name=e.get("seriesSlug", "") + " " + e.get("title", ""))
        return fam

    def _parse(self, e: dict) -> _ParsedEvent | None:
        markets = [
            m for m in e.get("markets") or []
            if m.get("sportsMarketType") == "moneyline" and not m.get("closed")
            and m.get("acceptingOrders") and m.get("enableOrderBook", True)
        ]
        if not markets:
            return None
        title = e.get("title", "")
        start = parse_iso(e.get("startTime")) or parse_iso(markets[0].get("gameStartTime"))
        url = f"https://polymarket.com/event/{e.get('slug', '')}"
        base = dict(feed=self.name, event_id=str(e.get("id")), sport=self._family(e), start=start,
                    live=bool(e.get("live")), score=str(e.get("score") or ""),
                    period=str(e.get("period") or ""), league=e.get("seriesSlug", ""), url=url)

        # Caso 2 vías: un mercado cuyos outcomes son los dos equipos
        if len(markets) == 1:
            m = markets[0]
            names, tokens = _jl(m.get("outcomes")), _jl(m.get("clobTokenIds"))
            if len(names) != 2 or len(tokens) != 2 or {n.lower() for n in names} & {"yes", "no"}:
                return None
            ev = SourceEvent(home=names[0], away=names[1], three_way=False, **base)
            common = _common(m, "ml")
            return _ParsedEvent(ev, [
                _Outcome(tokens[0], frozenset({HOME}), names[0], **common),
                _Outcome(tokens[1], frozenset({AWAY}), names[1], **common),
            ])

        # Caso 1X2: mercados Sí/No por resultado (local, empate, visitante)
        draw = [m for m in markets if "draw" in (m.get("groupItemTitle", "") + m.get("question", "")).lower()]
        teams = [m for m in markets if m not in draw]
        if len(draw) != 1 or len(teams) != 2:
            return None  # sin mercado de empate no es seguro tratarlo como 1X2
        tt = _title_teams(title)
        n0, n1 = teams[0].get("groupItemTitle", ""), teams[1].get("groupItemTitle", "")
        if tt and name_score(n1, tt[0]) > name_score(n0, tt[0]):
            teams.reverse()
        home_m, away_m = teams
        ev = SourceEvent(home=home_m.get("groupItemTitle", ""), away=away_m.get("groupItemTitle", ""),
                         three_way=True, **base)
        parsed = _ParsedEvent(ev)
        for m, res, label in ((home_m, HOME, ev.home), (draw[0], DRAW, "Empate"), (away_m, AWAY, ev.away)):
            names, tokens = _jl(m.get("outcomes")), _jl(m.get("clobTokenIds"))
            if [n.lower() for n in names] != ["yes", "no"] or len(tokens) != 2:
                return None
            common = _common(m, "1x2")
            parsed.outcomes.append(_Outcome(tokens[0], frozenset({res}), f"{label} (SI)", **common))
            parsed.outcomes.append(_Outcome(tokens[1], THREE_WAY - {res}, f"{label} (NO)", **common))
        return parsed

    # ------------------------------------------------------------------ books
    async def _books(self, client: httpx.AsyncClient,
                     token_ids: list[str]) -> dict[str, tuple[list[BookLevel], float | None]]:
        """token_id -> (asks de menor a mayor precio, mejor bid)."""
        async def chunk(ids: list[str]) -> list[dict]:
            r = await client.post(f"{CLOB}/books", json=[{"token_id": t} for t in ids])
            r.raise_for_status()
            return r.json()

        chunks = [token_ids[i:i + 100] for i in range(0, len(token_ids), 100)]
        results = await asyncio.gather(*(chunk(c) for c in chunks), return_exceptions=True)
        books: dict[str, tuple[list[BookLevel], float | None]] = {}
        for res in results:
            if isinstance(res, BaseException):
                continue
            for b in res:
                asks = sorted(
                    (BookLevel(float(a["price"]), float(a["size"])) for a in b.get("asks") or []),
                    key=lambda l: l.price,
                )
                bids = [float(x["price"]) for x in b.get("bids") or []]
                books[str(b.get("asset_id"))] = (asks[: self.max_levels], max(bids) if bids else None)
        return books

    # ------------------------------------------------------------------ fetch
    async def events(self, client: httpx.AsyncClient) -> list[SourceEvent]:
        """Fase 1: partidos sin cuotas (para emparejar antes de pedir libros)."""
        if time.time() - self._last_discovery > self.discovery_interval_s:
            await self._discover(client)
        if self._parsed_at == self._last_discovery:
            return [p.event for p in self._parsed.values()]
        groups: dict[str, list[dict]] = {}
        for e in self._raw_events:
            key = str(e.get("gameId") or e.get("slug", "").split("-more-markets")[0].split("-halftime")[0])
            groups.setdefault(key, []).append(e)
        parsed: dict[str, _ParsedEvent] = {}
        resolution: dict[str, dict[str, dict[str, tuple[str, str]]]] = {}
        for evs in groups.values():
            main = next((e for e in evs if any(m.get("sportsMarketType") == "moneyline"
                                                for m in e.get("markets") or [])), None)
            p = self._parse(main) if main else None
            if p is None:
                continue
            if p.event.sport == "soccer":
                for e in evs:
                    if e is not main:
                        p.outcomes += parse_extra_markets(e, p.event.home, p.event.away)
            parsed[p.event.event_id] = p
            # Qué token paga cada resultado de cada mercado (para liquidar operaciones en papel)
            res: dict[str, dict[str, tuple[str, str]]] = {}
            for o in p.outcomes:
                if len(o.covers) == 1 and o.condition_id:
                    (out,) = o.covers
                    res.setdefault(o.market, {})[out] = (o.condition_id, o.token_id)
            resolution[p.event.event_id] = res
        self._parsed = parsed
        self._parsed_at = self._last_discovery
        self.resolution = resolution  # solo partidos actuales: no crece sin límite en un servidor 24/7
        return [p.event for p in self._parsed.values()]

    async def fill_quotes(self, client: httpx.AsyncClient, event_ids: set[str] | None = None) -> list[SourceEvent]:
        """Fase 2: pide los order books (solo de `event_ids` si se indica) y crea las quotes."""
        for p in self._parsed.values():
            p.event.quotes = []  # los partidos se reutilizan entre ciclos: las cuotas, no
        parsed = [p for eid, p in self._parsed.items() if event_ids is None or eid in event_ids]
        now = time.time()
        # Vivo o por empezar: cada ciclo. Lejanos: menos seguido (sus libros casi no cambian
        # y así no se pasa del límite de /books con miles de tokens).
        due: list[str] = []
        for p in parsed:
            st = p.event.start.timestamp() if p.event.start else now
            ttl = 0 if p.event.live or st - now < 3600 else (20 if st - now < 6 * 3600 else 60)
            for o in p.outcomes:
                cached = self._book_cache.get(o.token_id)
                if cached is None or now - cached[0] >= ttl:
                    due.append(o.token_id)
        if due:
            fresh = await self._books(client, due)
            fetched = time.time()
            for tok, book in fresh.items():
                self._book_cache[tok] = (fetched, *book)
            self._book_cache = {t: v for t, v in self._book_cache.items() if fetched - v[0] < BOOK_CACHE_MAX_AGE_S}
        token_ids = [o.token_id for p in parsed for o in p.outcomes]
        books = {t: self._book_cache[t][1:] for t in token_ids if t in self._book_cache}
        book_ts = {t: self._book_cache[t][0] for t in token_ids if t in self._book_cache}

        out: list[SourceEvent] = []
        for p in parsed:
            for o in p.outcomes:
                asks, best_bid = books.get(o.token_id) or ([], None)
                if not asks or asks[0].price >= 0.995:
                    continue  # sin vendedores reales
                best = asks[0].price
                # Precio medio como probabilidad justa, solo si el spread es estrecho
                fair = (best + best_bid) / 2 if best_bid and best - best_bid <= MAX_FAIR_SPREAD else None
                p.event.quotes.append(Quote(
                    source=self.name, feed=self.name, covers=o.covers,
                    odds=poly_odds(best, o.fee_rate, o.fee_exponent), label=o.label,
                    url=p.event.url, ts=book_ts.get(o.token_id, now), asks=tuple(asks),
                    max_stake=book_capacity(tuple(asks), o.fee_rate, o.fee_exponent),
                    min_stake=o.min_shares * poly_cost_per_share(best, o.fee_rate, o.fee_exponent),
                    fee_rate=o.fee_rate, fee_exponent=o.fee_exponent, delay_s=o.delay_s,
                    token_id=o.token_id, fair_prob=fair, market=o.market, condition_id=o.condition_id,
                    near_depth=sum(l.price * l.size for l in asks if l.price <= best + 0.02), bid=best_bid,
                ))
            if p.event.quotes:
                out.append(p.event)
        live = sum(1 for p in self._parsed.values() if p.event.live)
        self.status = (f"{len(self._parsed)} partidos ({live} en vivo), "
                       f"libros pedidos para {len(parsed)} ({len(token_ids)} tokens)")
        return out

    async def fetch(self, client: httpx.AsyncClient) -> list[SourceEvent]:
        await self.events(client)
        return await self.fill_quotes(client)
