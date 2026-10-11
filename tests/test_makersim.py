"""Prueba con orden límite en Polymarket: se llena si el vendedor baja a nuestro precio; si no, se cubre."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from surebet.arb import ArbConfig, find_opportunities
from surebet.makersim import MakerSimulator, book_ref, summary
from surebet.models import AWAY, HOME, BookLevel, MatchedEvent, Quote, SourceEvent
from surebet.pricing import poly_odds

START = datetime.now(timezone.utc) + timedelta(hours=2)


def setup():
    p = Quote(source="polymarket", feed="polymarket", covers=frozenset({HOME}), odds=poly_odds(0.50, 0.05), label="A",
              asks=(BookLevel(0.50, 10000),), fee_rate=0.05, min_stake=2.5, market="ml", token_id="tok", bid=0.46)
    x = Quote(source="1xbet", feed="onexbet", covers=frozenset({AWAY}), odds=2.10, label="B", market="ml")
    pe = SourceEvent(feed="polymarket", event_id="P", sport="soccer", home="A", away="B", start=START,
                     three_way=False, quotes=[p])
    xe = SourceEvent(feed="onexbet", event_id="X", sport="soccer", home="A", away="B", start=START,
                     three_way=False, quotes=[x])
    m = MatchedEvent(anchor=pe)
    m.members = [(pe, False), (xe, False)]
    return find_opportunities(m, ArbConfig()), p, x


def test_limit_fills_without_fee_and_beats_taker(tmp_path):
    sim = MakerSimulator(tmp_path / "m.db")
    opps, p, _ = setup()
    sim.step(opps, {"tok": p}, lambda o: True)
    limit, = sim.conn.execute("SELECT limit_price FROM maker_sim WHERE mode='book_first'").fetchone()
    assert limit == 0.47                                         # 1¢ sobre el mejor comprador
    sim.step([], {"tok": replace(p, asks=(BookLevel(0.47, 500),))}, lambda o: True)
    status, net, taker = sim.conn.execute("SELECT status, net_pct, taker_net_pct FROM maker_sim WHERE mode='book_first'").fetchone()
    assert status == "filled" and net > taker


def test_hedges_at_market_after_deadline(tmp_path):
    sim = MakerSimulator(tmp_path / "m.db")
    opps, p, _ = setup()
    sim.step(opps, {"tok": p}, lambda o: True)
    sim.conn.execute("UPDATE maker_sim SET created_ts = created_ts - 3600")
    sim.step([], {"tok": replace(p, asks=(BookLevel(0.52, 500),))}, lambda o: True)
    status, = sim.conn.execute("SELECT status FROM maker_sim WHERE mode='book_first'").fetchone()
    assert status == "hedged"


def pf(sim):
    return sim.conn.execute("SELECT status, pnl, book_odds_fill FROM maker_sim WHERE mode='poly_first'").fetchone()


def test_poly_first_fills_and_1xbet_holds(tmp_path):
    sim = MakerSimulator(tmp_path / "m.db")
    opps, p, x = setup()
    sim.step(opps, {"tok": p}, lambda o: True, {book_ref(x): x})
    sim.step([], {"tok": replace(p, asks=(BookLevel(0.47, 500),))}, lambda o: True, {book_ref(x): replace(x, ts=x.ts)})
    status, pnl, odds = pf(sim)
    assert status == "covered" and pnl > 0 and odds == 2.10


def test_poly_first_exposed_when_1xbet_drops(tmp_path):
    sim = MakerSimulator(tmp_path / "m.db")
    opps, p, x = setup()
    sim.step(opps, {"tok": p}, lambda o: True, {book_ref(x): x})
    sim.step([], {"tok": replace(p, asks=(BookLevel(0.47, 500),), bid=0.44)}, lambda o: True,
             {book_ref(x): replace(x, odds=1.80)})
    status, pnl, _ = pf(sim)
    assert status == "exposed" and pnl < 0


def test_poly_first_cancelled_without_fill(tmp_path):
    sim = MakerSimulator(tmp_path / "m.db")
    opps, p, x = setup()
    sim.step(opps, {"tok": p}, lambda o: True, {book_ref(x): x})
    sim.conn.execute("UPDATE maker_sim SET created_ts = created_ts - 3600")
    sim.step([], {"tok": p}, lambda o: True, {book_ref(x): x})
    assert pf(sim)[0] == "cancelled"
    assert summary(sim.conn)["poly_first"]["cancelled"] == 1
