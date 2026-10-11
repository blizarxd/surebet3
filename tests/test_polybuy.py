"""Botón "Ya aposté en 1xBet → comprar en Polymarket" en modo DEMO: no envía nada, solo calcula."""

import asyncio

import httpx

from surebet.polybuy import PolyBuyer, walk_shares
from surebet.models import BookLevel
from test_makersim import setup


def run(buyer, oid, asks):
    def handler(request):
        assert request.url.path == "/book"
        return httpx.Response(200, json={"asks": [{"price": str(p), "size": str(s)} for p, s in asks], "tick_size": "0.01"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await buyer.execute(c, oid)
    return asyncio.run(go())


def test_walk_shares_exact():
    plan = walk_shares((BookLevel(0.40, 50), BookLevel(0.42, 100)), 100, 0.0)
    assert plan["top"] == 0.42 and abs(plan["spent"] - (50 * 0.40 + 50 * 0.42)) < 1e-9


def test_demo_buys_when_still_profitable(tmp_path):
    opps, p, _ = setup()
    b = PolyBuyer(tmp_path / "b.db", "tok", "1")
    oid = b.offer(opps[0], 100)
    assert oid and b.button(oid)[1] == f"cb:pb:{oid}"
    msg = run(b, oid, [(0.50, 10000)])
    assert msg.startswith("🧪 DEMO")
    status, net = b.conn.execute("SELECT status, net_pct FROM poly_orders").fetchone()
    assert status == "demo" and net > 0
    assert "ya se usó" in run(b, oid, [(0.50, 10000)])            # un botón, una sola vez


def test_rejects_when_price_moved(tmp_path):
    opps, p, _ = setup()
    b = PolyBuyer(tmp_path / "b.db", "tok", "1")
    oid = b.offer(opps[0], 100)
    msg = run(b, oid, [(0.56, 10000)])
    assert "NO compré" in msg
    assert b.conn.execute("SELECT status FROM poly_orders").fetchone()[0] == "rejected"


def test_pause_switch(tmp_path):
    opps, p, _ = setup()
    b = PolyBuyer(tmp_path / "b.db", "tok", "1")
    b._set_paused(True)
    assert "pausadas" in run(b, b.offer(opps[0], 100), [(0.50, 10000)])


def test_reject_feedback_and_in_play(tmp_path):
    opps, p, _ = setup()
    b = PolyBuyer(tmp_path / "b.db", "tok", "1")
    oid = b.offer(opps[0], 100)
    assert b.reject_button("pb", oid)[1] == f"cb:nx:pb:{oid}"
    assert "no entró" in b._feedback("pb", oid)
    assert b.conn.execute("SELECT status FROM poly_orders").fetchone()[0] == "rejected"
    assert b.conn.execute("SELECT COUNT(*) FROM manual_feedback").fetchone()[0] == 1
    oid2 = b.offer(opps[0], 100)
    run(b, oid2, [(0.50, 10000)])                          # demo: cuenta como en juego
    assert b.in_play() > 90
