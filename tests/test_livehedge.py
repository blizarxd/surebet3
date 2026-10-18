"""Cubrir en vivo al minuto 15 vs asegurar antes del partido (backtest con el historial de Polymarket)."""

import asyncio
import json
import time

import httpx
import pytest

from surebet.livehedge import JUMP, LiveHedgeTracker, evaluate, lock, points, summary
from surebet.nettrack import NetTracker


def add_pick(tr, key, start, odds=2.0, stake=20.0, status="quedó como valor", pnl=None, no_net=None):
    tr.conn.execute(
        "INSERT INTO net_picks (key, source, created_ts, start_ts, title, sport, odds, ev_pct, stake, limit_price, "
        "tokens, covers, status, result, pnl, pnl_no_net, skipped) VALUES (?, 'vivo', ?, ?, 'A vs B', 'soccer', ?, 3, ?, 0.49, "
        "?, ?, ?, ?, ?, ?, 0)",
        (key, start - 3600, start, odds, stake, json.dumps({"H": "tH", "A": "tA"}), json.dumps(["H"]), status,
         "asegurada" if status == "asegurada" else ("ganada" if (no_net or 0) > 0 else "perdida"), pnl, no_net))
    tr.conn.commit()


def run(tr, prices, now):
    """prices: token -> [(segundos desde el inicio, precio)]; el inicio va en el parámetro start del pick."""
    def handler(request):
        tok, a = request.url.params["market"], int(request.url.params["startTs"])
        start = a + 30 * 60
        return httpx.Response(200, json={"history": [{"t": start + s, "p": p} for s, p in prices[tok]]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await tr.update(c, now=now)
    return asyncio.run(go())


def test_lock_math():
    # lo apostado vale 0.60: el contrario cuesta 0.40 + 1¢ + comisión 0.05·0.4·0.6 = 0.422 → 40 shares × 0.578 − 20
    assert lock(20, 2.0, 0.60) == pytest.approx(40 * (1 - 0.422) - 20, abs=1e-6)
    assert lock(20, 2.0, 0.40) < 0 < lock(20, 2.0, 0.70)
    pre, p15 = points([(100, 0.5), (200, 0.52), (200 + 15 * 60 + 30, 0.66)], start=200)
    assert pre == 0.52 and p15 == 0.66


def test_tracker_records_close_and_minute_15(tmp_path):
    db = tmp_path / "p.db"
    NetTracker(db)                                       # crea net_picks
    tr = LiveHedgeTracker(db)
    start = time.time() - 3600
    add_pick(tr, "k1", start, status="quedó como valor", pnl=20.0, no_net=20.0)
    # al inicio 50/50; a los 15' lo apostado (H) vale 0.70: gol a favor
    n = run(tr, {"tH": [(-60, 0.50), (0, 0.50), (15 * 60, 0.70)], "tA": [(-60, 0.50), (0, 0.50), (15 * 60, 0.30)]},
            now=time.time())
    assert n == 1
    st, pre, p15 = tr.conn.execute("SELECT status, p_pre, p15 FROM live_hedge").fetchone()
    assert st == "ok" and pre == 0.5 and p15 == pytest.approx(0.70)
    s = summary(tr.conn)
    by = {x["id"]: x for x in s["all"]["strategies"]}
    assert s["all"]["picks"] == 1 and s["all"]["up"] == 1
    assert by["D"]["exp"] > by["C"]["exp"]               # subió: cubrir al 15' asegura más que al cierre
    assert by["E"]["hedged"] == 1 and by["A"]["real"] == 20.0


def test_no_history_retries_then_gives_up(tmp_path):
    db = tmp_path / "p.db"
    NetTracker(db)
    tr = LiveHedgeTracker(db)
    add_pick(tr, "k2", time.time() - 3600, no_net=-20.0)
    now = time.time()
    for i in range(3):
        run(tr, {"tH": [], "tA": []}, now=now + i * 700)
    assert tr.conn.execute("SELECT status, tries FROM live_hedge").fetchone() == ("sin datos", 3)


def test_strategies_on_same_picks():
    up = {"p_pre": 0.5, "p15": 0.5 + JUMP + 0.02, "stake": 20, "odds": 2.1, "red_locked": False, "red_pnl": -20, "no_net": -20}
    locked = {"p_pre": 0.5, "p15": 0.3, "stake": 20, "odds": 2.1, "red_locked": True, "red_pnl": 0.5, "no_net": 22}
    by = {s["id"]: s for s in evaluate([up, locked])}
    assert by["B"]["real"] == pytest.approx(-19.5)       # red actual: la 2ª asegurada, la 1ª perdió como valor
    assert by["F"]["hedged"] == 2                        # la 1ª se cubre al 15' (iba ganando), la 2ª ya tenía red
    assert by["F"]["real"] > by["B"]["real"]
    assert by["G"]["hedged"] == 2 and by["A"]["hedged"] == 0
