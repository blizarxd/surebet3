"""
Valor 1xBet A MANO con aviso limpio y botones (20/10): mide si tú, a mano, logras lo que hace el papel.

Cada pick de valor oro de 1xBet que entra a la cuenta de valor en papel (track 'valor', ~47/día) se avisa por Telegram
solo dentro de tu horario (MANUAL_VALUE_HOURS, hora Colombia; por defecto 8–20) y si estás en el PC (/estoy … /salgo).
Las de fuera de horario se anotan igual ("fuera de horario") para saber cuánto se pierde por no estar.

El aviso trae partido, hora, selección, cuota de ahora, cuota mínima aceptable (EV ≥ +1 % contra Polymarket) y el monto
fijo (MANUAL_VALUE_STAKE, $30). Botones: abrir en 1xBet, «✅ la puse a X» (tres cuotas: la del aviso, una intermedia y la
mínima) y «❌ no estaba / bajó». El resultado sale del mismo pick de la cuenta en papel (misma clave), así que se compara
1 a 1: tu ganancia real (tu cuota, $30) contra el papel (cuota del aviso, $30) y las que no tomaste.
Nunca apuesta nada en 1xBet (no hay bot sobre su web: regla del proyecto).
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .safety import OBSERVE_SPORTS

COL = timezone(timedelta(hours=-5))
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
SOURCES = ("1xbet", "onexbet")
MIN_EV = 1.0                 # cuota mínima aceptable: EV ≥ +1 % contra el precio justo de Polymarket
LOOKBACK_S = 900             # picks del papel de los últimos 15 min (no avisar viejos tras reiniciar)
SPORT_ICON = {"soccer": "⚽", "basketball": "🏀", "tennis": "🎾", "hockey": "🏒", "baseball": "⚾",
              "americanfootball": "🏈", "volleyball": "🏐", "mma": "🥊", "boxing": "🥊"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_value (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE,            -- la misma clave del pick en la cuenta de valor en papel (paper_ops.key)
    created_ts REAL, start_ts REAL, title TEXT, sport TEXT, market TEXT, label TEXT, source TEXT,
    odds REAL,                  -- cuota del aviso (la del papel)
    min_odds REAL,              -- mínima aceptable (EV +1 %)
    fair_odds REAL, ev_pct REAL, stake REAL, url TEXT,
    status TEXT,                -- avisada | puesta | no estaba | fuera de horario | no estabas
    placed_odds REAL,           -- la cuota a la que dijiste que la pusiste
    clicked_ts REAL
);
CREATE TABLE IF NOT EXISTS manual_value_meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _hours(raw: str) -> tuple[int, int]:
    try:
        a, b = (int(x) for x in raw.replace(" ", "").split("-"))
        return a, b
    except ValueError:
        return 8, 20


def _fmt_odds(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def _day(ts: float) -> str:
    d = datetime.fromtimestamp(ts, COL)
    return f"{DIAS[d.weekday()]} {d.day:02d}/{d.month:02d}"


def odds_levels(odds: float, min_odds: float) -> list[float]:
    """Las cuotas de los botones ✅: la del aviso, una intermedia y la mínima (sin repetir)."""
    mid = round((odds + min_odds) / 2, 2)
    out = []
    for x in (round(odds, 2), mid, round(min_odds, 2)):
        if x not in out and x <= round(odds, 2):
            out.append(x)
    return out


class ManualValor:
    def __init__(self, db_path: Path, stake: float = 30.0, hours: str = "8-20") -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.stake = stake
        self.start_h, self.end_h = _hours(hours)

    # ------------------------------------------------------------------ estado (/estoy, /salgo)
    def active(self) -> bool:
        r = self.conn.execute("SELECT v FROM manual_value_meta WHERE k='active'").fetchone()
        return r is None or r[0] == "1"

    def set_active(self, on: bool) -> str:
        self.conn.execute("INSERT OR REPLACE INTO manual_value_meta VALUES ('active', ?)", ("1" if on else "0",))
        self.conn.commit()
        if on:
            return (f"🟢 Avisos de valor 1xBet ACTIVOS ({self.start_h}–{self.end_h} h, ${self.stake:g} por pick). "
                    "/salgo cuando te vayas del PC.")
        return "⏸ Avisos de valor 1xBet en pausa (se siguen anotando para medir). /estoy cuando vuelvas al PC."

    def in_hours(self, ts: float) -> bool:
        h = datetime.fromtimestamp(ts, COL).hour
        return self.start_h <= h < self.end_h

    # ------------------------------------------------------------------ cada ciclo
    def step(self, now: float | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
        """Los picks nuevos de 1xBet de la cuenta de valor en papel → mensajes (texto, botones) para Telegram."""
        now = now or time.time()
        rows = self.conn.execute(
            "SELECT p.key, p.created_ts, p.start_ts, p.title, p.sport, p.market_name, p.market, p.edge_pct, p.legs_json "
            "FROM paper_ops p LEFT JOIN manual_value m ON m.key = p.key WHERE p.track='valor' AND p.kind='value' "
            "AND p.created_ts > ? AND m.key IS NULL ORDER BY p.created_ts", (now - LOOKBACK_S,)).fetchall()
        out = []
        on = self.active()
        for key, ts, start, title, sport, mname, market, ev, legs in rows:
            leg = (json.loads(legs or "[]") or [{}])[0]
            if leg.get("source") not in SOURCES or (sport or "") in OBSERVE_SPORTS:
                continue
            odds = float(leg.get("odds") or 0)
            fair = float(leg.get("fair_odds") or 0) or (odds / (1 + (ev or 0) / 100) if odds else 0)
            if odds <= 1 or fair <= 1:
                continue
            min_odds = min(math.ceil((1 + MIN_EV / 100) * fair * 100) / 100, round(odds, 2))
            status = "avisada" if on and self.in_hours(now) else ("no estabas" if not on else "fuera de horario")
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO manual_value (key, created_ts, start_ts, title, sport, market, label, source, odds, "
                "min_odds, fair_odds, ev_pct, stake, url, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, now, start, title, sport, mname or market, leg.get("label"), leg.get("source"), odds, min_odds,
                 fair, ev, self.stake, leg.get("url") or "", status))
            if status == "avisada" and cur.rowcount:
                out.append(self._message(cur.lastrowid, now))
        self.conn.commit()
        return out

    def _message(self, mid: int, now: float) -> tuple[str, list[tuple[str, str]]]:
        start, title, sport, market, label, odds, min_odds, ev, stake, url = self.conn.execute(
            "SELECT start_ts, title, sport, market, label, odds, min_odds, ev_pct, stake, url FROM manual_value WHERE id=?",
            (mid,)).fetchone()
        when = ""
        if start:
            mins = max(0, int((start - now) / 60))
            when = f" · empieza {datetime.fromtimestamp(start, COL):%H:%M} (en {mins // 60} h {mins % 60:02d} min)" \
                if mins >= 60 else f" · empieza {datetime.fromtimestamp(start, COL):%H:%M} (en {mins} min)"
        text = "\n".join([
            f"💰 VALOR 1xBet {ev:+.1f} % · #{mid}",
            f"{SPORT_ICON.get(sport, '🎯')} {title}{when}",
            f"👉 {market}: {label}",
            f"Cuota {_fmt_odds(odds)} · tómala si está a {_fmt_odds(min_odds)} o más",
            f"Apuesta: ${stake:g}",
        ])
        buttons = [("Abrir en 1xBet", url)] if url.startswith("http") else []
        buttons += [(f"✅ La puse a {_fmt_odds(x)}", f"cb:mv:ok:{mid}:{x:.2f}") for x in odds_levels(odds, min_odds)]
        buttons.append(("❌ No estaba / bajó de la mínima", f"cb:mv:no:{mid}"))
        return text, buttons

    # ------------------------------------------------------------------ botones
    def click(self, data: str) -> str:
        """data: 'mv:ok:<id>:<cuota>' | 'mv:no:<id>'."""
        parts = data.split(":")
        try:
            mid = int(parts[2])
        except (IndexError, ValueError):
            return "Botón desconocido."
        row = self.conn.execute("SELECT title, label, status, stake FROM manual_value WHERE id=?", (mid,)).fetchone()
        if not row:
            return "No encontré ese aviso."
        title, label, status, stake = row
        if parts[1] == "ok":
            try:
                odds = float(parts[3])
            except (IndexError, ValueError):
                return "Botón desconocido."
            self.conn.execute("UPDATE manual_value SET status='puesta', placed_odds=?, clicked_ts=? WHERE id=?",
                              (odds, time.time(), mid))
            self.conn.commit()
            again = " (corregida)" if status == "puesta" else ""
            return f"✅ Anotada{again}: {label} a {_fmt_odds(odds)} · ${stake:g} — {title}. {self.today_line()}"
        self.conn.execute("UPDATE manual_value SET status='no estaba', placed_odds=NULL, clicked_ts=? WHERE id=?",
                          (time.time(), mid))
        self.conn.commit()
        return f"❌ Anotado: {label} no estaba a la cuota mínima — {title}."

    def today_line(self) -> str:
        t = today_stats(self.conn)
        return f"Hoy: {t['placed']} puestas de {t['alerted']} avisadas."

    def text_today(self) -> str:
        t = today_stats(self.conn)
        return (f"📒 Valor 1xBet a mano · hoy\n"
                f"Avisadas {t['alerted']} · puestas {t['placed']} · no estaban {t['missed']} · sin tocar {t['untouched']}\n"
                f"Fuera de horario / no estabas: {t['away']}\n"
                f"Tu ganancia (cerradas): ${t['pnl']:+,.2f} · el papel en las mismas: ${t['paper_pnl']:+,.2f}\n"
                f"Avisos {'ACTIVOS' if self.active() else 'EN PAUSA'} ({self.start_h}–{self.end_h} h)")


# ---------------------------------------------------------------------- resultados y panel
def _rows(conn) -> list[dict]:
    cols = ("id", "key", "created_ts", "start_ts", "title", "sport", "market", "label", "odds", "min_odds", "ev_pct",
            "stake", "status", "placed_odds", "clicked_ts", "result")
    try:
        rs = conn.execute(
            "SELECT m.id, m.key, m.created_ts, m.start_ts, m.title, m.sport, m.market, m.label, m.odds, m.min_odds, "
            "m.ev_pct, m.stake, m.status, m.placed_odds, m.clicked_ts, p.status FROM manual_value m "
            "LEFT JOIN paper_ops p ON p.key = m.key ORDER BY m.created_ts").fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rs:
        d = dict(zip(cols, r))
        res = d["result"]
        settled = res in ("won", "lost", "void")
        d["paper_pnl"] = None if not settled else {"won": d["stake"] * (d["odds"] - 1), "lost": -d["stake"], "void": 0.0}[res]
        po = d["placed_odds"]
        d["pnl"] = None if not (settled and d["status"] == "puesta" and po) else \
            {"won": d["stake"] * (po - 1), "lost": -d["stake"], "void": 0.0}[res]
        out.append(d)
    return out


def today_stats(conn, now: float | None = None) -> dict:
    day = _day(now or time.time())
    rs = [r for r in _rows(conn) if _day(r["created_ts"]) == day]
    alerted = [r for r in rs if r["status"] in ("avisada", "puesta", "no estaba")]
    placed = [r for r in rs if r["status"] == "puesta"]
    return {"alerted": len(alerted), "placed": len(placed), "missed": sum(r["status"] == "no estaba" for r in rs),
            "untouched": sum(r["status"] == "avisada" for r in rs),
            "away": sum(r["status"] in ("fuera de horario", "no estabas") for r in rs),
            "pnl": round(sum(r["pnl"] or 0 for r in placed), 2),
            "paper_pnl": round(sum(r["paper_pnl"] or 0 for r in placed), 2)}


def summary(conn) -> dict | None:
    rows = _rows(conn)
    if not rows:
        return None

    def agg(rs: list[dict]) -> dict:
        placed = [r for r in rs if r["status"] == "puesta"]
        closed = [r for r in placed if r["pnl"] is not None]
        alerted = [r for r in rs if r["status"] in ("avisada", "puesta", "no estaba")]
        slips = [100 * (r["placed_odds"] / r["odds"] - 1) for r in placed if r["placed_odds"] and r["odds"]]
        away = [r for r in rs if r["status"] in ("fuera de horario", "no estabas")]
        return {"n": len(rs), "alerted": len(alerted), "placed": len(placed),
                "missed": sum(r["status"] == "no estaba" for r in rs), "untouched": sum(r["status"] == "avisada" for r in rs),
                "away": len(away), "closed": len(closed),
                "pnl": round(sum(r["pnl"] for r in closed), 2),
                "paper_pnl": round(sum(r["paper_pnl"] for r in closed), 2),            # el papel en las mismas que pusiste
                "alerted_paper_pnl": round(sum(r["paper_pnl"] or 0 for r in alerted), 2),  # el papel en todo lo avisado
                "away_paper_pnl": round(sum(r["paper_pnl"] or 0 for r in away), 2),
                "slip": round(sum(slips) / len(slips), 2) if slips else None,
                "win": round(100 * sum(r["result"] == "won" for r in closed) / len(closed)) if closed else None}

    days: dict[str, list] = {}
    for r in rows:
        days.setdefault(_day(r["created_ts"]), []).append(r)
    run, curve = 0.0, []
    for r in rows:
        if r["pnl"] is not None:
            run += r["pnl"]
            curve.append([r["created_ts"] * 1000, round(run, 2)])
    return {"total": agg(rows), "days": [{"day": k, **agg(v)} for k, v in reversed(list(days.items()))],
            "curve": curve,
            "rows": [{k: r[k] for k in ("created_ts", "start_ts", "title", "sport", "market", "label", "odds", "min_odds",
                                         "ev_pct", "stake", "status", "placed_odds", "result", "pnl", "paper_pnl")}
                     for r in reversed(rows)][:200]}
