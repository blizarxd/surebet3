"""
Detección y dimensionamiento de arbitrajes.

Para un partido con resultados {H, A} (2 vías) o {H, D, A} (1X2) se prueban todas
las particiones de esos resultados en bloques. Cada bloque necesita una apuesta que
cubra EXACTAMENTE esos resultados. Así un arbitraje no puede dejar un resultado sin
cubrir (el bug del escáner viejo: en fútbol ignoraba el empate).

    1X2:  {H}{D}{A}   {H}{D,A}   {D}{H,A}   {A}{H,D}
    2 vías: {H}{A}
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from .models import Leg, MatchedEvent, Opportunity, Quote, market_outcomes
from .pricing import effective_odds

POLYMARKET = "polymarket"

# Diferencias de reglamento que pueden romper un arbitraje aunque los números cuadren
SPORT_RULE_WARNINGS = {
    "tennis": "tenis: si un jugador abandona, cada casa liquida distinto",
    "americanfootball": "NFL/NCAA: revisa cómo liquida cada casa un empate",
    "cricket": "cricket: revisa reglas de partido suspendido/empate",
    "mma": "MMA: revisa reglas de empate/no-contest",
    "boxing": "boxeo: revisa reglas de empate",
}


@dataclass
class ArbConfig:
    stake: float = 100.0              # monto total de referencia a repartir entre las patas
    min_margin_pct: float = 0.5       # margen mínimo para considerarlo oportunidad
    max_bankroll: float = 5000.0      # tope para buscar el monto máximo
    max_age_live_s: float = 15.0      # cuotas en vivo más viejas que esto se descartan
    max_age_prematch_s: float = 600.0
    suspicious_margin_pct: float = 8.0
    slow_feeds: frozenset[str] = frozenset()  # fuentes que no son tiempo real
    outlier_pp: float = 0.08          # pata que paga > 8 pp sobre el consenso = descartada
    outlier_warn_pp: float = 0.03


def partitions(outcomes: frozenset[str]) -> list[list[frozenset[str]]]:
    """Particiones de `outcomes` con al menos 2 bloques."""
    def rec(items: list[str]):
        if not items:
            yield []
            return
        first, rest = items[0], items[1:]
        for p in rec(rest):
            for i in range(len(p)):
                yield p[:i] + [p[i] | {first}] + p[i + 1:]
            yield [frozenset({first})] + p

    return [p for p in rec(sorted(outcomes)) if len(p) >= 2]


_PARTITIONS: dict[frozenset[str], list[list[frozenset[str]]]] = {}


def _partitions_for(outcomes: frozenset[str]) -> list[list[frozenset[str]]]:
    if outcomes not in _PARTITIONS:
        _PARTITIONS[outcomes] = partitions(outcomes)
    return _PARTITIONS[outcomes]


def size_legs(quotes: list[Quote], total: float) -> list[Leg] | None:
    """
    Reparte `total` para que todas las patas paguen lo mismo, usando la cuota
    efectiva de cada pata para su monto (punto fijo: en Polymarket la cuota baja
    a medida que el monto recorre el libro). None si alguna pata no tiene liquidez.
    """
    odds = [q.odds for q in quotes]
    stakes: list[float] = []
    for _ in range(12):
        inv = [1.0 / o for o in odds]
        s = sum(inv)
        stakes = [total * i / s for i in inv]
        new = [effective_odds(q, st) for q, st in zip(quotes, stakes)]
        if any(o is None for o in new):
            return None
        converged = max(abs(a - b) for a, b in zip(new, odds)) < 1e-9
        odds = new  # type: ignore[assignment]
        if converged:
            break
    return [Leg(q, st, o) for q, st, o in zip(quotes, stakes, odds)]


def _profit(legs: list[Leg]) -> float:
    return min(l.payout for l in legs) - sum(l.stake for l in legs)


def max_profitable_stake(quotes: list[Quote], min_margin_pct: float, cap: float) -> float:
    """Mayor monto total que todavía rinde >= min_margin_pct (búsqueda binaria)."""
    def ok(total: float) -> bool:
        legs = size_legs(quotes, total)
        return legs is not None and 100 * _profit(legs) / total >= min_margin_pct

    # Monto mínimo para que cada pata alcance su orden mínima (Polymarket: 5 shares).
    # Por encima de ese piso el margen solo empeora al crecer, así que se puede bisecar.
    inv = sum(1.0 / q.odds for q in quotes)
    lo = max([1.0] + [q.min_stake * q.odds * inv * 1.02 for q in quotes])
    hi = cap
    if lo > hi or not ok(lo):
        return 0.0
    if ok(hi):
        return hi
    for _ in range(30):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _usable(q: Quote, outcomes: frozenset[str], max_age: float, now: float) -> bool:
    return q.odds > 1.0 and bool(q.covers) and q.covers < outcomes and q.age(now) <= max_age


def find_opportunities(event: MatchedEvent, cfg: ArbConfig, floor_pct: float | None = None,
                       strict: bool = True) -> list[Opportunity]:
    """
    Devuelve las combinaciones con margen >= floor_pct (por defecto cfg.min_margin_pct).
    Con un floor negativo y strict=False sirve de "radar": muestra qué tan cerca está
    cada partido aunque alguna cuota sea vieja o atípica (solo diagnóstico).
    """
    floor = cfg.min_margin_pct if floor_pct is None else floor_pct
    now = time.time()
    if strict:
        max_age = cfg.max_age_live_s if event.live else cfg.max_age_prematch_s
    else:
        max_age = float("inf")
    found: list[Opportunity] = []

    for market in sorted(event.markets()):
        outcomes = market_outcomes(market)
        quotes = [q for q in event.quotes_for(market) if _usable(q, outcomes, max_age, now)]
        if not quotes:
            continue
        cons, n_cons = consensus(quotes, outcomes)
        by_block: dict[frozenset[str], list[Quote]] = {}
        for q in quotes:
            by_block.setdefault(q.covers, []).append(q)
        for qs in by_block.values():
            qs.sort(key=lambda q: q.odds, reverse=True)

        for part in _partitions_for(outcomes):
            if any(b not in by_block for b in part):
                continue
            legs_q = [by_block[b][0] for b in part]
            sources = {q.source for q in legs_q}
            # Arbitraje dentro de una sola casa tradicional = dato roto, no oportunidad
            if len(sources) == 1 and POLYMARKET not in sources:
                continue
            inv = sum(1.0 / q.odds for q in legs_q)
            margin = 100 * (1.0 / inv - 1.0)
            if margin < floor:
                continue
            excess: dict[str, float] = {}
            for q in legs_q if cons else ():
                excess[q.source] = max(excess.get(q.source, -1.0), generosity(q, cons))
            if strict and excess and max(excess.values()) > cfg.outlier_pp:
                continue  # una pata paga muy por encima del resto: vieja, suspendida o error
            opp = _build(event, market, legs_q, margin, cfg)
            for src, ex in excess.items():
                if ex > cfg.outlier_warn_pp:
                    opp.warnings.append(f"{src} paga {100 * ex:.0f} pp más que el consenso de "
                                        f"{n_cons} casas: ¿cuota vieja? las casas anulan errores")
            found.append(opp)

    found.sort(key=lambda o: o.margin_pct, reverse=True)
    return found


def consensus(quotes: list[Quote], outcomes: frozenset[str]) -> tuple[dict[str, float], int]:
    """
    Probabilidad "justa" de cada resultado: mediana, entre las fuentes que cotizan todos
    los resultados, de sus probabilidades implícitas sin margen. Requiere >= 3 fuentes.
    """
    best: dict[str, dict[str, float]] = {}
    for q in quotes:
        if len(q.covers) == 1:
            (o,) = q.covers
            src = best.setdefault(q.source, {})
            src[o] = max(src.get(o, 0.0), q.odds)
    per_outcome: dict[str, list[float]] = {o: [] for o in outcomes}
    n = 0
    for odds in best.values():
        if set(odds) != outcomes:
            continue
        total = sum(1.0 / x for x in odds.values())
        for o, x in odds.items():
            per_outcome[o].append((1.0 / x) / total)
        n += 1
    if n < 3:
        return {}, n
    return {o: statistics.median(v) for o, v in per_outcome.items()}, n


def generosity(q: Quote, cons: dict[str, float]) -> float:
    """Cuánto más paga esta cuota que el consenso, en puntos de probabilidad."""
    return sum(cons[o] for o in q.covers) - 1.0 / q.odds


def _build(event: MatchedEvent, market: str, quotes: list[Quote], margin: float,
           cfg: ArbConfig) -> Opportunity:
    max_total = 0.0
    if margin >= cfg.min_margin_pct:
        max_total = max_profitable_stake(quotes, cfg.min_margin_pct, cfg.max_bankroll)
    total = min(cfg.stake, max_total) if max_total >= 1.0 else cfg.stake
    legs = size_legs(quotes, total)
    if legs is None:
        # Sin liquidez para el monto de referencia: mostrar al mejor precio
        inv = sum(1.0 / q.odds for q in quotes)
        legs = [Leg(q, total * (1.0 / q.odds) / inv, q.odds) for q in quotes]
    opp = Opportunity(
        event=event, market=market, legs=legs, margin_pct=margin,
        total_stake=total, profit=_profit(legs), max_total_stake=max_total,
    )
    opp.warnings = _warnings(opp, cfg)
    return opp


def _warnings(opp: Opportunity, cfg: ArbConfig) -> list[str]:
    w: list[str] = []
    sport = opp.event.anchor.sport
    if opp.margin_pct >= cfg.suspicious_margin_pct:
        w.append("margen sospechoso: confirma que sea el mismo partido y mercado")
    if sport in SPORT_RULE_WARNINGS:
        w.append(SPORT_RULE_WARNINGS[sport])
    delays = [l.quote.delay_s for l in opp.legs if l.quote.delay_s > 0]
    if opp.event.live and delays:
        w.append(f"Polymarket ejecuta con {max(delays):.0f}s de retraso en vivo")
    if opp.event.live and any(l.quote.feed in cfg.slow_feeds for l in opp.legs):
        w.append("cuota de casa vía API con retraso: verifícala antes de apostar")
    if opp.max_total_stake and opp.max_total_stake < cfg.stake:
        w.append(f"liquidez limita el total a ${opp.max_total_stake:,.0f}")
    if any(l.odds > 15 for l in opp.legs):
        w.append("cuota muy alta en una pata: alto riesgo si no entra la otra")
    return w


# ---------------------------------------------------------------------------
# Apuestas de valor: la casa paga más que la probabilidad "justa" de Polymarket.
# No es ganancia segura por apuesta; es ventaja esperada a largo plazo (se valida con CLV).
# ---------------------------------------------------------------------------

@dataclass
class ValueBet:
    event: MatchedEvent
    quote: Quote
    fair_prob: float            # probabilidad justa (Polymarket, sin margen) de lo que cubre la apuesta
    poly_tokens: dict[str, str]  # resultado -> token de Polymarket que paga si pasa (CLV y liquidación)
    market: str = "1x2"
    poly_conditions: dict[str, str] = field(default_factory=dict)  # resultado -> condition_id

    @property
    def ev_pct(self) -> float:
        return 100 * (self.quote.odds * self.fair_prob - 1)

    @property
    def kelly(self) -> float:
        """Fracción de Kelly completa: (cuota·p − 1) / (cuota − 1)."""
        return max(0.0, (self.quote.odds * self.fair_prob - 1) / (self.quote.odds - 1))

    @property
    def key(self) -> str:
        return f"{self.event.key}#{self.market}#{self.quote.source}:{''.join(sorted(self.quote.covers))}"


def fair_probs(quotes: list[Quote], outcomes: frozenset[str],
               min_depth: float = 0.0) -> tuple[dict[str, float], dict[str, str], dict[str, str]] | None:
    """
    Probabilidades justas desde los precios medios de los tokens SÍ de Polymarket.
    Solo si el mercado tiene liquidez real cerca del precio: en mercados finitos (esports
    menores, ITF) el precio de Polymarket no es más sabio que el de la casa.
    """
    probs: dict[str, float] = {}
    tokens: dict[str, str] = {}
    conds: dict[str, str] = {}
    for q in quotes:
        if q.source == POLYMARKET and len(q.covers) == 1 and q.fair_prob and q.near_depth >= min_depth:
            (o,) = q.covers
            probs[o], tokens[o], conds[o] = q.fair_prob, q.token_id, q.condition_id
    if set(probs) != outcomes:
        return None
    total = sum(probs.values())
    if not 0.9 < total < 1.1:  # libros incoherentes: mejor no usar
        return None
    return {o: p / total for o, p in probs.items()}, tokens, conds


def find_value_bets(event: MatchedEvent, cfg: ArbConfig, min_ev_pct: float,
                    max_odds: float = 10.0, min_poly_depth: float = 0.0) -> list[ValueBet]:
    now = time.time()
    max_age = cfg.max_age_live_s if event.live else cfg.max_age_prematch_s
    out: list[ValueBet] = []
    for market in sorted(event.markets()):
        outcomes = market_outcomes(market)
        quotes = [q for q in event.quotes_for(market) if _usable(q, outcomes, max_age, now)]
        fp = fair_probs(quotes, outcomes, min_poly_depth)
        if fp is None:
            continue
        fair, tokens, conds = fp
        for q in quotes:
            # Con cuotas altas la probabilidad es chica y el precio de Polymarket (pasos de 1
            # centavo) ya no sirve de referencia: a 7¢ un centavo es ±14 %.
            if q.source == POLYMARKET or q.odds > max_odds:
                continue
            p = sum(fair[o] for o in q.covers)
            if p - 1.0 / q.odds > cfg.outlier_pp:
                continue  # demasiado bueno: cuota vieja o partido mal emparejado
            vb = ValueBet(event, q, p, tokens, market, conds)
            if vb.ev_pct >= min_ev_pct:
                out.append(vb)
    out.sort(key=lambda v: v.ev_pct, reverse=True)
    return out
