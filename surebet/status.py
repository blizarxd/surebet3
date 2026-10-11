"""Estado del escáner para el panel web: fuentes y las mejores señales del momento (data/status.json)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .arb import ValueBet
from .alerts import BOOK_NAMES, _max_poly_price, _min_odds, book_of_the_day, gross_and_net
from .arb import POLYMARKET
from .models import Opportunity, market_label


def _leg(o: Opportunity, i: int, rotate: list[str]) -> dict:
    l = o.legs[i]
    q = l.quote
    mo = _min_odds(o.legs, i)
    house = BOOK_NAMES.get(q.source, q.source)
    if q.source in ("1xbet", "onexbet") and rotate:
        house = book_of_the_day(rotate)
    gross, net = gross_and_net(q, l.stake, l.odds)
    d = {"source": q.source, "house": house, "label": q.label, "odds": round(l.odds, 4), "stake": round(l.stake, 2),
         "gross": round(gross, 3), "net": round(net, 3),
         "url": q.url, "min_odds": round(mo, 3) if mo != float("inf") else None}
    if q.source == POLYMARKET:
        d["max_price"] = round(_max_poly_price(mo, q.fee_rate, q.fee_exponent), 4) if mo != float("inf") else None
    return d


def _opp(o: Opportunity, rotate: list[str] | None = None) -> dict:
    ev = o.event.anchor
    order = sorted(range(len(o.legs)), key=lambda i: o.legs[i].quote.source in (POLYMARKET, "sxbet"))
    return {
        "blocked": any(w.startswith("⛔") for w in o.warnings),
        "fast_legs": [_leg(o, i, rotate or []) for i in order],
        "title": ev.title, "sport": ev.sport, "market": market_label(o.market), "live": o.event.live,
        "margin": round(o.margin_pct, 3), "profit": round(o.profit, 2), "total": round(o.total_stake, 2),
        "max_total": round(o.max_total_stake, 2), "warnings": o.warnings,
        "legs": [{"source": l.quote.source, "label": l.quote.label, "odds": round(l.odds, 4),
                  "stake": round(l.stake, 2), "url": l.quote.url} for l in o.legs],
    }


def _value(v: ValueBet, bankroll: float, kelly_fraction: float) -> dict:
    ev = v.event.anchor
    stake = min(max(bankroll * v.kelly * kelly_fraction, 1.0), bankroll * 0.03)
    return {
        "title": ev.title, "sport": ev.sport, "market": market_label(v.market), "live": v.event.live,
        "source": v.quote.source, "label": v.quote.label, "odds": round(v.quote.odds, 3),
        "fair_odds": round(1 / v.fair_prob, 3), "ev": round(v.ev_pct, 2), "stake": round(stake, 2), "url": v.quote.url,
    }


def write_status(path: Path, sources, res, s, cycle_s: float, bankroll: float, top: int = 15) -> None:
    """Solo lo bueno: arbitrajes entre el margen mínimo y el sospechoso, y valor dentro de rango."""
    opps = [o for o in res.opps if s.min_margin_pct <= o.margin_pct < 8.0][:top]
    values = [v for v in res.values if s.value_min_ev_pct <= v.ev_pct < 15][:top]
    data = {
        "ts": time.time(), "cycle_s": round(cycle_s, 2), "matched": res.n_matched, "events": res.n_clusters,
        "sources": [{"name": src.name, "status": src.status} for src in sources],
        "opps": [_opp(o, s.rotate_books) for o in opps],
        "values": [_value(v, bankroll, s.kelly_fraction) for v in values],
        # Lo más cerca de ser surebet ahora mismo (aunque todavía pierda): para ver qué tan lejos estamos
        "near": [_opp(o) for o in res.radar if o.margin_pct < s.min_margin_pct][:10],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
