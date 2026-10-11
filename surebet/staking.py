"""
Monto de las apuestas de valor (un solo lado en la casa, con red opcional en Polymarket).

Kelly fraccional (1/4) con tope de 3 % de la banca, pero nunca menos de lo que hace que la operación
completa (casa + orden límite de la red) sume VALUE_MIN_TOTAL. Con montos de $3–8 no vale la pena el
trabajo: el mínimo lo pide el usuario (2026-10-10: "mínimo 30 entre los 2").
"""

from __future__ import annotations

import math

NET_LOCK_PCT = 1.0


def net_limit(odds: float, lock_pct: float = NET_LOCK_PCT) -> float:
    """Precio de la orden límite del lado contrario que asegura lock_pct (redondeado al centavo hacia abajo)."""
    return math.floor((1 / (1 + lock_pct / 100) - 1 / odds) * 100 + 1e-9) / 100


def value_stake(odds: float, ev_pct: float, bankroll: float, fraction: float = 0.25, max_pct: float = 0.03,
                min_total: float = 0.0, limit: float | None = None) -> float:
    """Monto en la casa. `min_total`: mínimo entre los dos lados; con red, el lado de Polymarket es
    cobro × precio límite, así que la casa pone min_total / (1 + cuota × límite). Sin red, la mitad."""
    k = max(0.0, (ev_pct / 100) / (odds - 1)) if odds > 1 else 0.0
    stake, cap = bankroll * k * fraction, bankroll * max_pct
    if min_total > 0:
        floor = min_total / (1 + odds * limit) if limit and limit > 0 else min_total / 2
        stake, cap = max(stake, floor), max(cap, floor)
    return round(min(stake, cap), 2)
