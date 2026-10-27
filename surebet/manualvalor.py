"""
Valor 1xBet A MANO con aviso limpio y botones (20/10): mide si tú, a mano, logras lo que hace el papel.

Cada pick de valor oro de 1xBet que entra a la cuenta de valor en papel (track 'valor', ~47/día) se avisa por Telegram
solo dentro de tu horario (MANUAL_VALUE_HOURS, hora Venezuela (UTC−4); por defecto 8–20) y si estás en el PC (/estoy … /salgo).
Las de fuera de horario se anotan igual ("fuera de horario") para saber cuánto se pierde por no estar.

El aviso trae partido, hora, selección, cuota de ahora, cuota mínima aceptable (EV ≥ +1 % contra Polymarket) y el monto
fijo (MANUAL_VALUE_STAKE, $30). Botones: abrir en 1xBet, «✅ la puse a X» (tres cuotas: la del aviso, una intermedia y la
mínima) y «❌ no estaba / bajó». El resultado sale del mismo pick de la cuenta en papel (misma clave), así que se compara
1 a 1: tu ganancia real (tu cuota, $30) contra el papel (cuota del aviso, $30) y las que no tomaste.
Nunca apuesta nada en 1xBet (no hay bot sobre su web: regla del proyecto).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .safety import OBSERVE_SPORTS

VET = timezone(timedelta(hours=-4))          # Venezuela (UTC−4), sin horario de verano
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
SOURCES = ("1xbet", "onexbet")
HOUSES = ("1xBet", "Melbet", "888starz")   # casas donde apuestas a mano (MANUAL_HOUSES en el .env); /casa elige la de hoy
NO_ALERT_SPORTS = ("basketball",)   # sin ventaja en 1xBet (CLV −0,1 %, 32 señales oro; 26/10): se anota, no se avisa
MAX_PER_GAME = 2             # tope de avisos por partido (2 picks de $30 como máximo; los demás se anotan sin avisar)
BATCH_S, BATCH_MIN = 60, 3   # «lote»: 3 o más señales nacidas en 60 s (suele ser 1xBet volviendo de un corte)
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
    clicked_ts REAL,
    house TEXT                  -- la casa donde la pusiste (o donde no estaba), la que estaba activa con /casa
);
CREATE TABLE IF NOT EXISTS manual_value_meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _hours(raw: str) -> tuple[int, int]:
    try:
        a, b = (int(x) for x in raw.replace(" ", "").split("-"))
        return a, b
    except ValueError:
        return 8, 20


def _clean_domain(raw: str) -> str:
    """'https://www.melbet.com/es/line' -> 'www.melbet.com' (solo el dominio)."""
    raw = raw.strip().lower()
    host = urlsplit(raw if "//" in raw else "//" + raw).netloc or raw.split("/")[0]
    return host if "." in host and " " not in host else ""


def swap_domain(url: str, domain: str) -> str | None:
    """El link del partido de 1xBet con el dominio de otra casa (las casas de su misma familia usan la misma ruta)."""
    if not url.startswith("http") or not domain:
        return None
    u = urlsplit(url)
    return urlunsplit(("https", domain, u.path, "", ""))


def _fmt_odds(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def _day(ts: float) -> str:
    d = datetime.fromtimestamp(ts, VET)
    return f"{DIAS[d.weekday()]} {d.day:02d}/{d.month:02d}"


def odds_levels(odds: float, min_odds: float) -> list[float]:
    """Las cuotas de los botones ✅: la del aviso, una intermedia y la mínima (sin repetir)."""
    mid = round((odds + min_odds) / 2, 2)
    out = []
    for x in (round(odds, 2), mid, round(min_odds, 2)):
        if x not in out and x <= round(odds, 2):
            out.append(x)
    return out


# ------------------------------------------------------------------ marcar desde el panel web (los mismos ✅ / ❌ de Telegram)
def _hash_key(raw: str) -> str:
    return hashlib.sha256(("surebet3|" + (raw or "").strip()).encode("utf-8")).hexdigest()


def set_panel_key(conn: sqlite3.Connection, raw: str) -> str:
    """La clave del panel se pone SOLO desde Telegram (tu chat): /clave <texto>. Sin clave, el panel no deja marcar."""
    raw = (raw or "").strip()
    conn.executescript(SCHEMA)
    if not raw:
        conn.execute("DELETE FROM manual_value_meta WHERE k='panel_key'")
        conn.commit()
        return "🔒 Clave del panel borrada: los botones de la web quedan desactivados."
    if len(raw) < 4:
        return "La clave debe tener al menos 4 caracteres: /clave tuclave"
    conn.execute("INSERT OR REPLACE INTO manual_value_meta VALUES ('panel_key', ?)", (_hash_key(raw),))
    conn.commit()
    return "🔑 Clave del panel guardada. En la web, al marcar ✅/❌ por primera vez te la pide (queda en ese navegador). Borra este mensaje del chat."


def check_panel_key(conn: sqlite3.Connection, raw: str) -> bool:
    try:
        r = conn.execute("SELECT v FROM manual_value_meta WHERE k='panel_key'").fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(r and r[0] and raw and hmac.compare_digest(r[0], _hash_key(raw)))


def _ensure_row(conn: sqlite3.Connection, key: str) -> int | None:
    """Señales que nunca se avisaron (CS2/Dota2 en observación, etc.) no tienen fila: se crea al marcarlas desde el panel."""
    r = conn.execute("SELECT id FROM manual_value WHERE key=?", (key,)).fetchone()
    if r:
        return r[0]
    p = conn.execute("SELECT created_ts, start_ts, title, sport, market_name, market, edge_pct, legs_json FROM paper_ops "
                     "WHERE key=? AND track IN ('valor','amplio')", (key,)).fetchone()
    if not p:
        return None
    ts, start, title, sport, mname, market, ev, legs = p
    leg = (json.loads(legs or "[]") or [{}])[0]
    odds = float(leg.get("odds") or 0)
    fair = float(leg.get("fair_odds") or 0) or (odds / (1 + (ev or 0) / 100) if odds else 0)
    if odds <= 1 or fair <= 1:
        return None
    min_odds = min(math.ceil((1 + MIN_EV / 100) * fair * 100) / 100, round(odds, 2))
    conn.execute("INSERT OR IGNORE INTO manual_value (key, created_ts, start_ts, title, sport, market, label, source, odds, "
                 "min_odds, fair_odds, ev_pct, stake, url, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (key, ts, start, title, sport, mname or market, leg.get("label"), leg.get("source"), odds, min_odds,
                  fair, ev, 30.0, leg.get("url") or "", "sin aviso"))
    conn.commit()
    r = conn.execute("SELECT id FROM manual_value WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


WRONG_RESULTS = ("pendiente", "ganada", "perdida", "anulada")


def ensure_wrong_cols(conn: sqlite3.Connection) -> None:
    """Columnas de «jugué otra apuesta»: qué jugaste en realidad, con cuánto y cómo salió (lo pones tú)."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(manual_value)")}
    for col, kind in (("wrong_pick", "TEXT"), ("wrong_stake", "REAL"), ("wrong_result", "TEXT")):
        if col not in have:
            conn.execute(f"ALTER TABLE manual_value ADD COLUMN {col} {kind}")
    conn.commit()


