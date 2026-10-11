"""
The Odds API (the-odds-api.com): cuotas h2h de muchas casas en una sola llamada.

Costo: 1 crédito por (deporte × región × mercado) en cada llamada. El plan gratis
trae 500 créditos/mes, así que por defecto se consulta cada 5 minutos y se deja de
consultar al llegar a la reserva mínima. Para vivo de verdad hace falta plan de pago
o una fuente directa de la casa: estas cuotas NO son tiempo real.

sport="upcoming" devuelve los partidos en vivo + próximos de todos los deportes.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..models import AWAY, DRAW, HOME, Quote, SourceEvent
from ..pricing import odds_after_commission
from ..sports import oddsapi_family
from .base import Source, parse_iso

BASE = "https://api.the-odds-api.com/v4"


class OddsApiSource(Source):
    name = "oddsapi"
    slow = True

    def __init__(self, api_key: str, sports: list[str], regions: str = "eu",
                 bookmakers: list[str] | None = None, interval_s: float = 300.0,
                 min_credits: int = 20, commissions: dict[str, float] | None = None,
                 dump_dir: Path | None = None) -> None:
        super().__init__()
        self.dump_dir = dump_dir  # guarda la última respuesta cruda (para depurar sin gastar créditos)
        self.api_key = api_key
        self.sports = sports or ["upcoming"]
        self.regions = regions
        self.bookmakers = [b for b in (bookmakers or []) if b]
        self.interval_s = interval_s
        self.min_credits = min_credits
        self.commissions = commissions or {}
        self.remaining: int | None = None
        self.used: int | None = None
        self._cache: list[SourceEvent] = []
        self._last = 0.0

    async def fetch(self, client: httpx.AsyncClient) -> list[SourceEvent]:
        if not self.api_key:
            self.status = "sin ODDS_API_KEY (desactivada)"
            return []
        if time.time() - self._last < self.interval_s:
            return self._cache
        if self.remaining is not None and self.remaining <= self.min_credits:
            self.status = f"pausada: quedan {self.remaining} créditos (reserva {self.min_credits})"
            return self._cache

        events: list[SourceEvent] = []
        for sport in self.sports:
            params = {"apiKey": self.api_key, "markets": "h2h", "oddsFormat": "decimal", "dateFormat": "iso"}
            if self.bookmakers:
                params["bookmakers"] = ",".join(self.bookmakers)
            else:
                params["regions"] = self.regions
            r = await client.get(f"{BASE}/sports/{sport}/odds", params=params)
            self._read_quota(r)
            if r.status_code != 200:
                self.status = f"HTTP {r.status_code}: {r.text[:120]}"
                continue
            fetched_at = time.time()
            data = r.json()
            if self.dump_dir:
                self.dump_dir.mkdir(parents=True, exist_ok=True)
                (self.dump_dir / f"oddsapi_{sport}.json").write_text(json.dumps(data), encoding="utf-8")
            for raw in data:
                ev = self._parse(raw, fetched_at)
                if ev:
                    events.append(ev)

        self._cache = events
        self._last = time.time()
        live = sum(1 for e in events if e.live)
        quota = f", créditos restantes {self.remaining}" if self.remaining is not None else ""
        self.status = f"{len(events)} partidos ({live} en vivo){quota}"
        return events

    def _read_quota(self, r: httpx.Response) -> None:
        try:
            self.remaining = int(float(r.headers.get("x-requests-remaining", "")))
            self.used = int(float(r.headers.get("x-requests-used", "")))
        except ValueError:
            pass

    def _parse(self, raw: dict, fetched_at: float) -> SourceEvent | None:
        home, away = raw.get("home_team"), raw.get("away_team")
        if not home or not away:
            return None
        start = parse_iso(raw.get("commence_time"))
        books = raw.get("bookmakers") or []
        h2h_by_book: dict[str, list[dict]] = {}
        updated_at: dict[str, float] = {}
        for b in books:
            for m in b.get("markets") or []:
                if m.get("key") == "h2h":
                    key = b.get("key", "?")
                    h2h_by_book[key] = m.get("outcomes") or []
                    # Cuándo cambió/confirmó la casa esta cuota. Clave en vivo: una cuota
                    # de antes de un gol parece un arbitraje enorme y no lo es.
                    lu = parse_iso(m.get("last_update") or b.get("last_update"))
                    updated_at[key] = min(lu.timestamp(), fetched_at) if lu else fetched_at
        three_way = any(o.get("name") == "Draw" for outs in h2h_by_book.values() for o in outs)
        ev = SourceEvent(
            feed=self.name, event_id=str(raw.get("id")), sport=oddsapi_family(raw.get("sport_key", "")),
            home=home, away=away, start=start, three_way=three_way,
            live=bool(start and start <= datetime.now(timezone.utc)), league=raw.get("sport_title", ""),
        )
        side = {home: HOME, away: AWAY, "Draw": DRAW}
        for book, outs in h2h_by_book.items():
            if three_way and len(outs) != 3:
                continue  # una casa con 2 vías en un partido con empate no es comparable
            commission = self.commissions.get(book, 0.0)
            for o in outs:
                res = side.get(o.get("name"))
                price = float(o.get("price") or 0)
                if not res or price <= 1.0:
                    continue
                ev.quotes.append(Quote(
                    source=book, feed=self.name, covers=frozenset({res}),
                    odds=odds_after_commission(price, commission) if commission else price,
                    label=o.get("name"), ts=updated_at[book],
                ))
        return ev if ev.quotes else None
