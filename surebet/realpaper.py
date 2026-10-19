"""
Tablero "📒 Real vs papel" del valor automático en Azuro: lo que apostó la wallet de verdad contra lo que anotó el papel.

  REAL  = las apuestas de la wallet del bot en Azuro (tabla real_bets: la llena el ejecutor desde el subgraph oficial de
          Azuro, con la cuota aceptada, el resultado y el pago reales).
  PAPEL = la sombra (sx_shadow): las señales oro de Azuro que se habrían llenado con el precio real, sin CS2/Dota2,
          recalculadas al MISMO monto por pick que el real (AUTO_AZURO_STAKE) para que se puedan comparar.
El papel siempre tiene más picks: anota todas las señales; el real solo las que el ejecutor llegó a apostar (topes,
saldo, partido repetido, la cuota cambió...). Las del papel que el real no tomó son "señales perdidas", no pérdidas.
Deslizamiento = cuánto cambió la cuota entre el papel (cuota real leída al ver la señal) y la que aceptó Azuro.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .models import market_label

COL = timezone(timedelta(hours=-5))
OBSERVED = ("esports:cs2", "esports:dota2")
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

REAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS real_bets (
    bet_id INTEGER PRIMARY KEY,   -- id de la apuesta en Azuro (token)
    created_ts REAL, amount REAL, odds REAL,
    status TEXT,                  -- Accepted | Resolved | Canceled (Azuro)
    result TEXT,                  -- Won | Lost | NULL
    payout REAL, pnl REAL,        -- pnl NULL mientras no se resuelve
    ref TEXT,                     -- condición|resultado (igual que sx_shadow.sx_ref y live_exec.az_ref)
    outcome_title TEXT, game_id TEXT,
    exec_id INTEGER, title TEXT, market TEXT, ev REAL, redeemed INTEGER, updated_ts REAL
);
"""


def _day(ts: float) -> str:
    d = datetime.fromtimestamp(ts, COL)
    return f"{DIAS[d.weekday()]} {d.day:02d}/{d.month:02d}"


def family(label: str) -> str:
    label = label or "?"
    if label.startswith("Más/Menos 1T"):
        return "Más/Menos 1T"
    if label.startswith("Más/Menos"):
        return "Más/Menos"
    return label


def ev_band(ev: float | None) -> str:
    if ev is None:
        return "?"
    return "EV 2–3 %" if ev < 3 else "EV 3–5 %" if ev < 5 else "EV 5–7 %" if ev < 7 else "EV 7 %+"


def bet_pnl(status: str, result: str | None, amount: float, payout: float | None) -> float | None:
    """Ganancia de una apuesta real de Azuro: anulada = 0 (devuelve), ganada = pago − monto, perdida = −monto."""
    if status == "Canceled":
        return 0.0
    if result == "Won":
        return round((payout or 0) - amount, 4)
    if result == "Lost":
        return -amount
    return None


def _group(rows: list[dict]) -> dict:
    done = [r for r in rows if r["pnl"] is not None]
    wins = sum(r["won"] for r in done)
    staked = sum(r["stake"] for r in done)
    return {"n": len(rows), "settled": len(done), "win": round(100 * wins / len(done)) if done else None,
            "odds": round(sum(r["odds"] for r in rows) / len(rows), 3) if rows else None,
            "pnl": round(sum(r["pnl"] for r in done), 2), "roi": round(100 * sum(r["pnl"] for r in done) / staked, 2) if staked else None}


