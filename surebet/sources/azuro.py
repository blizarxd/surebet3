"""
Azuro: casa de apuestas en blockchain (Polygon, USDT). Las cuotas y los límites se LEEN por su API pública
(la misma que usa su librería oficial @azuro-org/toolkit); para apostar se firma la apuesta con la billetera
(EIP-712) y la manda su relayer, sin gas. Base no tiene partidos hoy; Gnosis, Chiliz y BSC están cerradas.

  - GET  /market-manager/games-by-filters?environment=PolygonUSDT&gameState=Prematch&perPage=100&page=..
  - POST /market-manager/conditions-by-game-ids  {gameIds, environment}   mercados y cuotas de varios partidos
  - POST /bet/calculation  {bets: [{conditionId, outcomeId}], environment} -> {minBet, maxBet}
  - POST /market-manager/condition-batch  {conditionIds, environment}      estado y ganador (para liquidar)

Cuotas con el margen ya incluido (6-9 % en los mercados principales: más que Polymarket o SX), sin comisión
aparte. Se usan: resultado a 90' del fútbol (1X2) y ganador del partido (con prórroga) del resto.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from ..matching import MatchConfig, match_events
from ..models import AWAY, DRAW, HOME, Quote, SourceEvent
from .base import Source

API = "https://api.onchainfeed.org/api/v1/public"
NAME = "azuro"
ENV = "PolygonUSDT"
MIN_STAKE = 1.0
SPORT_FAMILY = {"football": "soccer", "ice-hockey": "hockey", "american-football": "americanfootball",
                "basketball": "basketball", "tennis": "tennis", "baseball": "baseball", "mma": "mma",
                "boxing": "boxing", "volleyball": "volleyball", "cs2": "esports:cs2", "lol": "esports:lol",
                "dota-2": "esports:dota2", "valorant": "esports:valorant"}
DISCOVERY_S = 600
GAMES_PER_CALL = 25
ODDS_CALLS_PER_PASS = 6
LIMIT_TTL_S = 1800
LIMITS_PER_PASS = 25
REQUEST_GAP_S = 0.1
MIN_PASS_S = 15.0         # una pasada como mucho cada 15 s (sin martillar la API)


def main_condition(conditions: list[dict], fam: str) -> tuple[str, dict] | None:
    """(mercado, condición) del ganador del partido: 1X2 a 90' en fútbol, ganador con prórroga en el resto."""
    for c in conditions:
        if c.get("state") != "Active" or c.get("hidden"):
            continue
        title, n = c.get("title", ""), len(c.get("outcomes") or [])
        if fam == "soccer" and title == "Match Result" and n == 3:
            return "1x2", c
        if fam != "soccer" and title.startswith("Match Winner") and n == 2:
            return "ml", c
    return None


def outcome_map(c: dict, home: str, away: str) -> dict[str, str] | None:
    """outcomeId -> H/A/D según el nombre de cada resultado (Azuro no los ordena)."""
    out = {}
    for o in c.get("outcomes") or []:
        t = o.get("title", "")
        res = HOME if t == home else AWAY if t == away else DRAW if t.lower() == "draw" else None
        if res is None or res in out.values():
            return None
        out[str(o["outcomeId"])] = res
    return out


