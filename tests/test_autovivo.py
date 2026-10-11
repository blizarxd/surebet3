"""Automático en vivo simulado: tras el retraso relee cada pata en la casa y solo llena si sigue en pie."""

import asyncio
import json
import time

import httpx

from surebet.autovivo import LiveAutoSimulator, fresh_odds, summary
from surebet.paper import PaperBook, PaperConfig


def mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro_fn):
    async def go():
        return await coro_fn()
    return asyncio.run(go())


def test_fresh_odds_each_house():
    def handler(r):
        if r.url.path == "/book":
            return httpx.Response(200, json={"asks": [{"price": "0.50", "size": "1000"}]})
        if r.url.path.endswith("/snapshot"):
            return httpx.Response(200, json={"status": "success", "data": {
                "outcomeOne": [{"percentageOdds": str(int(0.45 * 10 ** 20)), "size": str(500 * 10 ** 6)}], "outcomeTwo": []}})
        if "/events/" in r.url.path:
            return httpx.Response(200, json={"markets": {"m": {"submarkets": {"s": {"selections": [
                {"marketUrl": "x.winner/home", "price": "2.15", "status": "SELECTION_ENABLED", "maxStake": "500"}]}}}}})
        return httpx.Response(404)

    async def go():
        async with mock(handler) as c:
            poly = await fresh_odds(c, {"source": "polymarket", "stake": 50, "token": "t", "fee_rate": 0.0}, "")
            sx = await fresh_odds(c, {"source": "sxbet", "stake": 50, "ref": "0xh|1"}, "")
            cb = await fresh_odds(c, {"source": "cloudbet", "stake": 50, "ref": "9|x.winner/home"}, "k")
            cb_closed = await fresh_odds(c, {"source": "cloudbet", "stake": 50, "ref": "9|x.winner/away"}, "k")
            return poly, sx, cb, cb_closed
    poly, sx, cb, cb_closed = asyncio.run(go())
    assert abs(poly - 2.0) < 1e-9 and 2.18 < sx < 2.22 and cb == 2.15 and cb_closed is None


def _pending(sim, min_odds=(2.0, 2.0)):
    plan = [{"source": "cloudbet", "label": "A", "covers": ["H"], "stake": 50.0, "odds": 2.1, "min_odds": min_odds[0],
             "ref": "9|x.winner/home", "token": "", "fee_rate": 0, "fee_exp": 1, "url": ""},
            {"source": "polymarket", "label": "B", "covers": ["A"], "stake": 50.0, "odds": 2.1, "min_odds": min_odds[1],
             "ref": "", "token": "t", "fee_rate": 0.0, "fee_exp": 1, "url": ""}]
    sim.conn.execute("INSERT INTO exec_live (key, created_ts, title, sport, market, market_name, start_ts, sources, "
                     "margin_pct, stake, plan_json, resolve_json, latency_s, status) VALUES "
                     "('vivo#polymarket:1#ml', ?, 'A vs B', 'esports', 'ml', 'Ganador', ?, 'cloudbet + polymarket', 4, 100, ?, ?, 9, 'pending')",
                     (time.time() - 20, time.time() - 600, json.dumps(plan), json.dumps({"H": ["c", "tH"], "A": ["c", "tA"]})))
    sim.conn.commit()


def check(sim, cb_price, poly_ask):
    def handler(r):
        if r.url.path == "/book":
            return httpx.Response(200, json={"asks": [{"price": str(poly_ask), "size": "1000"}]})
        return httpx.Response(200, json={"markets": {"m": {"submarkets": {"s": {"selections": [
            {"marketUrl": "x.winner/home", "price": str(cb_price), "status": "SELECTION_ENABLED"}]}}}}})

    async def go():
        async with mock(handler) as c:
            await sim.check(c)
    asyncio.run(go())


def test_fills_when_both_legs_still_there(tmp_path):
    PaperBook(tmp_path / "p.db", PaperConfig())
    sim = LiveAutoSimulator(tmp_path / "p.db", "k")
    _pending(sim)
    check(sim, 2.10, 0.47)
    status, net = sim.conn.execute("SELECT status, net_pct FROM exec_live").fetchone()
    assert status == "filled" and net > 0
    assert sim.conn.execute("SELECT COUNT(*) FROM paper_ops WHERE track='autovivo'").fetchone()[0] == 1
    assert summary(sim.conn)["filled"] == 1


def test_rejects_when_odds_dropped_during_delay(tmp_path):
    PaperBook(tmp_path / "p.db", PaperConfig())
    sim = LiveAutoSimulator(tmp_path / "p.db", "k")
    _pending(sim)
    check(sim, 1.80, 0.47)
    status, reason = sim.conn.execute("SELECT status, reason FROM exec_live").fetchone()
    assert status == "rejected" and "mínima" in reason
