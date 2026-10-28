"""Parser del feed de 1xBet con muestras reales (oct-2026)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from surebet.matching import MatchConfig, match_events
from surebet.models import AWAY, DRAW, HOME, SourceEvent
from surebet.sources.onexbet import parse_event
from surebet.sports import esports_family, poly_family_from_tags

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "onexbet_sample.json").read_text(encoding="utf-8"))


def test_football_1x2_and_double_chance():
    ev = parse_event(SAMPLE["football"], 0.0, live=False)
    assert ev.sport == "soccer" and ev.three_way and not ev.live
    covers = {q.covers for q in ev.quotes}
    assert {frozenset({HOME}), frozenset({DRAW}), frozenset({AWAY})} <= covers
    assert frozenset({DRAW, AWAY}) in covers  # X2 sirve contra el SÍ del local en Polymarket
    assert all(q.source == "1xbet" and q.odds > 1 for q in ev.quotes)
    assert 1.0 < sum(1 / q.odds for q in ev.quotes if len(q.covers) == 1) < 1.15


def test_basketball_regulation_without_draw_is_not_a_winner_market():
    # G=1 en básquet es el tiempo reglamentario: con el empate bloqueado NO es "ganador" (falso arbitraje)
    raw = {"SI": 3, "O1": "Detroit Pistons", "O2": "Washington Wizards", "L": "NBA", "S": 2_000_000_000, "I": 1,
           "E": [{"G": 1, "T": 1, "C": 1.55}, {"G": 1, "T": 2, "C": 15.0, "B": True}, {"G": 1, "T": 3, "C": 2.75}]}
    assert parse_event(raw, 0.0, live=False) is None
    raw["E"].append({"G": 101, "T": 401, "C": 1.5})
    raw["E"].append({"G": 101, "T": 402, "C": 2.6})
    ev = parse_event(raw, 0.0, live=False)
    assert ev is not None and {q.odds for q in ev.quotes} == {1.5, 2.6}


def test_slim_raw_keeps_what_the_parser_needs():
    from surebet.sources.onexbet import slim_raw
    for name in ("football", "basketball"):
        full = parse_event(SAMPLE[name], 0.0, live=False)
        slim = parse_event(slim_raw(SAMPLE[name]), 0.0, live=False)
        assert [(q.covers, q.odds, q.market, q.label) for q in slim.quotes] == \
               [(q.covers, q.odds, q.market, q.label) for q in full.quotes]
        assert (slim.home, slim.away, slim.start, slim.url) == (full.home, full.away, full.start, full.url)


def test_basketball_uses_winner_including_overtime():
    ev = parse_event(SAMPLE["basketball"], 0.0, live=False)
    assert ev.sport == "basketball" and not ev.three_way
    assert {q.covers for q in ev.quotes} == {frozenset({HOME}), frozenset({AWAY})}


def test_tennis_two_way():
    ev = parse_event(SAMPLE["tennis"], 0.0, live=False)
    assert ev.sport == "tennis" and not ev.three_way and len(ev.quotes) == 2


def test_live_has_score():
    ev = parse_event(SAMPLE["live_football"], 0.0, live=True)
    assert ev.live and "-" in ev.score


def test_handicap_lines_are_ignored():
    raw = json.loads(json.dumps(SAMPLE["football"]))
    raw["E"] = [x for x in raw["E"] if "P" in x]  # solo líneas con hándicap/total
    assert parse_event(raw, 0.0, live=False) is None


def test_esports_split_by_game():
    assert poly_family_from_tags(["64", "100780"], "cs2") == "esports:cs2"
    assert esports_family(name="Rainbow Six. Brazil League") == "esports:r6"
    assert esports_family(name="CS 2. ANZC Locals Brisbane") == "esports:cs2"
    assert esports_family(name="rainbow-six-siege FURIA vs LOS") == "esports:r6"
    assert esports_family(name="League of Legends. Wild Rift") == "esports:wildrift"


def test_same_orgs_in_different_games_do_not_match():
    t = datetime(2026, 10, 9, 20, tzinfo=timezone.utc)
    r6 = SourceEvent(feed="polymarket", event_id="1", sport="esports:r6", home="FURIA Esports", away="LOS",
                     start=t, three_way=False)
    cs = SourceEvent(feed="onexbet", event_id="2", sport="esports:cs2", home="FURIA", away="LOS",
                     start=t + timedelta(minutes=30), three_way=False)
    assert len(match_events({"polymarket": [r6], "onexbet": [cs]}, MatchConfig())) == 2


def test_totals_and_first_half():
    from surebet.models import OVER, UNDER
    from surebet.sources.onexbet import parse_first_half, totals_quotes
    rows = [{"G": 17, "T": 9, "P": 2.5, "C": 1.9}, {"G": 17, "T": 10, "P": 2.5, "C": 1.95},
            {"G": 17, "T": 9, "P": 3.0, "C": 2.2}, {"G": 17, "T": 10, "P": 3.0, "C": 1.7},   # entera: fuera
            {"G": 17, "T": 9, "P": 3.5, "C": 2.9}]                                            # sin "menos": fuera
    qs = totals_quotes(rows, "ou", "", "", 0.0)
    assert {(q.market, frozenset(q.covers)) for q in qs} == {("ou_2.5", frozenset({OVER})), ("ou_2.5", frozenset({UNDER}))}
    parent = parse_event(SAMPLE["football"], 0.0, live=False)
    sub = {"PN": "1st half", "TG": "", "GE": [
        {"G": 1, "E": [[{"T": 1, "C": 3.3}], [{"T": 2, "C": 2.4}], [{"T": 3, "C": 2.7}]]},
        {"G": 8, "E": [[{"T": 4, "C": 1.41}], [{"T": 5, "C": 1.49}], [{"T": 6, "C": 1.28}]]},
        {"G": 17, "E": [[{"T": 9, "P": 0.5, "C": 1.23}], [{"T": 10, "P": 0.5, "C": 3.84}]]}]}
    h1 = parse_first_half(sub, parent, 0.0)
    markets = {q.market for q in h1}
    assert markets == {"1x2_h1", "ou_h1_0.5"}
    assert sum(1 for q in h1 if q.market == "1x2_h1") == 6   # 1, X, 2 y las tres dobles oportunidades
    assert parse_first_half({**sub, "TG": "Corners"}, parent, 0.0) == []   # sub-partido de córners: no


def test_hot_games_reload_args(tmp_path):
    from surebet.sources.onexbet import OneXBetSource
    s = OneXBetSource("https://1xbet-col.com", 285, tmp_path, [1])
    s.leagues = {"11": {"sport": 1}, "22": {"sport": 3}}
    assert s._reload_args("top:1") == (1, None)
    assert s._reload_args("champs:11,22") == (1, ["11", "22"])
    assert s._reload_args("explore:11") is None and s._reload_args("champs:99") is None
    assert s.hot == set()


def test_polling_interval_depends_on_how_soon_the_match_starts(tmp_path):
    import time as _t
    from surebet.sources.onexbet import OneXBetSource
    s = OneXBetSource("https://1xbet-col.com", 285, tmp_path, [1], prematch_interval_s=120, live_enabled=False)
    now = _t.time()
    s._line["near"] = (now, [{"S": now + 3600}])
    s._line["mid"] = (now, [{"S": now + 4 * 3600}, {"S": now + 20 * 3600}])
    s._line["far"] = (now, [{"S": now + 12 * 3600}])
    s._line["old"] = (now, [{"S": now - 600}])
    assert s._interval("near", now) == 120 and s._interval("mid", now) == 300 and s._interval("far", now) == 900
    assert s._interval("old", now) == 120 and s._interval("nuevo", now) == 120 and s.live_enabled is False


def test_short_1xbet_hiccups_do_not_pause_nor_notify(tmp_path):
    import asyncio
    import time
    import httpx
    from surebet.sources import onexbet as ox
    src = ox.OneXBetSource("https://x.test", 285, tmp_path, [1])
    src.requests = 0

    class Fail:
        async def get(self, *a, **k):
            raise httpx.ConnectError("down")

    class Ok:
        async def get(self, *a, **k):
            return httpx.Response(200, json={"Value": []})

    async def go(client, n):
        for _ in range(n):
            await src._raw_get(client, "https://x.test/a")
    ox.REQUEST_GAP_S = 0
    asyncio.run(go(Fail(), ox.FAIL_LIMIT - 1))
    assert src._paused_until == 0                                     # 7 fallos seguidos: todavía no pausa
    asyncio.run(go(Ok(), 1))
    assert src._fails_in_row == 0 and src.notices == []              # se recuperó solo: ni aviso de caída ni de vuelta
    asyncio.run(go(Fail(), ox.FAIL_LIMIT))
    assert src._paused_until > time.time() and src._down_since and src.notices == []   # pausa corta, sin avisar todavía
    src._down_since -= ox.NOTIFY_AFTER_S + 5                          # ya lleva más de 15 min caída
    src._paused_until = 0
    asyncio.run(go(Fail(), 1))
    assert len(src.notices) == 1 and "min sin responder" in src.notices[0]
    asyncio.run(go(Fail(), 3))
    assert len(src.notices) == 1                                      # un solo aviso por caída
    src._paused_until = 0
    asyncio.run(go(Ok(), 1))
    assert len(src.notices) == 2 and "volvió" in src.notices[1]
