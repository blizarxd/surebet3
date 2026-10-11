"""Drawdown, rachas y capital máximo en juego."""

from surebet.risk import risk_stats


def it(o, c, cap, pnl, out):
    return {"open_ts": o, "close_ts": c, "capital": cap, "pnl": pnl, "outcome": out}


def test_drawdown_streaks_and_capital():
    items = [it(0, 10, 100, +50, "won"), it(5, 20, 100, -30, "lost"), it(6, 30, 100, -40, "lost"),
             it(25, 40, 50, +10, "won"), it(35, None, 70, None, None)]
    r = risk_stats(items, 1000)
    assert r["max_dd"] == 70 and r["peak"] == 1050           # 1050 → 980
    assert r["loss_streak"] == 2 and r["win_streak"] == 1 and r["cur_streak"] == 1
    assert r["max_in_play"] == 300                            # las tres primeras a la vez (t=6..10)
    assert r["in_play"] == 70
