import time
from datetime import datetime, timezone

import pytest

from surebet.arb import ArbConfig, find_opportunities, partitions, size_legs
from surebet.models import (AWAY, DRAW, HOME, THREE_WAY, TWO_WAY, BookLevel, MatchedEvent, Quote,
                            SourceEvent)

START = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
CFG = ArbConfig(stake=100, min_margin_pct=0.5)


def q(source, covers, odds, **kw):
    return Quote(source=source, covers=frozenset(covers), odds=odds, label=f"{source}-{''.join(covers)}", **kw)


def event(feed, quotes, three_way, home="Levante", away="Athletic", live=False):
    return SourceEvent(feed=feed, event_id=f"{feed}1", sport="soccer" if three_way else "basketball",
                       home=home, away=away, start=START, three_way=three_way, quotes=quotes, live=live)


def matched(*members):
    m = MatchedEvent(anchor=members[0][0])
    m.members = list(members)
    return m


def test_partitions():
    assert len(partitions(TWO_WAY)) == 1
    p3 = partitions(THREE_WAY)
    assert sorted(len(p) for p in p3) == [2, 2, 2, 3]
    for p in p3:  # cada partición cubre todo exactamente una vez
        assert frozenset().union(*p) == THREE_WAY and sum(len(b) for b in p) == 3


def test_two_way_arb_found_with_equal_payouts():
    poly = event("polymarket", [q("polymarket", {HOME}, 2.10)], three_way=False)
    book = event("oddsapi", [q("pinnacle", {AWAY}, 2.05)], three_way=False)
    opps = find_opportunities(matched((poly, False), (book, False)), CFG)
    assert len(opps) == 1
    o = opps[0]
    expected = 100 * (1 / (1 / 2.10 + 1 / 2.05) - 1)
    assert o.margin_pct == pytest.approx(expected)
    payouts = [l.payout for l in o.legs]
    assert payouts[0] == pytest.approx(payouts[1])
    assert o.profit == pytest.approx(payouts[0] - 100)


def test_regression_soccer_draw_must_be_covered():
    """
    Bug del escáner viejo: Poly 'Levante SÍ' @3.5 + casa 'Athletic' @2.35 se marcaba como
    surebet de +28%. Falta el empate: NO es arbitraje.
    """
    poly = event("polymarket", [q("polymarket", {HOME}, 3.5)], three_way=True)
    book = event("oddsapi", [q("pinnacle", {AWAY}, 2.35)], three_way=True)
    assert find_opportunities(matched((poly, False), (book, False)), CFG) == []


def test_soccer_no_token_covers_draw_and_away():
    # Poly "Levante NO" (cubre empate+visitante) vs casa "Levante gana"
    poly = event("polymarket", [q("polymarket", {DRAW, AWAY}, 1.60)], three_way=True)
    book = event("oddsapi", [q("pinnacle", {HOME}, 2.90)], three_way=True)
    opps = find_opportunities(matched((poly, False), (book, False)), CFG)
    assert len(opps) == 1 and opps[0].margin_pct > 0
    assert {l.quote.covers for l in opps[0].legs} == {frozenset({HOME}), frozenset({DRAW, AWAY})}


def test_two_way_and_three_way_markets_never_mixed():
    poly = event("polymarket", [q("polymarket", {HOME}, 2.5)], three_way=False)
    book = event("oddsapi", [q("pinnacle", {AWAY}, 2.5)], three_way=True)
    assert find_opportunities(matched((poly, False), (book, False)), CFG) == []


def test_swapped_home_away_is_remapped():
    # La casa lista el partido al revés: su "H" es nuestro visitante
    poly = event("polymarket", [q("polymarket", {HOME}, 2.10)], three_way=False)
    book = event("oddsapi", [q("pinnacle", {HOME}, 2.05)], three_way=False, home="Athletic", away="Levante")
    opps = find_opportunities(matched((poly, False), (book, True)), CFG)
    assert len(opps) == 1


def test_single_bookie_arb_is_ignored():
    book = event("oddsapi", [q("pinnacle", {HOME}, 2.2), q("pinnacle", {AWAY}, 2.2)], three_way=False)
    assert find_opportunities(matched((book, False)), CFG) == []


def test_stale_quotes_ignored_in_live():
    old = time.time() - 60
    poly = event("polymarket", [q("polymarket", {HOME}, 2.10)], three_way=False, live=True)
    book = event("oddsapi", [q("pinnacle", {AWAY}, 2.05, ts=old)], three_way=False, live=True)
    m = matched((poly, False), (book, False))
    assert find_opportunities(m, CFG) == []
    assert len(find_opportunities(m, CFG, floor_pct=-100, strict=False)) == 1


