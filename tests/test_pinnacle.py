"""Pinnacle en sombra: probabilidad sin margen, EV con las dos referencias, distancia a 1xBet y resumen con CLV."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from surebet.arb import ValueBet
from surebet.models import AWAY, DRAW, HOME, MatchedEvent, Quote, SourceEvent
from surebet.pinnacle import MAX_PIN_AGE_S, RefShadow, pinnacle_view, shadow_row, summary

START = datetime.now(timezone.utc) + timedelta(hours=3)


def event(pin_odds=(2.00, 3.40, 3.60), ts=None):
    ts = time.time() if ts is None else ts
    pin = SourceEvent(feed="oddspapi", event_id="PIN", sport="soccer", home="A", away="B", start=START, three_way=True,
                      quotes=[Quote(source="pinnacle", feed="oddspapi", covers=frozenset({o}), odds=x, label=o,
                                    market="1x2", ts=ts) for o, x in zip((HOME, DRAW, AWAY), pin_odds)])
    book = SourceEvent(feed="onexbet", event_id="X", sport="soccer", home="A", away="B", start=START, three_way=True,
                       quotes=[Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=2.20, label="A",
                                     market="1x2")])
    m = MatchedEvent(anchor=book)
    m.members = [(book, False), (pin, False)]
    return m


def value(m, fair=0.50):
    return ValueBet(event=m, quote=next(q for q in m.quotes_for("1x2") if q.source == "1xbet"),
                    fair_prob=fair, poly_tokens={}, market="1x2")


def test_pinnacle_probs_remove_margin_proportionally():
    view = pinnacle_view(event(), "1x2")
    assert sum(view["probs"].values()) == pytest.approx(1.0)
    assert view["margin_pct"] == pytest.approx(100 * (1 / 2.0 + 1 / 3.4 + 1 / 3.6 - 1))
    assert view["probs"][HOME] == pytest.approx((1 / 2.0) / (1 / 2.0 + 1 / 3.4 + 1 / 3.6))


def test_stale_or_incomplete_pinnacle_is_ignored():
    assert pinnacle_view(event(ts=time.time() - MAX_PIN_AGE_S - 60), "1x2") is None
    m = event()
    m.members[1][0].quotes.pop()                       # falta un resultado: no es comparable
    assert pinnacle_view(m, "1x2") is None


def test_shadow_row_two_references_and_distance():
    m = event()
    r = shadow_row(value(m, fair=0.50))
    assert r["ev_pol"] == pytest.approx(100 * (2.20 * 0.50 - 1))                 # 10 % con Polymarket
    fair_pin = (1 / 2.0) / (1 / 2.0 + 1 / 3.4 + 1 / 3.6)
    assert r["fair_pin"] == pytest.approx(fair_pin) and r["ev_pin"] == pytest.approx(100 * (2.20 * fair_pin - 1))
    assert r["divergence"] == pytest.approx(r["ev_pin"] - r["ev_pol"])
    assert r["dist_pin_pct"] == pytest.approx(100 * (2.20 / (1 / (1 / 2.0)) - 1), abs=1e-6)   # 1xBet paga 10 % más


def test_record_once_and_summary_splits_clv(tmp_path):
    import sqlite3
    db = tmp_path / "r.db"
    rs = RefShadow(db)
    m = event()
    v = value(m, fair=0.50)
    assert rs.record([v]) == 1 and rs.record([v]) == 0                     # una sola vez por señal
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE value_bets (key TEXT PRIMARY KEY, source TEXT, close_prob REAL, clv_pct REAL)")
    c.execute("INSERT INTO value_bets VALUES (?, '1xbet', 0.52, 4.0)", (v.key,))
    c.commit()
    s = summary(c)
    assert s["n"] == 1 and s["closed"] == 1 and s["err_pol"] == pytest.approx(2.0)
    assert s["agree"]["n"] == 1 and s["agree"]["clv"] == 4.0 and s["disagree"]["n"] == 0
    assert s["apt"] == 0 and s["avg_dist"] == pytest.approx(10.0)          # a 10 % de Pinnacle: Overtime no serviría


def test_non_1xbet_signals_are_not_recorded(tmp_path):
    rs = RefShadow(tmp_path / "r.db")
    m = event()
    q = next(q for q in m.quotes_for("1x2") if q.source == "1xbet")
    q.source = "sxbet"
    assert rs.record([ValueBet(event=m, quote=q, fair_prob=0.5, poly_tokens={}, market="1x2")]) == 0
