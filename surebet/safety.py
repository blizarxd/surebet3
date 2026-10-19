"""
Filtros que evitan que una surebet "segura" termine en pérdida (ver el informe de investigación).

Devuelven motivos de BLOQUEO (empiezan con "⛔"): esas oportunidades no se avisan ni entran en la
cuenta real; se siguen anotando en medición para saber cuánto cuestan los filtros.

  - Tenis: si el rival se retira en el 1er set, Polymarket paga al que avanza y la casa anula.
    Un retiro borra ~27 surebets: se exige un margen mayor (TENNIS_MIN_MARGIN_PCT).
  - Cuota sospechosa de error: si la casa paga mucho más que el precio justo de Polymarket, lo
    probable es un error; 1xBet no lo anula, lo liquida a la cuota "correcta" (pérdida segura).
  - Hora de inicio distinta entre fuentes: la casa anula lo apostado después del inicio real.
"""

from __future__ import annotations

from .arb import POLYMARKET, fair_probs
from .models import Opportunity, market_outcomes

EXCHANGES = {POLYMARKET, "sxbet", "kalshi"}
# Kalshi liquida por la FECHA del partido ("originally scheduled for Oct 12"), no por la hora: su hora es
# aproximada (en tenis, hasta 2 h) y no sirve para comparar ni hay riesgo de anulación por eso
DATE_SETTLED = {"kalshi"}
TENNIS_MIN_MARGIN_PCT = 2.0
PALP_EV_PCT = 12.0          # la casa paga >12 % por encima del precio justo: sospechoso de error
MAX_START_DIFF_MIN = 15.0


def risk_flags(o: Opportunity, tennis_min_margin: float = TENNIS_MIN_MARGIN_PCT,
               palp_ev_pct: float = PALP_EV_PCT, max_start_diff_min: float = MAX_START_DIFF_MIN) -> list[str]:
    flags: list[str] = []
    ev = o.event
    if ev.anchor.sport.startswith("tennis") and o.margin_pct < tennis_min_margin:
        flags.append(f"⛔ tenis con margen < {tennis_min_margin:g} % (riesgo de retiro en el 1er set)")

    outcomes = market_outcomes(o.market)
    fp = fair_probs(ev.quotes_for(o.market), outcomes)
    if fp:
        probs = fp[0]
        for l in o.legs:
            if l.quote.source in EXCHANGES:
                continue
            ev_pct = 100 * (l.quote.odds * sum(probs[x] for x in l.quote.covers) - 1)
            if ev_pct > palp_ev_pct:
                flags.append(f"⛔ {l.quote.source} paga {ev_pct:.0f} % sobre el precio justo: posible cuota errónea")

    feeds = {l.quote.feed for l in o.legs} - DATE_SETTLED
    starts = [m.start for m, _ in ev.members if m.feed in feeds and m.start]
    if len(starts) >= 2:
        diff = (max(starts) - min(starts)).total_seconds() / 60
        if diff > max_start_diff_min:
            flags.append(f"⛔ hora de inicio distinta entre casas ({diff:.0f} min): la casa puede anular")
    return flags


# Valor "filtrado": lo que el CLV dice que funciona (1er día de datos: EV 2-4 % al detectar -> CLV
# mediano +3 % y 92 % positivo; en vivo el CLV mediano es negativo; cuotas muy altas o muy bajas, peor)
GOLD_EV = (2.0, 7.0)
GOLD_ODDS = (1.6, 4.0)
GOLD_MAX_HOURS = 6.0


def golden_value(live: bool, ev_pct: float, odds: float, hours_to_start: float | None) -> bool:
    return (not live and hours_to_start is not None and 0 < hours_to_start <= GOLD_MAX_HOURS
            and GOLD_EV[0] <= ev_pct < GOLD_EV[1] and GOLD_ODDS[0] <= odds < GOLD_ODDS[1])


# Valor "amplio": como el oro pero con EV 1,5–2 % (19/10: el CLV de esa franja es positivo, +1,7 a +2,7 %). Solo se
# MIDE en la sombra del valor automático; el bot real y los avisos siguen solo con oro hasta que los datos lo confirmen.
WIDE_EV = (1.5, 2.0)


def wide_value(live: bool, ev_pct: float, odds: float, hours_to_start: float | None) -> bool:
    return (not live and hours_to_start is not None and 0 < hours_to_start <= GOLD_MAX_HOURS
            and WIDE_EV[0] <= ev_pct < WIDE_EV[1] and GOLD_ODDS[0] <= odds < GOLD_ODDS[1])


# Azuro automático solo a ≤ 3 h del inicio (19/10: CLV de Azuro 0–1 h +3,0 %, 1–3 h +2,2 %, 3–6 h ~0 % con 33 señales).
# Una señal más lejana se espera: se prueba en Azuro cuando el partido entra a 3 h si sigue siendo valor.
AZURO_MAX_HOURS = 3.0


def blocked(o: Opportunity) -> bool:
    return any(w.startswith("⛔") for w in o.warnings)


# Esports con cuotas viejas en 1xBet que después se anulan (18/10: CS2 30 % anuladas en el automático, Dota2 17 % en
# la real): no entran al automático ni a la cuenta semi-manual (van a medición) y el aviso de Telegram lo advierte
OBSERVE_SPORTS = ("esports:cs2", "esports:dota2")


def observe_only(event, sports=OBSERVE_SPORTS) -> bool:
    return bool(sports) and any(m.sport in sports for m, _ in event.members)
