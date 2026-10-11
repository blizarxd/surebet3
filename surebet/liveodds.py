"""
Cuota ACTUAL de cada apuesta abierta, para comparar con la cuota con la que se entró.

El escáner, cada EVERY_S, busca las patas de casa (1xBet, Cloudbet, SX...) de las operaciones abiertas entre
las cuotas que acaba de leer y guarda {ref: [cuota, cuándo se vio]} en data/live_odds.json. Si una cuota deja
de aparecer (la casa la quitó o el partido salió del escaneo) queda la última vista, con su hora.
El panel lo lee para mostrar "entró a 2.02 → ahora 1.83".
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

EVERY_S = 60
KEEP_S = 2 * 86400


def leg_ref(source: str, url: str, market: str, label: str) -> str:
    return json.dumps([source, url, market, label])


class LiveOdds:
    def __init__(self, db_path: Path, out_path: Path) -> None:
        self.db_path, self.out_path = db_path, out_path
        self._last = 0.0
        try:
            self.data = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def update(self, clusters) -> None:
        """`clusters`: los partidos (MatchedEvent) leídos en este ciclo (ver scan.scan_cycle)."""
        now = time.time()
        if now - self._last < EVERY_S:
            return
        self._last = now
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            rows = conn.execute("SELECT market, legs_json FROM paper_ops WHERE status='open'").fetchall()
            conn.close()
        except sqlite3.OperationalError:
            return
        wanted = set()
        for market, legs_json in rows:
            for l in json.loads(legs_json or "[]"):
                if l.get("source") != "polymarket":
                    wanted.add(leg_ref(l.get("source"), l.get("url", ""), market, l.get("label")))
        from .models import default_market
        for c in clusters:
            for ev, _ in c.members:
                if ev.feed == "polymarket":
                    continue
                for q in ev.quotes:
                    ref = leg_ref(q.source, q.url, q.market or default_market(ev.three_way), q.label)
                    if ref in wanted:
                        self.data[ref] = [round(q.odds, 3), q.ts]
        self.data = {k: v for k, v in self.data.items() if k in wanted or now - v[1] < KEEP_S}
        try:
            tmp = self.out_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            tmp.replace(self.out_path)
        except OSError:
            pass


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
