"""Desglose del automático por par de casas, con topes por surebet simulados."""

from surebet.dashboard_server import _auto_breakdown


def op(pair, stake, pnl, status="won", sport="soccer", ts=0.0):
    a, b = pair
    return {"created_ts": ts, "settled_ts": ts + 3600 * 24, "title": "A vs B", "sport": sport, "market_name": "1X2",
            "edge_pct": 1.0, "total_stake": stake, "status": status, "pnl": pnl,
            "legs": [{"source": a, "label": "A", "stake": stake / 2, "odds": 2.0},
                     {"source": b, "label": "B", "stake": stake / 2, "odds": 2.1}]}


def test_caps_scale_profit_and_split_by_pair():
    ops = [op(("azuro", "polymarket"), 1000, 20.0), op(("azuro", "polymarket"), 20, -5.0, "void", "esports:cs2"),
           op(("kalshi", "cloudbet"), 300, 9.0)]
    b = _auto_breakdown(ops)
    pairs = {p["pair"]: p for p in b["pairs"]}
    assert pairs["azuro + polymarket"]["pnl"] == 15.0 and pairs["azuro + polymarket"]["void"] == 1
    assert pairs["azuro + polymarket"]["cap30"] == round(20 * 30 / 1000 - 5.0, 2)   # la grande se achica, la chica no
    assert pairs["cloudbet + kalshi"]["cap100"] == 3.0
    assert b["totals"]["sin CS2/Dota2"]["pnl"] == 29.0 and b["totals"]["todo"]["pnl"] == 24.0
    assert len(b["picks"]) == 3 and b["picks"][0]["cap30"] == 0.6
