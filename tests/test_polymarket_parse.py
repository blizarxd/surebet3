"""Parser de Polymarket con la forma real de la Gamma API (oct-2026)."""

import json

from surebet.models import AWAY, DRAW, HOME
from surebet.sources.polymarket import PolymarketSource, _title_teams

FEE = {"feesEnabled": True, "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True},
       "secondsDelay": 1, "orderMinSize": 5, "acceptingOrders": True, "enableOrderBook": True}


def yes_no(title, group, toks):
    return {"sportsMarketType": "moneyline", "question": title, "groupItemTitle": group,
            "outcomes": '["Yes", "No"]', "clobTokenIds": json.dumps(toks), **FEE}


SOCCER = {
    "id": "1", "title": "Levante UD vs. Athletic Club", "slug": "lal-lev-bil", "live": True,
    "score": "0-1", "startTime": "2026-10-09T18:00:00Z", "series": [{"id": "10193"}],
    "markets": [
        yes_no("Will Athletic Club win on 2026-10-09?", "Athletic Club", ["a_yes", "a_no"]),
        yes_no("Will Levante UD vs. Athletic Club end in a draw?", "Draw (Levante UD vs. Athletic Club)", ["d_yes", "d_no"]),
        yes_no("Will Levante UD win on 2026-10-09?", "Levante UD", ["h_yes", "h_no"]),
        {"sportsMarketType": "totals", "outcomes": '["Over", "Under"]', "clobTokenIds": '["o","u"]', **FEE},
    ],
}

TENNIS = {
    "id": "2", "title": "Braga: Matej Dodig vs Lukas Neumayer", "slug": "atp-x", "live": False,
    "startTime": "2026-10-10T09:00:00Z", "series": [{"id": "10365"}],
    "markets": [{"sportsMarketType": "moneyline", "outcomes": '["Matej Dodig", "Lukas Neumayer"]',
                 "clobTokenIds": '["t1", "t2"]', **FEE}],
}


def src():
    s = PolymarketSource()
    s._family_by_series = {"10193": "soccer", "10365": "tennis"}
    return s


def test_soccer_is_three_way_with_yes_no_complements():
    p = src()._parse(SOCCER)
    assert p.event.three_way and p.event.sport == "soccer"
    assert p.event.home == "Levante UD" and p.event.away == "Athletic Club"  # orden del título
    covers = {o.token_id: o.covers for o in p.outcomes}
    assert covers["h_yes"] == {HOME}
    assert covers["h_no"] == {DRAW, AWAY}
    assert covers["d_no"] == {HOME, AWAY}
    assert covers["a_yes"] == {AWAY}
    assert all(o.fee_rate == 0.05 and o.delay_s == 1 for o in p.outcomes)


def test_soccer_without_draw_market_is_skipped():
    ev = dict(SOCCER, markets=[m for m in SOCCER["markets"] if "draw" not in m.get("question", "").lower()])
    assert src()._parse(ev) is None


def test_tennis_two_way():
    p = src()._parse(TENNIS)
    assert not p.event.three_way and p.event.sport == "tennis"
    assert [o.covers for o in p.outcomes] == [{HOME}, {AWAY}]


def test_title_teams():
    assert _title_teams("Counter-Strike: MOUZ NXT vs Leo Team (BO3) - ESL Challenger") == ("MOUZ NXT", "Leo Team")
    assert _title_teams("Levante UD vs. Athletic Club") == ("Levante UD", "Athletic Club")


def test_extra_markets_halftime_and_totals():
    from surebet.models import OVER, UNDER
    from surebet.sources.polymarket import parse_extra_markets
    more = {"markets": [
        {"sportsMarketType": "totals", "line": 2.5, "outcomes": '["Over", "Under"]',
         "clobTokenIds": '["o25", "u25"]', "conditionId": "c25", **FEE},
        {"sportsMarketType": "totals", "line": 3.0, "outcomes": '["Over", "Under"]',
         "clobTokenIds": '["o3", "u3"]', "conditionId": "c3", **FEE},   # línea entera: se ignora
        {"sportsMarketType": "first_half_totals", "line": 0.5, "outcomes": '["Over", "Under"]',
         "clobTokenIds": '["oh", "uh"]', "conditionId": "ch", **FEE},
    ]}
    ht = {"markets": [
        {"sportsMarketType": "soccer_halftime_result", "groupItemTitle": t, "outcomes": '["Yes", "No"]',
         "clobTokenIds": json.dumps([f"{k}_y", f"{k}_n"]), "conditionId": f"c{k}", **FEE}
        for k, t in (("h", "Levante UD"), ("d", "Draw"), ("a", "Athletic Club"))
    ]}
    outs = parse_extra_markets(more, "Levante UD", "Athletic Club") + parse_extra_markets(ht, "Levante UD", "Athletic Club")
    by = {(o.market, o.token_id): o.covers for o in outs}
    assert by[("ou_2.5", "o25")] == {OVER} and by[("ou_2.5", "u25")] == {UNDER}
    assert by[("ou_h1_0.5", "oh")] == {OVER}
    assert not any(o.market == "ou_3" for o in outs)
    assert by[("1x2_h1", "h_y")] == {HOME} and by[("1x2_h1", "h_n")] == {DRAW, AWAY}
    assert by[("1x2_h1", "d_y")] == {DRAW} and by[("1x2_h1", "a_y")] == {AWAY}
