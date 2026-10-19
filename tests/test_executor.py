"""Ejecutor real de Azuro + Polymarket: firma, orden de las patas, pata suelta y frenos (todo contra APIs simuladas)."""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data

from surebet.azuro_exec import AzuroClient, raw_odds, typed_data
from surebet.executor import Executor

KEY = "0x" + "11" * 32
START = datetime.now(timezone.utc) + timedelta(hours=2)


class Buyer:
    def __init__(self, fills=(True,)):
        self.live, self.fills, self.orders = True, list(fills), []
        self.paused = False

    def _paused(self):
        return self.paused

    def post_buy(self, token, price, size, kind):
        self.orders.append((token, price, size, kind))
        return {"success": self.fills.pop(0) if self.fills else True}


def opp(key="polymarket:E1"):
    anchor = SimpleNamespace(title="Arsenal vs Leeds", start=START)
    return SimpleNamespace(event=SimpleNamespace(anchor=anchor, key=key), market="1x2")


def plan(az_stake=60.0, az_odds=2.30, pm_stake=40.0):
    return [{"source": "azuro", "label": "Arsenal", "stake": az_stake, "odds": az_odds, "fill_odds": az_odds,
             "min_odds": 2.25, "ref": "777|29"},
            {"source": "polymarket", "label": "Arsenal (NO)", "stake": pm_stake, "odds": 3.4, "fill_odds": 3.4,
             "min_odds": 3.3, "token": "tNO", "fee_rate": 0.05, "fee_exp": 1}]


def handler(order_state="Accepted", ask=0.29):
    seen = []

    def h(r):
        seen.append((r.method, r.url.path))
        p = r.url.path
        if p.endswith("/gas-info"):
            return httpx.Response(200, json=[{"relayerFeeAmount": "20000"}])          # $0,02
        if p.endswith("/condition-batch"):
            return httpx.Response(200, json={"conditions": [{"state": "Active", "outcomes": [
                {"outcomeId": 29, "odds": "2.30", "state": "Active"}]}]})
        if p.endswith("/bet/calculation"):
            return httpx.Response(200, json={"response": {"maxBet": "500"}})
        if p.endswith("/bet/orders/ordinar"):
            body = json.loads(r.content)
            assert body["bettorSignature"] and body["clientBetData"]["bet"]["minOdds"] == str(raw_odds(2.25))
            return httpx.Response(200, json={"id": "o1", "state": "Created"})
        if "/bet/orders/" in p:
            return httpx.Response(200, json={"id": "o1", "state": order_state, "odds": "2.30"})
        if p.endswith("/book"):
            return httpx.Response(200, json={"tick_size": "0.01", "asks": [{"price": str(ask), "size": "500"}]})
        return httpx.Response(404)
    h.seen = seen
    return h


def run(ex, h):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            await ex.step(c)
    asyncio.run(go())


def row(ex):
    cur = ex.conn.execute("SELECT * FROM live_exec ORDER BY id DESC LIMIT 1")
    return dict(zip([d[0] for d in cur.description], cur.fetchone()))


def test_signature_recovers_the_wallet():
    az = AzuroClient(KEY)
    body = az.build("777", "29", 12.5, 2.25, 20000)
    cd, bet = body["clientBetData"]["clientData"], body["clientBetData"]["bet"]
    assert bet["amount"] == "12500000" and bet["minOdds"] == "2250000000000"
    msg = encode_typed_data(full_message=typed_data(cd, bet))
    assert Account.recover_message(msg, signature=body["bettorSignature"]) == az.address
    assert body["bettor"] == az.address.lower() and cd["core"].startswith("0xF954")


def test_dry_arb_reads_prices_but_sends_nothing(tmp_path):
    buyer = Buyer()
    ex = Executor(tmp_path / "p.db", buyer, None, mode="dry", max_per_op=30)
    ex.queue_arb(opp(), plan())
    h = handler()
    run(ex, h)
    r = row(ex)
    assert r["status"] == "dry" and r["mode"] == "dry" and r["poly_shares"] == int(r["poly_shares"])
    assert r["total"] <= 30.5 and r["az_stake"] * 2.30 >= r["poly_shares"] - 0.05
    assert not any(p.endswith("/ordinar") for _, p in h.seen) and buyer.orders == []


def test_live_arb_bets_azuro_first_then_polymarket(tmp_path):
    buyer = Buyer()
    ex = Executor(tmp_path / "p.db", buyer, None, mode="live", max_per_op=30, azuro_key=KEY)
    assert ex.mode == "live"
    ex.queue_arb(opp(), plan())
    h = handler()
    run(ex, h)
    r = row(ex)
    assert r["status"] == "covered" and r["az_state"] == "Accepted" and r["net_pct"] > 0
    order = [p for _, p in h.seen if p.endswith("/ordinar") or p.endswith("/book")]
    assert order.index("/api/v1/public/bet/orders/ordinar") < len(order) - 1   # Azuro antes de comprar en Polymarket
    token, price, size, kind = buyer.orders[0]
    assert token == "tNO" and kind == "FOK" and size == int(size) and abs(round(price * size, 2) - price * size) < 1e-9


