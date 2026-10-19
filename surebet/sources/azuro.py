"""
Azuro: casa de apuestas en blockchain (Polygon, USDT). Las cuotas y los límites se LEEN por su API pública
(la misma que usa su librería oficial @azuro-org/toolkit); para apostar se firma la apuesta con la billetera
(EIP-712) y la manda su relayer, sin gas. Base no tiene partidos hoy; Gnosis, Chiliz y BSC están cerradas.

  - GET  /market-manager/games-by-filters?environment=PolygonUSDT&gameState=Prematch&perPage=100&page=..
  - POST /market-manager/conditions-by-game-ids  {gameIds, environment}   mercados y cuotas de varios partidos
  - POST /bet/calculation  {bets: [{conditionId, outcomeId}], environment} -> {minBet, maxBet}
  - POST /market-manager/condition-batch  {conditionIds, environment}      estado y ganador (para liquidar)

Cuotas con el margen ya incluido (6-9 % en los mercados principales: más que Polymarket o SX), sin comisión
aparte. Se usan: ganador del partido (con prórroga) en todo menos fútbol; en fútbol el resultado a 90' (1X2) y,
desde el 19/10, también más/menos goles (1,5 a 4,5), resultado del 1er tiempo y más/menos del 1er tiempo (0,5 a 2,5),
los mismos que tiene Polymarket (sin CS2/Dota2 el valor de Azuro quedaba con pocas entradas por día).
Los límites (maxBet) de esos mercados extra se piden solo para partidos que empiezan en menos de EXTRA_LIMITS_H.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from ..matching import MatchConfig, match_events
from ..models import AWAY, DRAW, HOME, OVER, UNDER, Quote, SourceEvent
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
LINES_FT = (1.5, 2.5, 3.5, 4.5)      # más/menos goles del partido que se leen
LINES_H1 = (0.5, 1.5, 2.5)           # más/menos goles del 1er tiempo
EXTRA_LIMITS_H = 8.0                 # límites de los mercados extra: solo partidos que empiezan pronto


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


def game_conditions(conditions: list[dict], fam: str) -> list[tuple[str, dict]]:
    """Todos los mercados que se usan de un partido: [(mercado, condición)], el principal primero."""
    main = main_condition(conditions, fam)
    out = [main] if main else []
    if fam != "soccer":
        return out
    for c in conditions:
        if c.get("state") != "Active" or c.get("hidden") or len(c.get("outcomes") or []) not in (2, 3):
            continue
        title = c.get("title", "")
        if title == "1st Half - Result" and len(c["outcomes"]) == 3:
            out.append(("1x2_h1", c))
            continue
        if title not in ("Total Goals Over/Under", "1st Half - Total Goals Over/Under") or len(c["outcomes"]) != 2:
            continue
        try:
            line = float(c["outcomes"][0].get("point"))
        except (TypeError, ValueError):
            continue
        half = title.startswith("1st Half")
        if line in (LINES_H1 if half else LINES_FT):
            out.append((f"ou_h1_{line:g}" if half else f"ou_{line:g}", c))
    return out


def outcome_map(c: dict, home: str, away: str) -> dict[str, str] | None:
    """outcomeId -> H/A/D según el nombre de cada resultado (Azuro no los ordena)."""
    out = {}
    for o in c.get("outcomes") or []:
        t = o.get("title", "")
        res = (HOME if t == home else AWAY if t == away else DRAW if t.lower() == "draw"
               else OVER if t.startswith("Over ") else UNDER if t.startswith("Under ") else None)
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
        self._conds: dict[str, tuple[float, list[tuple[str, dict]]]] = {}  # gameId -> (ts, [(mercado, condición)])
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
            fetched = self._conds.get(gid, (0.0, []))[0]
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
                picks = game_conditions(by_game.get(gid, []), g["sport"]) if g else []
                if picks:
                    self._conds[gid] = (ts, picks)
                else:
                    self._conds.pop(gid, None)
        # Límite (monto máximo) de cada resultado, con menos frecuencia
        need = []
        for gid in sorted(self._wanted, key=lambda g: self._games[g]["start"] if g in self._games else now):
            g = self._games.get(gid)
            soon = g is not None and g["start"].timestamp() - now <= EXTRA_LIMITS_H * 3600
            for i, (_mk, c) in enumerate(self._conds.get(gid, (0.0, []))[1]):
                if i and not soon:
                    continue      # mercado extra de un partido lejano: no hace falta su límite todavía
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
        live = {c["conditionId"] for _t, picks in self._conds.values() for _m, c in picks}
        self._limits = {k: v for k, v in self._limits.items() if k[0] in live}
        self._dirty = True
        self.status = (f"{len(self._games)} partidos | {len(self._wanted)} emparejados | "
                       f"mercados {sum(len(p) for _t, p in self._conds.values())} | llamadas {self.requests}"
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
        ts, picks = self._conds.get(gid, (0.0, []))
        if not g or not picks:
            return []
        out = []
        for market, c in picks:
            omap = outcome_map(c, g["home"], g["away"])
            if not omap:
                continue
            line = market.rsplit("_", 1)[-1] if market.startswith("ou_") else ""
            tag = "1T " if market.startswith(("ou_h1_", "1x2_h1")) else ""
            names = {HOME: f"{tag}{g['home']}", AWAY: f"{tag}{g['away']}", DRAW: f"{tag}Empate",
                     OVER: f"Más {tag}{line}", UNDER: f"Menos {tag}{line}"}
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
        c = next((c for mk, c in self._conds.get(event_id, (0.0, []))[1] if mk == market), None)
        omap = outcome_map(c, g["home"], g["away"]) if g and c else None
        return {res: (f"azuro:{c['conditionId']}", oid) for oid, res in omap.items()} if omap else {}

    def _build(self) -> list[SourceEvent]:
        if self._dirty:
            events = self._bare()
            out = []
            for gid in self._wanted:
                se = events.get(gid)
                if se:
                    se.quotes = self.quotes_for(gid)
                    if se.quotes:
                        out.append(se)
            self._cache, self._dirty = out, False
        # A su hora de inicio Azuro deja de aceptar prepartido y su última cuota queda vieja: daba falsas surebets
        # de segundos al empezar el partido (Chicken Coop vs Zomblers, +5,8 % durante 23 s el 12/10)
        now = time.time()
        return [e for e in self._cache if e.start and e.start.timestamp() > now]
