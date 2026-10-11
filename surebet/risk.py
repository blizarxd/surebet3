"""
Riesgo de una cuenta en papel: caída máxima (drawdown), rachas y capital en juego.

Entrada: una lista de jugadas {open_ts, close_ts, capital, pnl, outcome}
  - open_ts / close_ts: cuándo se puso el dinero y cuándo volvió (close_ts None = sigue en juego)
  - capital: dinero inmovilizado mientras está abierta (casa + Polymarket si hay red)
  - pnl: ganancia/pérdida al cerrar (None si sigue abierta)
  - outcome: 'won' | 'lost' | 'void' | None
"""

from __future__ import annotations

import time
from datetime import datetime


def risk_stats(items: list[dict], initial: float) -> dict:
    now = time.time()
    closed = sorted((i for i in items if i["close_ts"] is not None and i["pnl"] is not None and i["close_ts"] <= now),
                    key=lambda i: i["close_ts"])

    # Caída máxima: desde el punto más alto del capital hasta el más bajo que vino después
    eq = peak = initial
    max_dd = max_dd_pct = 0.0
    dd_from = dd_to = None
    peak_ts = closed[0]["close_ts"] if closed else None
    for i in closed:
        eq += i["pnl"]
        if eq > peak:
            peak, peak_ts = eq, i["close_ts"]
        dd = peak - eq
        if dd > max_dd:
            max_dd, max_dd_pct, dd_from, dd_to = dd, 100 * dd / peak if peak else 0.0, peak_ts, i["close_ts"]
    cur_dd = peak - eq

    # Rachas (las anuladas no cortan ni suman)
    best_win = best_loss = cur = 0
    for i in closed:
        if i["outcome"] == "won":
            cur = cur + 1 if cur > 0 else 1
        elif i["outcome"] == "lost":
            cur = cur - 1 if cur < 0 else -1
        else:
            continue
        best_win, best_loss = max(best_win, cur), max(best_loss, -cur)

    # Capital en juego: barrido de entradas y salidas de dinero
    events = []
    for i in items:
        events.append((i["open_ts"], i["capital"]))
        events.append((i["close_ts"] if i["close_ts"] is not None else now + 1e9, -i["capital"]))
    events.sort(key=lambda e: (e[0], e[1]))  # a la misma hora primero sale y después entra
    level = max_in = 0.0
    max_ts = None
    for ts, delta in events:
        level += delta
        if level > max_in + 1e-9:
            max_in, max_ts = level, ts
    in_play = sum(i["capital"] for i in items if i["close_ts"] is None or i["close_ts"] > now)

    days: dict[str, float] = {}
    for i in closed:
        d = datetime.fromtimestamp(i["close_ts"]).strftime("%d/%m")
        days[d] = days.get(d, 0.0) + i["pnl"]
    return {
        "max_dd": round(max_dd, 2), "max_dd_pct": round(max_dd_pct, 2), "dd_from": dd_from, "dd_to": dd_to,
        "cur_dd": round(cur_dd, 2), "cur_dd_pct": round(100 * cur_dd / peak, 2) if peak else 0.0,
        "peak": round(peak, 2),
        "win_streak": best_win, "loss_streak": best_loss,
        "cur_streak": cur,  # >0 ganando, <0 perdiendo
        "max_in_play": round(max_in, 2), "max_in_play_ts": max_ts,
        "max_in_play_pct": round(100 * max_in / initial, 1) if initial else None,
        "in_play": round(in_play, 2),
        "best_day": max(days.items(), key=lambda d: d[1]) if days else None,
        "worst_day": min(days.items(), key=lambda d: d[1]) if days else None,
        "closed": len(closed),
    }


def from_paper_ops(ops: list[dict]) -> list[dict]:
    """Operaciones de paper_ops: el dinero entra al anotarse y vuelve al liquidarse."""
    return [{"open_ts": o["created_ts"], "close_ts": o["settled_ts"] if o["status"] != "open" else None,
             "capital": o["total_stake"], "pnl": o["pnl"] if o["status"] != "open" else None,
             "outcome": o["status"] if o["status"] != "open" else None} for o in ops]
