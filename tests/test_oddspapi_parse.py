import json
from pathlib import Path

from surebet.models import AWAY, DRAW, HOME
from surebet.sources.oddspapi import fixture_to_event, main_markets, parse_book_odds

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "oddspapi_sample.json").read_text(encoding="utf-8"))


def test_main_markets_from_real_catalog():
    mm = main_markets(SAMPLE["markets"])
    assert mm[10] == ("101", {"101": HOME, "102": DRAW, "103": AWAY})
    assert mm[11][0] == "111" and set(mm[11][1].values()) == {HOME, AWAY}


def test_parse_real_stake_row():
    mm = main_markets(SAMPLE["markets"])
    ev = fixture_to_event(SAMPLE["fixture"], "soccer", True)
    qs = parse_book_odds(SAMPLE["row"], "stake", mm[10], 0.0, {HOME: ev.home, DRAW: "Empate", AWAY: ev.away})
    assert {q.covers for q in qs} == {frozenset({HOME}), frozenset({DRAW}), frozenset({AWAY})}
    assert all(q.source == "stake" and q.feed == "oddspapi" and q.odds > 1 for q in qs)
    assert {q.label for q in qs} == {ev.home, "Empate", ev.away}
    overround = sum(1 / q.odds for q in qs)
    assert 1.0 < overround < 1.2


def test_suspended_book_is_ignored():
    mm = main_markets(SAMPLE["markets"])
    row = json.loads(json.dumps(SAMPLE["row"]))
    row["bookmakerOdds"]["stake"]["suspended"] = True
    assert parse_book_odds(row, "stake", mm[10], 0.0, {HOME: "a", DRAW: "x", AWAY: "b"}) == []
