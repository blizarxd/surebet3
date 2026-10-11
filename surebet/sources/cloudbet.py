"""
Cloudbet: casa cripto "blanda" con API OFICIAL para leer cuotas (Feed) y apostar (Trading).
Docs: https://cloudbet.github.io/wiki/en/docs/sports/api/  ·  Swagger: https://www.cloudbet.com/api/

Hace falta una clave (CLOUDBET_API_KEY en .env), cabecera X-API-Key:
  - clave de afiliado: gratis, cuotas con hasta ~1 min de retraso (sirve para prepartido)
  - clave de trading: se genera tras depositar ~10 EUR, tiempo real y permite apostar por API
Sin clave la fuente queda apagada.

  GET /pub/v2/odds/sports                          -> deportes (key con guiones: "ice-hockey")
  GET /pub/v2/odds/sports/{sport}                  -> categorías y competiciones con eventCount
  GET /pub/v2/odds/competitions/{key}?markets=..   -> eventos con mercados -> submercados -> selecciones

Mercados que se leen (ver lista oficial de mercados): ganador de cada deporte y más/menos de fútbol.
Fuera a propósito: hockey (su ganador incluye prórroga pero hay que confirmar penales) y béisbol
(el ganador depende de los pitchers anunciados: si cambian, Cloudbet puede anular).
"""

from __future__ import annotations

import asyncio
import time
import httpx

from ..matching import MatchConfig, match_events
from ..models import AWAY, DRAW, HOME, OVER, UNDER, Quote, SourceEvent
from ..sports import esports_family
from .base import Source, parse_iso

API = "https://sports-api.cloudbet.com/pub/v2/odds"
# deporte de Cloudbet (con guiones bajos) -> (familia, mercado ganador de Cloudbet, mercado nuestro)
SPORTS = {
    "soccer": ("soccer", "soccer.match_odds", "1x2"),
    "basketball": ("basketball", "basketball.moneyline", "ml"),
    "american_football": ("americanfootball", "american_football.moneyline", "ml"),
    "tennis": ("tennis", "tennis.winner", "ml"),
    "table_tennis": ("tabletennis", "table_tennis.winner", "ml"),
    "volleyball": ("volleyball", "volleyball.winner", "ml"),
    "mma": ("mma", "mma.winner", "ml"),
    "counter_strike": ("esports", "counter_strike.winner", "ml"),
    "dota_2": ("esports", "dota_2.winner", "ml"),
    "league_of_legends": ("esports", "league_of_legends.winner", "ml"),
    "esport_valorant": ("esports", "esport_valorant.winner", "ml"),
    "rainbow_six": ("esports", "rainbow_six.winner", "ml"),
    "overwatch": ("esports", "overwatch.winner", "ml"),
    "call_of_duty": ("esports", "call_of_duty.winner", "ml"),
    "rocket_league": ("esports", "rocket_league.winner", "ml"),
}
TOTALS = "soccer.total_goals"
OUTCOME = {"home": HOME, "draw": DRAW, "away": AWAY, "over": OVER, "under": UNDER}
HOT_TTL_S = 120          # competiciones con partidos emparejados con Polymarket
COLD_TTL_S = 1800        # el resto: solo para descubrir partidos nuevos
SPORTS_TTL_S = 1800
CALLS_PER_PASS = 8
REQUEST_GAP_S = 0.5


def event_quotes(ev: dict, sport_key: str, ts: float, url: str) -> list[Quote]:
    """Selecciones habilitadas del ganador (y más/menos .5 en fútbol) a quotes."""
    _fam, winner_key, market = SPORTS[sport_key]
    names = {HOME: (ev.get("home") or {}).get("name", ""), AWAY: (ev.get("away") or {}).get("name", ""),
             DRAW: "Empate"}
    out: list[Quote] = []
    markets = ev.get("markets") or {}
    for key in (winner_key, TOTALS if sport_key == "soccer" else None):
        if not key or key not in markets:
            continue
        for sub in (markets[key].get("submarkets") or {}).values():
            for sel in sub.get("selections") or []:
                res = OUTCOME.get(sel.get("outcome"))
                price = float(sel.get("price") or 0)
                if res is None or price <= 1.0 or sel.get("status") != "SELECTION_ENABLED":
                    continue
                if key == TOTALS:
                    params = dict(p.split("=", 1) for p in (sel.get("params") or "").split("&") if "=" in p)
                    try:
                        line = float(params.get("total", ""))
                    except ValueError:
                        continue
                    if line % 1 != 0.5:
                        continue  # línea entera o asiática: puede haber devolución
                    mk, label = f"ou_{line:g}", f"{'Más' if res == OVER else 'Menos'} {line:g}"
                else:
                    if "pitcher" in (sel.get("params") or ""):
                        continue  # ganador condicionado a pitchers: no es el ganador simple
                    mk, label = market, names[res]
                out.append(Quote(source=CloudbetSource.name, feed=CloudbetSource.name, covers=frozenset({res}),
                                 odds=price, label=label, url=url, ts=ts, market=mk,
                                 ref=f"{ev.get('id')}|{sel.get('marketUrl', '')}",
                                 max_stake=float(sel["maxStake"]) if sel.get("maxStake") else None,
                                 min_stake=float(sel.get("minStake") or 0)))
    # 1X2 incompleto (falta empate o un lado): no se usa
    if market == "1x2" and len({q.covers for q in out if q.market == "1x2"}) < 3:
        out = [q for q in out if q.market != "1x2"]
    if market == "ml" and len({q.covers for q in out if q.market == "ml"}) < 2:
        out = [q for q in out if q.market != "ml"]
    return out


