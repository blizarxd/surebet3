"""Filtros de riesgo (tenis, cuota errónea, hora distinta), hockey fuera de 1xBet y parser de Cloudbet."""

from datetime import datetime, timedelta, timezone

from surebet.arb import ArbConfig, find_opportunities
from surebet.models import AWAY, DRAW, HOME, OVER, BookLevel, MatchedEvent, Quote, SourceEvent
from surebet.pricing import poly_odds
from surebet.safety import blocked, risk_flags
from surebet.sources.cloudbet import parse_event as cb_parse
from surebet.sources.onexbet import parse_event as xb_parse

START = datetime.now(timezone.utc) + timedelta(hours=2)


def poly(covers, price, fair=None):
    return Quote(source="polymarket", feed="polymarket", covers=frozenset(covers), odds=poly_odds(price, 0.05),
                 label="p", asks=(BookLevel(price, 10000),), fee_rate=0.05, market="ml", fair_prob=fair,
                 near_depth=5000)


def event(sport, poly_quotes, book_quotes, book_start=START):
    p = SourceEvent(feed="polymarket", event_id="P", sport=sport, home="A", away="B", start=START,
                    three_way=False, quotes=poly_quotes)
    b = SourceEvent(feed="onexbet", event_id="X", sport=sport, home="A", away="B", start=book_start,
                    three_way=False, quotes=book_quotes)
    m = MatchedEvent(anchor=p)
    m.members = [(p, False), (b, False)]
    return m


def xb(covers, odds):
    return Quote(source="1xbet", feed="onexbet", covers=frozenset(covers), odds=odds, label="x", market="ml")


def flags_for(ev):
    o = find_opportunities(ev, ArbConfig())[0]
    o.warnings += risk_flags(o)
    return o


def test_tennis_needs_bigger_margin():
    o = flags_for(event("tennis", [poly({HOME}, 0.50)], [xb({AWAY}, 2.10)]))   # ~+1 %
    assert blocked(o) and "tenis" in o.warnings[-1]
    assert not blocked(flags_for(event("soccer", [poly({HOME}, 0.50)], [xb({AWAY}, 2.10)])))


def test_suspicious_generous_odds_are_blocked():
    # Polymarket con libro dice 50/50; 1xBet paga 2,40 al B (+20 % sobre lo justo): huele a error
    ev = event("esports", [poly({HOME}, 0.50, fair=0.5), poly({AWAY}, 0.50, fair=0.5)], [xb({AWAY}, 2.40)])
    assert blocked(flags_for(ev))


def test_different_start_time_is_blocked():
    ev = event("soccer", [poly({HOME}, 0.50)], [xb({AWAY}, 2.10)], book_start=START + timedelta(hours=1))
    assert blocked(flags_for(ev))


def test_1xbet_hockey_winner_with_ot_is_not_used():
    raw = {"SI": 2, "O1": "Sharks", "O2": "Oilers", "L": "NHL", "S": 2_000_000_000, "I": 9,
           "E": [{"G": 101, "T": 401, "C": 2.4}, {"G": 101, "T": 402, "C": 1.6}]}
    assert xb_parse(raw, 0.0, live=False) is None


def test_cloudbet_soccer_1x2_and_totals():
    ev = {"id": 1, "home": {"name": "Arsenal"}, "away": {"name": "Leeds"}, "status": "TRADING",
          "cutoffTime": "2030-01-01T15:00:00Z",
          "markets": {
              "soccer.match_odds": {"submarkets": {"period=ft": {"selections": [
                  {"outcome": "home", "params": "", "price": 1.5, "maxStake": 500, "status": "SELECTION_ENABLED"},
                  {"outcome": "draw", "params": "", "price": 4.2, "maxStake": 500, "status": "SELECTION_ENABLED"},
                  {"outcome": "away", "params": "", "price": 6.0, "maxStake": 500, "status": "SELECTION_ENABLED"}]}}},
              "soccer.total_goals": {"submarkets": {"period=ft": {"selections": [
                  {"outcome": "over", "params": "total=2.5", "price": 1.9, "status": "SELECTION_ENABLED"},
                  {"outcome": "under", "params": "total=2.5", "price": 1.95, "status": "SELECTION_ENABLED"},
                  {"outcome": "over", "params": "total=2", "price": 1.6, "status": "SELECTION_ENABLED"}]}}}}}
    e = cb_parse(ev, "soccer", "Premier League", 0.0)
    assert e.sport == "soccer" and not e.live
    assert {(q.market, next(iter(q.covers))) for q in e.quotes} == {
        ("1x2", HOME), ("1x2", DRAW), ("1x2", AWAY), ("ou_2.5", OVER), ("ou_2.5", "U")} or \
        {q.market for q in e.quotes} == {"1x2", "ou_2.5"}
    assert all(q.source == "cloudbet" for q in e.quotes)
    assert not any(q.market == "ou_2" for q in e.quotes)          # línea entera: fuera


def test_cloudbet_incomplete_winner_is_dropped():
    ev = {"id": 2, "home": {"name": "A"}, "away": {"name": "B"}, "status": "TRADING_LIVE",
          "markets": {"tennis.winner": {"submarkets": {"period=default": {"selections": [
              {"outcome": "home", "price": 1.5, "status": "SELECTION_ENABLED"},
              {"outcome": "away", "price": 2.6, "status": "SELECTION_SUSPENDED"}]}}}}}
    assert cb_parse(ev, "tennis", "ATP", 0.0) is None
