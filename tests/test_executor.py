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
        if r.url.host.endswith("publicnode.com"):                                       # saldo y permiso de USDT
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(10 ** 12)})
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


def test_withdraw_payouts_encoding():
    data = AzuroClient.withdraw_data("0xF9548Be470A4e130c90ceA8b179FCD66D2972AC7", [466681, 7])
    assert data.startswith("0xe8e3e87b") and len(data) == 10 + 64 * 5
    assert data[10:74].endswith("f9548be470a4e130c90cea8b179fcd66d2972ac7")
    assert int(data[74:138], 16) == 64 and int(data[138:202], 16) == 2 and int(data[202:266], 16) == 466681


def test_azuro_only_live_without_polymarket(tmp_path):
    buyer = Buyer()
    buyer.live = False                                # Polymarket en demo: con red y surebets apagadas no hace falta
    ex = Executor(tmp_path / "p.db", buyer, None, mode="live", azuro_key=KEY, value_net=False, arb=False)
    assert ex.mode == "live" and "SIN red" in ex.status
    ex.queue_arb(opp(), plan())
    assert ex._queue == []                            # sin surebets


def test_live_value_without_net_only_bets_azuro(tmp_path):
    from surebet.models import Quote
    buyer = Buyer()
    ex = Executor(tmp_path / "p.db", buyer, None, mode="live", azuro_key=KEY, value_net=False, azuro_stake=30)
    ex._last_claim = time.time()
    q = Quote(source="azuro", feed="azuro", covers=frozenset({"H"}), odds=2.30, label="Arsenal", market="1x2", ref="777|29")
    v = SimpleNamespace(event=opp().event, market="1x2", poly_tokens={}, poly_conditions={})
    ex.queue_value(v, q, 13.0, 2.30, 0.46)
    h = handler()
    original = h

    def h2(r):                                        # minOdds del valor: la que deja EV ≥ 2 % (1,02 / 0,46)
        if r.url.path.endswith("/ordinar"):
            return httpx.Response(200, json={"id": "o1", "state": "Created"})
        return original(r)
    run(ex, h2)
    r = row(ex)
    assert r["status"] == "value" and r["az_stake"] == 30 and r["total"] == 30 and buyer.orders == []


def test_claims_winnings_automatically(tmp_path):
    ex = Executor(tmp_path / "p.db", Buyer(), None, mode="live", azuro_key=KEY)
    sent = []

    def h(r):
        if "subgraphs" in r.url.path:
            return httpx.Response(200, json={"data": {"v3Bets": [{"betId": "466681", "payout": "9.25", "core": {
                "address": "0xf9548be470a4e130c90cea8b179fcd66d2972ac7",
                "liquidityPool": {"address": "0x0fa7fb5407ea971694652e6e16c12a52625de1b8"}}}]}})
        body = json.loads(r.content)
        m = body["method"]
        sent.append(body)
        res = {"eth_getBalance": hex(10 ** 18), "eth_getTransactionCount": "0x3", "eth_gasPrice": hex(30 * 10 ** 9),
               "eth_estimateGas": hex(120_000), "eth_sendRawTransaction": "0xabc"}[m]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": res})

    run(ex, h)
    est = next(b for b in sent if b["method"] == "eth_estimateGas")["params"][0]
    assert est["to"].lower() == "0x0fa7fb5407ea971694652e6e16c12a52625de1b8" and est["data"].startswith("0xe8e3e87b")
    assert any(b["method"] == "eth_sendRawTransaction" for b in sent) and 466681 in ex._claiming
    sent.clear()
    ex._last_claim = 0
    run(ex, h)                                        # mientras confirma, no se vuelve a mandar el mismo cobro
    assert not any(b["method"] == "eth_sendRawTransaction" for b in sent)


def test_no_bet_without_funds_and_warns_once(tmp_path):
    from surebet.models import Quote
    ex = Executor(tmp_path / "p.db", Buyer(), None, mode="live", azuro_key=KEY, value_net=False, azuro_stake=5)
    ex._last_claim = time.time()
    base = handler()

    def poor(r):
        if r.url.host.endswith("publicnode.com"):           # 2 USDT en la wallet: no alcanza para $5
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(2 * 10 ** 6)})
        assert not r.url.path.endswith("/ordinar")    # nunca se manda la orden
        return base(r)

    q = Quote(source="azuro", feed="azuro", covers=frozenset({"H"}), odds=2.30, label="Arsenal", market="1x2", ref="777|29")
    for key in ("polymarket:E1", "polymarket:E2"):
        ex.queue_value(SimpleNamespace(event=opp(key).event, market="1x2", poly_tokens={}, poly_conditions={}), q, 5, 2.30, 0.46)
    run(ex, poor)
    reasons = [r for (r,) in ex.conn.execute("SELECT reason FROM live_exec")]
    assert len(reasons) == 2 and all("saldo insuficiente" in r for r in reasons) and ex._warned_funds