def mark_signal(conn: sqlite3.Connection, mid: int | None, action: str, odds: float | None = None, house: str | None = None,
                key: str | None = None, pick: str | None = None, stake: float | None = None, result: str | None = None) -> dict:
    """Lo mismo que los botones de Telegram: 'ok' = la puse (a la cuota que digas) · 'no' = no estaba. Sirve para cualquier señal.
    'wrong' = jugaste OTRA apuesta del partido (te equivocaste de selección): se guarda qué jugaste, cuota, monto y el resultado
    que marques tú (el papel solo sabe liquidar la apuesta de la señal)."""
    ensure_wrong_cols(conn)
    if mid is None and key:
        mid = _ensure_row(conn, key)
    row = conn.execute("SELECT status, odds FROM manual_value WHERE id=?", (mid,)).fetchone() if mid is not None else None
    if not row:
        return {"ok": False, "error": f"No encontré la señal #{mid}." if mid is not None else "No encontré esa señal."}
    h = next((x for x in HOUSES if x.lower() == (house or "").strip().lower()), None)
    if h is None:
        m = conn.execute("SELECT v FROM manual_value_meta WHERE k='house'").fetchone()
        h = (m[0] if m and m[0] in HOUSES else "1xBet")
    if action == "ok":
        if odds is None or not (1.0 < float(odds) < 1000):
            return {"ok": False, "error": "La cuota debe ser un número mayor que 1."}
        conn.execute("UPDATE manual_value SET status='puesta', placed_odds=?, clicked_ts=?, house=?, wrong_pick=NULL, "
                     "wrong_stake=NULL, wrong_result=NULL WHERE id=?", (round(float(odds), 3), time.time(), h, mid))
        conn.commit()
        return {"ok": True, "id": mid, "status": "puesta", "placed_odds": round(float(odds), 3), "house": h, "prev": row[0]}
    if action == "no":
        conn.execute("UPDATE manual_value SET status='no estaba', placed_odds=NULL, clicked_ts=?, house=?, wrong_pick=NULL, "
                     "wrong_stake=NULL, wrong_result=NULL WHERE id=?", (time.time(), h, mid))
        conn.commit()
        return {"ok": True, "id": mid, "status": "no estaba", "placed_odds": None, "house": h, "prev": row[0]}
    if action == "wrong":
        pick = (pick or "").strip()[:40]
        res = (result or "pendiente").strip().lower()
        try:
            o, s = float(odds), float(stake)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Pon la cuota y el monto que jugaste."}
        if not pick:
            return {"ok": False, "error": "Elige qué apuesta jugaste."}
        if not (1.0 < o < 1000) or not (0 < s < 100000):
            return {"ok": False, "error": "La cuota debe ser mayor que 1 y el monto mayor que 0."}
        if res not in WRONG_RESULTS:
            return {"ok": False, "error": "Resultado no válido."}
        conn.execute("UPDATE manual_value SET status='mal jugada', placed_odds=?, clicked_ts=?, house=?, wrong_pick=?, wrong_stake=?, "
                     "wrong_result=? WHERE id=?", (round(o, 3), time.time(), h, pick, round(s, 2), res, mid))
        conn.commit()
        return {"ok": True, "id": mid, "status": "mal jugada", "placed_odds": round(o, 3), "house": h, "prev": row[0],
                "wp": pick, "ws": round(s, 2), "wr": res}
    return {"ok": False, "error": "Acción no válida."}


