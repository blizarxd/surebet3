"""Tablas de terminal con rich."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .arb import ValueBet
from .models import Opportunity, market_label


def _legs(o: Opportunity) -> str:
    return "\n".join(
        f"{l.quote.source}: {l.quote.label} @ {l.odds:.3f} -> ${l.stake:,.2f}" for l in o.legs
    )


def _event(o: Opportunity) -> Text:
    ev = o.event.anchor
    t = Text()
    if o.event.live:
        t.append("● VIVO ", style="bold red")
        if ev.score:
            t.append(f"{ev.score} ", style="red")
    t.append(ev.title)
    if ev.league:
        t.append(f"\n{ev.league}", style="dim")
    return t


def opportunities_table(opps: list[Opportunity], title: str, stake: float) -> Table:
    t = Table(title=title, expand=True, show_lines=True, title_justify="left")
    t.add_column("Margen", justify="right")
    t.add_column(f"Gan. ${stake:,.0f}", justify="right")
    t.add_column("Máx $", justify="right")
    t.add_column("Dep.")
    t.add_column("Partido", ratio=2)
    t.add_column("Apuestas", ratio=3)
    t.add_column("Edad", justify="right")
    t.add_column("Avisos", ratio=2, style="yellow")
    for o in opps:
        color = "bold green" if o.margin_pct > 0 else "dim"
        t.add_row(
            Text(f"{o.margin_pct:+.2f}%", style=color),
            f"${o.profit:,.2f}",
            f"${o.max_total_stake:,.0f}" if o.max_total_stake else "-",
            f"{o.event.anchor.sport}\n{market_label(o.market)}",
            _event(o),
            _legs(o),
            f"{o.oldest_quote_age:.0f}s",
            "\n".join(o.warnings),
        )
    return t


def _starts_in(o_event) -> str:
    st = o_event.anchor.start
    if not st:
        return "-"
    h = (st - datetime.now(timezone.utc)).total_seconds() / 3600
    return "en vivo" if h < 0 else (f"{h * 60:.0f} min" if h < 1 else f"{h:.1f} h")


def value_table(values: list[ValueBet], bankroll: float, kelly_fraction: float) -> Table:
    t = Table(title="APUESTAS DE VALOR (no son seguras: ventaja esperada a largo plazo; valida con clv.py)",
              expand=True, show_lines=False, title_justify="left")
    t.add_column("EV", justify="right")
    t.add_column("Casa")
    t.add_column("Apuesta", ratio=2)
    t.add_column("Cuota", justify="right")
    t.add_column("Justa", justify="right")
    t.add_column(f"Monto (Kelly x{kelly_fraction:g})", justify="right")
    t.add_column("Partido", ratio=2)
    t.add_column("Empieza", justify="right")
    for v in values:
        stake = min(bankroll * v.kelly * kelly_fraction, bankroll * 0.03)
        t.add_row(
            Text(f"{v.ev_pct:+.1f}%", style="bold green"),
            v.quote.source, v.quote.label, f"{v.quote.odds:.2f}", f"{1 / v.fair_prob:.2f}",
            f"${stake:,.2f}", f"{v.event.anchor.title}\n{market_label(v.market)}", _starts_in(v.event),
        )
    return t


def status_panel(sources, n_clusters: int, n_matched: int, scan_s: float) -> Panel:
    lines = [f"[bold]{s.name}[/]: {s.status}" for s in sources]
    lines.append(f"emparejados en 2+ fuentes: [bold]{n_matched}[/] de {n_clusters} partidos  |  "
                 f"ciclo {scan_s:.1f}s  |  {datetime.now():%H:%M:%S}")
    return Panel("\n".join(lines), title="surebet3", border_style="cyan")


def render(sources, res, scan_s: float, s) -> Group:
    parts = [status_panel(sources, res.n_clusters, res.n_matched, scan_s)]
    if res.opps:
        parts.append(opportunities_table(res.opps, f"ARBITRAJES (margen >= {s.min_margin_pct}%)", s.stake))
    else:
        parts.append(Text(f"Sin arbitrajes >= {s.min_margin_pct}% en este ciclo.", style="dim"))
    shown = [v for v in res.values if v.ev_pct >= s.value_min_ev_pct]
    if shown:
        parts.append(value_table(shown[:25], s.bankroll, s.kelly_fraction))
    if res.radar:
        parts.append(opportunities_table(res.radar, "RADAR: mejor combinación por partido emparejado "
                                                    "(diagnóstico, puede incluir cuotas viejas)", s.stake))
    return Group(*parts)
