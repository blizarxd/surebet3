"""Prueba con orden límite en Polymarket: se llena si el vendedor baja a nuestro precio; si no, se cubre."""

import time
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


def value_setup():
    from surebet.arb import find_value_bets
    from surebet.models import DRAW
    from test_paper import make_event, poly_q
    poly = [poly_q({o}, p, f"t_{o.lower()}") for o, p in ((HOME, 0.50), (DRAW, 0.28), (AWAY, 0.22))]
    # el "NO" del local (cubre empate o visitante): la red de seguridad de una apuesta al local
    poly.append(replace(poly_q({DRAW, AWAY}, 0.52, "t_no_h"), bid=0.49))
    ev = make_event(poly, [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=2.08,
                                 label="Arsenal", market="1x2")])
    return [v for v in find_value_bets(ev, ArbConfig(), min_ev_pct=2.0) if v.quote.source == "1xbet"]


def test_value_net_locks_when_price_comes_to_us(tmp_path):
    from surebet.makersim import value_hedge
    from surebet.safety import golden_value
    vals = value_setup()
    h = value_hedge(vals[0], 100)
    assert h and h["token"] == "t_no_h" and h["limit"] == 0.50       # 1/1.01 − 1/2.08 = 0.509 → 50¢
    sim = MakerSimulator(tmp_path / "m.db")
    sim.step_value(vals, golden_value)
    no_h = next(q for q in vals[0].event.quotes_for("1x2") if q.token_id == "t_no_h")
    sim.step([], {"t_no_h": replace(no_h, asks=(BookLevel(0.50, 500),))}, lambda o: True)
    status, net = sim.conn.execute("SELECT status, net_pct FROM maker_sim WHERE mode='value_net'").fetchone()
    assert status == "locked" and net >= 1.0
    assert summary(sim.conn)["value_net"]["locked"] == 1


def test_value_net_kept_as_value_at_kickoff(tmp_path):
    from surebet.safety import golden_value
    vals = value_setup()
    sim = MakerSimulator(tmp_path / "m.db")
    sim.step_value(vals, golden_value)
    sim.conn.execute("UPDATE maker_sim SET start_ts = ?", (time.time() + 60,))
    sim.step([], {}, lambda o: True)
    assert sim.conn.execute("SELECT status FROM maker_sim").fetchone()[0] == "kept"


def setup_auto():
    """Polymarket + SX Bet: el automático usa límite en Polymarket y precio real de SX al llenarse."""
    p = Quote(source="polymarket", feed="polymarket", covers=frozenset({HOME}), odds=poly_odds(0.50, 0.05), label="A",
              asks=(BookLevel(0.50, 10000),), fee_rate=0.05, min_stake=2.5, market="ml", token_id="tok", bid=0.46)
    x = Quote(source="sxbet", feed="sxbet", covers=frozenset({AWAY}), odds=2.10, label="B", market="ml",
              ref="0xhash|2", max_stake=5000, min_stake=1.0, asks=(BookLevel(1 / 2.10, 5000 * 2.10),))
    pe = SourceEvent(feed="polymarket", event_id="P", sport="soccer", home="A", away="B", start=START,
                     three_way=False, quotes=[p])
    xe = SourceEvent(feed="sxbet", event_id="X", sport="soccer", home="A", away="B", start=START,
                     three_way=False, quotes=[x])
    m = MatchedEvent(anchor=pe)
    m.members = [(pe, False), (xe, False)]
    return find_opportunities(m, ArbConfig()), p, x


def run_auto(tmp_path, sx_odds):
    import asyncio

    import httpx
    from surebet.makersim import AUTO_HOUSES, POLY_FIRST
    sim = MakerSimulator(tmp_path / "m.db")
    opps, p, x = setup_auto()
    assert opps, "debe haber surebet Polymarket + SX"
    kw = dict(track="auto", book_sources=AUTO_HOUSES, modes=(POLY_FIRST,))
    sim.step(opps, {"tok": p}, lambda o: True, {book_ref(x): x}, **kw)
    sim.step([], {"tok": replace(p, asks=(BookLevel(0.47, 500),), bid=0.44)}, lambda o: True, {}, **kw)
    assert sim.conn.execute("SELECT status FROM maker_sim").fetchone()[0] == "filling"

    def handler(r):   # SX: libro de ahora
        pct = int(round(100 * 10 ** 18 / sx_odds)) * 10 ** 18 // 100
        return httpx.Response(200, json={"status": "success", "data": {"outcomeTwo": [
            {"percentageOdds": str(int(1 / sx_odds * 10 ** 20)), "size": str(5000 * 10 ** 6)}], "outcomeOne": []}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await sim.finish_auto(c)
    asyncio.run(go())
    return sim.conn.execute("SELECT status, pnl, track, taker_net_pct FROM maker_sim").fetchone()


def test_auto_maker_covers_with_real_price_of_the_other_house(tmp_path):
    status, pnl, track, taker = run_auto(tmp_path, 2.10)
    assert status == "covered" and pnl > 0 and track == "auto"


def test_auto_maker_exposed_when_other_house_price_dropped(tmp_path):
    status, pnl, _, _ = run_auto(tmp_path, 1.60)          # SX ya no paga: no se puede cubrir con ganancia
    assert status == "exposed" and pnl < 0
