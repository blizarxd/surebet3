"""Red de seguridad de apuestas de valor desde Telegram (modo DEMO: no envía nada)."""

import asyncio
import time
from dataclasses import replace

import httpx

from surebet.models import BookLevel
from surebet.polybuy import PolyBuyer
from surebet.valuenet import ValueNet
from test_makersim import value_setup


def click(nets, nid, asks):
    def handler(request):
        assert request.url.path == "/book"
        return httpx.Response(200, json={"asks": [{"price": str(p), "size": str(s)} for p, s in asks], "tick_size": "0.01"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await nets.click(c, nid)
    return asyncio.run(go())


def tick(nets, quotes):
    said = []

    async def say(t):
        said.append(t)
    asyncio.run(nets.tick(quotes, say))
    return said


def make(tmp_path):
    v = value_setup()[0]
    nets = ValueNet(PolyBuyer(tmp_path / "n.db", "tok", "1"))
    nid = nets.offer(v, 20)
    no_h = next(q for q in v.event.quotes_for("1x2") if q.token_id == "t_no_h")
    return nets, nid, no_h


def status(nets):
    return nets.conn.execute("SELECT status, net_pct FROM value_nets").fetchone()


def test_net_rests_then_locks_when_price_comes(tmp_path):
    nets, nid, no_h = make(tmp_path)
    assert nets.button(nid)[1] == f"cb:vn:{nid}"
    msg = click(nets, nid, [(0.53, 1000)])
    assert "Red puesta" in msg and "DEMO" in msg and status(nets)[0] == "resting"
    assert tick(nets, {"t_no_h": no_h}) == [] and status(nets)[0] == "resting"      # todavía caro
    said = tick(nets, {"t_no_h": replace(no_h, asks=(BookLevel(0.50, 500),))})
    st, net = status(nets)
    assert st == "locked" and net >= 1.0 and "ASEGURADA" in said[0]


def test_net_cancelled_at_kickoff_keeps_value(tmp_path):
    nets, nid, no_h = make(tmp_path)
    click(nets, nid, [(0.53, 1000)])
    nets.conn.execute("UPDATE value_nets SET start_ts=?", (time.time() + 300,))
    said = tick(nets, {"t_no_h": no_h})
    assert status(nets)[0] == "kept" and "no se llenó" in said[0]


def test_price_already_there_buys_now_if_still_safe(tmp_path):
    nets, nid, _ = make(tmp_path)
    msg = click(nets, nid, [(0.45, 1000)])          # muy por debajo del límite: aun con comisión queda ganancia
    st, net = status(nets)
    assert st == "locked" and net >= 0.5 and "ASEGURADA" in msg


def test_paused_does_not_place(tmp_path):
    nets, nid, _ = make(tmp_path)
    nets.buyer._set_paused(True)
    assert "Pausado" in click(nets, nid, [(0.53, 1000)])
