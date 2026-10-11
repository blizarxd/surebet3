"""
Hipótesis "congeladas": cada cambio de reglas queda anotado con su fecha y lo que se espera, y el panel compara
solo el ANTES y el DESPUÉS con los mismos números. Así se confirma o se descarta con datos, no a ojo.
Para agregar una: sumarla a EXPERIMENTS con la hora (epoch) en que se desplegó el cambio.
"""

from __future__ import annotations

import time

EXPERIMENTS = [
    {"id": "real-2h-early",
     "ts": 1791736066,   # 2026-10-11, despliegue b7e912c
     "name": "Cuenta real: solo partidos que empiezan en ≤ 2 h + liquidar cuando el ganador vale ≥ 99,7¢",
     "hypothesis": "Con los mismos $1000, la plata queda trabada ~4 h en vez de 8,4 h y entran ~el doble de "
                   "surebets por día, sin bajar el neto (~1,3 %) ni sumar pérdidas.",
     "kind": "real"},
    {"id": "auto-vivo",
     "ts": 1791736676,   # 2026-10-11, primera versión del automático en vivo simulado
     "name": "Automático en vivo (simulado): releer cada pata en la casa después del retraso real",
     "hypothesis": "Un bot por API llena al menos la mitad de las surebets en vivo aptas con neto > 0,5 %. "
                   "Si llena poco o el neto es ~0, en vivo no vale la pena.",
     "kind": "autovivo"},
]


def _metrics(ops: list[dict], a: float, b: float) -> dict:
    sel = [o for o in ops if a <= o["created_ts"] < b]
    days = max((min(b, time.time()) - a) / 86400, 1e-9)
    settled = [o for o in sel if o["status"] in ("won", "lost", "void") and o.get("settled_ts")]
    staked = sum(o["total_stake"] for o in settled)
    lock = [(o["settled_ts"] - o["created_ts"]) / 3600 for o in settled]
    return {"ops": len(sel), "per_day": round(len(sel) / days, 1), "days": round(days, 1),
            "lock_h": round(sum(lock) / len(lock), 1) if lock else None,
            "net": round(100 * sum(o["pnl"] or 0 for o in settled) / staked, 2) if staked else None,
            "pnl_day": round(sum(o["pnl"] or 0 for o in settled) / days, 2),
            "lost": sum(o["status"] == "lost" for o in settled), "settled": len(settled)}


def evaluate(ops: list[dict], started: float, live_exec: dict | None = None) -> list[dict]:
    out = []
    for e in EXPERIMENTS:
        if e["kind"] == "real":
            arbs = [o for o in ops if o["kind"] == "arb" and o["track"] == "real"]
            before, after = _metrics(arbs, started, e["ts"]), _metrics(arbs, e["ts"], float("inf"))
        else:
            auto = [o for o in ops if o["track"] == "autovivo"]
            before = None
            after = _metrics(auto, e["ts"], float("inf"))
            if live_exec:
                after.update({"fill_rate": live_exec.get("fill_rate"), "tries": live_exec.get("total"),
                              "exec_net": live_exec.get("avg_net")})
        out.append({**e, "before": before, "after": after})
    return out