def summary(conn) -> dict | None:
    try:
        meta = conn.execute("SELECT v FROM paper_meta WHERE k='executor'").fetchone()
    except sqlite3.OperationalError:
        return None
    cfg = json.loads(meta[0]) if meta else {}
    stake = float(cfg.get("azuro_stake") or 5)
    try:
        real = [{"ts": ts, "title": title or otitle, "market": market or "?", "label": otitle, "odds": odds, "stake": amount,
                 "pnl": pnl, "won": result == "Won", "status": status, "ref": ref, "ev": ev, "bet_id": bid}
                for bid, ts, amount, odds, status, result, pnl, ref, otitle, title, market, ev in conn.execute(
                    "SELECT bet_id, created_ts, amount, odds, status, result, pnl, ref, outcome_title, title, market, ev "
                    "FROM real_bets ORDER BY created_ts")]
    except sqlite3.OperationalError:
        real = []
    try:
        shadow = conn.execute(
            "SELECT created_ts, title, sport, market, label, fill_odds, signal_ev, result, sx_ref, sx_seen_odds FROM sx_shadow "
            "WHERE house='azuro' AND COALESCE(tier,'oro')='oro' AND status='llenada' ORDER BY created_ts").fetchall()
    except sqlite3.OperationalError:
        shadow = []
    paper = []
    for ts, title, sport, market, label, odds, ev, result, ref, seen in shadow:
        if (sport or "") in OBSERVED or not odds:
            continue
        pnl = {"ganada": stake * (odds - 1), "perdida": -stake, "anulada": 0.0}.get(result)
        paper.append({"ts": ts, "title": title, "market": market_label(market or ""), "label": label, "odds": odds,
                      "stake": stake, "pnl": None if pnl is None else round(pnl, 2), "won": result == "ganada",
                      "status": result or "pendiente", "ref": ref, "ev": ev, "seen": seen})

    paper_by_ref: dict[str, dict] = {}
    for p in paper:
        paper_by_ref.setdefault(p["ref"], p)
    for r in real:   # apuesta real sin fila del ejecutor (p. ej. hecha a mano): partido y mercado del papel si existe
        p = paper_by_ref.get(r["ref"])
        if p:
            r["market"] = r["market"] if r["market"] != "?" else p["market"]
            r["title"] = r["title"] or p["title"]
            r["ev"] = r["ev"] if r["ev"] is not None else p["ev"]

    days: dict[str, dict] = {}
    for side, rows in (("real", real), ("paper", paper)):
        for r in rows:
            days.setdefault(_day(r["ts"]), {"day": _day(r["ts"]), "ts": r["ts"], "real": [], "paper": []})[side].append(r)
    day_rows = []
    for d in sorted(days.values(), key=lambda d: d["ts"]):
        g_real, g_paper = _group(d["real"]), _group(d["paper"])
        winner = None
        if d["real"] and g_real["settled"] and g_paper["settled"]:
            per_r = g_real["pnl"] / g_real["settled"]
            per_p = g_paper["pnl"] / g_paper["settled"]
            winner = "real" if per_r > per_p + 1e-9 else "papel" if per_p > per_r + 1e-9 else "igual"
        day_rows.append({"day": d["day"], "real": g_real, "paper": g_paper, "winner": winner})

    # Deslizamiento: la cuota que aceptó Azuro contra la del papel para la MISMA selección
    slips = []
    for r in real:
        p = paper_by_ref.get(r["ref"])
        if p and p["odds"]:
            slips.append(round(100 * (r["odds"] / p["odds"] - 1), 2))
    favor, contra = [s for s in slips if s > 0], [s for s in slips if s < 0]

    # Señales del papel que el real no tomó (desde la primera apuesta real)
    real_refs = {r["ref"] for r in real}
    t0 = real[0]["ts"] if real else None
    reasons = {}
    try:
        for ref, reason in conn.execute("SELECT az_ref, reason FROM live_exec WHERE mode='live' AND status IN "
                                        "('rejected','error') ORDER BY created_ts"):
            reasons[ref] = reason
    except sqlite3.OperationalError:
        pass
    missed = [{**p, "why": reasons.get(p["ref"], "el ejecutor no la intentó")} for p in paper
              if t0 and p["ts"] >= t0 and p["ref"] not in real_refs]

    def split(key) -> list[dict]:
        names = sorted({key(r) for r in real + paper})
        return [{"name": n, "real": _group([r for r in real if key(r) == n]),
                 "paper": _group([p for p in paper if key(p) == n])} for n in names]

    def curve(rows: list[dict]) -> list[list[float]]:
        acc, out = 0.0, []
        for r in rows:
            if r["pnl"] is not None:
                acc += r["pnl"]
                out.append([r["ts"] * 1000, round(acc, 2)])
        return out

    return {
        "stake": stake, "mode": cfg.get("mode"), "status": cfg.get("status"), "wallet": cfg.get("wallet"),
        "total": {"real": _group(real), "paper": _group(paper)},
        "days": day_rows, "curve": {"real": curve(real), "paper": curve(paper)},
        "slip": {"n": len(slips), "avg": round(sum(slips) / len(slips), 2) if slips else None,
                 "favor": len(favor), "favor_avg": round(sum(favor) / len(favor), 2) if favor else None,
                 "contra": len(contra), "contra_avg": round(sum(contra) / len(contra), 2) if contra else None},
        "by_market": split(lambda r: family(r["market"])), "by_ev": split(lambda r: ev_band(r["ev"])),
        "missed": list(reversed(missed))[:60],
        "real_rows": list(reversed(real))[:200], "paper_rows": list(reversed(paper))[:300],
    }
