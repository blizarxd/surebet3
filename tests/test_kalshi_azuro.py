"""Kalshi y Azuro: lectura de partidos, libros/cuotas, emparejado de resultados y liquidación oficial."""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from surebet.models import AWAY, DRAW, HOME
from surebet.paper import PaperBook, PaperConfig
from surebet.pricing import effective_odds, poly_odds
from surebet.sources.azuro import AzuroSource, main_condition, outcome_map
from surebet.sources.kalshi import asks_from_bids, build_quotes, parse_event, series_family


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kalshi_event(series="KXNBAGAME", ticker="KXNBAGAME-26OCT12BKNWAS", codes=("BKN", "WAS"), tie=False, end_in_h=5):
    end = iso(time.time() + end_in_h * 3600)
    names = {"BKN": "Brooklyn", "WAS": "Washington", "COV": "Coventry", "NEW": "Newcastle", "TIE": "Tie"}
    mk = [{"ticker": f"{ticker}-{c}", "yes_sub_title": names[c], "status": "active", "occurrence_datetime": end}
          for c in (*codes, *(("TIE",) if tie else ()))]
    return {"event_ticker": ticker, "series_ticker": series, "category": "Sports",
            "product_metadata": {"competition": "X", "competition_scope": "Game"}, "markets": list(reversed(mk))}


def test_kalshi_event_names_order_and_start():
    ev = parse_event(kalshi_event(), time.time(), 24)
    assert ev["sport"] == "basketball" and ev["home"] == "Brooklyn Nets" and ev["away"] == "Washington Wizards"
    # Kalshi publica el final esperado: el inicio es 3 h antes
    assert abs(ev["start"].timestamp() - (time.time() + 2 * 3600)) < 5
    soccer = parse_event(kalshi_event("KXEPLGAME", "KXEPLGAME-26OCT12COVNEW", ("COV", "NEW"), tie=True), time.time(), 24)
    assert soccer["three_way"] and soccer["sport"] == "soccer" and soccer["home"] == "Coventry"
    assert [m["outcome"] for m in soccer["markets"]] == [HOME, AWAY, DRAW]
    # hándicaps, totales y partidos ya empezados no se usan
    assert series_family("KXATPGAMESPREAD") is None and series_family("KXATPMATCH") == "tennis"
    assert parse_event(kalshi_event(end_in_h=1), time.time(), 24) is None


def test_kalshi_quotes_from_opposite_bids_with_fee():
    lv = asks_from_bids([["0.4500", "100"], ["0.4700", "50"]])
    assert lv[0].price == pytest.approx(0.53) and lv[0].size == 50          # SÍ = 1 − mejor NO
    ev = parse_event(kalshi_event(), time.time(), 24)
    books = {ev["markets"][0]["ticker"]: (time.time(), {"yes_dollars": [["0.40", "500"]],
                                                         "no_dollars": [["0.55", "300"]]})}
    qs = {q.label: q for q in build_quotes(ev, books)}
    yes, no = qs["Brooklyn Nets (SI)"], qs["Brooklyn Nets (NO)"]
    assert yes.covers == frozenset({HOME}) and no.covers == frozenset({AWAY})
    assert yes.odds == pytest.approx(poly_odds(0.45, 0.07)) and no.odds == pytest.approx(poly_odds(0.60, 0.07))
    assert effective_odds(yes, 1000) is None and effective_odds(yes, 100) == pytest.approx(yes.odds)  # libro: 300×0,45


AZ_COND = {"conditionId": "c9", "state": "Active", "hidden": False, "title": "Match Result", "outcomes": [
    {"outcomeId": "1", "title": "Draw", "odds": "3.5", "state": "Active"},
    {"outcomeId": "2", "title": "Away FC", "odds": "2.9", "state": "Active"},
    {"outcomeId": "3", "title": "Home FC", "odds": "2.4", "state": "Active"}]}


def test_azuro_main_market_outcomes_and_limits():
    winner = {**AZ_COND, "title": "Match Winner", "outcomes": AZ_COND["outcomes"][1:]}
    assert main_condition([AZ_COND], "soccer")[0] == "1x2" and main_condition([winner], "tennis")[0] == "ml"
    assert main_condition([{**AZ_COND, "state": "Stopped"}], "soccer") is None
    assert outcome_map(AZ_COND, "Home FC", "Away FC") == {"1": DRAW, "2": AWAY, "3": HOME}
    src = AzuroSource()
    src._games["g1"] = {"id": "g1", "sport": "soccer", "start": datetime.now(timezone.utc) + timedelta(hours=3),
                        "home": "Home FC", "away": "Away FC", "league": "", "slug": ""}
    src._conds["g1"] = (time.time(), "1x2", AZ_COND)
    assert src.quotes_for("g1") == []                        # sin límite conocido: no se usa
    for oid, lim in (("1", 20.0), ("2", 50.0), ("3", 80.0)):
        src._limits[("c9", oid)] = (time.time(), lim)
    qs = {q.label: q for q in src.quotes_for("g1")}
    assert qs["Home FC"].covers == frozenset({HOME}) and qs["Home FC"].max_stake == 80.0
    assert effective_odds(qs["Empate"], 25) is None          # pasa del máximo de Azuro
    assert src.resolution_for("g1", "1x2")[HOME] == ("azuro:c9", "3")


@pytest.mark.parametrize("handler_result,expected", [
    ({"kalshi": "yes", "azuro": ("Resolved", ["9"])}, ("won", "H")),
    ({"kalshi": "no", "azuro": ("Resolved", ["7"])}, ("won", "A")),
])
def test_settle_with_kalshi_and_azuro_results(tmp_path, handler_result, expected):
    pb = PaperBook(tmp_path / "p.db", PaperConfig())
    legs = [{"source": "kalshi", "covers": ["H"], "stake": 50.0, "odds": 2.1},
            {"source": "azuro", "covers": ["A"], "stake": 50.0, "odds": 2.05}]
    pb.conn.execute(
        "INSERT INTO paper_ops (key, kind, created_ts, start_ts, total_stake, legs_json, resolve_json, status, track) "
        "VALUES ('k','arb',?,?,100,?,?, 'open','auto')",
        (time.time() - 4 * 3600, time.time() - 3 * 3600, json.dumps(legs),
         json.dumps({"H": ["kalshi:KX-H", "yes"], "A": ["azuro:c1", "7"]})))
    pb.conn.commit()

    def handler(request):
        if "kalshi" in str(request.url):
            return httpx.Response(200, json={"market": {"status": "finalized", "result": handler_result["kalshi"]}})
        state, won = handler_result["azuro"]
        return httpx.Response(201, json={"conditions": [{"state": state, "wonOutcomeIds": won}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await pb.settle(c, every_s=0)
    assert asyncio.run(run()) == 1
    status, out = pb.conn.execute("SELECT status, outcome FROM paper_ops").fetchone()
    assert (status, out) == expected
