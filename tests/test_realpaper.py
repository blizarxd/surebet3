"""Real vs papel: apuestas reales de la wallet (subgraph de Azuro) contra la sombra, al mismo monto por pick."""

import asyncio
import json
import time

import httpx

from surebet.executor import Executor
from surebet.realpaper import bet_pnl, summary
from surebet.sxshadow import SXShadow

KEY = "0x" + "11" * 32


class Buyer:
    live = True

    def _paused(self):
        return False


def shadow_row(sh, key, ts, odds, result, ref, sport="soccer", market="1x2"):
    sh.conn.execute("INSERT INTO sx_shadow (key, created_ts, start_ts, title, sport, market, label, covers, signal_source, "
                    "signal_odds, signal_ev, fair_prob, house, tier, sx_ref, sx_seen_odds, stake, status, fill_odds, ev_real, "
                    "result) VALUES (?,?,?,'A vs B',?,?,'A','H','azuro',?,3.0,0.5,'azuro','oro',?,?,13,'llenada',?,3.0,?)",
                    (key, ts, ts + 3600, sport, market, odds, ref, odds, odds, result))
    sh.conn.commit()


def test_bet_pnl():
    assert bet_pnl("Resolved", "Won", 5, 10.5) == 5.5 and bet_pnl("Resolved", "Lost", 5, None) == -5
    assert bet_pnl("Canceled", None, 5, 5) == 0 and bet_pnl("Accepted", None, 5, None) is None


def test_reconcile_and_board(tmp_path):
    db = tmp_path / "p.db"
    sh = SXShadow(db)
    ex = Executor(db, Buyer(), None, mode="live", azuro_key=KEY, value_net=False, arb=False, azuro_stake=5)
    t = time.time() - 86400
    shadow_row(sh, "s1", t, 2.10, "ganada", "c1|1")
    shadow_row(sh, "s2", t + 60, 3.00, "perdida", "c2|2")
    shadow_row(sh, "s3", t + 120, 2.50, "ganada", "c3|1", sport="esports:cs2")      # CS2: no cuenta en el papel
    shadow_row(sh, "s4", t + 7200, 1.90, "ganada", "c4|1", market="ou_2.5")         # el real no la tomó

    def h(r):
        bets = [{"betId": "11", "amount": "5", "odds": "2.12", "status": "Resolved", "result": "Won", "payout": "10.6",
                 "createdBlockTimestamp": str(int(t + 5)), "isRedeemed": True,
                 "selections": [{"outcome": {"outcomeId": "1", "title": "A", "condition": {"conditionId": "c1", "gameId": "g1"}}}]},
                {"betId": "12", "amount": "5", "odds": "2.95", "status": "Resolved", "result": "Lost", "payout": None,
                 "createdBlockTimestamp": str(int(t + 65)), "isRedeemed": False,
                 "selections": [{"outcome": {"outcomeId": "2", "title": "B", "condition": {"conditionId": "c2", "gameId": "g2"}}}]}]
        return httpx.Response(200, json={"data": {"v3Bets": bets}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            await ex._reconcile(c)
    asyncio.run(go())
    s = summary(ex.conn)
    assert s["stake"] == 5 and s["total"]["real"]["n"] == 2 and s["total"]["real"]["pnl"] == round(5.6 - 5, 2)
    assert s["total"]["paper"]["n"] == 3 and s["total"]["paper"]["pnl"] == round(5 * 1.10 - 5 + 5 * 0.90, 2)
    assert s["slip"]["n"] == 2 and s["slip"]["favor"] == 1 and s["slip"]["contra"] == 1
    assert [m["ref"] for m in s["missed"]] == ["c4|1"]
    assert {m["name"] for m in s["by_market"]} == {"1X2", "Más/Menos"}
    assert s["curve"]["real"][-1][1] == 0.6 and len(s["days"]) >= 1
    ex._last_reconcile = 0
    asyncio.run(go())                                   # repetir no duplica
    assert ex.conn.execute("SELECT COUNT(*) FROM real_bets").fetchone()[0] == 2
