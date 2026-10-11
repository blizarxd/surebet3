"""
surebet3 — escáner de arbitraje y apuestas de valor: Polymarket vs casas tradicionales.

    python scan.py                  # escaneo continuo (vivo + próximas HORIZON_H horas)
    python scan.py --live           # solo partidos en vivo
    python scan.py --once           # un ciclo y salir
    python scan.py --radar          # además: qué tan cerca está cada partido emparejado
    python scan.py --oddspapi-now   # forzar ya una ronda de OddsPapi (gasta ~4 peticiones)
    python clv.py                   # ¿las señales de valor tienen ventaja real?
    python scan.py --headless       # servidor (VPS): sin tabla en pantalla, una línea de resumen cada 5 min
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import time
from contextlib import nullcontext
from dataclasses import dataclass, field, replace

import httpx
from rich.console import Console
from rich.live import Live

from clv import build_report as build_clv_report
from surebet.alerts import (beep, format_ready, leg_buttons, format_round_summary, format_startup,
                            format_value_bet, send_telegram)
from surebet.arb import POLYMARKET, ArbConfig, ValueBet, find_opportunities, find_value_bets
from surebet.config import Settings
from surebet.display import render
from surebet.matching import MatchConfig, match_events
from surebet.models import Opportunity
from surebet.sources.oddsapi import OddsApiSource
from surebet.sources.oddspapi import OddsPapiSource
from surebet.sources.onexbet import OneXBetSource
from surebet.sources.polymarket import PolymarketSource
from surebet.sources.sxbet import SXBetSource
from surebet.sources.cloudbet import CloudbetSource
from surebet.safety import blocked, golden_value, risk_flags
from surebet.dashboard_server import build_state
from surebet.dashboard_server import serve as serve_dashboard
from surebet.paper import PaperBook, PaperConfig
from surebet.execsim import ExecSimulator
from surebet.makersim import MakerSimulator
from surebet.status import write_status
from surebet.store import OpportunityLog

console = Console()


@dataclass
class CycleResult:
    opps: list[Opportunity] = field(default_factory=list)
    values: list[ValueBet] = field(default_factory=list)      # se registran (papel, para CLV)
    radar: list[Opportunity] = field(default_factory=list)
    n_clusters: int = 0
    n_matched: int = 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Escáner de surebets Polymarket vs casas")
    p.add_argument("--once", action="store_true", help="un solo ciclo")
    p.add_argument("--cycles", type=int, default=0, help="parar tras N ciclos (0 = infinito)")
    p.add_argument("--live", action="store_true", help="solo partidos en vivo")
    p.add_argument("--radar", action="store_true", help="mostrar mejor combinación aunque sea negativa")
    p.add_argument("--stake", type=float, help="monto total de referencia")
    p.add_argument("--min-margin", type=float, help="margen mínimo en %%")
    p.add_argument("--no-oddsapi", action="store_true", help="no usar The Odds API")
    p.add_argument("--no-oddspapi", action="store_true", help="no usar OddsPapi")
    p.add_argument("--no-onexbet", action="store_true", help="no usar el feed de 1xBet")
    p.add_argument("--no-sxbet", action="store_true", help="no usar SX Bet")
    p.add_argument("--quiet", action="store_true", help="sin Telegram ni pitidos (para pruebas)")
    p.add_argument("--no-paper", action="store_true", help="no operar en papel")
    p.add_argument("--no-dashboard", action="store_true", help="no levantar el panel web")
    p.add_argument("--oddspapi-now", action="store_true", help="forzar una ronda de OddsPapi ahora")
    p.add_argument("--headless", action="store_true",
                   help="sin tabla en pantalla (servidor/VPS): una línea de resumen cada 5 minutos")
    return p.parse_args()


def heartbeat(sources, res: "CycleResult", paper) -> str:
    """Una línea para el registro del servidor (journalctl)."""
    parts = [time.strftime("%Y-%m-%d %H:%M:%S"), f"emparejados {res.n_matched}",
             f"surebets {len(res.opps)}", f"valor {len(res.values)}"]
    if paper:
        b = paper.balances()
        parts.append(f"papel ${b['equity']:.2f} (en juego ${b['in_play']:.2f})")
    parts += [f"{src.name}: {src.status}" for src in sources]
    return " | ".join(parts)


_match_memo: dict = {"inputs": None, "clusters": []}


def match_cached(by_feed: dict, mcfg: MatchConfig) -> list:
    """Si ninguna fuente trajo partidos nuevos (mismos objetos), se reutiliza el emparejamiento anterior."""
    prev = _match_memo["inputs"]
    if prev is not None and list(prev) == list(by_feed) and all(
            len(prev[f]) == len(evs) and all(a is b for a, b in zip(prev[f], evs)) for f, evs in by_feed.items()):
        return _match_memo["clusters"]
    clusters = match_events(by_feed, mcfg)
    # Se guardan las listas (no solo ids): así los objetos no se liberan y la comparación es segura
    _match_memo.update(inputs={f: list(evs) for f, evs in by_feed.items()}, clusters=clusters)
    return clusters


def daily_summary(s: Settings, status_path) -> str:
    """El resumen del papel para Telegram: cuenta real y rendimiento por tipo de jugada."""
    st = build_state(s.db_path, status_path)
    a = st["account"]
    lines = ["📊 Resumen del día (papel)",
             f"Cuenta real: ${a['equity']:.2f} ({a['pnl']:+.2f}) · en juego ${a['in_play']:.2f} · "
             f"{a['won']} ganadas / {a['lost']} perdidas",
             "", "¿Dónde está la ventaja? (cerradas, $100 por jugada)"]
    for g in st["measure"]:
        closed = g["won"] + g["lost"] + g.get("void", 0)
        roi = f"{g['roi_pct']:+.1f}%" if g["roi_pct"] is not None else "-"
        lines.append(f"• {g['name']}: {closed} cerradas, {g['pnl']:+.2f} (ROI {roi}) · {g['open']} abiertas")
    if s.panel_url:
        lines += ["", f"Panel: {s.panel_url}"]
    return "\n".join(lines)


def _allowed(o: Opportunity, my_books: list[str]) -> bool:
    if not my_books:
        return True
    return all(l.quote.source == POLYMARKET or l.quote.source in my_books for l in o.legs)


async def scan_cycle(poly: PolymarketSource, others: list, client: httpx.AsyncClient, s: Settings,
                     cfg: ArbConfig, mcfg: MatchConfig, radar: bool) -> CycleResult:
    # Estas fuentes eligen qué pedir según los partidos de Polymarket
    targeted = (OddsPapiSource, OneXBetSource, SXBetSource, CloudbetSource)
    plain = [src for src in others if not isinstance(src, targeted)]
    papi = [src for src in others if isinstance(src, targeted)]
    results = await asyncio.gather(poly.events(client), *(src.fetch(client) for src in plain),
                                   return_exceptions=True)
    by_feed = {}
    for src, res in zip([poly, *plain], results):
        if isinstance(res, BaseException):
            src.status = f"error: {res!r}"[:160]
            by_feed[src.name] = []
        else:
            by_feed[src.name] = res
    # OddsPapi y 1xBet necesitan los partidos de Polymarket para elegir qué torneos/ligas pedir
    sx_src = next((x for x in papi if isinstance(x, SXBetSource)), None)
    for src in papi:
        targets = by_feed.get(poly.name, [])
        if isinstance(src, SXBetSource):
            targets = targets + by_feed.get("cloudbet", [])      # surebets Cloudbet + SX sin Polymarket
        elif isinstance(src, CloudbetSource) and sx_src:
            targets = targets + list(sx_src._bare_events().values())
        try:
            by_feed[src.name] = await src.fetch(client, targets=targets)
        except Exception as e:
            src.status = f"error: {e!r}"[:160]
            by_feed[src.name] = []

    clusters = match_cached(by_feed, mcfg)
    matched = [c for c in clusters if len(c.feeds) >= 2]
    wanted = {ev.event_id for c in matched for ev, _ in c.members if ev.feed == poly.name}
    try:
        await poly.fill_quotes(client, wanted)
    except httpx.HTTPError as e:
        poly.status = f"error libros: {e!r}"[:160]

    # Partidos solo de Polymarket no se evalúan (no se piden sus libros, ahorra peticiones);
    # sí los emparejados y los que tienen varias casas de otra fuente (casa vs casa).
    evaluable = matched + [c for c in clusters if len(c.feeds) == 1 and c.anchor.feed != poly.name]
    res = CycleResult(n_clusters=len(clusters), n_matched=len(matched))
    res.opps = [o for c in evaluable for o in find_opportunities(c, cfg) if _allowed(o, s.my_books)]
    for o in res.opps:
        o.warnings += risk_flags(o)  # ⛔ = no se avisa ni entra en la cuenta real (sí se mide)
    res.opps.sort(key=lambda o: o.margin_pct, reverse=True)
    # Se registran en papel desde VALUE_LOG_MIN_EV_PCT (más datos para medir CLV);
    # en pantalla y alertas solo desde VALUE_MIN_EV_PCT
    log_min = min(s.value_log_min_ev_pct, s.value_min_ev_pct)
    res.values = [v for c in matched for v in find_value_bets(c, cfg, log_min, s.value_max_odds, s.value_min_poly_depth)
                  if not s.my_books or v.quote.source in s.my_books]
    res.values.sort(key=lambda v: v.ev_pct, reverse=True)

    if True:  # radar: siempre se calcula (lo usa el panel); en consola solo se muestra con --radar
        for c in matched:
            best = find_opportunities(c, cfg, floor_pct=-100, strict=False)
            # Solo combinaciones Polymarket + otra casa (el objetivo del proyecto)
            best = [o for o in best if any(l.quote.source == POLYMARKET for l in o.legs)
                    and len({l.quote.source for l in o.legs}) >= 2 and _allowed(o, s.my_books)]
            if best:
                res.radar.append(best[0])
        res.radar.sort(key=lambda o: o.margin_pct, reverse=True)
        res.radar = res.radar[:20]
    return res


async def main() -> None:
    args = parse_args()
    s = Settings()
    if args.live:
        s.mode = "live"
    if args.stake:
        s.stake = args.stake
    if args.min_margin is not None:
        s.min_margin_pct = args.min_margin
    if args.quiet:
        s.telegram_token, s.beep = "", False

    poly = PolymarketSource(mode=s.mode, horizon_h=s.horizon_h, discovery_interval_s=s.poly_discovery_s)
    mcfg = MatchConfig(threshold=s.match_threshold, time_window_min=s.match_window_min)
    others: list = []
    if s.onexbet_enabled and not args.no_onexbet:
        others.append(OneXBetSource(
            base_url=s.onexbet_base, partner=s.onexbet_partner, cache_dir=s.db_path.parent / "onexbet",
            sports=s.onexbet_sports, prematch_interval_s=s.onexbet_prematch_s, live_interval_s=s.onexbet_live_s,
            horizon_h=s.horizon_h, match_cfg=mcfg,
        ))
    if s.cloudbet_api_key:
        others.append(CloudbetSource(s.cloudbet_api_key, horizon_h=s.horizon_h, match_cfg=mcfg))
    if s.sxbet_enabled and not args.no_sxbet:
        others.append(SXBetSource(horizon_h=s.horizon_h, match_cfg=mcfg))
    if not args.no_oddspapi and s.oddspapi_key:
        papi = OddsPapiSource(
            api_key=s.oddspapi_key, cache_dir=s.db_path.parent / "oddspapi", sports=s.oddspapi_sports,
            bookmakers=s.oddspapi_bookmakers, interval_s=s.oddspapi_interval_h * 3600,
            horizon_h=min(s.horizon_h, 24), reserve=s.oddspapi_reserve, min_kickoff=s.oddspapi_min_kickoff,
            match_cfg=mcfg,
        )
        papi.force_next = args.oddspapi_now
        others.append(papi)
    if not args.no_oddsapi and s.odds_api_key:
        others.append(OddsApiSource(
            api_key=s.odds_api_key, sports=s.odds_api_sports, regions=s.odds_api_regions,
            bookmakers=s.odds_api_bookmakers, interval_s=s.odds_api_interval_s,
            min_credits=s.odds_api_min_credits, commissions=s.commissions,
            dump_dir=s.db_path.parent,
        ))
    sources = [poly, *others]
    cfg = ArbConfig(stake=s.stake, min_margin_pct=s.min_margin_pct, max_bankroll=s.max_bankroll,
                    max_age_live_s=s.max_age_live_s, max_age_prematch_s=s.max_age_prematch_s,
                    outlier_pp=s.outlier_pp,
                    slow_feeds=frozenset(src.name for src in sources if src.slow))
    log = OpportunityLog(s.db_path)
    paper = None
    if s.paper_enabled and not args.no_paper:
        paper = PaperBook(s.db_path, PaperConfig(
            bankroll=s.paper_bankroll, arb_stake=s.paper_arb_stake, value_max_pct=s.paper_value_max_pct,
            kelly_fraction=s.kelly_fraction, min_persist_s=s.paper_min_persist_s,
            min_margin_pct=s.min_margin_pct, suspicious_margin_pct=cfg.suspicious_margin_pct,
            value_min_ev_pct=s.value_min_ev_pct, include_live=s.paper_include_live,
            take_value=s.paper_take_value, measure=s.paper_measure, measure_stake=s.paper_measure_stake,
            real_max_hours=s.paper_real_max_hours, live_accept_delay_s=s.paper_live_delay_s,
            real_min_net_pct=s.paper_real_min_net_pct,
        ))
    execsim = ExecSimulator(s.db_path, s.paper_arb_stake) if paper else None
    makersim = MakerSimulator(s.db_path, s.paper_arb_stake) if paper else None
    status_path = s.db_path.parent / "status.json"
    if not args.no_dashboard:
        try:
            serve_dashboard(s.db_path, status_path, s.dashboard_port)
            console.print(f"[bold cyan]Panel web:[/] http://localhost:{s.dashboard_port}")
        except OSError as e:
            console.print(f"[yellow]No se pudo abrir el panel en el puerto {s.dashboard_port}: {e}[/]")

    sx_live = next((x for x in others if isinstance(x, SXBetSource)), None)

    def resolution(event, market: str) -> dict:
        """Cómo liquidar: con Polymarket si el partido está ahí; si no, con el resultado oficial de SX Bet."""
        key = event if isinstance(event, str) else event.key
        feed, _, eid = key.partition(":")
        if feed == poly.name:
            res = poly.resolution.get(eid, {}).get(market, {})
            if res:
                return res
        for ev, swapped in ([] if isinstance(event, str) else event.members):
            if ev.feed == "sxbet" and sx_live:
                res = sx_live.resolution_for(ev.event_id, market)
                if swapped:
                    res = {{"H": "A", "A": "H"}.get(o, o): v for o, v in res.items()}
                if res:
                    return res
        return {}

    headers = {"User-Agent": "surebet3/0.2", "Accept": "application/json"}
    cycles = 0
    papi = next((src for src in others if isinstance(src, OddsPapiSource)), None)
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        if not args.once and not args.cycles:
            await send_telegram(client, s.telegram_token, s.telegram_chat_id, format_startup(sources, s))
        last_beat = 0.0
        with nullcontext() if args.headless else Live(console=console, auto_refresh=False, transient=False) as live:
            while True:
                t0 = time.time()
                rounds_before = papi.rounds if papi else 0
                try:
                    res = await scan_cycle(poly, others, client, s, cfg, mcfg, args.radar or args.once)
                except Exception as e:  # un fallo puntual de una API no debe tumbar el escáner
                    poly.status = f"error en ciclo: {e!r}"[:160]
                    res = CycleResult()
                try:
                    fresh_opps = log.record(res.opps)
                    fresh_values = log.record_value(res.values)
                except sqlite3.OperationalError as e:  # base ocupada un momento: se reintenta el próximo ciclo
                    poly.status = f"base ocupada: {e}"[:160]
                    fresh_opps, fresh_values = [], []
                for o in fresh_opps:
                    # Márgenes "demasiado buenos" casi siempre son datos malos: se muestran, no se alertan
                    if s.alert_min_margin_pct <= o.margin_pct < cfg.suspicious_margin_pct and not blocked(o):
                        if s.beep:
                            beep()
                        await send_telegram(client, s.telegram_token, s.telegram_chat_id,
                                            format_ready(o, s.paper_arb_stake, s.rotate_books),
                                            leg_buttons(o, s.rotate_books))
                for v in fresh_values:
                    st_ = v.event.anchor.start
                    hrs = (st_.timestamp() - time.time()) / 3600 if st_ else None
                    if golden_value(v.event.live, v.ev_pct, v.quote.odds, hrs):  # solo el valor filtrado se avisa
                        await send_telegram(client, s.telegram_token, s.telegram_chat_id,
                                            format_value_bet(v, s.bankroll, s.kelly_fraction))
                report_file = s.db_path.parent / "daily_report.txt"
                today = time.strftime("%Y-%m-%d")
                if paper and 0 <= s.daily_report_hour <= int(time.strftime("%H")):
                    try:
                        sent = report_file.read_text(encoding="utf-8").strip()
                    except OSError:
                        sent = ""
                    if sent != today:
                        report_file.write_text(today, encoding="utf-8")
                        await send_telegram(client, s.telegram_token, s.telegram_chat_id, daily_summary(s, status_path))
                for src in others:
                    while getattr(src, "notices", None):
                        await send_telegram(client, s.telegram_token, s.telegram_chat_id, src.notices.pop(0))
                if paper:
                    try:
                        paper.consider(res.opps, res.values, resolution)
                        execsim.step(res.opps, paper.eligible_real,
                                     lambda o, plan: paper.enter_auto(o, plan, resolution(o.event, o.market)))
                        makersim.step(res.opps, {q.token_id: q for p in poly._parsed.values() for q in p.event.quotes},
                                      paper.eligible_real)
                        await paper.settle(client)
                    except Exception as e:  # el papel nunca debe tumbar el escáner
                        poly.status = f"error en papel: {e!r}"[:160]
                try:
                    equity = paper.balances()["equity"] if paper else s.bankroll
                    write_status(status_path, sources, res, s, time.time() - t0, equity)
                except OSError:
                    pass
                if papi and papi.rounds > rounds_before:
                    summary = format_round_summary(res, papi, s)
                    try:
                        clv_text = await build_clv_report(s)
                        if "Señales evaluadas: 0" not in clv_text:
                            summary += "\n\n📈 CLV acumulado\n" + clv_text
                    except Exception:
                        pass  # el informe de CLV es secundario: nunca debe frenar el escáner
                    await send_telegram(client, s.telegram_token, s.telegram_chat_id, summary)
                if args.headless:
                    if time.time() - last_beat >= 300:
                        last_beat = time.time()
                        print(heartbeat(sources, res, paper), flush=True)
                else:
                    shown = res if (args.radar or args.once) else replace(res, radar=[])
                    live.update(render(sources, shown, time.time() - t0, s), refresh=True)
                cycles += 1
                if args.once or (args.cycles and cycles >= args.cycles):
                    break
                await asyncio.sleep(max(0.0, s.poly_interval_s - (time.time() - t0)))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
