"""
Marcador en vivo de las apuestas de valor abiertas (columna «Resultado» del dashboard).

Cada ~2 min consulta a 1xBet (LiveFeed/GetGameZip, el mismo feed con el que se apuesta) SOLO los partidos de las
apuestas abiertas que ya empezaron: son unas pocas llamadas, sin tocar el ritmo del escaneo normal. El resultado se
guarda en la tabla live_scores y el dashboard lo lee; si el dato es viejo (el partido terminó o 1xBet no responde)
la web vuelve a mostrar «abierta».
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time

import httpx

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_scores (
    game_id TEXT PRIMARY KEY,
    ts REAL,
    text TEXT,
    ended INTEGER DEFAULT 0     -- 1 = el feed ya no lo trae (terminó): text es el último marcador visto = final
);
"""
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
ID_RE = re.compile(r"/(\d+)-[^/]+$")
INTERVAL_S = 120
LATE_INTERVAL_S = 30      # recta final (fútbol desde el 78', básquet 4.º cuarto): así el último marcador visto es el final
MAX_PER_CYCLE = 12
EARLY_S = 45 * 60          # también los que «empiezan» en < 45 min: la hora guardada puede ser la de Polymarket, que en tenis llega 30 min tarde
BAD_LIMIT, HOLD_S = 3, 600   # 3 fallas seguidas propias: pausa de 10 min
KEEP_DAYS = 30
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
    if "finish" in low or low in ("ended", "final", "game over"):
        return "terminado"
    m = re.match(r"(\d+)(?:st|nd|rd|th) (half|quarter|set|map|period|inning)", low)
    if m:
        i, kind = m.group(1), m.group(2)
        return {"half": f"{i}.er tiempo" if i == "1" else f"{i}.º tiempo", "quarter": f"Q{i}",
                "set": f"{i}.er set" if i == "1" else f"{i}.º set", "map": f"mapa {i}", "period": f"periodo {i}",
                "inning": f"entrada {i}"}[kind]
    return low


def is_late(sport: str, value: dict) -> bool:
    sc = value.get("SC") if isinstance(value.get("SC"), dict) else {}
    fam = sport.split(":")[0]
    if fam == "soccer":
        return isinstance(sc.get("TS"), (int, float)) and sc["TS"] >= 78 * 60
    if fam == "basketball":
        cps = str(sc.get("CPS") or "").lower()
        return cps.startswith("4th") or "overtime" in cps
    return False


def is_finished(value: dict) -> bool:
    """El feed a veces deja el partido un rato con el periodo «game finished» antes de quitarlo."""
    sc = value.get("SC") if isinstance(value.get("SC"), dict) else {}
    return _period(str(sc.get("CPS") or "")) == "terminado"


def final_score(text: str) -> str:
    """De «1-0 · 88'» o «sets 2-1 · 3.º set 6-4» deja solo el marcador («1-0», «sets 2-1»)."""
    return text.split(" · ")[0]


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
    def __init__(self, db_path: str, base: str, partner: str | int, interval_s: float = INTERVAL_S,
                 guard=None) -> None:
        """guard: función que devuelve True si 1xBet está con problemas (el escáner principal tiene prioridad: aquí se frena)."""
        self.guard = guard
        self._hold_until = 0.0
        self._bad_in_row = 0
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.executescript(SCHEMA)
        try:                     # base anterior a la columna «ended»
            self.conn.execute("ALTER TABLE live_scores ADD COLUMN ended INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        self.base = base.rstrip("/")
        self.partner = partner
        self.interval_s = interval_s
        self._due: dict[str, float] = {}
        self.status = ""

    def _games(self, now: float) -> list[tuple[str, str]]:
        """Partidos a consultar: los de apuestas abiertas ya empezadas y, para anotar el marcador final, los de apuestas
        recién cerradas de las que ya se vio el marcador en vivo y aún no se vio terminar el partido."""
        rows = self.conn.execute(
            "SELECT legs_json, sport, status FROM paper_ops WHERE track IN ('valor','amplio') AND kind='value' "
            "AND start_ts < ? AND start_ts > ?", (now + EARLY_S, now - MAX_AGE_H * 3600)).fetchall()
        seen = {g: e for g, e in self.conn.execute("SELECT game_id, ended FROM live_scores")}
        out: dict[str, str] = {}
        for legs, sport, status in rows:
            try:
                url = (json.loads(legs)[0] or {}).get("url") or ""
            except (ValueError, IndexError, TypeError):
                continue
            gid = game_id(url)
            if not gid or gid in out:
                continue
            if status == "open" or (gid in seen and not seen[gid]):
                out[gid] = sport or ""
        return [(g, s) for g, s in out.items() if self._due.get(g, 0) <= now][:MAX_PER_CYCLE]

    async def update(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        if now < self._hold_until or (self.guard and self.guard()):
            self.status = "en pausa (1xBet con fallas: el escáner principal tiene prioridad)"
            return
        games = self._games(now)
        ok = 0
        for gid, sport in games:
            url = (f"{self.base}/service-api/LiveFeed/GetGameZip?id={gid}&lng=en&isSubGames=false&GroupEvents=false"
                   f"&countevents=1&partner={self.partner}")
            try:
                r = await client.get(url, headers=UA, timeout=15)
                data = r.json()
            except (httpx.HTTPError, ValueError):
                self._bad_in_row += 1
                if self._bad_in_row >= BAD_LIMIT:        # 1xBet no contesta: se deja de insistir un rato
                    self._hold_until = time.time() + HOLD_S
                    self._bad_in_row = 0
                    break
                continue
            self._bad_in_row = 0
            value =data.get("Value") if isinstance(data, dict) else None
            text = describe(sport, value) if isinstance(value, dict) else ""
            if text:
                self.conn.execute("INSERT OR REPLACE INTO live_scores VALUES (?,?,?,?)",
                                  (gid, time.time(), text, 1 if is_finished(value) else 0))
                self._due[gid] = time.time() + (LATE_INTERVAL_S if is_late(sport, value) else self.interval_s)
                ok += 1
            else:        # el feed ya no lo trae: el partido terminó; se queda el último marcador visto como final
                self.conn.execute("UPDATE live_scores SET ended=1 WHERE game_id=?", (gid,))
                self._due[gid] = time.time() + self.interval_s
            await asyncio.sleep(GAP_S)
        self.conn.execute("DELETE FROM live_scores WHERE ts < ?", (now - KEEP_DAYS * 86400,))
        self.conn.commit()
        if len(self._due) > 500:
            self._due = {g: d for g, d in self._due.items() if d > now}
        self.status = f"{ok}/{len(games)} partidos"
