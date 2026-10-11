"""Simulación de recompensas de liquidez de Polymarket: puntaje, parte de la recompensa, llenadas y cierre."""

import asyncio
import time
from datetime import datetime, timezone

import httpx

from surebet.lprewards import RewardsSimulator, q_min, quote_prices, score, summary


def test_score_and_quotes():
    assert abs(score(0.49, 0.50, 2.5) - ((2.5 - 1) / 2.5) ** 2) < 1e-9
    assert score(0.47, 0.50, 2.5) == 0.0                         # a 3¢ no puntúa
    assert q_min(100, 0, 0.5) == 100 / 3                          # un solo lado vale un tercio
    yes, no = quote_prices(0.50, 0.49, 0.51, 2.5, 0.01)
    assert yes == 0.49 and abs(no - 0.49) < 1e-9                  # compra SÍ a 49¢ y NO a 49¢ (= vende SÍ a 51¢)


def make_client(state):
    start = datetime.fromtimestamp(time.time() + 3 * 3600, timezone.utc).isoformat()

    def handler(r):
        if r.url.path == "/sampling-markets":
            return httpx.Response(200, json={"next_cursor": "LTE=", "data": [{
                "condition_id": "c1", "question": "A vs B", "market_slug": "a-b", "game_start_time": start,
                "accepting_orders": True, "closed": False, "minimum_tick_size": 0.01,
                "rewards": {"max_spread": 2.5, "min_size": 50, "rates": [{"rewards_daily_rate": 144}]},
                "tokens": [{"token_id": "y"}, {"token_id": "n"}]}]})
        if r.url.path == "/book":
            return httpx.Response(200, json=state["book"])
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run_step(sim, state):
    async def go():
        async with make_client(state) as c:
            await sim.step(c)
    asyncio.run(go())


def test_opens_earns_and_pairs(tmp_path):
    sim = RewardsSimulator(tmp_path / "lp.db", capital=1000, per_market=100)
    state = {"book": {"bids": [{"price": "0.49", "size": "100"}], "asks": [{"price": "0.51", "size": "100"}]}}
    run_step(sim, state)
    assert sim.conn.execute("SELECT COUNT(*) FROM lp_markets WHERE status='active'").fetchone()[0] == 1
    sim.conn.execute("UPDATE lp_markets SET last_step = last_step - 60")
    sim._cand_ts = time.time()
    run_step(sim, state)                                          # un minuto con las dos órdenes: suma recompensa
    reward, = sim.conn.execute("SELECT reward_usd FROM lp_markets").fetchone()
    assert reward > 0
    # alguien vende hasta nuestra compra del SÍ (49¢) y otro compra hasta nuestra venta (51¢): se llenan las dos
    state["book"] = {"bids": [{"price": "0.51", "size": "100"}], "asks": [{"price": "0.49", "size": "100"}]}
    run_step(sim, state)
    st, pnl = sim.conn.execute("SELECT status, inv_pnl_usd FROM lp_markets").fetchone()
    assert st == "closed" and pnl > 0                             # SÍ + NO por menos de $1: ganancia fija
    s = summary(sim.conn)
    assert s["pairs"] == 1 and s["net"] > 0 and s["daily"]


def test_only_matches():
    from surebet.lprewards import is_match
    assert is_match("Bears vs. Packers") and is_match("Bears vs. Packers: O/U 46.5") and is_match("Spread: Jets (-2.5)")
    assert is_match("Will Real Madrid vs Villarreal end in a draw?")
    assert not is_match("Will the highest temperature in Denver be between 60-61°F?")
