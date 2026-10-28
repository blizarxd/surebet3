"""
1xBet (y 888starz, misma plataforma y mismas cuotas): feed JSON público que usa su propia web.

Gratis, sin key y en tiempo real (prepartido y EN VIVO). Detalles verificados (oct-2026,
espejo 1xbet-col.com, partner 285):
  - /service-api/{LineFeed|LiveFeed}/Get1x2_VZip  -> partidos con cuotas (máx. 50 por llamada)
  - /service-api/LineFeed/GetChampsZip?sport=N     -> ligas de un deporte
  - El servidor exige un ORDEN FIJO de parámetros (si no, responde 406).
  - `champs` acepta varias ligas si los IDs van en orden ASCENDENTE (máx. 50 partidos por respuesta).

Como fútbol solo ya tiene ~500 ligas, no se piden todas: la fuente APRENDE qué ligas tienen
partidos que también están en Polymarket (se guarda en data/onexbet/leagues.json), refresca
esas cada minuto y explora el resto poco a poco, empezando por las de nombre parecido.

Mercados (campo E, sin línea "P"):
  G=1   T=1/2/3   -> 1 / X / 2            (fútbol, hockey: 1X2 tiempo regular; tenis/esports: 1 y 2)
  G=8   T=4/5/6   -> 1X / 12 / X2         (doble oportunidad)
  G=101 T=401/402 -> ganador con prórroga (básquet)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import httpx

from ..matching import MatchConfig, match_events
from ..models import AWAY, DRAW, HOME, OVER, UNDER, Quote, SourceEvent
from ..sports import esports_family
from .base import Source

SPORT_FAMILY = {1: "soccer", 2: "hockey", 3: "basketball", 4: "tennis", 6: "volleyball", 10: "tabletennis",
                40: "esports", 66: "cricket", 13: "americanfootball", 5: "baseball", 21: "darts"}
FAMILY_SPORTS = {}
for _sid, _fam in SPORT_FAMILY.items():
    FAMILY_SPORTS.setdefault(_fam, []).append(_sid)

# (G, T) -> resultados que cubre
MAIN_3WAY = {(1, 1): {HOME}, (1, 2): {DRAW}, (1, 3): {AWAY},
             (8, 4): {HOME, DRAW}, (8, 5): {HOME, AWAY}, (8, 6): {DRAW, AWAY}}
MAIN_2WAY = {(1, 1): {HOME}, (1, 3): {AWAY}}
OT_2WAY = {(101, 401): {HOME}, (101, 402): {AWAY}}

PARAM_ORDER = ["sports", "champs", "count", "lng", "mode", "partner", "getEmpty"]
H1_INTERVAL_S = 600        # primer tiempo (sub-partido): cada 10 min; 2 min si empieza en < 1 h
H1_MAX_PER_PASS = 3        # llamadas de primer tiempo por pasada (repartidas: sin ráfagas al arrancar)
REQUEST_TIMEOUT_S = 10
REQUEST_GAP_S = 0.6        # pausa entre llamadas (23/10: subida de 0,3 a 0,6 s: 1xBet nos rechazaba por ratos, probable límite de ritmo)
BACKOFF_S = [120, 300, 900, 2400, 3600]  # si deja de responder: pausa 2, 5, 15, 40, 60 min (los tropiezos de segundos se superan solos)
FAIL_LIMIT = 8                           # fallos SEGUIDOS para pausar (con 4 se pausaba por tropiezos normales de 1xBet: ~2-4 % de errores)
NOTIFY_AFTER_S = 15 * 60                 # solo se avisa por Telegram si la caída dura al menos esto (antes: 4 avisos por hora de noche)
TOTALS_G, OVER_T, UNDER_T = 17, 9, 10
# Deportes donde el tiempo reglamentario puede terminar empatado: su G=1 sin empate NO es "ganador"
REGULATION_DRAW = {"basketball", "hockey", "americanfootball"}
BATCH_GAMES = 50     # una llamada devuelve como mucho 50 partidos
BATCH_LEAGUES = 25   # y no conviene una URL enorme
NEAR_H, MID_H = 2.0, 6.0   # partidos que empiezan en < 2 h: consulta normal; entre 2 y 6 h: más espaciada; más lejos: casi nada
MID_INTERVAL_S, FAR_INTERVAL_S = 300, 900
HOT_S = 45           # partidos con una señal de valor activa: su consulta se repite cada 45 s (el resto, cada prematch_interval_s)
STALE_LINE_S = 900   # consultas que ya no se repiten (exploración, lotes viejos): se olvidan a los 15 min
STALE_LIVE_S = 120
LEAGUES_SAVE_S = 300  # leagues.json se guarda como mucho cada 5 min


def result_quotes(main: dict, table: dict, names: dict, market: str, url: str, ts: float) -> list[Quote]:
    """1X2 / doble oportunidad / ganador desde {(G, T): cuota}."""
    out = []
    for gt, covers in table.items():
        odds = main.get(gt, 0.0)
        if odds > 1.0:
            label = " o ".join(names[o] for o in (HOME, DRAW, AWAY) if o in covers)
            out.append(Quote(source="1xbet", feed=OneXBetSource.name, covers=frozenset(covers), odds=odds,
                             label=label if len(covers) == 1 else f"{label} (doble oport.)", url=url, ts=ts,
                             market=market))
    return out


def totals_quotes(rows: list[dict], prefix: str, tag: str, url: str, ts: float) -> list[Quote]:
    """Más/menos: solo líneas .5 (sin devolución) y solo si están ambos lados."""
    by_line: dict[float, dict[int, float]] = {}
    for x in rows:
        p, t, c = x.get("P"), x.get("T"), float(x.get("C") or 0)
        if p is None or x.get("B") or c <= 1.0 or t not in (OVER_T, UNDER_T) or float(p) % 1 != 0.5:
            continue
        by_line.setdefault(float(p), {})[t] = c
    out = []
    for line, sides in by_line.items():
        if len(sides) != 2:
            continue
        market = f"{prefix}_{line:g}"
        out.append(Quote(source="1xbet", feed=OneXBetSource.name, covers=frozenset({OVER}), odds=sides[OVER_T],
                         label=f"Más {tag}{line:g}", url=url, ts=ts, market=market))
        out.append(Quote(source="1xbet", feed=OneXBetSource.name, covers=frozenset({UNDER}), odds=sides[UNDER_T],
                         label=f"Menos {tag}{line:g}", url=url, ts=ts, market=market))
    return out


def parse_first_half(raw: dict, parent: SourceEvent, ts: float) -> list[Quote]:
    """
    Sub-partido "1st half" de GetGameZip (GE agrupado por columnas): 1X2 y doble oportunidad
    del primer tiempo (1x2_h1) y totales del primer tiempo (ou_h1_<línea>).
    """
    if (raw.get("PN") or "").lower() != "1st half" or raw.get("TG"):
        return []
    rows = [{"G": grp.get("G"), **x} for grp in raw.get("GE") or [] for col in grp.get("E") or [] for x in col]
    main = {(x.get("G"), x.get("T")): float(x.get("C") or 0) for x in rows if "P" not in x and not x.get("B")}
    names = {HOME: parent.home, DRAW: "Empate", AWAY: parent.away}
    quotes = [q for q in result_quotes(main, MAIN_3WAY, names, "1x2_h1", parent.url, ts)]
    if sum(1 for q in quotes if len(q.covers) == 1) < 3:
        quotes = []
    for q in quotes:
        q.label = f"1T {q.label}"
    quotes += totals_quotes([x for x in rows if x.get("G") == TOTALS_G], "ou_h1", "1T ", parent.url, ts)
    return quotes


def slim_raw(raw: dict) -> dict:
    """Solo lo que usa parse_event (cuotas E, totales alternativos AE[G=17], nombres, hora, marcador)."""
    out = {k: raw[k] for k in ("SI", "O1", "O2", "L", "S", "SN", "LI", "CI", "I") if k in raw}
    out["E"] = [{k: x[k] for k in ("G", "T", "C", "P", "B") if k in x} for x in raw.get("E") or []]
    out["AE"] = [{"G": g.get("G"), "ME": [{k: x[k] for k in ("G", "T", "C", "P", "B") if k in x}
                                          for x in g.get("ME") or []]}
                 for g in raw.get("AE") or [] if g.get("G") == TOTALS_G]
    sc = raw.get("SC")
    if isinstance(sc, dict) and isinstance(sc.get("FS"), dict):
        out["SC"] = {"FS": sc["FS"]}
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class OneXBetSource(Source):
    name = "onexbet"

    def __init__(self, base_url: str, partner: int, cache_dir: Path, sports: list[int],
                 prematch_interval_s: float = 120, live_interval_s: float = 10,
                 champs_refresh_s: float = 3600, explore_per_cycle: int = 4, horizon_h: float = 24,
                 concurrency: int = 2, match_cfg: MatchConfig | None = None, live_enabled: bool = True) -> None:
        super().__init__()
        self.base = base_url.rstrip("/")
        self.partner = partner
        self.cache_dir = cache_dir
        self.sports = sports
        self.prematch_interval_s = prematch_interval_s
        self.live_interval_s = live_interval_s
        self.live_enabled = live_enabled     # el valor y las surebets que se avisan son de prepartido: el en vivo es tráfico de más
        self.champs_refresh_s = champs_refresh_s
        self.explore_per_cycle = explore_per_cycle
        self.horizon_h = horizon_h
        self.match_cfg = match_cfg or MatchConfig()
        self._sem = asyncio.Semaphore(concurrency)
        self._leagues_file = cache_dir / "leagues.json"
        # LI -> {"name", "sport", "hits", "last_hit", "explored"}
        try:
            self.leagues: dict[str, dict] = json.loads(self._leagues_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.leagues = {}
        self._champs: dict[int, tuple[float, list[dict]]] = {}
        self._line: dict[str, tuple[float, list[dict]]] = {}   # clave de consulta -> (ts, eventos crudos)
        self._live: dict[int, tuple[float, list[dict]]] = {}
        self._task: asyncio.Task | None = None
        self._h1: dict[str, tuple[float, list[Quote]]] = {}   # id partido -> (ts, quotes 1er tiempo)
        self._matched: dict[str, float] = {}                   # partidos de fútbol emparejados -> inicio
        self.hot: set[str] = set()                              # ids de partidos con señal de valor activa (lo pone scan.py)
        self._targets: list[SourceEvent] = []
        self._parents: dict[str, SourceEvent] = {}
        # Partidos ya parseados: solo se rehacen cuando llegan datos nuevos (antes, todo el feed cada ciclo)
        self._events_cache: list[SourceEvent] = []
        self._cache_ts = 0.0
        self._dirty = True
        self._leagues_saved = 0.0
        self._errors = 0
        self.requests = 0
        self._fails_in_row = 0
        self._backoff_level = 0
        self._paused_until = 0.0
        self._down_since: float | None = None
        self._down_notified = False
        self.notices: list[str] = []  # avisos para Telegram (caída / vuelta del feed)

    # ------------------------------------------------------------------ http
    def _url(self, feed: str, method: str, **params) -> str:
        params.setdefault("lng", "en")
        params.setdefault("partner", self.partner)
        ordered = [f"{k}={params[k]}" for k in PARAM_ORDER if k in params]
        return f"{self.base}/service-api/{feed}/{method}?{'&'.join(ordered)}"

    async def _raw_get(self, client: httpx.AsyncClient, url: str) -> object | None:
        """GET con pausa entre llamadas; cuenta fallos seguidos para pausar si 1xBet deja de responder."""
        if time.time() < self._paused_until:
            return None
        async with self._sem:
            if time.time() < self._paused_until:  # se pausó mientras esperaba turno
                return None
            try:
                r = await client.get(url, headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT_S)
                self.requests += 1
            except httpx.HTTPError:
                r = None
            await asyncio.sleep(REQUEST_GAP_S)
        if r is None or r.status_code != 200:
            self._errors += 1
            self._fails_in_row += 1
            if self._fails_in_row >= FAIL_LIMIT and time.time() >= self._paused_until:
                pause = BACKOFF_S[min(self._backoff_level, len(BACKOFF_S) - 1)]
                self._paused_until = time.time() + pause
                self._backoff_level += 1
                if self._down_since is None:
                    self._down_since = time.time()
            if self._down_since is not None and not self._down_notified and time.time() - self._down_since >= NOTIFY_AFTER_S:
                self._down_notified = True
                self.notices.append(f"⚠️ 1xBet lleva {(time.time() - self._down_since) / 60:.0f} min sin responder (posible bloqueo "
                                    "temporal de la IP). El escáner sigue con las demás fuentes y lo reintenta solo; te aviso cuando vuelva.")
            return None
        self._fails_in_row = 0
        self._backoff_level = 0
        if self._down_since is not None:
            mins = (time.time() - self._down_since) / 60
            self._down_since = None
            if self._down_notified:                       # si nunca se avisó de la caída, tampoco de la vuelta
                self.notices.append(f"✅ 1xBet volvió a responder (estuvo caído {mins:.0f} min). "
                                    "El escáner ya está usando sus cuotas otra vez.")
            self._down_notified = False
        try:
            return r.json()
        except ValueError:
            return None

    async def _get(self, client: httpx.AsyncClient, url: str) -> list[dict]:
        data = await self._raw_get(client, url)
        return (data.get("Value") or []) if isinstance(data, dict) else []

    async def _events(self, client: httpx.AsyncClient, feed: str, sport: int, champs: list[str] | None = None) -> list[dict]:
        params = {"sports": sport, "count": 50, "mode": 4, "getEmpty": "true"}
        if champs:
            # El feed exige los IDs de liga en orden ascendente (si no, 406)
            params["champs"] = ",".join(sorted(champs, key=int))
        return [slim_raw(r) for r in await self._get(client, self._url(feed, "Get1x2_VZip", **params))
                if isinstance(r, dict)]

    # ------------------------------------------------------------------ refresco en segundo plano
    async def fetch(self, client: httpx.AsyncClient, targets: list[SourceEvent] | None = None) -> list[SourceEvent]:
        self._targets = targets or []
        if self._task is None or self._task.done():
            if self._task and self._task.exception():
                self.status = f"error: {self._task.exception()!r}"[:160]
            self._task = asyncio.create_task(self._refresh(client))
        return self._build_events()

    async def _refresh(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        if now < self._paused_until:
            self.status = (f"1xBet no responde (¿IP bloqueada temporalmente?): en pausa "
                           f"{(self._paused_until - now) / 60:.0f} min más | {len(self._build_events())} partidos en caché")
            return
        jobs = []
        poly_fams = {t.sport.split(":")[0] for t in self._targets}   # "esports:cs2" -> "esports"
        live_fams = {t.sport.split(":")[0] for t in self._targets if t.live}
        sports = [s for s in self.sports if SPORT_FAMILY.get(s) in poly_fams]

        for sport in sports:
            # Ligas del deporte (para explorar)
            if now - self._champs.get(sport, (0, []))[0] > self.champs_refresh_s:
                jobs.append(self._refresh_champs(client, sport))
            # Top 50 prepartido (ligas grandes) sin filtro de liga
            key = f"top:{sport}"
            if now - self._line.get(key, (0, []))[0] > self._interval(key, now):
                jobs.append(self._load_line(client, key, sport, None))
            # Vivo
            if self.live_enabled and SPORT_FAMILY[sport] in live_fams and now - self._live.get(sport, (0, []))[0] > self.live_interval_s:
                jobs.append(self._load_live(client, sport))

        # Ligas con partidos en Polymarket (últimos 3 días): refresco periódico, agrupadas
        hot = [li for li, info in self.leagues.items()
               if info.get("sport") in sports and now - info.get("last_hit", 0) < 3 * 86400]
        for sport, batch in self._batches_by_sport(hot):
            key = "champs:" + ",".join(sorted(batch, key=int))
            if now - self._line.get(key, (0, []))[0] > self._interval(key, now):
                jobs.append(self._load_line(client, key, sport, batch))

        # Exploración: ligas aún no vistas, primero las de nombre parecido a las de Polymarket
        for sport, batch in self._batches_by_sport(self._explore_candidates(sports))[: self.explore_per_cycle]:
            jobs.append(self._load_line(client, "explore:" + ",".join(batch), sport, batch, explore=True))

        # Primer tiempo de los partidos de fútbol emparejados con Polymarket (sub-partido = id + 1)
        due = []
        for eid, start in self._matched.items():
            ttl = 120 if start - now < 3600 else H1_INTERVAL_S
            if start > now and now - self._h1.get(eid, (0, []))[0] > ttl:
                due.append((self._h1.get(eid, (0, []))[0], eid))
        for _, eid in sorted(due)[:H1_MAX_PER_PASS]:
            jobs.append(self._load_first_half(client, eid))

        # Reconfirmar rápido: las consultas que traen un partido con señal activa se repiten antes (45 s, no 120 s), así una
        # cuota que cambió en 1xBet se nota pronto y «vigente» significa algo. Solo las que aún no tocaba el ciclo normal.
        if self.hot:
            for key, (ts, rows) in list(self._line.items()):
                if HOT_S < now - ts <= self._interval(key, now) and any(str(r.get("I")) in self.hot for r in rows):
                    args = self._reload_args(key)
                    if args:
                        jobs.append(self._load_line(client, key, *args))
        if not jobs:
            return  # nada nuevo: no hace falta volver a emparejar ni guardar
        await asyncio.gather(*jobs, return_exceptions=True)
        self._prune()
        self._learn()
        explored = sum(1 for i in self.leagues.values() if i.get("explored"))
        if time.time() < self._paused_until:
            self.status = (f"1xBet dejó de responder: pausa de {(self._paused_until - time.time()) / 60:.0f} min "
                           f"(evita que bloqueen la IP)")
            return
        self.status = (f"{len(self._build_events())} partidos | ligas útiles {len(hot)}, "
                       f"exploradas {explored}/{len(self.leagues)} | llamadas {self.requests}"
                       + (f" | errores {self._errors}" if self._errors else ""))

    async def _refresh_champs(self, client: httpx.AsyncClient, sport: int) -> None:
        rows = await self._get(client, self._url("LineFeed", "GetChampsZip", sports=sport)
                               .replace("sports=", "sport="))
        self._champs[sport] = (time.time(), [])  # solo importa cuándo; las ligas quedan en self.leagues
        for c in rows:
            li = str(c.get("LI"))
            info = self.leagues.setdefault(li, {"name": c.get("L", ""), "sport": sport, "hits": 0})
            info["name"], info["games"] = c.get("L", ""), c.get("GC", 0)

    async def _load_line(self, client, key: str, sport: int, champs: list[str] | None, explore: bool = False) -> None:
        rows = await self._events(client, "LineFeed", sport, champs)
        self._line[key] = (time.time(), rows)
        self._dirty = True
        if explore and champs:
            for li in champs:
                self.leagues[li]["explored"] = time.time()

    def _interval(self, key: str, now: float) -> float:
        """Cada cuánto repetir una consulta: según lo cerca que esté el primer partido que trae (lo que se avisa es de ≤ 6 h)."""
        rows = self._line.get(key, (0, []))[1]
        starts = [r.get("S", 0) for r in rows if r.get("S", 0) > now]
        if not starts:
            return self.prematch_interval_s
        ahead_h = (min(starts) - now) / 3600
        return self.prematch_interval_s if ahead_h < NEAR_H else MID_INTERVAL_S if ahead_h < MID_H else FAR_INTERVAL_S

    def _reload_args(self, key: str) -> tuple[int, list[str] | None] | None:
        """(deporte, ligas) para repetir una consulta guardada en self._line; None si no se puede (exploración)."""
        if key.startswith("top:"):
            return int(key[4:]), None
        if key.startswith("champs:"):
            lis = [x for x in key[7:].split(",") if x]
            sport = self.leagues.get(lis[0], {}).get("sport") if lis else None
            return (sport, lis) if sport is not None else None
        return None

    async def _load_first_half(self, client, eid: str) -> None:
        url = (f"{self.base}/service-api/LineFeed/GetGameZip?id={int(eid) + 1}&lng=en&isSubGames=true"
               f"&GroupEvents=true&countevents=250&grMode=4&partner={self.partner}")
        data = await self._raw_get(client, url)
        ts = time.time()
        raw = (data.get("Value") or {}) if isinstance(data, dict) else {}
        parent = self._parents.get(eid)
        ok = parent is not None and isinstance(raw, dict) and raw.get("O1") == parent.home
        self._h1[eid] = (ts, parse_first_half(raw, parent, ts) if ok else [])
        self._dirty = True

    async def _load_live(self, client, sport: int) -> None:
        self._live[sport] = (time.time(), await self._events(client, "LiveFeed", sport))
        self._dirty = True

    def _prune(self) -> None:
        """Olvida lo que ya no se refresca (en un servidor 24/7 si no, crece sin parar)."""
        now = time.time()
        line = {k: v for k, v in self._line.items() if now - v[0] <= STALE_LINE_S}
        live = {k: v for k, v in self._live.items() if now - v[0] <= STALE_LIVE_S}
        h1 = {k: v for k, v in self._h1.items() if k in self._matched}
        if (len(line), len(live), len(h1)) != (len(self._line), len(self._live), len(self._h1)):
            self._line, self._live, self._h1 = line, live, h1
            self._dirty = True

    def _batches_by_sport(self, leagues: list[str]) -> list[tuple[int, list[str]]]:
        """
        Agrupa ligas del mismo deporte en lotes de hasta ~50 partidos (lo máximo que devuelve
        una llamada), respetando el orden de prioridad de `leagues`.
        """
        batches: list[tuple[int, list[str]]] = []
        open_: dict[int, tuple[list[str], int]] = {}
        for li in leagues:
            info = self.leagues[li]
            sport, games = info["sport"], max(1, int(info.get("games") or 1))
            cur, total = open_.get(sport, ([], 0))
            if cur and (total + games > BATCH_GAMES or len(cur) >= BATCH_LEAGUES):
                batches.append((sport, cur))
                cur, total = [], 0
            open_[sport] = (cur + [li], total + games)
        batches += [(sport, lis) for sport, (lis, _) in open_.items() if lis]
        rank = {li: i for i, li in enumerate(leagues)}
        batches.sort(key=lambda b: min(rank[li] for li in b[1]))
        return batches

    def _explore_candidates(self, sports: list[int]) -> list[str]:
        """Ligas no exploradas (o hace >12 h), ordenadas por parecido con las ligas de Polymarket."""
        words: set[str] = set()
        for t in self._targets:
            words |= {w for w in re.split(r"[^a-z]+", t.league.lower()) if len(w) > 2 and w not in ("games", "league")}
        now = time.time()
        cands = []
        for li, info in self.leagues.items():
            if info.get("sport") not in sports or not info.get("games"):
                continue
            if now - info.get("explored", 0) < 24 * 3600:
                continue
            name_words = set(re.split(r"[^a-z]+", info.get("name", "").lower()))
            cands.append((len(words & name_words), info.get("games", 0), li))
        cands.sort(reverse=True)
        return [li for *_, li in cands]

    def _learn(self) -> None:
        """Marca qué ligas de 1xBet tienen partidos que también están en Polymarket."""
        events = self._build_events()
        if not events or not self._targets:
            return
        clusters = match_events({"polymarket": self._targets, self.name: events}, self.match_cfg)
        now = time.time()
        matched: dict[str, float] = {}
        for c in clusters:
            if len(c.feeds) == 2:
                for ev, _ in c.members:
                    if ev.feed == self.name and ev.league in self.leagues:
                        info = self.leagues[ev.league]
                        info["hits"] = info.get("hits", 0) + 1
                        info["last_hit"] = now
                    if ev.feed == self.name and ev.sport == "soccer" and not ev.live and ev.start:
                        matched[ev.event_id] = ev.start.timestamp()
        self._matched = matched
        if now - self._leagues_saved >= LEAGUES_SAVE_S:
            self._leagues_saved = now
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._leagues_file.write_text(json.dumps(self.leagues), encoding="utf-8")

    # ------------------------------------------------------------------ parseo
    def _build_events(self) -> list[SourceEvent]:
        if not self._dirty and time.time() - self._cache_ts < 60:
            return self._events_cache
        out: dict[str, SourceEvent] = {}
        self._parents = {}
        horizon = time.time() + self.horizon_h * 3600
        for ts, rows in self._line.values():
            for raw in rows:
                if raw.get("S", 0) <= horizon:
                    ev = self.parse_event(raw, ts, live=False)
                    if ev:
                        out[ev.event_id] = ev
        for ts, rows in self._live.values():
            for raw in rows:
                ev = self.parse_event(raw, ts, live=True)
                if ev:
                    out[ev.event_id] = ev  # el vivo pisa al prepartido del mismo partido
        for eid, ev in out.items():
            self._parents[eid] = ev
            h1 = self._h1.get(eid)
            if h1 and not ev.live:
                ev.quotes.extend(h1[1])
        self._events_cache, self._cache_ts, self._dirty = list(out.values()), time.time(), False
        return self._events_cache

    def parse_event(self, raw: dict, ts: float, live: bool) -> SourceEvent | None:
        return parse_event(raw, ts, live, self.base)


def parse_event(raw: dict, ts: float, live: bool, base: str = "https://1xbet-col.com") -> SourceEvent | None:
    from datetime import datetime, timezone

    sport = raw.get("SI")
    fam = SPORT_FAMILY.get(sport)
    home, away = raw.get("O1"), raw.get("O2")
    if not fam or not home or not away:
        return None
    if fam == "esports":
        fam = esports_family(name=raw.get("L", ""))
    main = {}
    for x in raw.get("E") or []:
        if "P" in x or x.get("B"):  # líneas de hándicap/total o mercado bloqueado
            continue
        main[(x.get("G"), x.get("T"))] = float(x.get("C") or 0)
    # Totales: la línea principal viene en E y las alternativas en AE[G=17].ME
    total_rows = [x for x in raw.get("E") or [] if x.get("G") == TOTALS_G]
    for grp in raw.get("AE") or []:
        if grp.get("G") == TOTALS_G:
            total_rows += grp.get("ME") or []

    if (1, 2) in main:                      # hay empate: 1X2 + doble oportunidad
        table, three_way = MAIN_3WAY, True
    elif (101, 401) in main:                # ganador con prórroga (básquet)
        if fam == "hockey":
            return None  # su "con prórroga" no incluye penales y Polymarket sí: pueden perder las dos patas
        table, three_way = OT_2WAY, False
    elif fam in REGULATION_DRAW:
        return None  # solo 1 y 2 del reglamentario (empate bloqueado): no equivale al ganador de Polymarket
    else:
        table, three_way = MAIN_2WAY, False
    names = {HOME: home, DRAW: "Empate", AWAY: away}
    league = raw.get("L", "")
    url = (f"{base}/en/{'live' if live else 'line'}/{_slug(raw.get('SN', ''))}/"
           f"{raw.get('LI')}-{_slug(league)}/{raw.get('CI') or raw.get('I')}-{_slug(home)}-{_slug(away)}")
    market = "1x2" if three_way else "ml"
    quotes = result_quotes(main, table, names, market, url, ts)
    singles = sum(1 for q in quotes if len(q.covers) == 1)
    if singles < (3 if three_way else 2):
        return None  # mercado principal incompleto o suspendido
    if fam == "soccer":
        quotes += totals_quotes(total_rows, "ou", "", url, ts)
    score = ""
    sc = raw.get("SC") or {}
    if live and isinstance(sc, dict):
        fs = sc.get("FS") or {}
        score = f"{fs.get('S1', 0)}-{fs.get('S2', 0)}"
    return SourceEvent(
        feed=OneXBetSource.name, event_id=str(raw.get("I")), sport=fam, home=home, away=away,
        start=datetime.fromtimestamp(raw.get("S", 0), timezone.utc) if raw.get("S") else None,
        three_way=three_way, quotes=quotes, live=live, score=score,
        period=str(sc.get("CPS", "")) if isinstance(sc, dict) else "", league=str(raw.get("LI", "")), url=url,
    )

