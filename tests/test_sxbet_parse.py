"""SX Bet: mercados y libros (perspectiva del apostador) a quotes, con la comisión del 1 % incluida."""

import pytest

from surebet.models import AWAY, DRAW, HOME, OVER, UNDER
from surebet.pricing import effective_odds
from surebet.sources.sxbet import build_quotes, effective_price, market_sides

BASE = {"teamOneName": "Ipswich Town", "teamTwoName": "Fulham", "sportId": 5, "leagueLabel": "Premier League",
        "gameTime": 2_000_000_000, "sportXeventId": "L1", "marketHash": "0xabc"}


def lvl(pct, usdc):
    return {"percentageOdds": str(int(pct * 10 ** 18)), "size": str(int(usdc * 10 ** 6))}


def test_fee_is_one_percent_of_profit():
    # Probabilidad 40 % -> cuota bruta 2,50; ganancia 1,50 menos 1 % -> cobra 2,485 por cada $1
    assert 1 / effective_price(0.40) == pytest.approx(2.485)


def test_soccer_1x2_is_yes_no_per_result_like_polymarket():
    m = {**BASE, "type": 1, "outcomeOneName": "Tie", "outcomeTwoName": "Not tie"}
    qs = build_quotes(m, {"outcomeOne": [lvl(30, 50)], "outcomeTwo": [lvl(72, 80)]}, 0.0)
    by_cov = {q.covers: q for q in qs}
    assert by_cov[frozenset({DRAW})].market == "1x2"
    assert frozenset({HOME, AWAY}) in by_cov
    assert by_cov[frozenset({DRAW})].odds == pytest.approx(1 / effective_price(0.30))
    m2 = {**BASE, "type": 1, "outcomeOneName": "Fulham", "outcomeTwoName": "Not Fulham"}
    assert market_sides(m2)[1] == frozenset({AWAY})


def test_winner_and_totals():
    nba = {**BASE, "sportId": 1, "type": 226, "teamOneName": "Knicks", "teamTwoName": "76ers",
           "outcomeOneName": "Knicks", "outcomeTwoName": "76ers"}
    assert market_sides(nba)[0] == "ml"
    assert market_sides({**nba, "type": 52}) is None          # en básquet el ganador es con prórroga (226)
    tot = {**BASE, "type": 2, "line": 2.5, "outcomeOneName": "Over 2.5", "outcomeTwoName": "Under 2.5"}
    assert market_sides(tot)[:2] == ("ou_2.5", frozenset({OVER}))
    assert market_sides({**tot, "line": 3.0}) is None         # línea entera: puede haber devolución
    assert market_sides({**tot, "type": 2, "line": 2.5})[3] == frozenset({UNDER})


def test_book_walk_gets_worse_with_size():
    m = {**BASE, "type": 1, "outcomeOneName": "Fulham", "outcomeTwoName": "Not Fulham"}
    q = build_quotes(m, {"outcomeOne": [lvl(50, 10), lvl(55, 1000)], "outcomeTwo": []}, 0.0)[0]
    assert effective_odds(q, 5) == pytest.approx(q.odds, rel=1e-6)
    assert effective_odds(q, 100) < q.odds