class AzuroSource(Source):
    name = NAME

    def __init__(self, horizon_h: float = 24, match_cfg: MatchConfig | None = None) -> None:
        super().__init__()
        self.horizon_h = horizon_h
        self.match_cfg = match_cfg or MatchConfig()
        self._games: dict[str, dict] = {}                     # gameId -> partido
        self._games_ts = 0.0
        self._conds: dict[str, tuple[float, str, dict]] = {}  # gameId -> (ts, mercado, condición)
        self._limits: dict[tuple[str, str], tuple[float, float]] = {}  # (condición, resultado) -> (ts, máx)
        self._wanted: set[str] = set()
        self._targets: list[SourceEvent] = []
        self._task: asyncio.Task | None = None
        self._cache: list[SourceEvent] = []
        self._dirty = True
        self.requests = 0
        self._errors = 0
        self._last_err = ""
        self._pass_ts = 0.0

    async def _call(self, client: httpx.AsyncClient, method: str, path: str, **kw) -> dict | None:
        try:
            r = await client.request(method, f"{API}{path}", timeout=20, **kw)
            self.requests += 1
        except httpx.HTTPError as e:
            self._errors += 1
            self._last_err = f"{path.split('?')[0]}: {type(e).__name__}"
            return None
        finally:
            await asyncio.sleep(REQUEST_GAP_S)
        if r.status_code not in (200, 201):
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
        if now - self._games_ts > DISCOVERY_S:
            await self._discover(client)
        if self._targets:
            self._match()
        # Cuotas de los partidos emparejados (varios por llamada)
        due = []
        for gid in self._wanted:
            g = self._games.get(gid)
            if not g:
                continue
            to_start = g["start"].timestamp() - now
            ttl = 60 if to_start < 3600 else 180 if to_start < 6 * 3600 else 600
            fetched = self._conds.get(gid, (0.0, "", {}))[0]
            if now - fetched >= ttl:
                due.append((fetched, gid))
        due = [gid for _, gid in sorted(due)[:GAMES_PER_CALL * ODDS_CALLS_PER_PASS]]
        for i in range(0, len(due), GAMES_PER_CALL):
            chunk = due[i:i + GAMES_PER_CALL]
            data = await self._call(client, "POST", "/market-manager/conditions-by-game-ids",
                                    json={"gameIds": chunk, "environment": ENV})
            ts = time.time()
            by_game: dict[str, list] = {}
            for c in (data or {}).get("conditions") or []:
                by_game.setdefault(str((c.get("game") or {}).get("gameId")), []).append(c)
            for gid in chunk:
                g = self._games.get(gid)
                pick = main_condition(by_game.get(gid, []), g["sport"]) if g else None
                if pick:
                    self._conds[gid] = (ts, pick[0], pick[1])
                else:
                    self._conds.pop(gid, None)
        # Límite (monto máximo) de cada resultado, con menos frecuencia
        need = []
        for gid in self._wanted:
            _ts, _mk, c = self._conds.get(gid, (0.0, "", {}))
            for o in c.get("outcomes") or []:
                k = (c["conditionId"], str(o["outcomeId"]))
                if now - self._limits.get(k, (0.0, 0.0))[0] >= LIMIT_TTL_S:
                    need.append(k)
        for cid, oid in need[:LIMITS_PER_PASS]:
            data = await self._call(client, "POST", "/bet/calculation",
                                    json={"bets": [{"conditionId": cid, "outcomeId": int(oid)}], "environment": ENV})
            resp = (data or {}).get("response") or {}
            # Sin límite (p. ej. 409 "BadData": ese resultado no acepta apuestas ahora) = 0, y no se reintenta
            # hasta el próximo refresco; si no, se volvía a pedir en cada pasada
            self._limits[(cid, oid)] = (time.time(), float(resp["maxBet"]) if "maxBet" in resp else 0.0)
        live = {c["conditionId"] for _t, _m, c in self._conds.values()}
        self._limits = {k: v for k, v in self._limits.items() if k[0] in live}
        self._dirty = True
        self.status = (f"{len(self._games)} partidos | {len(self._wanted)} emparejados | "
                       f"mercados {len(self._conds)} | llamadas {self.requests}"
                       + (f" | errores {self._errors} (último: {self._last_err})" if self._errors else ""))

    async def _discover(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        games: dict[str, dict] = {}
        for page in range(1, 40):
            data = await self._call(client, "GET", "/market-manager/games-by-filters", params={
                "environment": ENV, "gameState": "Prematch", "perPage": 100, "page": page,
                "orderBy": "startsAt", "orderDirection": "asc"})
            if data is None:
                return
            stop = False
            for g in data.get("games") or []:
                start = float(g.get("startsAt") or 0)
                if start > now + self.horizon_h * 3600:
                    stop = True
                    break
                fam = SPORT_FAMILY.get((g.get("sport") or {}).get("slug", ""))
                parts = g.get("participants") or []
                if fam and len(parts) == 2 and start > now:
                    games[str(g["gameId"])] = {
                        "id": str(g["gameId"]), "sport": fam, "start": datetime.fromtimestamp(start, timezone.utc),
                        "home": parts[0].get("name", ""), "away": parts[1].get("name", ""),
                        "league": (g.get("league") or {}).get("name", ""), "slug": g.get("slug", "")}
            if stop or page >= int(data.get("totalPages") or 1):
                break
        self._games, self._games_ts = games, time.time()

    def _bare(self) -> dict[str, SourceEvent]:
        return {gid: SourceEvent(feed=self.name, event_id=gid, sport=g["sport"], home=g["home"], away=g["away"],
                                 start=g["start"], three_way=g["sport"] == "soccer", league=g["league"],
                                 url="https://bookmaker.xyz")
                for gid, g in self._games.items()}

    def _match(self) -> None:
        bare = self._bare()
        clusters = match_events({"otros": self._targets, self.name: list(bare.values())}, self.match_cfg)
        self._wanted = {ev.event_id for c in clusters if self.name in c.feeds and len(c.feeds) >= 2
                        for ev, _ in c.members if ev.feed == self.name}

    def quotes_for(self, gid: str) -> list[Quote]:
        g = self._games.get(gid)
        ts, market, c = self._conds.get(gid, (0.0, "", {}))
        if not g or not c:
            return []
        omap = outcome_map(c, g["home"], g["away"])
        if not omap:
            return []
        names = {HOME: g["home"], AWAY: g["away"], DRAW: "Empate"}
        out = []
        for o in c.get("outcomes") or []:
            oid = str(o["outcomeId"])
            lim = self._limits.get((c["conditionId"], oid))
            odds = float(o.get("odds") or 0)
            if lim is None or lim[1] < MIN_STAKE or odds <= 1 or o.get("state") not in (None, "Active"):
                continue      # sin límite conocido todavía: no se arriesga a suponer liquidez
            out.append(Quote(source=NAME, feed=NAME, covers=frozenset({omap[oid]}), odds=odds,
                             label=names[omap[oid]], url="https://bookmaker.xyz", ts=ts, max_stake=lim[1],
                             min_stake=MIN_STAKE, market=market, ref=f"{c['conditionId']}|{oid}"))
        return out

    def resolution_for(self, event_id: str, market: str) -> dict[str, tuple[str, str]]:
        """{resultado: ("azuro:<conditionId>", outcomeId)} para liquidar con el resultado oficial de Azuro."""
        g = self._games.get(event_id)
        _ts, mk, c = self._conds.get(event_id, (0.0, "", {}))
        omap = outcome_map(c, g["home"], g["away"]) if g and c and mk == market else None
        return {res: (f"azuro:{c['conditionId']}", oid) for oid, res in omap.items()} if omap else {}

    def _build(self) -> list[SourceEvent]:
        if not self._dirty:
            return self._cache
        events = self._bare()
        out = []
        for gid in self._wanted:
            se = events.get(gid)
            if se:
                se.quotes = self.quotes_for(gid)
                if se.quotes:
                    out.append(se)
        self._cache, self._dirty = out, False
        return out
