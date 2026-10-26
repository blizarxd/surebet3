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
import json
import sqlite3
import time
from contextlib import nullcontext
from dataclasses import dataclass, field, replace

import httpx
from rich.console import Console
from rich.live import Live

from clv import build_report as build_clv_report
from clv import report as clv_report
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
from surebet.sources.kalshi import KalshiSource
from surebet.sources.azuro import AzuroSource
from surebet.safety import blocked, golden_value, observe_only, risk_flags, wide_value
from surebet.semaforo import Semaforo
from surebet.livehedge import LiveHedgeTracker
from surebet.sxshadow import SXShadow
from surebet.executor import Executor
from surebet.dashboard_server import build_state
from surebet.dashboard_server import serve as serve_dashboard
from surebet.paper import PaperBook, PaperConfig
from surebet.execsim import ExecSimulator
from surebet.makersim import AUTO_HOUSES, MakerSimulator, POLY_FIRST, book_ref
from surebet.pinnacle import RefShadow
from surebet.polybuy import PolyBuyer
from surebet.valuenet import ValueNet
from surebet.manualvalor import SOURCES as MANUAL_SOURCES, ManualValor
from surebet.nettrack import NetTracker
from surebet.liveodds import LiveOdds
from surebet.autovivo import LiveAutoSimulator
from surebet.lprewards import RewardsSimulator
from surebet.staking import net_limit, value_stake
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
    book_quotes: dict = field(default_factory=dict)  # cuota actual de cada casa (prueba de orden límite)
    clusters: list = field(default_factory=list)      # todos los partidos del ciclo (cuota actual de lo abierto)


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
    p.add_argument("--no-kalshi", action="store_true", help="no usar Kalshi")
    p.add_argument("--no-azuro", action="store_true", help="no usar Azuro")
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
             f"{a['won']} ganadas / {a['lost']} perdidas"]
    sem = st.get("semaforo")
    if sem:
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
        lines.append("Semáforo: " + " · ".join(f"{icon[l['color']]} {l['name']}" for l in sem["lights"]))
    lines += ["", "¿Dónde está la ventaja? (cerradas, $100 por jugada)"]
    for g in st["measure"]:
        closed = g["won"] + g["lost"] + g.get("void", 0)
        roi = f"{g['roi_pct']:+.1f}%" if g["roi_pct"] is not None else "-"
        lines.append(f"• {g['name']}: {closed} cerradas, {g['pnl']:+.2f} (ROI {roi}) · {g['open']} abiertas")
    if s.panel_url:
        lines += ["", f"Panel: {s.panel_url}"]
    return "\n".join(lines)


API_EXCHANGES = {"kalshi", "azuro", "limitless"}   # nuevos exchanges por API: solo medición automática


def _allowed(o: Opportunity, my_books: list[str]) -> bool:
    if not my_books:
        return True
    return all(l.quote.source == POLYMARKET or l.quote.source in my_books for l in o.legs)


async def scan_cycle(poly: PolymarketSource, others: list, client: httpx.AsyncClient, s: Settings,
                     cfg: ArbConfig, mcfg: MatchConfig, radar: bool) -> CycleResult:
    # Estas fuentes eligen qué pedir según los partidos de Polymarket
    targeted = (OddsPapiSource, OneXBetSource, SXBetSource, CloudbetSource, KalshiSource, AzuroSource)
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
        elif isinstance(src, KalshiSource):   # Kalshi contra Polymarket, SX y Cloudbet (todo por API)
            targets = targets + by_feed.get("cloudbet", []) + (list(sx_src._bare_events().values()) if sx_src else [])
        elif isinstance(src, AzuroSource):    # Azuro contra todo lo que se puede operar por API
            ka = next((x for x in papi if isinstance(x, KalshiSource)), None)
            targets = (targets + by_feed.get("cloudbet", []) + (list(sx_src._bare_events().values()) if sx_src else [])
                       + (list(ka._bare().values()) if ka else []))
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
    res = CycleResult(n_clusters=len(clusters), n_matched=len(matched), clusters=clusters)
    res.book_quotes = {book_ref(q): q for c in matched for ev, _ in c.members if ev.feed in ("onexbet", "1xbet", *AUTO_HOUSES)
                       for q in ev.quotes}
    res.opps = [o for c in evaluable for o in find_opportunities(c, cfg) if _allowed(o, s.my_books)]
    for o in res.opps:
        o.warnings += risk_flags(o)  # ⛔ = no se avisa ni entra en la cuenta real (sí se mide)
    res.opps.sort(key=lambda o: o.margin_pct, reverse=True)
    # Se registran en papel desde VALUE_LOG_MIN_EV_PCT (más datos para medir CLV);
    # en pantalla y alertas solo desde VALUE_MIN_EV_PCT
    log_min = min(s.value_log_min_ev_pct, s.value_min_ev_pct)
    res.values = [v for c in matched for v in find_value_bets(c, cfg, log_min, s.value_max_odds, s.value_min_poly_depth)
                  if (not s.my_books or v.quote.source in s.my_books) and v.quote.source not in API_EXCHANGES - {"azuro"}]
    res.values.sort(key=lambda v: v.ev_pct, reverse=True)
    hot = {ev.event_id for v in res.values if v.ev_pct >= 1.5 and not v.event.live and v.quote.source in ("1xbet", "onexbet")
           for ev, _ in v.event.members if ev.feed == "onexbet"}
    for src in others:
        if isinstance(src, OneXBetSource):
            src.hot = hot       # 1xBet reconfirma más seguido los partidos con señal activa

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


