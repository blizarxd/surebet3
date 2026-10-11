"""Partidos sin Polymarket (Cloudbet + SX): se liquidan con el resultado oficial de SX Bet."""

import asyncio
import json
import time

import httpx

from surebet.models import AWAY, DRAW, HOME
from surebet.paper import PaperBook, PaperConfig
from surebet.sources.sxbet import SXBetSource


def test_sx_resolution_for_winner_and_1x2():
    sx = SXBetSource()
    base = {"teamOneName": "A", "teamTwoName": "B", "sportXeventId": "L1", "gameTime": 2_000_000_000}
    sx._markets = {
        "0xml": {**base, "sportId": 6, "type": 52, "outcomeOneName": "A", "outcomeTwoName": "B", "marketHash": "0xml"},
        "0xtie": {**base, "sportId": 5, "type": 1, "outcomeOneName": "Tie", "outcomeTwoName": "Not tie", "marketHash": "0xtie"},
    }
    assert sx.resolution_for("L1", "ml") == {HOME: ("sx:0xml", "1"), AWAY: ("sx:0xml", "2")}
    assert sx.resolution_for("L1", "1x2") == {DRAW: ("sx:0xtie", "1")}


def test_settle_with_sx_result(tmp_path):
    pb = PaperBook(tmp_path / "p.db", PaperConfig())
    legs = [{"source": "cloudbet", "covers": [HOME], "stake": 50.0, "odds": 2.1},
            {"source": "sxbet", "covers": [AWAY], "stake": 50.0, "odds": 2.05}]
    pb.conn.execute(
        "INSERT INTO paper_ops (key, kind, created_ts, start_ts, total_stake, legs_json, resolve_json, status, track) "
        "VALUES ('k','arb',?,?,100,?,?, 'open','medicion')",
        (time.time() - 7200, time.time() - 7200, json.dumps(legs),
         json.dumps({HOME: ["sx:0xml", "1"], AWAY: ["sx:0xml", "2"]})))
    pb.conn.commit()

    def handler(request):
        assert request.url.path == "/markets/find"
        return httpx.Response(200, json={"status": "success", "data": [{"marketHash": "0xml", "outcome": 2}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await pb.settle(c, every_s=0)

    assert asyncio.run(run()) == 1
    status, pnl, out = pb.conn.execute("SELECT status, pnl, outcome FROM paper_ops").fetchone()
    assert status == "won" and out == AWAY and round(pnl, 2) == 2.5