def test_rejected_by_azuro_never_touches_polymarket(tmp_path):
    buyer = Buyer()
    ex = Executor(tmp_path / "p.db", buyer, None, mode="live", azuro_key=KEY)
    ex.queue_arb(opp(), plan())
    run(ex, handler(order_state="Rejected"))
    assert row(ex)["status"] == "rejected" and buyer.orders == []


def test_loose_leg_is_retried_and_covered(tmp_path):
    buyer = Buyer(fills=(False, True))
    ex = Executor(tmp_path / "p.db", buyer, None, mode="live", azuro_key=KEY)
    ex._last_loose = time.time()                      # el reintento no corre en el mismo paso
    ex.queue_arb(opp(), plan())
    run(ex, handler())
    assert row(ex)["status"] == "loose"
    ex._last_loose = 0
    run(ex, handler())
    assert row(ex)["status"] == "covered" and len(buyer.orders) == 2


def test_loose_leg_not_covered_at_a_big_loss(tmp_path):
    buyer = Buyer(fills=(False,))
    ex = Executor(tmp_path / "p.db", buyer, None, mode="live", azuro_key=KEY)
    ex._last_loose = time.time()
    ex.queue_arb(opp(), plan())
    run(ex, handler())
    ex._last_loose = 0
    run(ex, handler(ask=0.62))                       # cubrir ahora perdería mucho más que 1 %
    assert row(ex)["status"] == "loose" and len(buyer.orders) == 1


def test_brakes_pause_duplicate_and_daily_cap(tmp_path):
    buyer = Buyer()
    ex = Executor(tmp_path / "p.db", buyer, None, mode="dry", max_per_op=30, max_daily=40)
    buyer.paused = True
    ex.queue_arb(opp(), plan())
    run(ex, handler())
    assert row(ex)["status"] == "rejected" and "pausa" in row(ex)["reason"]
    buyer.paused = False
    ex.queue_arb(opp("polymarket:E2"), plan())
    run(ex, handler())
    ex.queue_arb(opp("polymarket:E2"), plan())
    run(ex, handler())
    assert "ya tiene" in row(ex)["reason"]
    ex.queue_arb(opp("polymarket:E3"), plan())
    run(ex, handler())
    assert "tope del día" in row(ex)["reason"]


def test_live_without_keys_falls_back_to_dry(tmp_path):
    buyer = Buyer()
    buyer.live = False
    ex = Executor(tmp_path / "p.db", buyer, None, mode="live")
    assert ex.mode == "dry" and "pediste live" in ex.status


def test_dry_value_on_azuro(tmp_path):
    from surebet.models import Quote
    buyer = Buyer()
    ex = Executor(tmp_path / "p.db", buyer, None, mode="dry", max_per_op=30)
    q = Quote(source="azuro", feed="azuro", covers=frozenset({"H"}), odds=2.30, label="Arsenal", market="1x2", ref="777|29")
    v = SimpleNamespace(event=opp().event, market="1x2", poly_tokens={}, poly_conditions={})
    ex.queue_value(v, q, 13.0, 2.30, 0.46)           # EV 2.30 × 0.46 − 1 = +5,8 %
    run(ex, handler())
    r = row(ex)
    assert r["status"] == "dry" and r["kind"] == "value" and r["total"] <= 30.01 and r["net_pct"] > 2


def test_cs2_value_is_only_observed(tmp_path):
    from surebet.models import MatchedEvent, Quote, SourceEvent
    ex = Executor(tmp_path / "p.db", Buyer(), None, mode="dry", max_per_op=30)
    se = SourceEvent(feed="azuro", event_id="Z1", sport="esports:cs2", home="A", away="B", start=START, three_way=False)
    ev = MatchedEvent(anchor=se)
    ev.members = [(se, False)]
    q = Quote(source="azuro", feed="azuro", covers=frozenset({"H"}), odds=2.30, label="A", market="ml", ref="777|29")
    v = SimpleNamespace(event=ev, market="ml", poly_tokens={}, poly_conditions={})
    ex.queue_value(v, q, 13.0, 2.30, 0.46)
    run(ex, handler())
    r = row(ex)
    assert r["status"] == "rejected" and "CS2" in r["reason"]


def test_value_uses_fixed_azuro_stake(tmp_path):
    from surebet.models import Quote
    ex = Executor(tmp_path / "p.db", Buyer(), None, mode="dry", azuro_stake=30, max_per_op=100)
    q = Quote(source="azuro", feed="azuro", covers=frozenset({"H"}), odds=2.30, label="Arsenal", market="1x2", ref="777|29")
    v = SimpleNamespace(event=opp().event, market="1x2", poly_tokens={}, poly_conditions={})
    ex.queue_value(v, q, 13.0, 2.30, 0.46)
    run(ex, handler())
    r = row(ex)
    assert r["az_stake"] == 30 and 60 < r["total"] < 100          # $30 en Azuro + la red (~$36) del lado contrario
