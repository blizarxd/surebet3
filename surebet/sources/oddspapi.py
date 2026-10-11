"""
OddsPapi v4 (api.oddspapi.io): 1xBet, Stake, Rollbit, Pinnacle y 350+ casas.

Plan gratis (verificado con /v4/account): 250 peticiones/mes, SOLO prepartido, sin WebSocket.
Cada llamada a un endpoint cuenta 1 (incluso si da error), excepto /account e /historical-odds.

Estrategia para gastar poco:
  1. /fixtures por deporte (cache varias horas) -> nombres, torneo y hora de cada partido.
  2. Emparejar esos partidos con los de Polymarket (gratis, local).
  3. /odds-by-tournaments: UNA casa por llamada y máximo 5 torneos. Se eligen los 5
     torneos con más partidos emparejados -> ~4 peticiones cubren ~50-80 partidos.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..matching import MatchConfig, match_events
from ..models import AWAY, DRAW, HOME, Quote, SourceEvent
from .base import Source, parse_iso

BASE = "https://api.oddspapi.io/v4"
PACE_S = 6.0        # separación entre llamadas al mismo endpoint (evita el 429)
RETRY_429_S = 15.0
KICKOFF_MIN_S, KICKOFF_MAX_S = 3600, 3 * 3600  # ventana "cerca del inicio" para gastar cupo

# sportId de OddsPapi -> familia usada en el resto del sistema
SPORT_FAMILY = {10: "soccer", 11: "basketball", 12: "tennis", 13: "baseball", 14: "americanfootball",
                15: "hockey", 16: "esports:dota2", 17: "esports:cs2", 18: "esports:lol", 19: "darts", 20: "mma",
                21: "boxing", 22: "handball", 23: "volleyball", 25: "tabletennis", 26: "rugby", 27: "cricket"}
OUTCOME_SIDE = {"1": HOME, "X": DRAW, "2": AWAY}


class OddsPapiSource(Source):
    name = "oddspapi"
    slow = True

    def __init__(self, api_key: str, cache_dir: Path, sports: list[int], bookmakers: list[str],
                 interval_s: float = 6 * 3600, fixtures_ttl_s: float = 6 * 3600,
                 horizon_h: float = 24, max_tournaments: int = 5, reserve: int = 20,
                 min_kickoff: int = 5, match_cfg: MatchConfig | None = None) -> None:
        super().__init__()
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.sports = sports
        self.bookmakers = bookmakers
        self.interval_s = interval_s
        self.fixtures_ttl_s = fixtures_ttl_s
        self.horizon_h = horizon_h
        self.max_tournaments = max_tournaments
        self.reserve = reserve
        self.min_kickoff = min_kickoff
        self._next_check = 0.0
        self.match_cfg = match_cfg or MatchConfig()
        self.used: int | None = None
        self.limit: int | None = None
        self._markets: dict[int, tuple[str, dict[str, str]]] = {}  # sportId -> (marketId, {outcomeId: H/D/A})
        self._fixtures: dict[int, tuple[float, list[dict]]] = {}
        self._cache: list[SourceEvent] = []
        self._last_call: dict[str, float] = {}
        self.rounds = 0  # rondas completadas en esta ejecución (para avisar por Telegram)
        self.force_next = False
        # La hora de la última ronda se guarda en disco: reiniciar el escáner no gasta cupo
        self._state = cache_dir / "state.json"
        try:
            self._saved = json.loads(self._state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._saved = {}
        self._last_round = float(self._saved.get("last_round", 0.0))

    # ------------------------------------------------------------------ http
    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> object:
        if path != "account":
            # Cada endpoint tiene un tiempo mínimo entre llamadas; un 429 también gasta cupo
            wait = PACE_S - (time.time() - self._last_call.get(path, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
        for attempt in range(2):
            r = await client.get(f"{BASE}/{path}", params={**params, "apiKey": self.api_key})
            if path != "account":
                self.used = (self.used or 0) + 1
                self._last_call[path] = time.time()
            if r.status_code == 429 and attempt == 0:
                await asyncio.sleep(float(r.headers.get("Retry-After") or RETRY_429_S))
                continue
            break
        data = r.json()
        if r.status_code != 200:
            raise RuntimeError(f"OddsPapi {path}: HTTP {r.status_code} {str(data)[:160]}")
        return data

    async def refresh_quota(self, client: httpx.AsyncClient) -> None:
        """/account no consume cupo: dice cuántas peticiones quedan."""
        data = await self._get(client, "account")
        sub = next(s for s in data["subscriptions"] if s.get("is_active"))
        self.used, self.limit = int(sub["request_count"]), int(sub["request_limit"])

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None or self.used is None else self.limit - self.used

    # ------------------------------------------------------------------ catálogos (cacheados en disco)
    async def _load_markets(self, client: httpx.AsyncClient) -> None:
        path = self.cache_dir / "markets.json"
        if path.exists():
            markets = json.loads(path.read_text(encoding="utf-8"))
        else:
            markets = await self._get(client, "markets")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(markets), encoding="utf-8")
        self._markets = main_markets(markets)

    async def _fixtures_for(self, client: httpx.AsyncClient, sport_id: int) -> list[dict]:
        cached = self._fixtures.get(sport_id)
        if cached and time.time() - cached[0] < self.fixtures_ttl_s:
            return cached[1]
        now = datetime.now(timezone.utc)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        data = await self._get(client, "fixtures", sportId=sport_id, statusId=0,
                               **{"from": now.strftime(fmt), "to": (now + timedelta(hours=self.horizon_h)).strftime(fmt)})
        fixtures = [f for f in data if f.get("hasOdds")] if isinstance(data, list) else []
        self._fixtures[sport_id] = (time.time(), fixtures)
        return fixtures

    # ------------------------------------------------------------------ ronda
    async def fetch(self, client: httpx.AsyncClient, targets: list[SourceEvent] | None = None) -> list[SourceEvent]:
        if not self.api_key:
            self.status = "sin ODDSPAPI_KEY (desactivada)"
            return []
        now = time.time()
        if not self.force_next:
            wait = self.interval_s - (now - self._last_round)
            if wait > 0:
                if not self._cache:
                    self.status = f"próxima ronda en {wait / 3600:.1f} h o más (--oddspapi-now para forzar)"
                return self._cache
            if now < self._next_check:
                return self._cache
            # Se gasta cupo cuando hay partidos por empezar en 1-3 h: ahí se mueven las cuotas
            soon = self._kickoff_count(targets or [])
            if soon < self.min_kickoff:
                self.status = f"esperando partidos que empiecen en 1-3 h (hay {soon}, mínimo {self.min_kickoff})"
                self._next_check = now + 300
                return self._cache
        self.force_next = False
        await self.refresh_quota(client)
        needed = len(self.sports) + len(self.sports) * len(self.bookmakers)
        if self.remaining is not None and self.remaining - needed < self.reserve:
            self.status = f"pausada: quedan {self.remaining} peticiones (reserva {self.reserve})"
            self._next_check = now + 3600
            return self._cache
        allowed, used_today = self._daily_budget()
        if used_today + needed > allowed:
            self.status = f"cupo del día usado ({used_today}/{allowed:.0f}); sigue mañana"
            self._next_check = now + 1800
            return self._cache
        if not self._markets:
            await self._load_markets(client)

        events: list[SourceEvent] = []
        errors: list[str] = []
        fetched_at = time.time()
        for sport_id in self.sports:
            fam = SPORT_FAMILY.get(sport_id)
            if fam is None or sport_id not in self._markets:
                continue
            fixtures = await self._fixtures_for(client, sport_id)
            by_id = {f["fixtureId"]: f for f in fixtures}
            tournaments = self._pick_tournaments(fixtures, fam, targets or [])
            if not tournaments:
                continue
            per_fixture: dict[str, SourceEvent] = {}
            for book in self.bookmakers:
                try:
                    rows = await self._get(client, "odds-by-tournaments", bookmaker=book,
                                           tournamentIds=",".join(map(str, tournaments)))
                except RuntimeError as e:
                    errors.append(f"{book}: {e}"[:120])
                    continue
                for row in rows if isinstance(rows, list) else []:
                    fx = by_id.get(row.get("fixtureId"))
                    if fx is None:
                        continue
                    ev = per_fixture.get(fx["fixtureId"])
                    if ev is None:
                        ev = per_fixture[fx["fixtureId"]] = fixture_to_event(fx, fam, sport_id == 10)
                    ev.quotes.extend(parse_book_odds(row, book, self._markets[sport_id], fetched_at,
                                                     {HOME: ev.home, DRAW: "Empate", AWAY: ev.away}))
            events.extend(e for e in per_fixture.values() if e.quotes)

        self._cache = events
        self._last_round = time.time()
        self.rounds += 1
        self._saved["last_round"] = self._last_round
        self._save_state()
        self.status = (f"{len(events)} partidos con cuotas ({', '.join(self.bookmakers)}) | "
                       f"peticiones usadas {self.used}/{self.limit} | próxima ronda: cuando haya partidos "
                       f"en 1-3 h (mín. {self.interval_s / 3600:.1f} h)"
                       + (f" | errores: {'; '.join(errors)}" if errors else ""))
        return events

    def _save_state(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._state.write_text(json.dumps(self._saved), encoding="utf-8")

    def _daily_budget(self) -> tuple[float, int]:
        """Reparte lo que queda del mes entre los días que faltan. Devuelve (permitido hoy, usado hoy)."""
        today = datetime.now(timezone.utc).date()
        if self._saved.get("day") != today.isoformat():
            self._saved["day"], self._saved["used_at_day_start"] = today.isoformat(), self.used or 0
            self._save_state()
        used_today = (self.used or 0) - int(self._saved.get("used_at_day_start", self.used or 0))
        days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
        left = max(0, (self.remaining or 0) + used_today - self.reserve)
        return left / days_left, used_today

    def _kickoff_count(self, targets: list[SourceEvent]) -> int:
        fams = {SPORT_FAMILY.get(s) for s in self.sports}
        now = time.time()
        return sum(1 for t in targets if t.sport in fams and not t.live and t.start
                   and KICKOFF_MIN_S <= t.start.timestamp() - now <= KICKOFF_MAX_S)

    def _pick_tournaments(self, fixtures: list[dict], family: str, targets: list[SourceEvent]) -> list[int]:
        """Los torneos con más partidos que también están en Polymarket (máx. 5 por llamada)."""
        candidates = [fixture_to_event(f, family, family == "soccer") for f in fixtures]
        poly = [t for t in targets if t.sport == family and not t.live]
        if not poly:
            return []
        clusters = match_events({"polymarket": poly, self.name: candidates}, self.match_cfg)
        counts: Counter[int] = Counter()
        now = time.time()
        for c in clusters:
            if len(c.feeds) == 2:
                for ev, _ in c.members:
                    if ev.feed == self.name:
                        # Pesan mucho más los partidos que empiezan en 1-3 h
                        soon = ev.start and KICKOFF_MIN_S <= ev.start.timestamp() - now <= KICKOFF_MAX_S
                        counts[int(ev.league)] += 10 if soon else 1
        return [t for t, _ in counts.most_common(self.max_tournaments)]


def main_markets(markets: list[dict]) -> dict[int, tuple[str, dict[str, str]]]:
    """Mercado principal por deporte: 1X2 en fútbol, ganador (moneyline) en el resto."""
    out: dict[int, tuple[str, dict[str, str]]] = {}
    for sport_id in SPORT_FAMILY:
        want = "1x2" if sport_id == 10 else "moneyline"
        cands = sorted(
            (m for m in markets if m.get("sportId") == sport_id and m.get("marketType") == want
             and not m.get("playerProp") and not m.get("handicap")
             and m.get("period") in ("fulltime", "result")),
            key=lambda m: m["marketId"],
        )
        if cands:
            m = cands[0]
            sides = {str(o["outcomeId"]): OUTCOME_SIDE[o["outcomeName"]]
                     for o in m["outcomes"] if o["outcomeName"] in OUTCOME_SIDE}
            out[sport_id] = (str(m["marketId"]), sides)
    return out


def fixture_to_event(fx: dict, family: str, three_way: bool) -> SourceEvent:
    # league guarda el tournamentId: lo usa _pick_tournaments
    return SourceEvent(
        feed=OddsPapiSource.name, event_id=fx["fixtureId"], sport=family,
        home=fx.get("participant1Name", ""), away=fx.get("participant2Name", ""),
        start=parse_iso(fx.get("startTime")), three_way=three_way,
        league=str(fx.get("tournamentId", "")),
    )


def parse_book_odds(row: dict, book: str, market: tuple[str, dict[str, str]], fetched_at: float,
                    names: dict[str, str]) -> list[Quote]:
    bo = (row.get("bookmakerOdds") or {}).get(book) or {}
    if not bo.get("bookmakerIsActive", True) or bo.get("suspended"):
        return []
    market_id, sides = market
    m = (bo.get("markets") or {}).get(market_id)
    if not m:
        return []
    quotes: list[Quote] = []
    for outcome_id, side in sides.items():
        player = (((m.get("outcomes") or {}).get(outcome_id) or {}).get("players") or {}).get("0") or {}
        price = float(player.get("price") or 0)
        if not player.get("active", True) or price <= 1.0:
            continue
        quotes.append(Quote(
            source=book, feed=OddsPapiSource.name, covers=frozenset({side}), odds=price,
            label=names[side],
            url=bo.get("fixturePath") or "", ts=fetched_at,
            max_stake=float(player["limit"]) if player.get("limit") else None,
        ))
    if len(quotes) != len(sides):
        return []  # mercado incompleto: no es comparable
    return quotes
