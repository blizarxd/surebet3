"""Configuración desde variables de entorno / archivo .env (nunca keys en el código)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _commissions(raw: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in raw.split(","):
        if ":" in item:
            k, v = item.split(":", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                pass
    return out


@dataclass
class Settings:
    # Escaneo
    mode: str = field(default_factory=lambda: os.getenv("MODE", "all"))          # all | live
    horizon_h: float = field(default_factory=lambda: _f("HORIZON_H", 24))
    poly_interval_s: float = field(default_factory=lambda: _f("POLY_INTERVAL_S", 3))
    poly_discovery_s: float = field(default_factory=lambda: _f("POLY_DISCOVERY_S", 60))

    # Arbitraje
    stake: float = field(default_factory=lambda: _f("STAKE", 100))
    min_margin_pct: float = field(default_factory=lambda: _f("MIN_MARGIN_PCT", 0.5))
    max_bankroll: float = field(default_factory=lambda: _f("MAX_BANKROLL", 5000))
    max_age_live_s: float = field(default_factory=lambda: _f("MAX_AGE_LIVE_S", 15))
    max_age_prematch_s: float = field(default_factory=lambda: _f("MAX_AGE_PREMATCH_S", 900))
    outlier_pp: float = field(default_factory=lambda: _f("OUTLIER_PP", 0.08))

    # Emparejamiento
    match_threshold: float = field(default_factory=lambda: _f("MATCH_THRESHOLD", 85))
    match_window_min: float = field(default_factory=lambda: _f("MATCH_WINDOW_MIN", 120))

    # The Odds API
    odds_api_key: str = field(default_factory=lambda: os.getenv("ODDS_API_KEY", ""))
    odds_api_sports: list[str] = field(default_factory=lambda: _list("ODDS_API_SPORTS", "upcoming"))
    odds_api_regions: str = field(default_factory=lambda: os.getenv("ODDS_API_REGIONS", "eu"))
    odds_api_bookmakers: list[str] = field(default_factory=lambda: _list("ODDS_API_BOOKMAKERS"))
    odds_api_interval_s: float = field(default_factory=lambda: _f("ODDS_API_INTERVAL_S", 6 * 3600))
    odds_api_min_credits: int = field(default_factory=lambda: int(_f("ODDS_API_MIN_CREDITS", 50)))
    commissions: dict[str, float] = field(default_factory=lambda: _commissions(
        os.getenv("COMMISSIONS", "betfair_ex_eu:0.05,betfair_ex_uk:0.05,matchbook:0.02,smarkets:0.02")))

    # 1xBet (feed público gratis, prepartido y vivo). 888starz usa la misma plataforma y cuotas.
    onexbet_enabled: bool = field(default_factory=lambda: os.getenv("ONEXBET_ENABLED", "1") == "1")
    # SX Bet (exchange cripto, API pública para leer; ver surebet/sources/sxbet.py)
    cloudbet_api_key: str = field(default_factory=lambda: os.getenv("CLOUDBET_API_KEY", ""))
    # Casas con las MISMAS cuotas que 1xBet (sus clones), una por día en los avisos para repartir la actividad
    rotate_books: list[str] = field(default_factory=lambda: _list("ROTATE_BOOKS", ""))
    sxbet_enabled: bool = field(default_factory=lambda: os.getenv("SXBET_ENABLED", "1") == "1")
    onexbet_base: str = field(default_factory=lambda: os.getenv("ONEXBET_BASE", "https://1xbet-col.com"))
    onexbet_partner: int = field(default_factory=lambda: int(_f("ONEXBET_PARTNER", 285)))
    onexbet_sports: list[int] = field(default_factory=lambda: [int(x) for x in _list("ONEXBET_SPORTS", "1,3,4,10,40,2,6")])
    onexbet_prematch_s: float = field(default_factory=lambda: _f("ONEXBET_PREMATCH_S", 120))
    onexbet_live_s: float = field(default_factory=lambda: _f("ONEXBET_LIVE_S", 10))

    # OddsPapi (1xBet, Stake, Rollbit...). Plan gratis: 250 peticiones/mes, solo prepartido.
    oddspapi_key: str = field(default_factory=lambda: os.getenv("ODDSPAPI_KEY", ""))
    oddspapi_sports: list[int] = field(default_factory=lambda: [int(x) for x in _list("ODDSPAPI_SPORTS", "10")])
    oddspapi_bookmakers: list[str] = field(default_factory=lambda: _list("ODDSPAPI_BOOKMAKERS", "stake,rollbit"))
    oddspapi_interval_h: float = field(default_factory=lambda: _f("ODDSPAPI_INTERVAL_H", 3))
    oddspapi_min_kickoff: int = field(default_factory=lambda: int(_f("ODDSPAPI_MIN_KICKOFF", 5)))
    oddspapi_reserve: int = field(default_factory=lambda: int(_f("ODDSPAPI_RESERVE", 20)))

    # Apuestas de valor (cuota de la casa por encima de la probabilidad justa de Polymarket)
    value_min_ev_pct: float = field(default_factory=lambda: _f("VALUE_MIN_EV_PCT", 2.0))
    value_min_poly_depth: float = field(default_factory=lambda: _f("VALUE_MIN_POLY_DEPTH", 1000))
    value_max_odds: float = field(default_factory=lambda: _f("VALUE_MAX_ODDS", 10))
    value_log_min_ev_pct: float = field(default_factory=lambda: _f("VALUE_LOG_MIN_EV_PCT", 0.0))
    value_alert_min_ev_pct: float = field(default_factory=lambda: _f("VALUE_ALERT_MIN_EV_PCT", 4.0))
    bankroll: float = field(default_factory=lambda: _f("BANKROLL", 500))
    kelly_fraction: float = field(default_factory=lambda: _f("KELLY_FRACTION", 0.25))

    # Cuenta en papel (simulación con dinero ficticio) y panel web local
    paper_enabled: bool = field(default_factory=lambda: os.getenv("PAPER_ENABLED", "1") == "1")
    paper_bankroll: float = field(default_factory=lambda: _f("PAPER_BANKROLL", 300))
    paper_arb_stake: float = field(default_factory=lambda: _f("PAPER_ARB_STAKE", 100))
    paper_value_max_pct: float = field(default_factory=lambda: _f("PAPER_VALUE_MAX_PCT", 0.03))
    paper_min_persist_s: float = field(default_factory=lambda: _f("PAPER_MIN_PERSIST_S", 5))
    paper_take_value: bool = field(default_factory=lambda: os.getenv("PAPER_TAKE_VALUE", "0") == "1")
    paper_include_live: bool = field(default_factory=lambda: os.getenv("PAPER_INCLUDE_LIVE", "0") == "1")
    # Medición: lo que la cuenta real no toma (vivo, valor) se anota igual, sin tocar el saldo
    paper_measure: bool = field(default_factory=lambda: os.getenv("PAPER_MEASURE", "1") == "1")
    paper_measure_stake: float = field(default_factory=lambda: _f("PAPER_MEASURE_STAKE", 100))
    paper_real_max_hours: float = field(default_factory=lambda: _f("PAPER_REAL_MAX_HOURS", 6))
    paper_live_delay_s: float = field(default_factory=lambda: _f("PAPER_LIVE_DELAY_S", 8))
    paper_real_min_net_pct: float = field(default_factory=lambda: _f("PAPER_REAL_MIN_NET_PCT", 0.5))
    dashboard_port: int = field(default_factory=lambda: int(_f("DASHBOARD_PORT", 8765)))
    panel_url: str = field(default_factory=lambda: os.getenv("PANEL_URL", ""))
    # Resumen diario por Telegram a esta hora local (-1 = desactivado)
    daily_report_hour: int = field(default_factory=lambda: int(_f("DAILY_REPORT_HOUR", 9)))

    # Solo mostrar oportunidades en casas donde tienes cuenta (vacío = todas)
    my_books: list[str] = field(default_factory=lambda: _list("MY_BOOKS"))

    # Alertas
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    alert_min_margin_pct: float = field(default_factory=lambda: _f("ALERT_MIN_MARGIN_PCT", 1.0))
    beep: bool = field(default_factory=lambda: os.getenv("BEEP", "1") == "1")

    db_path: Path = field(default_factory=lambda: ROOT / "data" / "opportunities.db")
