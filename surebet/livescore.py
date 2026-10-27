"""
Marcador en vivo de las apuestas de valor abiertas (columna «Resultado» del dashboard).

Cada ~2 min consulta a 1xBet (LiveFeed/GetGameZip, el mismo feed con el que se apuesta) SOLO los partidos de las
apuestas abiertas que ya empezaron: son unas pocas llamadas, sin tocar el ritmo del escaneo normal. El resultado se
guarda en la tabla live_scores y el dashboard lo lee; si el dato es viejo (el partido terminó o 1xBet no responde)
la web vuelve a mostrar «abierta».
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import time

import httpx

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_scores (
    game_id TEXT PRIMARY KEY,
    ts REAL,
    text TEXT
);
"""
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
ID_RE = re.compile(r"/(\d+)-[^/]+$")
INTERVAL_S = 120
MAX_GAMES = 25
GAP_S = 0.8
MAX_AGE_H = 5            # un partido que empezó hace más de esto ya terminó: no se consulta
FRESH_S = 6 * 60         # el dashboard solo muestra el marcador si es de hace menos de esto


def game_id(url: str) -> str | None:
    m = ID_RE.search(url or "")
    return m.group(1) if m else None


def _period(name: str) -> str:
    n = (name or "").strip()
    low = n.lower()
    if low in ("half time", "halftime", "break", "interval"):
        return "descanso"
    m = re.match(r"(\d+)(?:st|nd|rd|th) (half|quarter|set|map|period|inning)", low)
    if m:
        i, kind = m.group(1), m.group(2)
        return {"half": f"{i}.er tiempo" if i == "1" else f"{i}.º tiempo", "quarter": f"Q{i}",
                "set": f"{i}.er set" if i == "1" else f"{i}.º set", "map": f"mapa {i}", "period": f"periodo {i}",
                "inning": f"entrada {i}"}[kind]
    return low


def describe(sport: str, value: dict) -> str:
    """Texto corto del marcador: «1-0 · 32'», «sets 1-1 · 3.er set 4-2», «mapas 0-0 · mapa 1 10-10»."""
    sc = value.get("SC")
    if not isinstance(sc, dict):
        return ""
    fs = sc.get("FS") if isinstance(sc.get("FS"), dict) else {}
    a, b = fs.get("S1", 0), fs.get("S2", 0)
    period = _period(str(sc.get("CPS") or ""))
    ps = [p.get("Value") or {} for p in (sc.get("PS") or []) if isinstance(p, dict)]
    cur = f"{ps[-1].get('S1', 0)}-{ps[-1].get('S2', 0)}" if ps else ""
    fam = sport.split(":")[0]
    if fam == "soccer":
        ts = sc.get("TS")
        minute = f"{int(ts) // 60}'" if isinstance(ts, (int, float)) and ts > 0 and period != "descanso" else ""
        return f"{a}-{b} · {minute or period}".strip(" ·")
    if fam == "tennis":
        return f"sets {a}-{b}" + (f" · {period} {cur}" if cur else "")
    if fam == "esports":
        return f"mapas {a}-{b}" + (f" · {period} {cur}" if cur else "")
    return f"{a}-{b}" + (f" · {period}" if period else "")


class LiveScores:
    def __init__(self, db_path: str, base: str, partner: str | int, interval_s: float = INTERVAL_S) -> None:
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.executescript(SCHEMA)
        self.base = base.rstrip("/")
        self.partner = partner
        self.interval_s = interval_s
        self._last = 0.0
        self.status = ""

    def _open_games(self, now: float) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT legs_json, sport FROM paper_ops WHERE track IN ('valor','amplio') AND kind='value' AND status='open' "
            "AND start_ts < ? AND start_ts > ?", (now, now - MAX_AGE_H * 3600)).fetchall()
        import json
        out: dict[str, str] = {}
        for legs, sport in rows:
            try:
                url = (json.loads(legs)[0] or {}).get("url") or ""
            except (ValueError, IndexError, TypeError):
                continue
            gid = game_id(url)
            if gid:
                out.setdefault(gid, sport or "")
        return list(out.items())[:MAX_GAMES]

    async def update(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        if now - self._last < self.interval_s:
            return
        self._last = now
        games = self._open_games(now)
        ok = 0
        for gid, sport in games:
            url = (f"{self.base}/service-api/LiveFeed/GetGameZip?id={gid}&lng=en&isSubGames=false&GroupEvents=false"
                   f"&countevents=1&partner={self.partner}")
            try:
                r = await client.get(url, headers=UA, timeout=15)
                data = r.json()
            except (httpx.HTTPError, ValueError):
                continue
            value = data.get("Value") if isinstance(data, dict) else None
            text = describe(sport, value) if isinstance(value, dict) else ""
            if text:
                self.conn.execute("INSERT OR REPLACE INTO live_scores VALUES (?,?,?)", (gid, time.time(), text))
                ok += 1
            await asyncio.sleep(GAP_S)
        self.conn.execute("DELETE FROM live_scores WHERE ts < ?", (now - 24 * 3600,))
        self.conn.commit()
        self.status = f"{ok}/{len(games)} partidos"