def cap_warning(in_play: float, needed: float, cap: float) -> str:
    """Aviso si, según tus botones, esta entrada te pasaría del tope de dinero en juego."""
    if cap and in_play + needed > cap:
        return (f"\n\n⚠ Según tus botones ya tienes ${in_play:,.0f} en juego (tope ${cap:,.0f}). "
                "Si la tomas te pasas: mejor sáltala o espera a que se resuelvan otras.")
    return ""


async def update_value_clv(s, paper) -> None:
    """CLV (cuota vs. precio de cierre de Polymarket) de las apuestas de la cuenta de valor ya empezadas."""
    keys = []
    for ek, market, legs in paper.conn.execute(
            "SELECT event_key, market, legs_json FROM paper_ops WHERE kind='value' AND track='valor' "
            "AND start_ts < ? AND start_ts > ?", (time.time() - 120, time.time() - 3 * 86400)):
        leg = (json.loads(legs) or [{}])[0]
        keys.append(f"{ek}#{market}#{leg.get('source')}:{''.join(leg.get('covers') or [])}")
    if keys:
        await clv_report(s, keys)


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
            sports=s.onexbet_sports, prematch_interval_s=s.onexbet_prematch_s, live_interval_s=s.onexbet_live_s, live_enabled=s.onexbet_live,
            horizon_h=s.horizon_h, match_cfg=mcfg,
        ))
    if s.cloudbet_api_key:
        others.append(CloudbetSource(s.cloudbet_api_key, horizon_h=s.horizon_h, match_cfg=mcfg))
    if s.sxbet_enabled and not args.no_sxbet:
        others.append(SXBetSource(horizon_h=s.horizon_h, match_cfg=mcfg))
    if s.kalshi_enabled and not args.no_kalshi:
        others.append(KalshiSource(horizon_h=s.horizon_h, match_cfg=mcfg))
    if s.azuro_enabled and not args.no_azuro:
        others.append(AzuroSource(horizon_h=s.horizon_h, match_cfg=mcfg))
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
            real_min_net_pct=s.paper_real_min_net_pct, value_bankroll=s.value_bankroll,
            value_min_total=s.value_min_total,
        ))
    execsim = (ExecSimulator(s.db_path, s.auto_max_stake, cash_fn=lambda: paper.auto_balances()["cash"],
                             fresh=True, cloudbet_key=s.cloudbet_api_key)   # precio real de cada casa al ejecutar
               if paper else None)
    # automático en vivo (simulado): descartado, solo corre con AUTOVIVO_ENABLED=1
    autovivo = LiveAutoSimulator(s.db_path, s.cloudbet_api_key) if paper and s.autovivo_enabled else None
    makersim = MakerSimulator(s.db_path, s.paper_arb_stake, cloudbet_key=s.cloudbet_api_key) if paper else None
    refshadow = RefShadow(s.db_path) if paper else None   # Pinnacle en sombra: solo mide, no decide nada
    # Valor automático en sombra (SX, Azuro, Cloudbet): cada señal oro con el precio leído al ejecutar (nunca envía nada)
    sxshadow = SXShadow(s.db_path, s.value_bankroll, s.value_min_total, s.cloudbet_api_key) if paper else None
    buyer = PolyBuyer(s.db_path, s.telegram_token, s.telegram_chat_id, live=s.poly_live,
                      private_key=s.poly_private_key, funder=s.poly_funder, signature_type=s.poly_signature_type,
                      max_order_usd=s.poly_max_order_usd, max_daily_usd=s.poly_max_daily_usd)
    nets = ValueNet(buyer)  # red de seguridad de las apuestas de valor (botón 🛟 en el aviso)
    # Semáforo de riesgo: en rojo no se avisa ni se toma nada NUEVO de esa cuenta o casa (las coberturas siguen)
    semaforo = Semaforo(s.db_path)
    buyer.semaforo = semaforo
    if paper:
        paper.gate = semaforo
    # Ejecutor real Azuro + Polymarket: por defecto "dry" (anota lo que haría); "live" solo con AUTO_EXEC=live y claves
    executor = (Executor(s.db_path, buyer, nets, semaforo, s.auto_exec, s.auto_max_per_op, s.auto_max_daily,
                         s.auto_max_open, s.azuro_private_key, s.azuro_affiliate,
                         (s.telegram_token, s.telegram_chat_id), s.auto_azuro_stake, s.auto_value_net, s.auto_arb)
                if paper and s.auto_exec != "off" else None)
    # Valor 1xBet a mano: aviso limpio con botones de cada pick de la cuenta de valor (solo en tu horario)
    manual = ManualValor(s.db_path, s.manual_value_stake, s.manual_value_hours, tuple(s.manual_houses)) if paper else None
    buyer.manual = manual
    if executor and sxshadow:
        sxshadow.on_fill = executor.queue_value

    def on_auto_fill(o, plan):
        """El automático confirmó la surebet con el precio real: cuenta en papel y, si es Azuro + Polymarket, al ejecutor."""
        paper.enter_auto(o, plan, resolution(o.event, o.market))
        if executor:
            executor.queue_arb(o, plan)
    live_odds = LiveOdds(s.db_path, s.db_path.parent / "live_odds.json")  # cuota de ahora de lo abierto
    status_path = s.db_path.parent / "status.json"
    if not args.no_dashboard:
        try:
            serve_dashboard(s.db_path, status_path, s.dashboard_port)
            console.print(f"[bold cyan]Panel web:[/] http://localhost:{s.dashboard_port}")
        except OSError as e:
            console.print(f"[yellow]No se pudo abrir el panel en el puerto {s.dashboard_port}: {e}[/]")

    sx_live = next((x for x in others if isinstance(x, SXBetSource)), None)
    kalshi = next((x for x in others if isinstance(x, KalshiSource)), None)
    azuro = next((x for x in others if isinstance(x, AzuroSource)), None)

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
        for ev, swapped in ([] if isinstance(event, str) else event.members):
            src_ = {"kalshi": kalshi, "azuro": azuro}.get(ev.feed)
            if src_:   # sin Polymarket ni SX: resultado oficial de Kalshi o de Azuro
                res = src_.resolution_for(ev.event_id, market)
                if swapped:
                    res = {{"H": "A", "A": "H"}.get(o, o): v for o, v in res.items()}
                if res:
                    return res
        return {}

    alerted: dict[str, tuple[float, float]] = {}  # partido#mercado -> (cuándo, neto) para no repetir avisos

    def worth_alert(o) -> bool:
        """Solo prepartido que empieza pronto, neto real de comisiones, con capacidad y sin repetir."""
        now = time.time()
        st_ = o.event.anchor.start
        if o.event.live or st_ is None or not (0 < st_.timestamp() - now <= s.alert_max_hours * 3600):
            return False
        if blocked(o) or not (s.alert_min_margin_pct <= o.margin_pct < cfg.suspicious_margin_pct):
            return False
        if any(l.quote.source in API_EXCHANGES for l in o.legs):
            return False   # Kalshi/Azuro: las mide el automático; avisarlas solo satura Telegram
        if o.max_total_stake and o.max_total_stake < s.alert_min_stake:
            return False
        if semaforo.blocks("real", {l.quote.source for l in o.legs}):
            return False
        net = paper._net_pct(o) if paper else o.margin_pct
        if net < s.alert_min_net_pct:
            return False
        k = f"{o.event.key}#{o.market}"
        last = alerted.get(k)
        if last and now - last[0] < s.alert_repeat_min * 60 and net < last[1] + 0.5:
            return False  # ya avisado hace poco y no mejoró de verdad
        alerted[k] = (now, net)
        return True

    async def send_manual(client) -> None:
        for text, buttons in (manual.step() if manual else []):
            await send_telegram(client, s.telegram_token, s.telegram_chat_id, text, buttons)

    def worth_value(v, hrs) -> bool:
        if v.quote.source in API_EXCHANGES:
            return False   # Azuro: valor solo medido (CLV), sin avisos
        if manual and v.quote.source in MANUAL_SOURCES:
            return False   # 1xBet: lo avisa ManualValor (aviso limpio con botones, cada pick de la cuenta de valor)
        if not golden_value(v.event.live, v.ev_pct, v.quote.odds, hrs):
            return False
        if semaforo.blocks("valor", {v.quote.source, POLYMARKET}):
            return False
        k = f"v#{v.event.key}#{v.market}"
        if k in alerted and time.time() - alerted[k][0] < 3 * 3600:
            return False
        alerted[k] = (time.time(), v.ev_pct)
        return True

    headers = {"User-Agent": "surebet3/0.2", "Accept": "application/json"}
    cycles = 0
    papi = next((src for src in others if isinstance(src, OddsPapiSource)), None)
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        if not args.once and not args.cycles:
            await send_telegram(client, s.telegram_token, s.telegram_chat_id, format_startup(sources, s))
            if executor and executor.mode == "live":
                await send_telegram(client, s.telegram_token, s.telegram_chat_id,
                                    f"🤖 Ejecutor REAL de Azuro + Polymarket ACTIVO · {executor.status}. /pausa lo frena.")
            bot_task = asyncio.create_task(buyer.run())  # botones y comandos de Telegram  # noqa: F841
            if paper:  # "Valor + red" en papel: backtest de lo anotado y seguimiento pick por pick
                # Recompensas Polymarket: apagado el 13/10 (base −$42, protegida −$16: las llenadas se comen lo cobrado)
                lp_tasks = [asyncio.create_task(RewardsSimulator(s.db_path, variant=v).run())  # noqa: F841
                            for v in ("base", "protegida")] if s.lp_enabled else []
                net_task = asyncio.create_task(  # noqa: F841
                    NetTracker(s.db_path, s.value_bankroll, min_total=s.value_min_total,
                               max_in_play=s.value_max_in_play).run())
                # Cubrir en vivo (minuto 15) vs asegurar antes del partido: backtest y sombra, nunca ejecuta
                hedge_task = asyncio.create_task(LiveHedgeTracker(s.db_path).run())  # noqa: F841
                # Ejecutor de Azuro + Polymarket (dry por defecto): su propia tarea, las confirmaciones tardan segundos
                exec_task = asyncio.create_task(executor.run()) if executor else None  # noqa: F841
        last_clv = time.time()
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
                try:   # antes de avisar: así un rojo nuevo ya frena los avisos de este mismo ciclo
                    for msg in semaforo.step(sources):
                        await send_telegram(client, s.telegram_token, s.telegram_chat_id, msg)
                except Exception as e:  # el semáforo nunca tumba al escáner
                    poly.status = f"error en semáforo: {e!r}"[:160]
                for o in fresh_opps:
                    if worth_alert(o):
                        if s.beep:
                            beep()
                        buttons = leg_buttons(o, s.rotate_books)
                        oid = buyer.offer(o, s.alert_arb_stake)
                        if oid:
                            buttons += [buyer.button(oid), buyer.reject_button("pb", oid)]
                        observe = ("\n\n⚠ CS2/Dota2: solo observación (fuera del automático y de la cuenta real). "
                                   "1xBet suele anular estas por cuotas viejas: si la tomas, a mano y con cuidado."
                                   if observe_only(o.event) else "")
                        await send_telegram(client, s.telegram_token, s.telegram_chat_id,
                                            format_ready(o, s.alert_arb_stake, s.rotate_books) + observe
                                            + cap_warning(buyer.in_play(), min(s.alert_arb_stake, o.max_total_stake or s.alert_arb_stake),
                                                          s.value_max_in_play), buttons)
                for v in fresh_values:
                    st_ = v.event.anchor.start
                    hrs = (st_.timestamp() - time.time()) / 3600 if st_ else None
                    if worth_value(v, hrs):  # solo el valor "oro", una vez por partido
                        stake = value_stake(v.quote.odds, v.ev_pct, s.bankroll, s.kelly_fraction, 0.03,
                                            s.value_min_total, net_limit(v.quote.odds))
                        buttons = [(f"Abrir {v.quote.source}", v.quote.url)] if v.quote.url.startswith("http") else []
                        nid = nets.offer(v, stake)
                        if nid:
                            buttons += [nets.button(nid), buyer.reject_button("vn", nid)]
                        await send_telegram(client, s.telegram_token, s.telegram_chat_id,
                                            format_value_bet(v, s.bankroll, s.kelly_fraction, stake)
                                            + cap_warning(buyer.in_play(), s.value_min_total, s.value_max_in_play),
                                            buttons)
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
                if len(alerted) > 5000:
                    cut = time.time() - 6 * 3600
                    alerted = {k: v for k, v in alerted.items() if v[0] > cut}
                for src in others:
                    while getattr(src, "notices", None):
                        await send_telegram(client, s.telegram_token, s.telegram_chat_id, src.notices.pop(0))
                if paper:
                    # Cada paso por separado: si uno falla (p. ej. base ocupada) los demás igual corren; antes un
                    # fallo en la entrada saltaba también la liquidación y quedaban apuestas sin cerrar por horas
                    poly_quotes = {q.token_id: q for p in poly._parsed.values() for q in p.event.quotes}
                    steps = [
                        # La cuenta real/medición es la semi-manual: las de Kalshi/Azuro las hace el bot (cuenta automática)
                        ("papel", lambda: paper.consider([o for o in res.opps if not any(
                            l.quote.source in API_EXCHANGES for l in o.legs)],
                            [v for v in res.values if v.quote.source not in API_EXCHANGES - {"azuro"}], resolution)),
                        ("valor-manual", lambda: send_manual(client)),
                        ("automático", lambda: execsim.step(res.opps, paper.eligible_auto, on_auto_fill)),
                        ("ejecución", lambda: execsim.verify(client)),
                        ("cuotas", lambda: live_odds.update(res.clusters)),
                        ("maker", lambda: (makersim.step_value(res.values, golden_value),
                                           makersim.step(res.opps, poly_quotes, paper.eligible_real, res.book_quotes))),
                        # Automático como maker (apagado por defecto: perdía; se enciende con MAKER_AUTO_ENABLED=1)
                        *([("maker-auto", lambda: makersim.step(res.opps, poly_quotes, paper.eligible_auto,
                                                                res.book_quotes, track="auto",
                                                                book_sources=AUTO_HOUSES, modes=(POLY_FIRST,))),
                           ("maker-precio", lambda: makersim.finish_auto(client))] if s.maker_auto_enabled else []),
                        ("sombra-pinnacle", lambda: refshadow.record(res.values)),
                        ("sombra-sx", lambda: sxshadow.step(res.values, golden_value, wide_value)),
                        ("sombra-sx-precio", lambda: sxshadow.verify(client)),
                        ("sombra-sx-cierre", lambda: sxshadow.settle(client)),
                        ("red", lambda: nets.tick(poly_quotes, lambda t: send_telegram(client, s.telegram_token,
                                                                                       s.telegram_chat_id, t))),
                        ("vivo", lambda: (autovivo.detect(res.opps, resolution), autovivo.check(client))[1]
                         if autovivo else None),
                        ("liquidación", lambda: paper.settle(client)),
                    ]
                    for name, step in steps:
                        try:
                            out = step()
                            if asyncio.iscoroutine(out):
                                await out
                        except Exception as e:  # el papel nunca debe tumbar el escáner
                            poly.status = f"error en papel ({name}): {e!r}"[:160]
                            for conn in (paper.conn, execsim.conn, sxshadow.conn):
                                try:
                                    conn.rollback()
                                except sqlite3.Error:
                                    pass
                try:
                    equity = paper.balances()["equity"] if paper else s.bankroll
                    write_status(status_path, sources, res, s, time.time() - t0, equity)
                except OSError:
                    pass
                if paper and time.time() - last_clv >= 3600:  # CLV de la cuenta de valor, cada hora
                    last_clv = time.time()
                    try:
                        await update_value_clv(s, paper)
                    except Exception:
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
