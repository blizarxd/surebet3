"""
Registro de oportunidades en SQLite.

Lo importante no es solo encontrar un arbitraje sino CUÁNTO DURA: si en vivo duran
2 segundos no se pueden ejecutar a mano. first_seen/last_seen responde eso.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .arb import ValueBet
from .models import Opportunity

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    key TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    sport TEXT,
    title TEXT,
    live INTEGER,
    best_margin_pct REAL,
    last_margin_pct REAL,
    max_total_stake REAL,
    legs_json TEXT
);
CREATE TABLE IF NOT EXISTS value_bets (
    key TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    sport TEXT,
    title TEXT,
    start_ts REAL,
    source TEXT,
    label TEXT,
    covers TEXT,
    odds REAL,          -- cuota cuando se detectó (la que habrías tomado)
    best_odds REAL,
    fair_prob REAL,
    ev_pct REAL,
    poly_tokens TEXT,   -- {resultado: token SÍ de Polymarket} para medir el cierre
    url TEXT,
    close_prob REAL,    -- lo completa clv.py
    clv_pct REAL
);
"""


class OpportunityLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.executescript(SCHEMA)

    def record(self, opps: list[Opportunity]) -> list[Opportunity]:
        """Guarda/actualiza y devuelve las que son nuevas (para alertar)."""
        now = time.time()
        new: list[Opportunity] = []
        cur = self.conn.cursor()
        for o in opps:
            legs = json.dumps([
                {"source": l.quote.source, "label": l.quote.label, "odds": round(l.odds, 4),
                 "stake": round(l.stake, 2), "covers": sorted(l.quote.covers)}
                for l in o.legs
            ], ensure_ascii=False)
            row = cur.execute("SELECT last_seen FROM opportunities WHERE key = ?", (o.key,)).fetchone()
            if row is None or now - row[0] > 300:
                new.append(o)
            if row is None:
                cur.execute(
                    "INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (o.key, now, now, 1, o.event.anchor.sport, o.event.anchor.title, int(o.event.live),
                     o.margin_pct, o.margin_pct, o.max_total_stake, legs),
                )
            else:
                cur.execute(
                    """UPDATE opportunities SET last_seen=?, seen_count=seen_count+1,
                       best_margin_pct=MAX(best_margin_pct, ?), last_margin_pct=?,
                       max_total_stake=?, legs_json=?, live=? WHERE key=?""",
                    (now, o.margin_pct, o.margin_pct, o.max_total_stake, legs, int(o.event.live), o.key),
                )
        self.conn.commit()
        return new

    def duration_s(self, key: str) -> float:
        row = self.conn.execute("SELECT first_seen, last_seen FROM opportunities WHERE key=?", (key,)).fetchone()
        return (row[1] - row[0]) if row else 0.0

    def record_value(self, bets: list[ValueBet]) -> list[ValueBet]:
        """Guarda/actualiza apuestas de valor; devuelve las nuevas (para alertar)."""
        now = time.time()
        new: list[ValueBet] = []
        cur = self.conn.cursor()
        for v in bets:
            row = cur.execute("SELECT 1 FROM value_bets WHERE key = ?", (v.key,)).fetchone()
            if row is None:
                new.append(v)
                ev = v.event.anchor
                cur.execute(
                    """INSERT INTO value_bets (key, first_seen, last_seen, sport, title, start_ts, source,
                       label, covers, odds, best_odds, fair_prob, ev_pct, poly_tokens, url)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (v.key, now, now, ev.sport, ev.title, ev.start.timestamp() if ev.start else None,
                     v.quote.source, v.quote.label, "".join(sorted(v.quote.covers)), v.quote.odds,
                     v.quote.odds, v.fair_prob, v.ev_pct, json.dumps(v.poly_tokens), v.quote.url),
                )
            else:
                cur.execute("UPDATE value_bets SET last_seen=?, best_odds=MAX(best_odds, ?) WHERE key=?",
                            (now, v.quote.odds, v.key))
        self.conn.commit()
        return new
