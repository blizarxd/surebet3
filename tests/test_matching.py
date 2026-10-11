from datetime import datetime, timedelta, timezone

from surebet.matching import MatchConfig, match_events, name_score, normalize
from surebet.models import SourceEvent

T0 = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
MCFG = MatchConfig()


def ev(feed, home, away, sport="soccer", start=T0, eid="1"):
    return SourceEvent(feed=feed, event_id=eid, sport=sport, home=home, away=away, start=start, three_way=True)


def test_normalize_strips_noise_years_accents():
    assert normalize("Bologna FC 1909") == "bologna"
    assert normalize("Atlético de Madrid") == normalize("Atletico Madrid")
    assert normalize("FC Internazionale Milano") == normalize("Inter Milan")


def test_subset_names_match():
    assert name_score("Borussia Dortmund", "Dortmund") >= 85
    assert name_score("Levante UD", "Levante") >= 85
    assert name_score("Lakers", "Los Angeles Lakers") >= 85


def test_different_clubs_same_city_do_not_match():
    assert name_score("Manchester United", "Manchester City") < 85
    assert name_score("Real Madrid", "Real Madrid B") < 85
    assert name_score("Brazil", "Brazil (W)") < 85


def test_tennis_initials():
    assert name_score("B. Shelton", "Ben Shelton", "tennis") >= 85
    assert name_score("I. Dodig", "Matej Dodig", "tennis") < 85


def test_match_events_links_and_detects_swap():
    poly = [ev("polymarket", "Levante UD", "Athletic Club")]
    odds = [ev("oddsapi", "Athletic Bilbao", "Levante", start=T0 + timedelta(minutes=5))]
    clusters = match_events({"polymarket": poly, "oddsapi": odds}, MCFG)
    assert len(clusters) == 1
    (_, swapped) = clusters[0].members[1]
    assert swapped is True


def test_match_events_respects_time_window_and_sport():
    poly = [ev("polymarket", "Levante", "Athletic Club")]
    late = [ev("oddsapi", "Levante", "Athletic Club", start=T0 + timedelta(hours=5))]
    other_sport = [ev("oddsapi", "Levante", "Athletic Club", sport="basketball")]
    assert len(match_events({"polymarket": poly, "oddsapi": late}, MCFG)) == 2
    assert len(match_events({"polymarket": poly, "oddsapi": other_sport}, MCFG)) == 2


def test_derby_not_cross_matched():
    poly = [ev("polymarket", "Manchester United", "Manchester City", eid="a"),
            ev("polymarket", "Liverpool", "Everton", eid="b")]
    odds = [ev("oddsapi", "Man Utd", "Man City", eid="x")]
    clusters = match_events({"polymarket": poly, "oddsapi": odds}, MCFG)
    derby = next(c for c in clusters if c.anchor.event_id == "a")
    assert len(derby.members) == 2 and derby.members[1][1] is False
