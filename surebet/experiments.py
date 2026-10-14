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
     "kind": "real", "until": 1791831279,
     "verdict": "NO VÁLIDA: casi toda la prueba corrió con la liquidación trabada (base bloqueada por la simulación "
                "de recompensas, 11/10 13:35 → 12/10 14:20, hora Colombia): apuestas terminadas tardaron 13–18 h en cerrarse, la plata "
                "quedaba en juego y por eso entraron pocas. Se volvió a 6 h; se puede repetir con la base sana"},
    {"id": "auto-vivo",
     "ts": 1791736676,   # 2026-10-11, primera versión del automático en vivo simulado
     "name": "Automático en vivo (simulado): releer cada pata en la casa después del retraso real",
     "hypothesis": "Un bot por API llena al menos la mitad de las surebets en vivo aptas con neto > 0,5 %. "
                   "Si llena poco o el neto es ~0, en vivo no vale la pena.",
     "kind": "autovivo", "verdict": "DESCARTADO el 12/10: 1 llenada de 38 (la cuota baja o la pata desaparece en el retraso)"},
    {"id": "real-6h-early",
     "ts": 1791832785,   # 2026-10-12: vuelta a 6 h, medida desde el arreglo de la base bloqueada
     "name": "Cuenta real: otra vez hasta 6 h antes del partido, con liquidación anticipada",
     "hypothesis": "Vuelve el volumen (~30 surebets/día como antes) pero con la plata trabada menos que las 8,5 h "
                   "de antes (≈ 5 h), gracias a liquidar en cuanto el partido queda decidido.",
     "kind": "real"},
    {"id": "auto-500",
     "ts": 1791832785,   # 2026-10-12: automático de prepartido hasta lo que aguante el libro (desde el arreglo)
     "name": "Automático prepartido: monto hasta lo que aguante el libro (tope $500)",
     "hypothesis": "Poner lo que aguanta cada libro (el monto de más ganancia, tope $500) en vez de $100 duplica "
                   "la ganancia por día del automático. El 'antes' es la historia RE-SIMULADA con esta regla (estimada: "
                   "a $100 fijos eran $13,27 y re-simulado ~$27); el 'después' es lo medido: sirve para ver si la "
                   "estimación se cumple.",
     "kind": "auto"},
    {"id": "auto-kalshi-azuro",
     "ts": 1791851518,   # 2026-10-12: Kalshi (exchange, API) y Azuro (Polygon, API) se suman al automático
     "name": "Automático con Kalshi y Azuro (más casas por API)",
     "hypothesis": "Con dos casas más que se operan por API (Kalshi ~480 partidos/día, Azuro ~300), el automático "
                   "prepartido pasa de ~7 surebets/día a 15 o más y se acerca a $30/día. Si en una semana no suben "
                   "las surebets automáticas, esas casas no aportan.",
     "kind": "auto"},
    {"id": "auto-1500",
     "ts": 1791945241,   # 2026-10-13: tope $1.500 por surebet, 90 % de lo que aguanta el libro, cuenta automática $3.000
     "name": "Automático: tope $1.500 por surebet (90 % de lo que aguanta el libro)",
     "hypothesis": "Subir el tope de $500 a $1.500 lleva el automático de ~$17/día a ~$30/día. La ganancia extra "
                   "depende de pocas surebets con libro grande: si en una semana no pasa de ~$22/día, no vale el "
                   "capital extra ($3.000-4.000 repartidos entre casas).",
     "kind": "auto"},
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
        if e["kind"] in ("real", "auto"):
            arbs = [o for o in ops if o["kind"] == "arb" and o["track"] == e["kind"]]
            before, after = _metrics(arbs, started, e["ts"]), _metrics(arbs, e["ts"], e.get("until", float("inf")))
        else:
            auto = [o for o in ops if o["track"] == "autovivo"]
            before = None
            after = _metrics(auto, e["ts"], float("inf"))
            if live_exec:
                after.update({"fill_rate": live_exec.get("fill_rate"), "tries": live_exec.get("total"),
                              "exec_net": live_exec.get("avg_net")})
        out.append({**e, "before": before, "after": after})
    return out
