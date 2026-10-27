"""Hora de inicio: la más temprana entre las fuentes (Polymarket estima mal el tenis) y nada de señales a segundos del inicio."""

from datetime import datetime, timedelta, timezone

from surebet.models import MatchedEvent, SourceEvent
from surebet.safety import golden_value, wide_value


def test_event_start_is_the_earliest_source():
    t = datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)
    poly = SourceEvent("polymarket", "1", "tennis", "Mayo", "Barton", t + timedelta(minutes=30), False)
    onex = SourceEvent("onexbet", "2", "tennis", "Mayo", "Barton", t, False)
    ev = MatchedEvent(anchor=poly, members=[(poly, False), (onex, False)])
    assert ev.start == t                                   # 1xBet dice 12:15, Polymarket 12:45: manda la de 1xBet
    assert MatchedEvent(anchor=poly, members=[(poly, False)]).start == t + timedelta(minutes=30)
    nada = SourceEvent("polymarket", "3", "soccer", "A", "B", None, True)
    assert MatchedEvent(anchor=nada, members=[(nada, False)]).start is None


def test_no_value_signal_minutes_before_start():
    assert not golden_value(False, 3.0, 2.2, 2 / 60)       # a 2 min del inicio: no hay tiempo de apostar
    assert golden_value(False, 3.0, 2.2, 10 / 60)
    assert not wide_value(False, 1.7, 2.2, 1 / 60) and wide_value(False, 1.7, 2.2, 0.5)
