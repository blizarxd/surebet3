import time

from surebet.models import AWAY, DRAW, HOME
from surebet.sources.oddsapi import OddsApiSource

RAW = {
    "id": "e1", "sport_key": "soccer_russia_premier_league", "sport_title": "Russia PL",
    "commence_time": "2026-10-09T16:30:00Z", "home_team": "FK Rostov", "away_team": "Akron Tolyatti",
    "bookmakers": [
        {"key": "pinnacle", "last_update": "2026-10-09T16:20:00Z",
         "markets": [{"key": "h2h", "last_update": "2026-10-09T16:20:00Z", "outcomes": [
             {"name": "FK Rostov", "price": 2.12}, {"name": "Akron Tolyatti", "price": 3.6},
             {"name": "Draw", "price": 3.3}]}]},
        {"key": "betfair_ex_eu", "last_update": "2026-10-09T17:25:00Z",
         "markets": [{"key": "h2h", "outcomes": [
             {"name": "FK Rostov", "price": 1.30}, {"name": "Akron Tolyatti", "price": 15.0},
             {"name": "Draw", "price": 4.4}]}]},
        {"key": "dosvias", "markets": [{"key": "h2h", "outcomes": [
             {"name": "FK Rostov", "price": 1.2}, {"name": "Akron Tolyatti", "price": 4.0}]}]},
    ],
}


def test_parse_uses_bookmaker_last_update_and_commission():
    src = OddsApiSource(api_key="x", sports=["upcoming"], commissions={"betfair_ex_eu": 0.05})
    ev = src._parse(RAW, fetched_at=time.time())
    assert ev.three_way and ev.sport == "soccer" and ev.live
    pin = [q for q in ev.quotes if q.source == "pinnacle"]
    # la cuota de Pinnacle es de antes del gol: su edad debe reflejarlo
    assert all(q.age() > 3600 for q in pin)
    assert {q.covers for q in pin} == {frozenset({HOME}), frozenset({DRAW}), frozenset({AWAY})}
    bf = next(q for q in ev.quotes if q.source == "betfair_ex_eu" and q.covers == {AWAY})
    assert abs(bf.odds - (1 + 14.0 * 0.95)) < 1e-9
    # casa con 2 vías en un partido con empate: descartada
    assert not any(q.source == "dosvias" for q in ev.quotes)
