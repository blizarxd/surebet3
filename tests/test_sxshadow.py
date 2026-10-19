"""Sombra del valor automático (SX, Azuro, Cloudbet): la señal oro se prueba contra el precio leído al ejecutar."""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from surebet.arb import ValueBet
from surebet.models import AWAY, DRAW, HOME, BookLevel, MatchedEvent, Quote, SourceEvent
from surebet.sxshadow import SXShadow, capacity, fill_odds, summary

START = datetime.now(timezone.utc) + timedelta(hours=3)
TOKENS = {HOME: "tH", DRAW: "tD", AWAY: "tA"}
CONDS = {HOME: "cH", DRAW: "cD", AWAY: "cA"}


def value(sx_odds=2.30, azuro_odds=None, with_sx=True):
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
    if azuro_odds:
        az = Quote(source="azuro", feed="azuro", covers=frozenset({HOME}), odds=azuro_odds, label="Arsenal",
                   market="1x2", ref="777|29", ts=time.time() - 30)
        ev.members.append((SourceEvent(feed="azuro", event_id="Z1", sport="soccer", home="Arsenal", away="Leeds",
                                       start=START, three_way=True, quotes=[az]), False))
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


def aged(sh):
    sh.conn.execute("UPDATE sx_shadow SET created_ts = created_ts - 10")


def test_book_math():
    levels = [BookLevel(0.44, 50), BookLevel(0.475, 1000)]      # 50 shares a 0.44 = $22; a 0.475 el EV es +1 %
    assert 0 < capacity(levels, 0.48) < 30                        # solo el primer nivel deja EV ≥ 2 %
    assert abs(fill_odds(levels, 10) - 1 / 0.44) < 1e-9 and fill_odds([BookLevel(0.44, 5)], 10) is None


def test_fills_when_real_book_keeps_the_value(tmp_path):
    sh = SXShadow(tmp_path / "p.db")
    assert sh.step([value()], gold) == 1
    key, st, age, stake = sh.conn.execute("SELECT key, status, sx_seen_age_s, stake FROM sx_shadow").fetchone()
    assert key.endswith("#sxbet") and st == "pendiente" and age >= 239 and stake >= 10
    aged(sh)
    run(sh.verify, lambda r: httpx.Response(200, json=snapshot(0.44)))    # cuota real ~2.26 → EV ~+8 %
    st, odds, ev, res = sh.conn.execute("SELECT status, fill_odds, ev_real, result FROM sx_shadow").fetchone()
    assert st == "llenada" and 2.2 < odds < 2.3 and ev > 2 and res == "pendiente"
    assert sh.step([value()], gold) == 0                          # una sola vez por selección


def test_rejected_when_sx_odds_dropped(tmp_path):
    sh = SXShadow(tmp_path / "p.db")
    sh.step([value()], gold)
    aged(sh)
    run(sh.verify, lambda r: httpx.Response(200, json=snapshot(0.48)))    # ~2.07: EV negativo
    st, reason = sh.conn.execute("SELECT status, reason FROM sx_shadow").fetchone()
    assert st == "rechazada" and "cuota real" in reason and "manual" in reason
    s = summary(sh.conn)
    assert s["fill_rate"] == 0 and s["drop_pct"] > 5 and s["reasons"][0]["reason"].startswith("SX:")


def test_no_api_house_stays_manual(tmp_path):
    sh = SXShadow(tmp_path / "p.db")
    sh.step([value(with_sx=False)], gold)
    assert sh.conn.execute("SELECT status, house FROM sx_shadow").fetchone() == ("sin casa API", None)
    s = summary(sh.conn)
    assert s["no_api"] == 1 and s["selections"] == 1 and s["n"] == 0


def test_azuro_uses_its_odds_and_max_bet(tmp_path):
    sh = SXShadow(tmp_path / "p.db")
    assert sh.step([value(azuro_odds=2.40)], gold) == 2            # la misma selección en SX y en Azuro

    def handler(r):
        if "orderbook" in r.url.path:
            return httpx.Response(200, json=snapshot(0.48))         # SX ya no tiene el valor
        if "condition-batch" in r.url.path:
            return httpx.Response(200, json={"conditions": [{"state": "Active", "outcomes": [
                {"outcomeId": 29, "odds": "2.35", "state": "Active"}]}]})
        return httpx.Response(200, json={"response": {"maxBet": "80"}})   # /bet/calculation

    aged(sh)
    run(sh.verify, handler)
    got = dict(sh.conn.execute("SELECT house, status FROM sx_shadow").fetchall())
    assert got == {"sxbet": "rechazada", "azuro": "llenada"}
    cap, odds = sh.conn.execute("SELECT capacity, fill_odds FROM sx_shadow WHERE house='azuro'").fetchone()
    assert cap == 80 and odds == 2.35
    s = summary(sh.conn)
    assert s["filled_sel"] == 1 and {h["name"]: h["fills"] for h in s["by_house"]} == {"SX": 0, "Azuro": 1}