def parse_event(ev: dict, sport_key: str, competition: str, ts: float) -> SourceEvent | None:
    fam = SPORTS[sport_key][0]
    if fam == "esports":
        fam = esports_family(name=f"{sport_key.replace('_', ' ')} {competition}")
    home, away = (ev.get("home") or {}).get("name"), (ev.get("away") or {}).get("name")
    if not home or not away:
        return None
    start = parse_iso(ev.get("startTime") or ev.get("cutoffTime"))
    url = "https://www.cloudbet.com/en/sports"
    quotes = event_quotes(ev, sport_key, ts, url)
    if not quotes:
        return None
    return SourceEvent(feed=CloudbetSource.name, event_id=str(ev.get("id")), sport=fam, home=home, away=away,
                       start=start, three_way=fam == "soccer", quotes=quotes,
                       live=ev.get("status") == "TRADING_LIVE", league=competition, url=url)


class CloudbetSource(Source):
    name = "cloudbet"

    def __init__(self, api_key: str, horizon_h: float = 24, match_cfg: MatchConfig | None = None) -> None:
        super().__init__()
        self.api_key = api_key
        self.horizon_h = horizon_h
        self.match_cfg = match_cfg or MatchConfig()
        self._sem = asyncio.Semaphore(2)
        self._sports_ts = 0.0
        self._competitions: dict[str, tuple[str, str]] = {}   # key -> (deporte, nombre)
        self._comp_ts: dict[str, float] = {}
        self._comp_events: dict[str, list[SourceEvent]] = {}
        self._hot: set[str] = set()
        self._targets: list[SourceEvent] = []
        self._task: asyncio.Task | None = None
        self.requests = 0
        self._errors = 0
        if not api_key:
            self.status = "sin CLOUDBET_API_KEY (desactivada)"

    async def _get(self, client: httpx.AsyncClient, path: str, params=None) -> dict | None:
        async with self._sem:
            try:
                r = await client.get(f"{API}{path}", params=params, timeout=20,
                                     headers={"X-API-Key": self.api_key, "Accept": "application/json"})
                self.requests += 1
            except httpx.HTTPError:
                self._errors += 1
                return None
            finally:
                await asyncio.sleep(REQUEST_GAP_S)
        if r.status_code != 200:
            self._errors += 1
            if r.status_code in (401, 403):
                self.status = f"Cloudbet rechazó la clave (HTTP {r.status_code})"
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def fetch(self, client: httpx.AsyncClient, targets: list[SourceEvent] | None = None) -> list[SourceEvent]:
        if not self.api_key:
            return []
        self._targets = targets or []
        if self._task is None or self._task.done():
            if self._task and self._task.exception():
                self.status = f"error: {self._task.exception()!r}"[:160]
            self._task = asyncio.create_task(self._refresh(client))
        return [e for evs in self._comp_events.values() for e in evs]

    async def _refresh(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        if now - self._sports_ts > SPORTS_TTL_S:
            await self._load_competitions(client)
        due = sorted((self._comp_ts.get(k, 0.0), k) for k in self._competitions
                     if now - self._comp_ts.get(k, 0.0) >= (HOT_TTL_S if k in self._hot else COLD_TTL_S))
        due = [k for _, k in due[:CALLS_PER_PASS]]
        if not due:
            return
        for key in due:
            sport, comp_name = self._competitions[key]
            _fam, winner, _m = SPORTS[sport]
            markets = [winner] + ([TOTALS] if sport == "soccer" else [])
            data = await self._get(client, f"/competitions/{key}", params=[("markets", m) for m in markets])
            self._comp_ts[key] = time.time()
            if data is None:
                continue
            ts, horizon = time.time(), time.time() + self.horizon_h * 3600
            evs = [parse_event(ev, sport, comp_name, ts) for ev in data.get("events") or []]
            self._comp_events[key] = [e for e in evs if e and (not e.start or e.start.timestamp() <= horizon)]
        self._learn()
        n = sum(len(v) for v in self._comp_events.values())
        self.status = (f"{n} partidos | competiciones {len(self._competitions)} (con Polymarket {len(self._hot)}) | "
                       f"llamadas {self.requests}" + (f" | errores {self._errors}" if self._errors else ""))

    async def _load_competitions(self, client: httpx.AsyncClient) -> None:
        data = await self._get(client, "/sports")
        if data is None:
            return
        comps: dict[str, tuple[str, str]] = {}
        for sp in data.get("sports") or []:
            sport = str(sp.get("key", "")).replace("-", "_")
            if sport not in SPORTS or not sp.get("eventCount"):
                continue
            detail = await self._get(client, f"/sports/{sp['key']}")
            for cat in (detail or {}).get("categories") or []:
                for c in cat.get("competitions") or []:
                    if c.get("eventCount"):
                        comps[c["key"]] = (sport, c.get("name", ""))
        if comps:
            self._competitions, self._sports_ts = comps, time.time()
            self._comp_events = {k: v for k, v in self._comp_events.items() if k in comps}

    def _learn(self) -> None:
        """Competiciones con partidos que también están en Polymarket: se refrescan seguido."""
        mine = [e for evs in self._comp_events.values() for e in evs]
        if not mine or not self._targets:
            return
        clusters = match_events({"polymarket": self._targets, self.name: mine}, self.match_cfg)
        ids = {ev.event_id for c in clusters if len(c.feeds) == 2 for ev, _ in c.members if ev.feed == self.name}
        self._hot = {k for k, evs in self._comp_events.items() if any(e.event_id in ids for e in evs)}

