"""
"¿Listos para dinero real?": condiciones medibles para pasar cada estrategia del papel a real, por fases.

Cada condición se calcula sola con lo medido; las que no se pueden medir (cuentas, fondos, claves) van como
lista para revisar a mano. Las metas son prudentes a propósito: con pocos días cualquier ROI es ruido, y lo
que decide es el CLV y que las surebets de prepartido no pierdan.
"""

from __future__ import annotations

import os
import time

MIN_DAYS_PHASE1, MIN_DAYS_PHASE2 = 7, 14


def _c(name: str, value: float | None, target: float, fmt: str, higher: bool = True, unit: str = "") -> dict:
    ok = value is not None and (value >= target if higher else value <= target)
    progress = 0.0 if value is None else (min(1.0, value / target) if higher and target else (1.0 if ok else 0.5))
    return {"name": name, "value": "-" if value is None else fmt.format(value) + unit,
            "target": ("≥ " if higher else "≤ ") + fmt.format(target) + unit, "ok": ok, "progress": round(progress, 2)}


def evaluate(started: float, ops: list[dict], netpaper: dict | None, buttons: dict, is_gold) -> dict:
    now = time.time()
    days = (now - started) / 86400 if started else 0.0

    arbs = [o for o in ops if o["kind"] == "arb" and not o["live"] and o["status"] in ("won", "lost", "void")]
    arb_lost = sum(o["status"] == "lost" for o in arbs)
    staked = sum(o["total_stake"] for o in arbs)
    arb_net = 100 * sum(o["pnl"] or 0 for o in arbs) / staked if staked else None

    gold = [o for o in ops if o["kind"] == "value" and o.get("clv") is not None and is_gold(o)]
    clv = sum(o["clv"] for o in gold) / len(gold) if gold else None
    clv_pos = 100 * sum(o["clv"] > 0 for o in gold) / len(gold) if gold else None

    live = (netpaper or {}).get("live") or {}
    levels = {l["name"]: l for l in (netpaper or {}).get("by_level") or []}
    no_net = (levels.get("Sin red (solo valor)") or {}).get("exp_roi")
    best_net = max((l["exp_roi"] for n, l in levels.items() if n != "Sin red (solo valor)" and l["exp_roi"] is not None),
                   default=None)
    dd = ((netpaper or {}).get("risk") or {}).get("max_dd_pct")

    tries = buttons.get("clicked", 0) + buttons.get("rejected", 0)
    reject_rate = 100 * buttons.get("rejected", 0) / tries if tries >= 20 else None

    phase1 = [
        _c("Días medidos", days, MIN_DAYS_PHASE1, "{:.1f}", unit=" días"),
        _c("Surebets prepartido liquidadas", len(arbs), 200, "{:.0f}"),
        _c("Surebets prepartido perdidas (deben ser 0)", arb_lost, 0, "{:.0f}", higher=False),
        _c("Neto medio de surebets prepartido", arb_net, 1.0, "{:+.2f}", unit=" %"),
        _c("Toques de botones en demo (práctica)", tries, 30, "{:.0f}"),
    ]
    phase2 = [
        _c("Días medidos", days, MIN_DAYS_PHASE2, "{:.1f}", unit=" días"),
        _c("Apuestas oro con CLV calculado", len(gold), 200, "{:.0f}"),
        _c("CLV medio de las oro", clv, 1.5, "{:+.2f}", unit=" %"),
        _c("Oro con CLV a favor", clv_pos, 60, "{:.0f}", unit=" %"),
        _c("Picks de valor + red seguidos en vivo (no backtest)", live.get("decided", 0), 50, "{:.0f}"),
        _c("Caída máxima de valor + red", dd, 15, "{:.1f}", higher=False, unit=" %"),
        _c("1xBet rechazó o cambió la cuota (botón ❌)", reject_rate, 15, "{:.0f}", higher=False, unit=" %"),
    ]
    p1_ok, p2_ok = all(c["ok"] for c in phase1), all(c["ok"] for c in phase2)
    net_note = None
    if no_net is not None and best_net is not None:
        net_note = (f"Red: la mejor versión espera {best_net:+.2f} % vs {no_net:+.2f} % sin red. "
                    + ("Usar red." if best_net >= no_net else "Por ahora conviene sin red (o red solo para bajar riesgo)."))
    # Plan hacia $30/día (etapa actual: PLAN_STAGE en .env) y proyección con lo medido
    stage = os.getenv("PLAN_STAGE", "0")
    per_day = len(arbs) / days if days > 0 else 0.0
    feasible = min(25.0, per_day)                      # lo que una persona alcanza a hacer en el día
    net_used = min(arb_net, 1.3) if arb_net else 1.3   # conservador: rechazos y cuotas que cambian
    stake_now = float(os.getenv("ALERT_ARB_STAKE", "50"))
    stages = [
        {"id": "0", "name": "Práctica en demo", "when": "ahora · 1 semana", "stake": "$50 (demo)", "bank": "—",
         "target": "—", "gate": "Semáforo de la Fase 1 en verde (7 días, 30 toques de botones)",
         "progress": round(sum(c["progress"] for c in phase1) / len(phase1), 2)},
        {"id": "1", "name": "Real chico", "when": "semanas 2–3", "stake": "$50", "bank": "$1.000 ($500 1xBet / $500 Polymarket)",
         "target": "$10–13/día", "gate": "Neto real ≥ 1 %, rechazos de 1xBet ≤ 15 %, sin límites de monto"},
        {"id": "2", "name": "Subir el monto", "when": "semanas 4–6", "stake": "$100–120", "bank": "$2.000",
         "target": "$26–33/día ✔ meta $30", "gate": "Lo mismo y que 1xBet siga aceptando ~$50–60 por apuesta"},
        {"id": "2b", "name": "Sumar valor oro", "when": "con la Fase 2 en verde", "stake": "mínimo $30 (con red)",
         "bank": "+ $1.000", "target": "+ $4–8/día (con vaivén)", "gate": "CLV ≥ 1,5 % y 50 picks de valor + red en vivo"},
        {"id": "3", "name": "Escalar", "when": "si 1xBet no limita", "stake": "$200–300", "bank": "~$5.000",
         "target": "$60–100/día mientras duren las cuentas", "gate": "Monto máximo aceptado estable; rotar clones"},
    ]
    projection = [{"stake": st, "per_day": round(feasible, 1), "usd_day": round(feasible * st * net_used / 100, 2)}
                  for st in sorted({stake_now, 100.0, 300.0})]
    plan = {"stage": stage, "stages": stages, "projection": projection, "net_used": round(net_used, 2),
            "measured_per_day": round(per_day, 1)}
    return {
        "days": round(days, 1),
        "plan": plan,
        "phases": [
            {"name": "Fase 1 · Surebets prepartido 1xBet + Polymarket con dinero real",
             "plan": "$50 por surebet (ALERT_ARB_STAKE=50), tope $500 en juego, 2 semanas. Primero 1xBet, después Polymarket "
                     "(a mano o con el botón ✅ en REAL). Marca ❌ cada vez que 1xBet no acepte.",
             "checks": phase1, "ok": p1_ok},
            {"name": "Fase 2 · Apuestas de valor oro con $30 (y red si conviene)",
             "plan": "Mínimo $30 entre 1xBet y la red, tope $1000 en juego. Solo prepartido, solo oro.",
             "checks": phase2, "ok": p2_ok, "note": net_note},
            {"name": "Fase 3 · Automático Cloudbet / SX + Polymarket",
             "plan": "Solo si la simulación llena ≥ 70 % con neto positivo durante semanas (pestaña Automático). "
                     "Hoy hay muy pocas de prepartido: no es prioridad.",
             "checks": [], "ok": False},
        ],
        "manual": [
            "Polymarket a tu nombre, verificado y con USDC, operando desde Colombia (no desde Venezuela).",
            "1xBet / 888starz verificados (KYC) antes de ganar: así no te traban un retiro.",
            "Saldo cargado en los dos lados antes de empezar el día (no depositar con la oportunidad abierta).",
            "Para el botón en real: clave de Polymarket solo en el .env del VPS, pip install py-clob-client, POLY_LIVE=1.",
            "Topes ajustados: POLY_MAX_ORDER_USD y POLY_MAX_DAILY_USD acordes a la fase.",
            "Banca separada: solo plata que puedas perder sin que te afecte.",
        ],
    }
