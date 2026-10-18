"""Sombra del valor automático en SX: la señal oro se prueba contra el libro de SX leído al ejecutar."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx

from surebet.arb import ValueBet
from surebet.models import AWAY, DRAW, HOME, BookLevel, MatchedEvent, Quote, SourceEvent
from surebet.sxshadow import SXShadow, capacity, fill_odds, summary

START = datetime.now(timezone.utc) + timedelta(hours=3)
TOKENS = {HOME: "tH", DRAW: "tD", AWAY: "tA"}
CONDS = {HOME: "cH", DRAW: "cD", AWAY: "cA"}


def value(with_sx=True, sx_odds=2.30):
    one = Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=2.20, label="Arsenal", market="1x2")
    poly = SourceEvent(feed="polymarket", event_id="E1", sport="soccer", home="Arsenal", away="Leeds", start=START,
                       three_way=True, quotes=[])
    book = SourceEvent(feed="onexbet", event_id="X1", sport="soccer", home="Arsenal", away="Leeds", start=START,
                       three_way=True, quotes=[one])
    ev = MatchedEvent(anchor=poly)
    ev.members = [(poly, False), (book, False)]
    if with_sx:
        sx = Quote(source="sxbet", feed="sxbet", covers=frozenset({HOME}), odds=sx_odds, label="Arsenal (SI)",
                   market="1x2", ref="0xabc|1", ts=time.time() - 240)
        ev.members.append((SourceEvent(feed="sxbet", event_id="S1", sport="soccer", home="Arsenal", away="Leeds",
                                       start=START, three_way=True, quotes=[sx]), False))
    return ValueBet(ev, one, 0.48, TOKENS, "1x2", CONDS)   # EV 1xBet = 2.20 × 0.48 − 1 = +5,6 %


def gold(*_):
    return True


def snapshot(p, usd=500):
    return {"data": {"outcomeOne": [{"percentageOdds": str(int(p * 10 ** 20)), "size": str(int(usd * 1e6))}],
                     "outcomeTwo": []}}


def run(coro_fn, handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await coro_fn(c)
    return asyncio.run(go())


def test_book_math():
    levels = [BookLevel(0.44, 50), BookLevel(0.475, 1000)]      # 50 shares a 0.44 = $22; a 0.475 el EV es +1 %
    assert 0 < capacity(levels, 0.48) < 30                        # solo el primer nivel deja EV ≥ 2 %
    assert abs(fill_odds(levels, 10) - 1 / 0.44) < 1e-9 and fill_odds([BookLevel(0.44, 5)], 10) is None


def test_fills_when_real_book_keeps_the_value(tmp_path):
    sh = SXShadow(tmp_path / "p.db")
    assert sh.step([value()], gold) == 1
    st, age, stake = sh.conn.execute("SELECT status, sx_seen_age_s, stake FROM sx_shadow").fetchone()
    assert st == "pendiente" and age >= 239 and stake >= 10
    sh.conn.execute("UPDATE sx_shadow SET created_ts = created_ts - 10")
    run(sh.verify, lambda r: httpx.Response(200, json=snapshot(0.44)))    # cuota real ~2.26 → EV ~+8 %
    st, odds, ev, res = sh.conn.execute("SELECT status, fill_odds, ev_real, result FROM sx_shadow").fetchone()
    assert st == "llenada" and 2.2 < odds < 2.3 and ev > 2 and res == "pendiente"
    assert sh.step([value()], gold) == 0                          # una sola vez por selección


def test_rejected_when_sx_odds_dropped(tmp_path):
    sh = SXShadow(tmp_path / "p.db")
    sh.step([value()], gold)
    sh.conn.execute("UPDATE sx_shadow SET created_ts = created_ts - 10")
    run(sh.verify, lambda r: httpx.Response(200, json=snapshot(0.48)))    # ~2.07: EV negativo
    st, reason = sh.conn.execute("SELECT status, reason FROM sx_shadow").fetchone()
    assert st == "rechazada" and "cuota real" in reason and "manual" in reason
    s = summary(sh.conn)
    assert s["fill_rate"] == 0 and s["drop_pct"] > 5 and s["reasons"][0]["n"] == 1


def test_no_sx_selection_stays_manual(tmp_path):
    sh = SXShadow(tmp_path / "p.db")
    sh.step([value(with_sx=False)], gold)
    assert sh.conn.execute("SELECT status FROM sx_shadow").fetchone()[0] == "sin SX"
    assert summary(sh.conn)["no_sx"] == 1


def test_clv_and_result_of_filled(tmp_path):
    sh = SXShadow(tmp_path / "p.db")
    sh.step([value()], gold)
    sh.conn.execute("UPDATE sx_shadow SET created_ts = created_ts - 10")
    run(sh.verify, lambda r: httpx.Response(200, json=snapshot(0.44)))
    start = time.time() - 3 * 3600
    sh.conn.execute("UPDATE sx_shadow SET start_ts=?", (start,))

    def handler(r):
        if "prices-history" in r.url.path:
            p = {"tH": 0.50, "tD": 0.25, "tA": 0.25}[r.url.params["market"]]
            return httpx.Response(200, json={"history": [{"t": int(start) - 60, "p": p}]})
        cid = r.url.path.rsplit("/", 1)[-1]
        won = {"cH": "tH"}.get(cid)
        return httpx.Response(200, json={"closed": True, "tokens": [
            {"token_id": "t" + cid[-1], "winner": won is not None}, {"token_id": "no" + cid[-1], "winner": won is None}]})

    run(sh.settle, handler)
    clv, res, pnl = sh.conn.execute("SELECT clv_real, result, pnl FROM sx_shadow").fetchone()
    assert clv > 10 and res == "ganada" and pnl > 0               # cerró a 0.50: CLV = cuota × 0.50 − 1
    s = summary(sh.conn)
    assert s["clv"] == clv and s["settled"] == 1 and s["pnl"] == pnl
