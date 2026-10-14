"""Simulación de ejecución automática: arma las órdenes, espera la latencia y verifica precios."""

import json
from datetime import datetime, timedelta, timezone

from surebet.arb import ArbConfig, find_opportunities
from surebet.execsim import ExecSimulator
from surebet.models import AWAY, HOME, BookLevel, MatchedEvent, Quote, SourceEvent
from surebet.pricing import poly_odds

START = datetime.now(timezone.utc) + timedelta(hours=2)


def opp(cb_odds):
    p = Quote(source="polymarket", feed="polymarket", covers=frozenset({HOME}), odds=poly_odds(0.50, 0.05), label="A",
              asks=(BookLevel(0.50, 10000),), fee_rate=0.05, min_stake=2.5, market="ml", token_id="tok")
    c = Quote(source="cloudbet", feed="cloudbet", covers=frozenset({AWAY}), odds=cb_odds, label="B", market="ml",
              ref="123|tennis.winner/away")
    pe = SourceEvent(feed="polymarket", event_id="P", sport="soccer", home="A", away="B", start=START,
                     three_way=False, quotes=[p])
    ce = SourceEvent(feed="cloudbet", event_id="C", sport="soccer", home="A", away="B", start=START,
                     three_way=False, quotes=[c])
    m = MatchedEvent(anchor=pe)
    m.members = [(pe, False), (ce, False)]
    return find_opportunities(m, ArbConfig())


def age(sim):
    sim.conn.execute("UPDATE exec_sim SET created_ts = created_ts - 10")


def test_plan_then_fill(tmp_path):
    sim = ExecSimulator(tmp_path / "x.db")
    sim.step(opp(2.20), lambda o: True)
    plan = json.loads(sim.conn.execute("SELECT plan_json FROM exec_sim").fetchone()[0])
    orders = {p["source"]: p["order"] for p in plan}
    assert orders["cloudbet"]["marketUrl"] == "tennis.winner/away" and orders["polymarket"]["tokenID"] == "tok"
    age(sim)
    sim.step(opp(2.20), lambda o: True)
    status, net = sim.conn.execute("SELECT status, net_pct FROM exec_sim").fetchone()
    assert status == "filled" and net > 0


def test_rejected_when_price_moves(tmp_path):
    sim = ExecSimulator(tmp_path / "x.db")
    sim.step(opp(2.20), lambda o: True)
    age(sim)
    sim.step([], lambda o: True)                     # al ejecutar, la surebet ya no está
    status, reason = sim.conn.execute("SELECT status, reason FROM exec_sim").fetchone()
    assert status == "rejected" and "ya no" in reason


def test_stake_up_to_book_capacity_and_half_free_cash(tmp_path):
    from surebet.execsim import ExecSimulator
    sim = ExecSimulator(tmp_path / "e.db", 500, cash_fn=lambda: 600.0)
    assert sim.stake == 500 and sim.cash_fn() * 0.5 == 300.0      # tope: min(500, lo que aguanta, 300)


def test_best_stake_stops_where_thin_book_stops_paying():
    from surebet.arb import _profit, best_stake, size_legs
    # Polymarket: solo 100 shares a 45¢ y después 60¢; la casa paga 2,00. Hasta ~$95 gana 5 %; más allá pierde
    p = Quote(source="polymarket", feed="polymarket", covers=frozenset({HOME}), odds=poly_odds(0.45, 0.0), label="A",
              asks=(BookLevel(0.45, 100), BookLevel(0.60, 10000)), fee_rate=0.0, market="ml", token_id="t")
    c = Quote(source="cloudbet", feed="cloudbet", covers=frozenset({AWAY}), odds=2.0, label="B", market="ml", ref="1|x")
    s = best_stake([p, c], 500)
    assert 80 <= s <= 110
    assert _profit(size_legs([p, c], s)) > max(0.0, _profit(size_legs([p, c], 500)))


def test_book_capacity_is_stored(tmp_path):
    sim = ExecSimulator(tmp_path / "x.db", 500)
    sim.step(opp(2.20), lambda o: True)
    stake, book_max = sim.conn.execute("SELECT stake, book_max FROM exec_sim").fetchone()
    assert book_max and stake <= min(500, book_max) + 0.01


def test_fresh_mode_fills_only_with_real_house_prices(tmp_path):
    """Modo precio real: no se usa lo que vio el escáner; se le pide el libro a cada casa al ejecutar."""
    import asyncio
    import httpx
    filled = []
    for cb_price, expect in ((2.20, "filled"), (1.80, "rejected")):
        sim = ExecSimulator(tmp_path / f"f{cb_price}.db", fresh=True, cloudbet_key="k")
        sim.step(opp(2.20), lambda o: True, lambda o, plan: filled.append(plan))
        age(sim)

        def handler(r, cb_price=cb_price):
            if "clob" in str(r.url):
                return httpx.Response(200, json={"asks": [{"price": "0.50", "size": "10000"}]})
            return httpx.Response(200, json={"markets": {"m": {"submarkets": {"s": {"selections": [
                {"marketUrl": "tennis.winner/away", "status": "SELECTION_ENABLED", "price": str(cb_price),
                 "maxStake": "1000"}]}}}}})

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                await sim.verify(c)
        asyncio.run(run())
        status, reason = sim.conn.execute("SELECT status, reason FROM exec_sim").fetchone()
        assert status == expect, reason
    assert len(filled) == 1 and all(p.get("fill_odds") for p in filled[0])   # se anota con las cuotas frescas
