"""
Conversión de precios a cuotas efectivas.

Polymarket (docs.polymarket.com/trading/fees):
    fee_usdc = C × feeRate × p × (1 − p)        C = shares, p = precio
Deportes: feeRate = 0.05, solo taker. Comprar 1 share a precio p cuesta
    p × (1 + feeRate × (1 − p))
y paga 1 USDC si gana  ->  cuota decimal efectiva = 1 / ese costo.

Ejemplo: p = 0.50 -> cuesta 0.5125 -> cuota 1.951 (no 2.00 como creía el código viejo).
"""

from __future__ import annotations

from .models import BookLevel, Quote


def poly_fee_per_share(price: float, fee_rate: float, exponent: float = 1.0) -> float:
    return fee_rate * (price * (1.0 - price)) ** exponent


def poly_cost_per_share(price: float, fee_rate: float, exponent: float = 1.0) -> float:
    return price + poly_fee_per_share(price, fee_rate, exponent)


def poly_odds(price: float, fee_rate: float, exponent: float = 1.0) -> float:
    return 1.0 / poly_cost_per_share(price, fee_rate, exponent)


def odds_after_commission(odds: float, commission: float) -> float:
    """Exchanges (Betfair, etc.) cobran comisión sobre la ganancia neta."""
    return 1.0 + (odds - 1.0) * (1.0 - commission)


def fill_budget(asks: tuple[BookLevel, ...], budget: float, fee_rate: float,
                exponent: float = 1.0) -> tuple[float, float]:
    """
    Recorre el libro (asks ordenados de menor a mayor precio) gastando hasta `budget`
    USDC incluyendo comisiones. Devuelve (shares obtenidas, USDC gastados).
    """
    shares = spent = 0.0
    remaining = budget
    for lvl in asks:
        if remaining <= 1e-9:
            break
        cost = poly_cost_per_share(lvl.price, fee_rate, exponent)
        take = min(lvl.size, remaining / cost)
        shares += take
        spent += take * cost
        remaining -= take * cost
    return shares, spent


def book_capacity(asks: tuple[BookLevel, ...], fee_rate: float, exponent: float = 1.0) -> float:
    """USDC necesarios para barrer todo el lado ask disponible."""
    return sum(l.size * poly_cost_per_share(l.price, fee_rate, exponent) for l in asks)


def effective_odds(quote: Quote, stake: float) -> float | None:
    """
    Cuota real que obtienes apostando `stake` en esta quote.
    None si no hay liquidez/límite suficiente para ese monto.
    """
    if quote.min_stake and stake < quote.min_stake - 1e-9:
        return None
    if quote.asks:
        shares, spent = fill_budget(quote.asks, stake, quote.fee_rate, quote.fee_exponent)
        if spent < stake - 0.01 or shares <= 0:
            return None
        return shares / spent
    if quote.max_stake is not None and stake > quote.max_stake + 1e-9:
        return None
    return quote.odds


def fill_detail(asks: tuple[BookLevel, ...], budget: float, fee_rate: float,
                exponent: float = 1.0) -> dict[str, float]:
    """
    Simula una compra real en Polymarket gastando `budget` USDC (comisión incluida):
    shares obtenidas, precio medio pagado, comisión total y niveles del libro usados.
    """
    shares = spent = fee = 0.0
    levels = 0
    remaining = budget
    for lvl in asks:
        if remaining <= 1e-9:
            break
        cost = poly_cost_per_share(lvl.price, fee_rate, exponent)
        take = min(lvl.size, remaining / cost)
        if take <= 0:
            continue
        shares += take
        spent += take * cost
        fee += take * poly_fee_per_share(lvl.price, fee_rate, exponent)
        remaining -= take * cost
        levels += 1
    avg = (spent - fee) / shares if shares else 0.0
    return {"shares": shares, "spent": spent, "fee": fee, "avg_price": avg, "levels": levels}