def test_liquidity_limits_max_stake():
    asks = (BookLevel(0.45, 50),)  # solo 50 shares a 0.45 (~$22.5)
    poly = event("polymarket", [q("polymarket", {HOME}, 1 / 0.45, asks=asks)], three_way=False)
    book = event("oddsapi", [q("pinnacle", {AWAY}, 2.0)], three_way=False)
    o = find_opportunities(matched((poly, False), (book, False)), CFG)[0]
    assert 0 < o.max_total_stake < 60
    assert any("liquidez" in w for w in o.warnings)


def test_size_legs_converges_with_book_walk():
    asks = (BookLevel(0.45, 40), BookLevel(0.47, 1000))
    poly_q = q("polymarket", {HOME}, 1 / 0.45, asks=asks)
    legs = size_legs([poly_q, q("pinnacle", {AWAY}, 2.0)], 200)
    assert legs is not None
    assert legs[0].payout == pytest.approx(legs[1].payout, rel=1e-6)
    assert legs[0].odds < 1 / 0.45  # empeoró por recorrer el libro


def test_min_order_size_does_not_zero_max_stake():
    # Pata de Polymarket chica pero con mínimo de 5 shares: antes daba "Máx $ -"
    asks = (BookLevel(0.12, 100000),)
    poly_q = q("polymarket", {HOME}, 1 / 0.12, asks=asks, min_stake=5 * 0.12)
    poly = event("polymarket", [poly_q], three_way=False)
    book = event("oddsapi", [q("pinnacle", {AWAY}, 1.36)], three_way=False)
    o = find_opportunities(matched((poly, False), (book, False)), CFG)[0]
    assert o.max_total_stake > 100


def test_stale_outlier_rejected_by_consensus():
    """Caso real (Brann 0-2 Viking, en vivo): Pinnacle congelado en 1.36, el resto ~1.07-1.14."""
    books = {"pinnacle": (8.46, 5.06, 1.36), "betsson": (17.5, 9.1, 1.07),
             "unibet_nl": (14.0, 8.0, 1.11), "matchbook": (25.0, 11.0, 1.14)}
    quotes = [q(src, {o}, x) for src, xs in books.items() for o, x in zip((HOME, DRAW, AWAY), xs)]
    bk = event("oddsapi", quotes, three_way=True)
    m = matched((bk, False))
    assert find_opportunities(m, CFG) == []
    radar = find_opportunities(m, CFG, floor_pct=-100, strict=False)
    assert radar and any("consenso" in w for w in radar[0].warnings)


def test_value_bet_against_polymarket_fair_price():
    from surebet.arb import find_value_bets
    poly_quotes = [q("polymarket", {o}, 1 / (p + 0.01), fair_prob=p, token_id=f"t{o}")
                   for o, p in ((HOME, 0.50), (DRAW, 0.28), (AWAY, 0.22))]
    poly = event("polymarket", poly_quotes, three_way=True)
    book = event("oddspapi", [q("stake", {HOME}, 2.15), q("stake", {AWAY}, 4.0)], three_way=True)
    vbs = find_value_bets(matched((poly, False), (book, False)), CFG, min_ev_pct=2.0)
    assert [v.quote.covers for v in vbs] == [frozenset({HOME})]  # 2.15 x 0.50 = +7.5%; 4.0 x 0.22 = -12%
    v = vbs[0]
    assert v.ev_pct == pytest.approx(7.5)
    assert v.kelly == pytest.approx(0.075 / 1.15)
    assert v.poly_tokens == {HOME: "tH", DRAW: "tD", AWAY: "tA"}


def test_value_bet_needs_complete_polymarket_fair_prices():
    from surebet.arb import find_value_bets
    poly = event("polymarket", [q("polymarket", {HOME}, 1.9, fair_prob=0.5)], three_way=True)
    book = event("oddspapi", [q("stake", {HOME}, 3.0)], three_way=True)
    assert find_value_bets(matched((poly, False), (book, False)), CFG, 2.0) == []


def test_value_bets_skip_long_odds():
    from surebet.arb import find_value_bets
    poly_quotes = [q("polymarket", {o}, 1 / (p + 0.01), fair_prob=p, token_id=f"t{o}")
                   for o, p in ((HOME, 0.85), (DRAW, 0.08), (AWAY, 0.07))]
    poly = event("polymarket", poly_quotes, three_way=True)
    book = event("oddspapi", [q("1xbet", {DRAW}, 14.9), q("1xbet", {AWAY}, 32.0)], three_way=True)
    m = matched((poly, False), (book, False))
    assert find_value_bets(m, CFG, min_ev_pct=2.0, max_odds=10) == []
    assert len(find_value_bets(m, CFG, min_ev_pct=2.0, max_odds=50)) == 2
