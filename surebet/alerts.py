"""Alertas: pitido en Windows y mensaje de Telegram (si hay token en .env)."""

from __future__ import annotations

import time

import httpx

from .arb import POLYMARKET, ValueBet, size_legs
from .models import Opportunity, market_label
from .pricing import fill_detail, poly_cost_per_share

BOOK_NAMES = {"1xbet": "1xBet", "onexbet": "1xBet", "polymarket": "Polymarket", "sxbet": "SX Bet",
              "stake": "Stake", "rollbit": "Rollbit", "cloudbet": "Cloudbet",
              "rushbet.co": "Rushbet (CO)", "betplay": "BetPlay (CO)"}
EXCHANGES = {POLYMARKET, "sxbet"}   # libro visible: se apuestan DESPUÉS de la casa
SX_FEE = 0.01


def gross_and_net(q, stake: float, net: float) -> tuple[float, float]:
    """(cuota que muestra la casa, cuota real descontando su comisión) al monto apostado."""
    if q.source == POLYMARKET and q.asks:
        fd = fill_detail(q.asks, stake, q.fee_rate, q.fee_exponent)
        return (1 / fd["avg_price"] if fd["avg_price"] else net), net
    if q.source == "sxbet":
        return 1 + (net - 1) / (1 - SX_FEE), net
    return net, net


def _min_odds(legs, i: int) -> float:
    """Cuota mínima de la pata i para que la surebet siga existiendo con las otras fijas."""
    rest = sum(1 / l.odds for j, l in enumerate(legs) if j != i)
    return 1 / (1 - rest) if rest < 1 else float("inf")


def _max_poly_price(min_odds: float, fee_rate: float, exponent: float) -> float:
    """Precio máximo por share en Polymarket (con su comisión) que todavía da esa cuota."""
    target, lo, hi = 1 / min_odds, 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if poly_cost_per_share(mid, fee_rate, exponent) <= target else (lo, mid)
    return lo


def book_of_the_day(books: list[str]) -> str | None:
    """Una casa por día de la lista (clones de 1xBet con las mismas cuotas)."""
    return books[time.localtime().tm_yday % len(books)] if books else None


def format_ready(o: Opportunity, stake: float, rotate_books: list[str] | None = None) -> str:
    """Surebet lista para apostar a mano: montos, orden, links y hasta qué cuota/precio sigue valiendo."""
    ev = o.event.anchor
    target = min(stake, o.max_total_stake) if o.max_total_stake else stake
    legs = size_legs([l.quote for l in o.legs], round(target, 2)) or o.legs
    total = sum(l.stake for l in legs)
    win = min(l.stake * l.odds for l in legs)
    when = ""
    if ev.start:
        mins = (ev.start.timestamp() - time.time()) / 60
        hhmm = time.strftime("%d/%m %H:%M", time.localtime(ev.start.timestamp()))
        when = f"empieza {hhmm}" + (f" (en {int(mins // 60)} h {int(mins % 60)} min)" if mins > 0 else "")
    head = "🔴 EN VIVO (la casa puede rechazar)" if o.event.live else "✅ SUREBET prepartido"
    lines = [f"{head} {100 * (win / total - 1):+.2f}% neto",
             f"{ev.title} [{ev.sport}] · {market_label(o.market)}",
             f"{when}".strip(),
             f"Pones ${total:,.2f} → cobras mínimo ${win:,.2f} salga lo que salga ({win - total:+,.2f})", ""]
    order = sorted(range(len(legs)), key=lambda i: (legs[i].quote.source in EXCHANGES, legs[i].quote.source == POLYMARKET))
    for n, i in enumerate(order, 1):
        l, q = legs[i], legs[i].quote
        name = BOOK_NAMES.get(q.source, q.source)
        today = book_of_the_day(rotate_books or [])
        if today and q.source in ("1xbet", "onexbet"):
            name = f"{today} (casa de hoy; mismas cuotas que 1xBet, confírmala)"
        min_odds = _min_odds(legs, i)
        if q.source == POLYMARKET and q.asks:
            fd = fill_detail(q.asks, l.stake, q.fee_rate, q.fee_exponent)
            cap = _max_poly_price(min_odds, q.fee_rate, q.fee_exponent)
            gross, net = gross_and_net(q, l.stake, l.odds)
            lines.append(f"{n}. {name}: {q.label} @ {gross:.3f} (con comisión: {net:.3f}) → compra ${l.stake:,.2f}"
                         f" (~{fd['shares']:,.0f} shares a {100 * fd['avg_price']:.1f}¢)")
            lines.append(f"   precio máximo {100 * cap:.1f}¢ (si está más caro, ya no es surebet)")
        else:
            shown, limit = l.odds, min_odds
            if q.source == "sxbet":  # SX muestra la cuota bruta; la comisión (1 % de la ganancia) va aparte
                shown, limit = 1 + (l.odds - 1) / (1 - SX_FEE), 1 + (min_odds - 1) / (1 - SX_FEE)
            fee_txt = f" (con comisión: {l.odds:.3f})" if q.source == "sxbet" else ""
            lines.append(f"{n}. {name}: {q.label} @ {shown:.3f}{fee_txt} → apuesta ${l.stake:,.2f}")
            lines.append(f"   cuota mínima {limit:.3f} (si baja de ahí, ya no es surebet)")
        if q.url:
            lines.append(f"   {q.url}")
    lines += ["", "Orden: primero la casa (puede rechazar o bajar el monto); cuando la acepte, el exchange."]
    lines += [f"⚠ {w}" for w in o.warnings]
    return "\n".join(x for x in lines if x is not None)