def test_clv_and_result_of_filled(tmp_path):
    sh = SXShadow(tmp_path / "p.db")
    sh.step([value()], gold)
    aged(sh)
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


def test_old_sx_only_table_is_migrated(tmp_path):
    import sqlite3
    from surebet.sxshadow import SCHEMA
    old = "\n".join(l for l in SCHEMA.splitlines() if not l.strip().startswith("house TEXT"))
    c = sqlite3.connect(tmp_path / "p.db")
    c.executescript(old)                       # la tabla de la primera versión (solo SX, sin columna house)
    c.execute("INSERT INTO sx_shadow (key, status) VALUES ('vieja', 'sin SX')")
    c.commit()
    sh = SXShadow(tmp_path / "p.db")
    assert sh.conn.execute("SELECT COUNT(*) FROM sx_shadow").fetchone()[0] == 0
    s = summary(sh.conn)
    assert isinstance(s, dict) and s["selections"] == 0 and json.dumps(s)


def test_wide_tier_is_measured_apart_and_never_sent_to_executor(tmp_path):
    from surebet.safety import golden_value, wide_value
    sh = SXShadow(tmp_path / "p.db")
    sent = []
    sh.on_fill = lambda *a: sent.append(a)
    v = value(sx_odds=2.30)
    v = ValueBet(v.event, replace_odds(v.quote, 2.12), 0.48, TOKENS, "1x2", CONDS)   # EV 2.12 × 0.48 − 1 = +1,8 %
    assert not golden_value(False, v.ev_pct, 2.12, 3) and wide_value(False, v.ev_pct, 2.12, 3)
    assert sh.step([v], golden_value, wide_value) == 1
    key, tier = sh.conn.execute("SELECT key, tier FROM sx_shadow").fetchone()
    assert key.endswith("#sxbet#amplio") and tier == "amplio"
    aged(sh)
    run(sh.verify, lambda r: httpx.Response(200, json=snapshot(0.475)))   # cuota real ~2.10: EV ~+1 %… < 1,5 %
    assert sh.conn.execute("SELECT status FROM sx_shadow").fetchone()[0] == "rechazada"
    s = summary(sh.conn)
    assert s["n"] == 0 and s["wide"]["n"] == 1 and sent == []          # el oro no se mezcla con el amplio


def replace_odds(q, odds):
    from dataclasses import replace
    return replace(q, odds=odds)


def test_azuro_waits_until_3h_before_start(tmp_path, monkeypatch):
    import sys
    mod = sys.modules[__name__]
    sh = SXShadow(tmp_path / "p.db")
    monkeypatch.setattr(mod, "START", datetime.now(timezone.utc) + timedelta(hours=5))
    assert sh.step([value(azuro_odds=2.40)], gold) == 1               # a 5 h: solo SX, Azuro espera
    assert [h for (h,) in sh.conn.execute("SELECT house FROM sx_shadow")] == ["sxbet"]
    assert sh.step([value(azuro_odds=2.40)], gold) == 0
    monkeypatch.setattr(mod, "START", datetime.now(timezone.utc) + timedelta(hours=2))
    assert sh.step([value(azuro_odds=2.40)], gold) == 1               # a 2 h: entra Azuro (SX ya estaba)
    assert sorted(h for (h,) in sh.conn.execute("SELECT house FROM sx_shadow")) == ["azuro", "sxbet"]
    assert sh.step([value(azuro_odds=2.40)], gold) == 0
    only = SXShadow(tmp_path / "q.db")                                 # solo Azuro y lejos: no queda como "sin casa API"
    monkeypatch.setattr(mod, "START", datetime.now(timezone.utc) + timedelta(hours=5))
    assert only.step([value(azuro_odds=2.40, with_sx=False)], gold) == 0
    assert only.conn.execute("SELECT COUNT(*) FROM sx_shadow").fetchone()[0] == 0
