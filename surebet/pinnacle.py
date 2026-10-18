"""
Referencia "en sombra": ¿Pinnacle estima mejor que Polymarket? (solo mide, no cambia ninguna decisión)

En cada señal de valor de 1xBet donde OddsPapi también trae las cuotas de Pinnacle, se guarda:
  - ev_pol / fair_pol: el EV y la probabilidad justa que usa el sistema hoy (precio medio de Polymarket);
  - ev_pin / fair_pin: lo mismo con la probabilidad justa de Pinnacle (sus cuotas sin margen, proporcional);
  - dist_pin_pct: cuánto más paga 1xBet que Pinnacle por ese mismo resultado.

Para qué sirve la distancia: las cuotas de Overtime Markets vienen de Pinnacle (docs.overtime.io, "About Odds
Providers") y luego se les resta su propio margen, así que Overtime nunca paga más que Pinnacle. Si 1xBet ya paga
más de 1,5 % por encima de Pinnacle, Overtime queda todavía más lejos: no sirve como reemplazo de 1xBet. Es una
cota superior, sin necesitar la clave de la API de Overtime (que no se da para arbitraje ni para bots).

Lo que se compara con el cierre: |fair - close_prob|, con close_prob = precio de cierre de Polymarket (lo completa
clv.py). OJO: ese cierre es de Polymarket, así que favorece a Polymarket; el CLV partido según lo que dice
Pinnacle (acuerdo / desacuerdo) es la lectura más justa.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .models import market_outcomes

PIN = "pinnacle"
BOOKS = ("1xbet", "onexbet")
MAX_PIN_AGE_S = 3 * 3600      # OddsPapi trae rondas cada pocas horas: más viejo que esto no se compara
APT_DIST_PCT = 1.5            # regla del usuario: "apta" si la otra casa está a ≤ 1,5 % de 1xBet

SCHEMA = """
CREATE TABLE IF NOT EXISTS ref_shadow (
    key TEXT PRIMARY KEY,          -- igual que value_bets.key (para unir el CLV)
    first_ts REAL, source TEXT, title TEXT, sport TEXT, market TEXT, selection TEXT, start_ts REAL,
    odds REAL,                     -- cuota de 1xBet
    ev_pol REAL, fair_pol REAL,    -- referencia actual: Polymarket
    ev_pin REAL, fair_pin REAL,    -- referencia nueva: Pinnacle sin margen
    divergence REAL,               -- ev_pin - ev_pol
    pin_odds REAL, pin_margin_pct REAL, pin_age_s REAL,
    dist_pin_pct REAL              -- cuánto más paga 1xBet que Pinnacle
);
"""


def pinnacle_view(event, market: str, now: float | None = None) -> dict | None:
    """Probabilidades sin margen de Pinnacle para este mercado, o None si no hay cuotas completas y frescas."""
    now = now or time.time()
    outcomes = market_outcomes(market)
    best: dict[str, object] = {}
    for q in event.quotes_for(market):
        if q.source == PIN and len(q.covers) == 1 and q.odds > 1.0:
            (o,) = q.covers
            if o not in best or q.ts > best[o].ts:
                best[o] = q
    if set(best) != outcomes:
        return None
    age = now - min(q.ts for q in best.values())
    if age > MAX_PIN_AGE_S:
        return None
    inv = {o: 1.0 / q.odds for o, q in best.items()}
    total = sum(inv.values())
    return {"probs": {o: p / total for o, p in inv.items()}, "inv": inv, "margin_pct": 100 * (total - 1.0),
            "age_s": age}


def shadow_row(v, now: float | None = None) -> dict | None:
    """Comparación de una señal de valor con la referencia de Pinnacle (None si Pinnacle no la cubre)."""
    now = now or time.time()
    view = pinnacle_view(v.event, v.market, now)
    if view is None:
        return None
    fair_pin = sum(view["probs"][o] for o in v.quote.covers)
    pin_inv = sum(view["inv"][o] for o in v.quote.covers)       # cuota de Pinnacle para lo mismo = 1 / suma
    pin_odds = 1.0 / pin_inv
    ev_pin = 100 * (v.quote.odds * fair_pin - 1)
    return {"fair_pin": fair_pin, "ev_pin": ev_pin, "ev_pol": v.ev_pct, "fair_pol": v.fair_prob,
            "divergence": ev_pin - v.ev_pct, "pin_odds": pin_odds, "pin_margin_pct": view["margin_pct"],
            "pin_age_s": view["age_s"], "dist_pin_pct": 100 * (v.quote.odds / pin_odds - 1)}


class RefShadow:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def record(self, values: list) -> int:
        """Guarda las señales de valor de 1xBet nuevas que Pinnacle también cubre. Devuelve cuántas."""
        now = time.time()
        n = 0
        for v in values:
            if v.quote.source not in BOOKS:
                continue
            if self.conn.execute("SELECT 1 FROM ref_shadow WHERE key=?", (v.key,)).fetchone():
                continue
            r = shadow_row(v, now)
            if r is None:
                continue
            a = v.event.anchor
            self.conn.execute(
                "INSERT INTO ref_shadow (key, first_ts, source, title, sport, market, selection, start_ts, odds, ev_pol, "
                "fair_pol, ev_pin, fair_pin, divergence, pin_odds, pin_margin_pct, pin_age_s, dist_pin_pct) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (v.key, now, v.quote.source, a.title, a.sport, v.market, v.quote.label,
                 a.start.timestamp() if a.start else None, v.quote.odds, round(r["ev_pol"], 3), round(r["fair_pol"], 4),
                 round(r["ev_pin"], 3), round(r["fair_pin"], 4), round(r["divergence"], 3), round(r["pin_odds"], 4),
                 round(r["pin_margin_pct"], 2), round(r["pin_age_s"]), round(r["dist_pin_pct"], 2)))
            n += 1
        self.conn.commit()
        return n


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def _clv_group(rows: list[dict]) -> dict:
    c = [r["clv"] for r in rows if r["clv"] is not None]
    return {"n": len(rows), "with_clv": len(c), "clv": _avg(c),
            "clv_pos": round(100 * sum(x > 0 for x in c) / len(c)) if c else None}


def summary(conn) -> dict:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "ref_shadow" not in tables:
        return {"n": 0}
    join = "value_bets" in tables
    sql = ("SELECT r.first_ts, r.title, r.selection, r.market, r.odds, r.ev_pol, r.ev_pin, r.divergence, r.pin_odds, "
           "r.dist_pin_pct, r.fair_pol, r.fair_pin, " + ("vb.close_prob, vb.clv_pct FROM ref_shadow r "
                                                         "LEFT JOIN value_bets vb ON vb.key = r.key " if join else
                                                         "NULL, NULL FROM ref_shadow r ") + "ORDER BY r.first_ts DESC")
    cols = ("ts", "title", "selection", "market", "odds", "ev_pol", "ev_pin", "divergence", "pin_odds", "dist",
            "fair_pol", "fair_pin", "close", "clv")
    rows = [dict(zip(cols, r)) for r in conn.execute(sql)]
    closed = [r for r in rows if r["close"] is not None]
    total_signals = conn.execute("SELECT COUNT(*) FROM value_bets WHERE source IN ('1xbet','onexbet')").fetchone()[0] \
        if join else None
    apt = [r for r in rows if r["dist"] <= APT_DIST_PCT]
    return {
        "n": len(rows), "coverage_of": total_signals,
        "avg_ev_pol": _avg([r["ev_pol"] for r in rows]), "avg_ev_pin": _avg([r["ev_pin"] for r in rows]),
        "avg_divergence": _avg([r["divergence"] for r in rows]),
        "closed": len(closed),
        "err_pol": _avg([100 * abs(r["fair_pol"] - r["close"]) for r in closed]),
        "err_pin": _avg([100 * abs(r["fair_pin"] - r["close"]) for r in closed]),
        "agree": _clv_group([r for r in rows if r["ev_pin"] >= 1.0]),       # Pinnacle también ve valor
        "disagree": _clv_group([r for r in rows if r["ev_pin"] < 0.0]),     # Pinnacle dice que no hay valor
        "apt": len(apt), "avg_dist": _avg([r["dist"] for r in rows]),
        "rows": rows[:50],
    }