def format_opportunity(o: Opportunity) -> str:
    ev = o.event.anchor
    live = f"🔴 EN VIVO {ev.score} " if o.event.live else ""
    lines = [
        f"{live}{ev.title} [{ev.sport}]",
        f"Margen {o.margin_pct:.2f}% | ${o.total_stake:,.0f} -> ganancia ${o.profit:,.2f} | máx ${o.max_total_stake:,.0f}",
    ]
    for l in o.legs:
        lines.append(f"  • {l.quote.source}: {l.quote.label} @ {l.odds:.3f} -> apostar ${l.stake:,.2f}")
        if l.quote.url:
            lines.append(f"    {l.quote.url}")
    for w in o.warnings:
        lines.append(f"  ⚠ {w}")
    return "\n".join(lines)


def format_value_bet(v: ValueBet, bankroll: float, kelly_fraction: float, stake: float | None = None) -> str:
    ev = v.event.anchor
    if stake is None:
        stake = min(bankroll * v.kelly * kelly_fraction, bankroll * 0.03)
    lines = [
        f"VALOR {v.ev_pct:+.1f}% | {ev.title} [{ev.sport}]",
        f"  {v.quote.source}: {v.quote.label} @ {v.quote.odds:.2f} (justa {1 / v.fair_prob:.2f} según Polymarket)",
        f"  monto sugerido ${stake:,.2f} en {v.quote.source} (Kelly x{kelly_fraction:g} con mínimo, banca ${bankroll:,.0f})",
    ]
    if v.quote.url:
        lines.append(f"  {v.quote.url}")
    from .makersim import VALUE_LOCK_PCT, value_hedge
    h = value_hedge(v, stake) if stake > 0 else None
    if h:
        lines += ["",
                  f"🛟 Red de seguridad (opcional, en Polymarket): orden LÍMITE de compra de {h['shares']:,.0f} shares de "
                  f"«{h['label']}» a {100 * h['limit']:.0f}¢ o menos. Déjala esperando.",
                  f"   Si se llena, ganas +{VALUE_LOCK_PCT:g}% pase lo que pase (sin comisión: es orden límite).",
                  "   Si no se llena antes del partido, cancélala y te quedas con la apuesta de valor.",
                  "   O toca «🛟 Ya aposté» y el bot la pone, la vigila y la cancela por ti."]
    lines.append("  No es apuesta segura: verifica la cuota en la casa antes de apostar.")
    return "\n".join(lines)


def format_startup(sources, s) -> str:
    lines = ["▶️ surebet3 iniciado"]
    lines += [f"  • {src.name}: {src.status}" for src in sources]
    lines.append(f"  Alertas: arbitraje ≥ {s.alert_min_margin_pct}% | valor ≥ {s.value_alert_min_ev_pct}%")
    return "\n".join(lines)


def format_round_summary(res, papi, s) -> str:
    """Resumen tras cada ronda de cuotas de las casas (aunque no haya oportunidades)."""
    good = [v for v in res.values if v.ev_pct >= s.value_min_ev_pct]
    lines = [
        f"📊 Ronda de cuotas ({', '.join(papi.bookmakers)})",
        f"Partidos emparejados con Polymarket: {res.n_matched}",
        f"Arbitrajes ≥ {s.min_margin_pct}%: {len(res.opps)}",
    ]
    if res.radar:
        b = res.radar[0]
        legs = " + ".join(f"{l.quote.source} {l.quote.label} @{l.odds:.2f}" for l in b.legs)
        lines.append(f"Mejor combinación: {b.margin_pct:+.2f}% — {b.event.anchor.title} ({legs})")
    lines.append(f"Valor ≥ {s.value_min_ev_pct}%: {len(good)} | registradas para CLV: {len(res.values)}")
    for v in good[:3]:
        lines.append(f"  • {v.ev_pct:+.1f}% {v.quote.source}: {v.quote.label} @{v.quote.odds:.2f} — {v.event.anchor.title}")
    lines.append(f"Peticiones OddsPapi: {papi.used}/{papi.limit}")
    return "\n".join(lines)


def beep() -> None:
    try:
        import winsound
        winsound.Beep(1800, 250)
        winsound.Beep(2400, 250)
    except Exception:
        pass


def leg_buttons(o: Opportunity, rotate_books: list[str] | None = None) -> list[tuple[str, str]]:
    """Botones de Telegram que abren cada casa en el partido (primero la casa, después el exchange)."""
    out = []
    for l in sorted(o.legs, key=lambda l: l.quote.source in EXCHANGES):
        if l.quote.url.startswith("http"):
            name = BOOK_NAMES.get(l.quote.source, l.quote.source)
            if l.quote.source in ("1xbet", "onexbet") and rotate_books:
                name = book_of_the_day(rotate_books)
            out.append((f"Abrir {name}", l.quote.url))
    return out


async def send_telegram(client: httpx.AsyncClient, token: str, chat_id: str, text: str,
                        buttons: list[tuple[str, str]] | None = None) -> None:
    if not token or not chat_id:
        return
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if buttons:
        # "cb:<dato>" = botón que le habla al bot (callback); lo demás abre un link
        payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": u[3:]} if u.startswith("cb:")
                                                        else {"text": t, "url": u}] for t, u in buttons]}
    try:
        await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
    except httpx.HTTPError:
        pass
