"""
Re-simula la cuenta automática con la regla nueva: en cada surebet, lo que aguante el libro (tope $500 y
nunca más de la mitad de la plata libre), en vez de $100 fijos.

No hay libro guardado del momento de entrar, así que el margen a más plata es una ESTIMACIÓN:
  - capacidad del libro = `max_total_stake` de la tabla opportunities (monto en que el margen promedio cae a
    0,5 %), solo si esa foto es igual o peor que la de la entrada (si no, no se sabe y se deja el monto original);
  - el margen promedio baja en línea recta desde el medido al monto original hasta 0,5 % en esa capacidad;
  - se elige el monto que deja más ganancia (en libros finos, poner más deja menos).

Uso:  python tools/resim_auto.py data/opportunities.db [--capital 1000] [--apply]
--apply guarda el resultado en la cuenta automática (copia de seguridad en paper_ops_auto_orig).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time

CAP = 500.0
MIN_PCT = 0.5


def best_stake(s0: float, net0: float, cap: float | None, limit: float) -> tuple[float, float]:
    """(monto, margen %) que deja más ganancia con el modelo lineal, sin pasar de `limit`."""
    if not cap or cap <= s0 * 1.05 or net0 <= MIN_PCT + 0.02:
        s = min(s0, limit)
        return s, net0
    b = (net0 - MIN_PCT) / (cap - s0)
    s_pm = (net0 + b * s0) / (2 * b)          # máximo de S * margen(S)
    s = max(min(s_pm, cap, limit), min(s0, limit))
    return s, (net0 - b * (s - s0)) if s > s0 else net0


def load(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    ops = [dict(r) for r in conn.execute("SELECT * FROM paper_ops WHERE track='auto' ORDER BY created_ts")]
    execs = [dict(r) for r in conn.execute(
        "SELECT e.created_ts, e.title, e.margin_pct, o.max_total_stake cap, o.last_margin_pct snap "
        "FROM exec_sim e LEFT JOIN opportunities o ON o.key=e.key WHERE e.status='filled'")]
    for op in ops:
        ex = min((e for e in execs if e["title"] == op["title"] and abs(e["created_ts"] - op["created_ts"]) < 60),
                 key=lambda e: abs(e["created_ts"] - op["created_ts"]), default=None)
        op["cap"], op["cap_src"] = None, "sin foto del libro"
        if ex and ex["cap"]:
            if ex["snap"] is not None and ex["snap"] <= op["edge_pct"] + 0.05:
                op["cap"], op["cap_src"] = ex["cap"], f"libro aguantaba ${ex['cap']:,.0f}"
            else:
                op["cap_src"] = "la foto del libro es de otro momento (no se sabe)"
    return ops


def replay(ops: list[dict], capital: float, resim_from: float) -> list[dict]:
    """Recorre las operaciones en orden con la plata libre de cada momento."""
    events = []
    for op in ops:
        events.append((op["created_ts"], 1, op))
        if op["status"] != "open" and op["settled_ts"]:
            events.append((op["settled_ts"], 0, op))
    realized, in_play, out = 0.0, {}, []
    for ts, kind, op in sorted(events, key=lambda e: (e[0], e[1])):
        if kind == 0:
            r = op["_new"]
            in_play.pop(op["id"], None)
            realized += r["pnl"]
            continue
        s0 = op["total_stake"]
        net0 = 100 * op["expected_profit"] / s0
        cash = capital + realized - sum(in_play.values())
        if op["created_ts"] >= resim_from:       # ya entró con la regla nueva: se deja como está
            s, net = s0, net0
        else:
            s, net = best_stake(s0, net0, op["cap"], min(CAP, 0.5 * cash))
        f = s / s0
        k = (1 + net / 100) / (1 + net0 / 100)
        new = {"stake": round(s, 2), "net_pct": round(net, 3), "expected": round(s * net / 100, 4), "f": f, "k": k,
               "pnl": None, "payout": None, "cash_before": round(cash, 2)}
        if op["status"] != "open":
            payout0 = (op["payout"] if op["payout"] is not None else op["pnl"] + s0)
            new["payout"] = round(payout0 * f * k, 4)
            new["pnl"] = round(new["payout"] - s, 4)
        op["_new"] = new
        in_play[op["id"]] = s
        out.append(op)
    return out


def scaled_legs(legs_json: str, f: float, k: float) -> str:
    legs = json.loads(legs_json)
    for l in legs:
        l["stake"] = round(l["stake"] * f, 2)
        l["odds"] = round(l["odds"] * k, 4)
        if "shares" in l:
            l["shares"] = round(l["stake"] * l["odds"], 2)
            l["fee"] = round(l.get("fee", 0) * f, 4)
            l["avg_price"] = round((l["stake"] - l["fee"]) / l["shares"], 4) if l["shares"] else l.get("avg_price")
    return json.dumps(legs, ensure_ascii=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--resim-from", type=float, default=1791831279, help="desde aquí ya rige la regla nueva")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db, timeout=30)
    ops = replay(load(conn), a.capital, a.resim_from)
    tot0 = tot1 = peak = 0.0
    open_now: dict[int, float] = {}
    print(f"{'partido':45} {'antes':>8} {'gana':>7} {'ahora':>8} {'gana':>7}  capacidad")
    for op in ops:
        n = op["_new"]
        p0 = op["pnl"] if op["pnl"] is not None else op["expected_profit"]
        p1 = n["pnl"] if n["pnl"] is not None else n["expected"]
        tot0 += p0
        tot1 += p1
        flag = "" if op["status"] != "open" else " (abierta)"
        print(f"{op['title'][:45]:45} {op['total_stake']:8.2f} {p0:7.2f} {n['stake']:8.2f} {p1:7.2f}  {op['cap_src']}{flag}")
    # Plata máxima en juego a la vez con la regla nueva
    ev = sorted([(o["created_ts"], o["_new"]["stake"]) for o in ops] +
                [(o["settled_ts"], -o["_new"]["stake"]) for o in ops if o["settled_ts"]])
    cur = 0.0
    for _, d in ev:
        cur += d
        peak = max(peak, cur)
    print(f"\nTotal: antes ${tot0:.2f}  ->  regla nueva ${tot1:.2f}   | capital {a.capital:.0f} -> "
          f"{a.capital + sum(o['_new']['pnl'] or 0 for o in ops):.2f} | máximo en juego a la vez ${peak:.0f}")
    if not a.apply:
        return
    conn.execute("CREATE TABLE IF NOT EXISTS paper_ops_auto_orig AS SELECT * FROM paper_ops WHERE 0")
    conn.execute("""CREATE TABLE IF NOT EXISTS auto_resim (op_id INTEGER PRIMARY KEY, ts REAL, orig_stake REAL,
                    orig_pnl REAL, orig_expected REAL, new_stake REAL, new_pnl REAL, new_expected REAL,
                    net_pct REAL, cap REAL, cap_src TEXT)""")
    for op in ops:
        n = op["_new"]
        if abs(n["f"] - 1) < 1e-9 and abs(n["k"] - 1) < 1e-9:
            continue
        if conn.execute("SELECT 1 FROM auto_resim WHERE op_id=?", (op["id"],)).fetchone():
            continue        # ya re-simulada (no se escala dos veces)
        conn.execute("INSERT INTO paper_ops_auto_orig SELECT * FROM paper_ops WHERE id=?", (op["id"],))
        conn.execute("UPDATE paper_ops SET total_stake=?, fees=?, expected_profit=?, legs_json=?, payout=?, pnl=? "
                     "WHERE id=?", (n["stake"], round((op["fees"] or 0) * n["f"], 4), n["expected"],
                                    scaled_legs(op["legs_json"], n["f"], n["k"]), n["payout"], n["pnl"], op["id"]))
        conn.execute("INSERT INTO auto_resim VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (op["id"], time.time(), op["total_stake"], op["pnl"], op["expected_profit"], n["stake"],
                      n["pnl"], n["expected"], n["net_pct"], op["cap"], op["cap_src"]))
    conn.execute("INSERT OR REPLACE INTO paper_meta VALUES ('auto_initial', ?)", (str(a.capital),))
    conn.commit()
    print("aplicado")


if __name__ == "__main__":
    main()
