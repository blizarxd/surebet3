"""Aviso de Telegram 'listo para apostar' (caso real del 10/10: Karmine Corp Blue vs Anubis Gaming)."""

from datetime import datetime, timedelta, timezone

from surebet.alerts import _max_poly_price, format_ready
from surebet.arb import ArbConfig, find_opportunities
from surebet.models import AWAY, HOME, BookLevel, MatchedEvent, Quote, SourceEvent
from surebet.pricing import poly_odds


def kcb_anubis():
    start = datetime.now(timezone.utc) + timedelta(hours=1, minutes=33)
    poly = Quote(source="polymarket", feed="polymarket", covers=frozenset({HOME}), odds=poly_odds(0.82, 0.05),
                 label="Karmine Corp Blue", asks=(BookLevel(0.82, 5000),), fee_rate=0.05, min_stake=5 * 0.82,
                 market="ml", url="https://polymarket.com/event/kcb-anubis")
    xb = Quote(source="1xbet", feed="onexbet", covers=frozenset({AWAY}), odds=6.15, label="Anubis Gaming",
               market="ml", url="https://1xbet-col.com/en/line/esports/x")
    p = SourceEvent(feed="polymarket", event_id="P", sport="esports:lol", home="Karmine Corp Blue",
                    away="Anubis Gaming", start=start, three_way=False, quotes=[poly])
    b = SourceEvent(feed="onexbet", event_id="X", sport="esports:lol", home="Karmine Corp Blue",
                    away="Anubis Gaming", start=start, three_way=False, quotes=[xb])
    m = MatchedEvent(anchor=p)
    m.members = [(p, False), (b, False)]
    return find_opportunities(m, ArbConfig())[0]


def test_ready_message_has_order_amounts_and_limits():
    text = format_ready(kcb_anubis(), 100)
    assert text.index("1. 1xBet") < text.index("2. Polymarket")      # primero la casa, después el exchange
    assert "Anubis Gaming @ 6.150 → apuesta $16." in text
    assert "precio máximo" in text and "cuota mínima" in text
    assert "+1.0" in text                                               # ~+1 % neto con la comisión de Polymarket


def test_max_poly_price_inverts_the_fee():
    # Si la cuota mínima es justo la del precio 0.82 con comisión, el precio máximo es 0.82
    assert abs(_max_poly_price(poly_odds(0.82, 0.05), 0.05, 1.0) - 0.82) < 1e-6