class ManualValor:
    def __init__(self, db_path: Path, stake: float = 30.0, hours: str = "8-20", houses: tuple[str, ...] = HOUSES) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        if "house" not in {r[1] for r in self.conn.execute("PRAGMA table_info(manual_value)")}:
            self.conn.execute("ALTER TABLE manual_value ADD COLUMN house TEXT")
        self.conn.commit()
        ensure_wrong_cols(self.conn)
        self.stake = stake
        self.start_h, self.end_h = _hours(hours)
        self._load_hours()
        self.houses = tuple(houses) or HOUSES

    # ------------------------------------------------------------------ dominio de cada casa (/dominio)
    def domain(self, house: str) -> str | None:
        r = self.conn.execute("SELECT v FROM manual_value_meta WHERE k=?", (f"domain:{house}",)).fetchone()
        return r[0] if r and r[0] else None

    def set_domain(self, args: str = "") -> str:
        words = args.split()
        if not words:
            conf = [f"{h}: {self.domain(h) or ('sin dominio (usa el del escáner)' if h == '1xBet' else 'sin dominio')}" for h in self.houses]
            return "🌐 Dominios de las casas (para armar el link del partido):\n" + "\n".join(conf) + \
                "\nPonerlo: /dominio melbet melbet.com"
        pick = next((h for h in self.houses if h.lower() == words[0].lower()), None)
        if not pick:
            return "Casa no válida. Usa: " + ", ".join(h.lower() for h in self.houses)
        dom = _clean_domain(words[1]) if len(words) > 1 else ""
        if not dom:
            return f"Falta el dominio. Ejemplo: /dominio {pick.lower()} {pick.lower()}.com"
        self.conn.execute("INSERT OR REPLACE INTO manual_value_meta VALUES (?, ?)", (f"domain:{pick}", dom))
        self.conn.commit()
        return (f"🌐 {pick} → {dom}. Desde ahora el aviso trae el link del partido en {pick}.\n"
                f"Pruébalo con el próximo aviso: si no abre el mismo partido, avísame (cambia la ruta según la casa).")

    # ------------------------------------------------------------------ casa de hoy (/casa)
    def house(self) -> str:
        r = self.conn.execute("SELECT v FROM manual_value_meta WHERE k='house'").fetchone()
        return r[0] if r and r[0] in self.houses else self.houses[0]

    def set_house(self, name: str = "") -> str:
        name = name.strip().lower()
        if not name:
            return f"🏦 Casa de hoy: {self.house()}\nCambiar: " + " · ".join(f"/casa {h.lower()}" for h in self.houses)
        pick = next((h for h in self.houses if h.lower() == name), None)
        if not pick:
            return f"No conozco «{name}». Casas: " + ", ".join(self.houses)
        self.conn.execute("INSERT OR REPLACE INTO manual_value_meta VALUES ('house', ?)", (pick,))
        self.conn.commit()
        return f"🏦 Casa de hoy: {pick}. Tus ✅ y ❌ de ahora en adelante se anotan en {pick}."

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

    def _load_hours(self) -> None:
        """El horario que fijaste con /horario (si no, el de MANUAL_VALUE_HOURS)."""
        r = self.conn.execute("SELECT v FROM manual_value_meta WHERE k='hours'").fetchone()
        if r and r[0]:
            self.start_h, self.end_h = _hours(r[0])

    def set_hours(self, arg: str = "") -> str:
        words = arg.replace(" ", "")
        if not words:
            return f"🕒 Tu horario de avisos: {self.start_h}–{self.end_h} h (hora Venezuela). Cambiarlo: /horario 12-20"
        try:
            a, b = (int(x) for x in words.split("-"))
        except ValueError:
            return "Uso: /horario 12-20  (hora de inicio y de fin, hora Venezuela)"
        if not (0 <= a < b <= 24):
            return "Horas no válidas: la de inicio debe ser menor que la de fin, entre 0 y 24."
        self.conn.execute("INSERT OR REPLACE INTO manual_value_meta VALUES ('hours', ?)", (f"{a}-{b}",))
        self.conn.commit()
        self.start_h, self.end_h = a, b
        return f"🕒 Horario de avisos: {a}–{b} h (hora Venezuela). Lo que salga fuera de ese rango se anota pero no se avisa."

    def in_hours(self, ts: float) -> bool:
        self._load_hours()
        h = datetime.fromtimestamp(ts, VET).hour
        return self.start_h <= h < self.end_h

    # ------------------------------------------------------------------ cada ciclo
    def step(self, now: float | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
        """Los picks nuevos de 1xBet de la cuenta de valor en papel → mensajes (texto, botones) para Telegram."""
        now = now or time.time()
        rows = self.conn.execute(
            "SELECT p.key, p.created_ts, p.start_ts, p.title, p.sport, p.market_name, p.market, p.edge_pct, p.legs_json, p.event_key, "
            "p.track FROM paper_ops p LEFT JOIN manual_value m ON m.key = p.key WHERE p.track IN ('valor','amplio') AND p.kind='value' "
            "AND p.created_ts > ? AND m.key IS NULL ORDER BY p.created_ts", (now - LOOKBACK_S,)).fetchall()
        out = []
        on = self.active()
        for key, ts, start, title, sport, mname, market, ev, legs, ek, track in rows:
            leg = (json.loads(legs or "[]") or [{}])[0]
            if leg.get("source") not in SOURCES or (sport or "") in OBSERVE_SPORTS:
                continue
            odds = float(leg.get("odds") or 0)
            fair = float(leg.get("fair_odds") or 0) or (odds / (1 + (ev or 0) / 100) if odds else 0)
            if odds <= 1 or fair <= 1:
                continue
            min_odds = min(math.ceil((1 + MIN_EV / 100) * fair * 100) / 100, round(odds, 2))
            status = "avisada" if on and self.in_hours(now) else ("no estabas" if not on else "fuera de horario")
            prev = self.conn.execute(
                "SELECT COUNT(*) FROM manual_value m JOIN paper_ops p ON p.key = m.key WHERE p.event_key = ? "
                "AND m.status IN ('avisada','puesta','no estaba')", (ek,)).fetchone()[0]
            if status == "avisada" and (sport or "") in NO_ALERT_SPORTS:
                status = "sin ventaja (básquet)"       # se anota, no se avisa
            if status == "avisada" and prev >= MAX_PER_GAME:
                status = "sobre el tope"            # el 3.º pick del mismo partido: se anota, no se avisa
            lote = self.conn.execute(
                "SELECT COUNT(*) FROM paper_ops WHERE track IN ('valor','amplio') AND kind='value' AND legs_json LIKE '%xbet%' "
                "AND created_ts BETWEEN ? AND ?", (ts - BATCH_S, ts + BATCH_S)).fetchone()[0]
            tags = ([f"⚠️ LOTE: {lote} señales nacieron juntas (suele pasar cuando 1xBet vuelve de un corte). "
                     "Mira la cuota en la casa ANTES de apostar."] if lote >= BATCH_MIN else []) + \
                   ([f"2️⃣ 2.º pick de este partido (tope {MAX_PER_GAME}: no se avisan más)"] if prev == 1 else [])
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO manual_value (key, created_ts, start_ts, title, sport, market, label, source, odds, "
                "min_odds, fair_odds, ev_pct, stake, url, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, now, start, title, sport, mname or market, leg.get("label"), leg.get("source"), odds, min_odds,
                 fair, ev, self.stake, leg.get("url") or "", status))
            if status == "avisada" and cur.rowcount:
                out.append(self._message(cur.lastrowid, now, tags, track == "amplio"))
        self.conn.commit()
        return out

    def _message(self, mid: int, now: float, tags: list[str] | None = None, amplio: bool = False) -> tuple[str, list[tuple[str, str]]]:
        start, title, sport, market, label, odds, min_odds, ev, stake, url = self.conn.execute(
            "SELECT start_ts, title, sport, market, label, odds, min_odds, ev_pct, stake, url FROM manual_value WHERE id=?",
            (mid,)).fetchone()
        house = self.house()
        hurl = swap_domain(url, self.domain(house))          # el dominio que usas de esa casa (también el de 1xBet: ve1xbet.com…)
        when = ""
        if start:
            mins = max(0, int((start - now) / 60))
            when = f" · empieza {datetime.fromtimestamp(start, VET):%H:%M} (en {mins // 60} h {mins % 60:02d} min)" \
                if mins >= 60 else f" · empieza {datetime.fromtimestamp(start, VET):%H:%M} (en {mins} min)"
        text = "\n".join([
            (f"🥈 VALOR AMPLIADO {ev:+.1f} % · #{mid} · 🏦 {house}" if amplio else f"💰 VALOR {ev:+.1f} % · #{mid} · 🏦 {house}"),
            f"{SPORT_ICON.get(sport, '🎯')} {title}{when}",
            f"👉 {market}: {label}",
            (f"Cuota {_fmt_odds(odds)} · tómala si está a {_fmt_odds(min_odds)} o más" if house == "1xBet" else
             f"Cuota de 1xBet {_fmt_odds(odds)} · en {house} tómala si está a {_fmt_odds(min_odds)} o más (mira su cuota)"),
            f"Apuesta: ${stake:g} · cambiar de casa: /casa",
        ] + (tags or []) + ([f"🔗 {house}: {hurl}"] + ([f"🔗 1xBet: {url}"] if house != "1xBet" else []) if hurl else
              [f"🔗 {'Partido en 1xBet' if house != '1xBet' else 'Partido'}: {url}"] if url.startswith("http") else [])
          + ([f"ℹ️ Para el link en {house}: /dominio {house.lower()} <su dominio>"] if house != "1xBet" and not hurl else []))
        buttons = ([(f"Abrir en {house}", hurl)] if hurl else []) + (
            [("Partido en 1xBet" if house != "1xBet" else "Abrir en 1xBet", url)]
            if url.startswith("http") and (house != "1xBet" or not hurl) else [])
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
            self.conn.execute("UPDATE manual_value SET status='puesta', placed_odds=?, clicked_ts=?, house=? WHERE id=?",
                              (odds, time.time(), self.house(), mid))
            self.conn.commit()
            again = " (corregida)" if status == "puesta" else ""
            return f"✅ Anotada{again} en {self.house()}: {label} a {_fmt_odds(odds)} · ${stake:g} — {title}. {self.today_line()}"
        self.conn.execute("UPDATE manual_value SET status='no estaba', placed_odds=NULL, clicked_ts=?, house=? WHERE id=?",
                          (time.time(), self.house(), mid))
        self.conn.commit()
        return f"❌ Anotado: {label} no estaba a la cuota mínima en {self.house()} — {title}."

    def pending(self, limit: int = 5, now: float | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
        """Las últimas señales que aún no marcaste (fuera de horario, sobre el tope, sin avisar…) y cuyo partido no terminó,
        cada una con sus botones ✅ / ❌: así se marcan desde Telegram (tu chat), sin abrir nada en la web."""
        now = now or time.time()
        rows = self.conn.execute(
            "SELECT id, status FROM manual_value WHERE status NOT IN ('puesta','no estaba') AND created_ts > ? "
            "AND (start_ts IS NULL OR start_ts > ?) ORDER BY id DESC LIMIT ?", (now - 12 * 3600, now - 3 * 3600, max(1, limit))).fetchall()
        tag = {"fuera de horario": "🕒 Fuera de tu horario", "no estabas": "💤 No estabas (avisos en pausa)",
               "sobre el tope": "🔝 Sobre el tope de 2 por partido", "avisada": "📍 En tu horario, sin marcar"}
        return [self._message(mid, now, [tag.get(st, st)]) for mid, st in reversed(rows)]

    def web_text(self, mid: int, prev: str | None = None) -> str:
        """El mismo aviso que dan los botones de Telegram, para cuando se marca desde el panel web."""
        row = self.conn.execute("SELECT label, title, stake, status, placed_odds, house, wrong_pick, wrong_stake, wrong_result "
                                "FROM manual_value WHERE id=?", (mid,)).fetchone()
        if not row:
            return ""
        label, title, stake, status, placed, house, wpick, wstake, wres = row
        if status == "mal jugada":
            icon = {"ganada": "✅ ganada", "perdida": "❌ perdida", "anulada": "↩️ anulada"}.get(wres, "⏳ pendiente")
            return (f"🌐 ⚠️ Mal jugada desde el panel en {house}: jugaste {wpick} a {_fmt_odds(placed)} · ${wstake:g} "
                    f"(la señal era {label}) — {title}. Resultado: {icon}.")
        fixed = " (corregida)" if prev in ("puesta", "no estaba") else ""
        if status == "puesta":
            return f"🌐 ✅ Anotada{fixed} desde el panel en {house}: {label} a {_fmt_odds(placed)} · ${stake:g} — {title}. {self.today_line()}"
        return f"🌐 ❌ Anotado{fixed} desde el panel: {label} no la tomaste / no estaba en {house} — {title}."

    def set_key(self, args: list[str]) -> str:
        return set_panel_key(self.conn, " ".join(args))

    def register(self, cmd: str, args: list[str]) -> str:
        """/puse <n.º> [cuota]  |  /noestaba <n.º>: anotar a mano una señal (p. ej. una «sobre el tope» que sí jugaste)."""
        try:
            mid = int(args[0].lstrip("#"))
        except (IndexError, ValueError):
            return "Uso: /puse 227 [cuota]  ·  /noestaba 227  (el n.º sale en el aviso y en el dashboard, columna «Tu botón»)"
        row = self.conn.execute("SELECT odds FROM manual_value WHERE id=?", (mid,)).fetchone()
        if not row:
            return f"No encontré la señal #{mid}."
        if cmd == "/noestaba":
            return self.click(f"mv:no:{mid}")
        try:
            odds = float(args[1].replace(",", ".")) if len(args) > 1 else float(row[0])
        except ValueError:
            return "La cuota debe ser un número, por ejemplo: /puse 227 3.19"
        return self.click(f"mv:ok:{mid}:{odds:.2f}")

    def today_line(self) -> str:
        t = today_stats(self.conn)
        return f"Hoy: {t['placed']} puestas de {t['alerted']} avisadas."

    def text_today(self) -> str:
        t = today_stats(self.conn)
        return (f"📒 Valor a mano · hoy · casa activa: {self.house()}\n"
                f"Avisadas {t['alerted']} · puestas {t['placed']} · no estaban {t['missed']} · sin tocar {t['untouched']}\n"
                f"Fuera de horario / no estabas: {t['away']}\n"
                f"Tu ganancia (cerradas): ${t['pnl']:+,.2f} · el papel en las mismas: ${t['paper_pnl']:+,.2f}\n"
                f"Avisos {'ACTIVOS' if self.active() else 'EN PAUSA'} ({self.start_h}–{self.end_h} h)")


# ---------------------------------------------------------------------- resultados y panel
def _rows(conn) -> list[dict]:
    cols = ("id", "key", "created_ts", "start_ts", "title", "sport", "market", "label", "odds", "min_odds", "ev_pct",
            "stake", "status", "placed_odds", "clicked_ts", "result", "house")
    try:
        rs = conn.execute(
            "SELECT m.id, m.key, m.created_ts, m.start_ts, m.title, m.sport, m.market, m.label, m.odds, m.min_odds, "
            "m.ev_pct, m.stake, m.status, m.placed_odds, m.clicked_ts, p.status, m.house FROM manual_value m "
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
    by_house = {}
    for r in rows:
        if r["status"] in ("puesta", "no estaba"):
            by_house.setdefault(r["house"] or "1xBet", []).append(r)
    return {"total": agg(rows), "days": [{"day": k, **agg(v)} for k, v in reversed(list(days.items()))],
            "by_house": [{"house": h, **agg(v)} for h, v in sorted(by_house.items())],
            "curve": curve,
            "rows": [{k: r[k] for k in ("created_ts", "start_ts", "title", "sport", "market", "label", "odds", "min_odds",
                                         "ev_pct", "stake", "status", "placed_odds", "result", "pnl", "paper_pnl")}
                     for r in reversed(rows)][:200]}
