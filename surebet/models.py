"""
Estructuras de datos que circulan por todo el sistema.

Idea central: cada apuesta posible es un `Quote` que dice QUÉ resultados cubre
(`covers`) y a qué cuota decimal neta de comisiones. Un arbitraje es un conjunto
de quotes cuyos `covers` forman una partición exacta de los resultados posibles
del evento, con suma de probabilidades implícitas < 1.

Resultados: "H" (local), "D" (empate), "A" (visitante), "O" (más), "U" (menos).
  - Cuota a ganador local en una casa            -> covers {"H"}
  - Polymarket "Will Levante win?" YES           -> covers {"H"}
  - Polymarket "Will Levante win?" NO            -> covers {"D", "A"}
  - Doble oportunidad X2 en una casa             -> covers {"D", "A"}

Cada quote pertenece a un MERCADO, y solo se combinan quotes del mismo mercado:
  "1x2"        resultado a 90' (con empate)        "ml"      ganador (sin empate / con prórroga)
  "1x2_h1"     resultado del primer tiempo
  "ou_2.5"     más/menos 2.5 goles                 "ou_h1_1.5"  más/menos 1.5 en el primer tiempo
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

HOME, DRAW, AWAY = "H", "D", "A"
OVER, UNDER = "O", "U"
TWO_WAY = frozenset({HOME, AWAY})
THREE_WAY = frozenset({HOME, DRAW, AWAY})
OVER_UNDER = frozenset({OVER, UNDER})

_SWAP = {HOME: AWAY, AWAY: HOME, DRAW: DRAW, OVER: OVER, UNDER: UNDER}


def market_outcomes(market: str) -> frozenset[str]:
    if market.startswith("ou"):
        return OVER_UNDER
    if market.startswith("1x2"):
        return THREE_WAY
    return TWO_WAY


def market_label(market: str) -> str:
    """Nombre legible: '1x2' -> '1X2', 'ou_h1_1.5' -> 'Más/Menos 1T 1.5'."""
    if market.startswith("ou_h1_"):
        return f"Más/Menos 1T {market[6:]}"
    if market.startswith("ou_"):
        return f"Más/Menos {market[3:]}"
    return {"1x2": "1X2", "1x2_h1": "1X2 1er tiempo", "ml": "Ganador"}.get(market, market)


def default_market(three_way: bool) -> str:
    return "1x2" if three_way else "ml"


def swap_covers(covers: frozenset[str]) -> frozenset[str]:
    """Invierte local/visitante (cuando otra fuente lista el partido al revés)."""
    return frozenset(_SWAP[o] for o in covers)


@dataclass(frozen=True)
class BookLevel:
    """Un nivel del lado ask de un order book de Polymarket."""
    price: float  # USDC por share (0-1)
    size: float   # shares disponibles a ese precio


@dataclass
class Quote:
    source: str                    # casa concreta: "polymarket", "pinnacle", "onexbet"...
    covers: frozenset[str]         # resultados con los que esta apuesta gana
    odds: float                    # cuota decimal al mejor precio, YA neta de comisiones
    label: str                     # texto humano: "Levante UD (SI)", "Athletic Club"
    feed: str = ""                 # de qué fuente de datos vino: "polymarket", "oddsapi"...
    url: str = ""
    ts: float = field(default_factory=time.time)  # cuándo se observó este precio (epoch)
    max_stake: float | None = None  # dinero aceptado a esta cuota (None = desconocido)
    min_stake: float = 0.0
    # Solo Polymarket: escalera de asks para calcular la cuota real según el monto
    asks: tuple[BookLevel, ...] = ()
    fee_rate: float = 0.0
    fee_exponent: float = 1.0
    delay_s: float = 0.0            # retraso de matching en vivo (secondsDelay)
    token_id: str = ""
    condition_id: str = ""          # Polymarket: mercado on-chain (para saber el resultado y liquidar)
    market: str = ""                # "1x2", "ml", "1x2_h1", "ou_2.5"... ("" = el principal del evento)
    # Solo Polymarket: probabilidad "justa" = precio medio (bid+ask)/2 si el spread es estrecho
    fair_prob: float | None = None
    near_depth: float = 0.0  # USDC ofrecidos a <= 2 centavos del mejor precio (¿mercado con liquidez real?)
    bid: float | None = None  # Polymarket: mejor comprador (para simular órdenes límite)
    ref: str = ""            # para apostar por API: Cloudbet "evento|marketUrl", SX "marketHash|1 o 2"

    def age(self, now: float | None = None) -> float:
        return (now or time.time()) - self.ts

    def with_covers(self, covers: frozenset[str]) -> "Quote":
        from dataclasses import replace
        return replace(self, covers=covers)


@dataclass
class SourceEvent:
    """Un partido tal como lo ve UNA fuente de datos (Polymarket, The Odds API...)."""
    feed: str                  # "polymarket" | "oddsapi" | ...
    event_id: str
    sport: str                 # familia normalizada: soccer, basketball, tennis...
    home: str
    away: str
    start: datetime | None     # UTC, timezone-aware
    three_way: bool            # True si el mercado incluye empate (1X2)
    quotes: list[Quote] = field(default_factory=list)
    live: bool = False
    score: str = ""
    period: str = ""
    league: str = ""
    url: str = ""

    @property
    def title(self) -> str:
        return f"{self.home} vs {self.away}"


@dataclass
class MatchedEvent:
    """
    El mismo partido visto por varias fuentes, con todas las quotes ya orientadas
    al local/visitante del evento ancla (el primero que entró al cluster).
    """
    anchor: SourceEvent
    members: list[tuple[SourceEvent, bool]] = field(default_factory=list)  # (evento, invertido?)

    @property
    def key(self) -> str:
        return f"{self.anchor.feed}:{self.anchor.event_id}"

    @property
    def feeds(self) -> set[str]:
        return {ev.feed for ev, _ in self.members}

    @property
    def live(self) -> bool:
        return any(ev.live for ev, _ in self.members)

    def markets(self) -> set[str]:
        return {q.market or default_market(ev.three_way) for ev, _ in self.members for q in ev.quotes}

    def quotes_for(self, market: str) -> list[Quote]:
        """Quotes de ESE mercado en todas las fuentes, orientadas al local/visitante del ancla."""
        out: list[Quote] = []
        for ev, swapped in self.members:
            for q in ev.quotes:
                if (q.market or default_market(ev.three_way)) != market:
                    continue
                out.append(q.with_covers(swap_covers(q.covers)) if swapped else q)
        return out


@dataclass
class Leg:
    quote: Quote
    stake: float
    odds: float        # cuota efectiva para ESTE monto (Polymarket empeora al recorrer el libro)

    @property
    def payout(self) -> float:
        return self.stake * self.odds


@dataclass
class Opportunity:
    event: MatchedEvent
    market: str
    legs: list[Leg]
    margin_pct: float          # ganancia % al mejor precio (sin considerar profundidad)
    total_stake: float
    profit: float              # ganancia garantizada (peor caso) con total_stake
    max_total_stake: float     # monto máximo que mantiene el margen mínimo
    warnings: list[str] = field(default_factory=list)

    @property
    def roi_pct(self) -> float:
        return 100 * self.profit / self.total_stake if self.total_stake else 0.0

    @property
    def oldest_quote_age(self) -> float:
        now = time.time()
        return max(leg.quote.age(now) for leg in self.legs)

    @property
    def key(self) -> str:
        legs = "|".join(sorted(f"{l.quote.source}:{''.join(sorted(l.quote.covers))}" for l in self.legs))
        return f"{self.event.key}#{self.market}#{legs}"