def test_value_far_from_start_is_not_bet(tmp_path):
    from surebet.models import Quote
    ex = Executor(tmp_path / "p.db", Buyer(), None, mode="dry", azuro_stake=5, max_per_op=100)
    anchor = SimpleNamespace(title="Arsenal vs Leeds", start=datetime.now(timezone.utc) + timedelta(hours=4))
    v = SimpleNamespace(event=SimpleNamespace(anchor=anchor, key="polymarket:E9"), market="1x2",
                        poly_tokens={}, poly_conditions={})
    q = Quote(source="azuro", feed="azuro", covers=frozenset({"H"}), odds=2.30, label="Arsenal", market="1x2", ref="777|29")
    ex.queue_value(v, q, 13.0, 2.30, 0.46)
    run(ex, handler())
    r = row(ex)
    assert r["status"] == "rejected" and "más de 3 h" in r["reason"]


def test_telegram_messages_for_real_bet_and_result(tmp_path):
    from surebet.models import Quote
    sent = []
    ex = Executor(tmp_path / "p.db", Buyer(), None, mode="live", azuro_key=KEY, value_net=False, azuro_stake=5,
                  telegram=("tok", "chat"))
    ex._last_claim = time.time()
    q = Quote(source="azuro", feed="azuro", covers=frozenset({"H"}), odds=2.30, label="Arsenal", market="1x2", ref="777|29")
    v = SimpleNamespace(event=opp().event, market="1x2", poly_tokens={}, poly_conditions={})
    ex.queue_value(v, q, 13.0, 2.30, 0.46)
    base = handler()

    def h(r):
        if r.url.host == "api.telegram.org":
            sent.append(json.loads(r.content)["text"])
            return httpx.Response(200, json={"ok": True})
        if r.url.path.endswith("/ordinar"):
            return httpx.Response(200, json={"id": "o1", "state": "Created"})
        return base(r)
    run(ex, h)
    assert any("AZURO · APUESTA REAL" in s and "Arsenal" in s and "$5.00 @ 2.30" in s for s in sent)
    # un resultado: un solo aviso, con el récord
    ex.conn.execute("INSERT INTO real_bets (bet_id, created_ts, amount, odds, status, result, payout, pnl, title, outcome_title, "
                    "market) VALUES (1, ?, 5, 2.3, 'Resolved', 'Won', 11.5, 6.5, 'Arsenal vs Leeds', 'Arsenal', '1X2')", (time.time(),))
    ex.conn.execute("INSERT INTO real_bets (bet_id, created_ts, amount, odds, status, result, payout, pnl, title, outcome_title) "
                    "VALUES (2, ?, 5, 2.0, 'Resolved', 'Lost', 0, -5, 'Roma vs Lazio', 'Roma')", (time.time(),))
    ex.conn.commit()
    sent.clear()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            await ex._notify_results(c)
            await ex._notify_results(c)
    asyncio.run(go())
    assert len(sent) == 2 and "GANÓ" in sent[0] and "+$6.50" in sent[0] and "PERDIÓ" in sent[1] and "1G-1P" in sent[1]


def test_unplaced_signal_is_reported(tmp_path):
    from surebet.models import Quote
    sent = []
    ex = Executor(tmp_path / "p.db", Buyer(), None, mode="live", azuro_key=KEY, value_net=False, azuro_stake=5,
                  telegram=("tok", "chat"))
    anchor = SimpleNamespace(title="Arsenal vs Leeds", start=datetime.now(timezone.utc) + timedelta(hours=5))
    v = SimpleNamespace(event=SimpleNamespace(anchor=anchor, key="polymarket:E8"), market="1x2", poly_tokens={}, poly_conditions={})
    q = Quote(source="azuro", feed="azuro", covers=frozenset({"H"}), odds=2.30, label="Arsenal", market="1x2", ref="777|29")
    ex.queue_value(v, q, 13.0, 2.30, 0.46)
    base = handler()

    def h(r):
        if r.url.host == "api.telegram.org":
            sent.append(json.loads(r.content)["text"])
            return httpx.Response(200, json={"ok": True})
        return base(r)
    run(ex, h)
    assert len(sent) == 1 and "NO APOSTADA" in sent[0] and "más de 3 h" in sent[0]


def test_one_bet_per_game_across_markets(tmp_path):
    from surebet.models import Quote
    ex = Executor(tmp_path / "p.db", Buyer(), None, mode="dry", azuro_stake=5, max_per_op=100)
    q = Quote(source="azuro", feed="azuro", covers=frozenset({"H"}), odds=2.30, label="Arsenal", market="1x2", ref="777|29")
    for mk in ("1x2", "ou_2.5"):                        # dos mercados del mismo partido
        v = SimpleNamespace(event=opp().event, market=mk, poly_tokens={}, poly_conditions={})
        ex.queue_value(v, q, 13.0, 2.30, 0.46)
    run(ex, handler())
    rows = ex.conn.execute("SELECT status, reason FROM live_exec ORDER BY id").fetchall()
    assert rows[0][0] == "dry" and rows[1][0] == "rejected" and "ya tiene una jugada" in rows[1][1]
