"""
Semáforo de riesgo (🟢 / 🟡 / 🔴) de cada cuenta y de las APIs, con PAUSA en rojo.

  🟢 verde:    caída < 10 % (y en valor, CLV de las oro a favor en la semana)
  🟡 amarillo: caída del 10 al 20 %, o CLV medio de las oro negativo 3 días seguidos (o en toda la semana)
  🔴 rojo:     caída > 20 %, cuenta bloqueada (la casa rechaza la clave, 1xBet no responde, o lo marcas con
               /bloqueada) o una API caída más de 20 min

El rojo PAUSA solo entradas NUEVAS (avisos de Telegram y cuentas en papel). Las coberturas de lo ya apostado
(botones ✅ y 🛟) siguen funcionando: una pata suelta es peor que cualquier caída.
  - Caída > 20 %: esa cuenta queda en pausa hasta /reanudar (desde ahí la caída se vuelve a medir). No se levanta
    sola: hay que mirar qué pasó y, con dinero real, retirar lo que no está en juego.
  - API caída o casa bloqueada: nada que use esa casa; si cae Polymarket, nada (es la cobertura de todo). Se
    levanta sola cuando la casa responde 10 min seguidos; lo marcado con /bloqueada, solo con /reanudar.
Cada cambio de color se avisa una vez por Telegram. El estado queda en paper_meta('semaforo') para el panel.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from datetime import datetime
from pathlib import Path

DD_YELLOW, DD_RED = 10.0, 20.0
DD_BACK_GREEN = 8.0        # de amarillo vuelve a verde bajo 8 %: sin esto una caída de ~10 % titila y llena Telegram
CLV_DAYS, CLV_DAY_MIN_N, CLV_WEEK_MIN_N = 3, 5, 10
API_DOWN_S, API_OK_S = 1200.0, 600.0   # 28/10: con 5 min, cada tropiezo de 1xBet (2-4 % de errores) daba rojo y pausa; ahora 20 min
EVERY_S = 60.0
CORE = ("polymarket", "onexbet", "cloudbet", "sxbet", "kalshi", "azuro")   # casas que se operan: su caída pausa
ALIASES = {"onexbet": {"onexbet", "1xbet"}, "1xbet": {"onexbet", "1xbet"}}  # la fuente se llama onexbet; sus cuotas, 1xbet
ACCOUNTS = {   # id: (nombre, track de paper_ops, clave del capital inicial en paper_meta)
    "real": ("Surebets semi-manual", "real", "initial"),
    "auto": ("Automático", "auto", "auto_initial"),
    "valor": ("Valor oro (+ red)", "valor", "valor_initial"),
}
ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
WORD = {"green": "VERDE", "yellow": "AMARILLO", "red": "ROJO"}
RANK = {"green": 0, "yellow": 1, "red": 2}


def classify(status: str) -> str:
    """'ok' | 'down' (la API falla) | 'blocked' (la casa nos rechaza) | 'off' (apagada o sin cupo) | 'skip'."""
    s = (status or "").lower()
    if not s or s.startswith("sin datos") or "desactivada" in s or s.startswith(
            ("pausada:", "cupo del día", "próxima ronda", "esperando partidos")):
        return "off"
    if "no responde" in s or "dejó de responder" in s or "rechazó la clave" in s:
        return "blocked"
    if s.startswith(("error en papel", "error en semáforo", "base ocupada")):
        return "skip"   # falla nuestra escrita en el estado de Polymarket, no de la casa: no mueve los relojes
    if s.startswith(("error", "http ")):
        return "down"
    return "ok"


def drawdown(closes, initial: float, since: float = 0.0) -> tuple[float, float, float]:
    """(caída actual en % bajo el pico, pico, capital) con las liquidaciones [(ts, pnl)].
    Con `since` (al reanudar) el pico vuelve a contar desde el capital de ese momento."""
    eq = peak = initial
    reset = not since
    for ts, pnl in sorted(closes):
        if not reset and ts > since:
            peak, reset = eq, True
        eq += pnl
        peak = max(peak, eq)
    if not reset:
        peak = eq
    return (100 * (peak - eq) / peak if peak > 0 else 0.0), round(peak, 2), round(eq, 2)


def clv_days(rows, min_n: int = CLV_DAY_MIN_N) -> tuple[float | None, int, list[dict]]:
    """rows: [(inicio del partido, clv %)] de la semana. -> (media de la semana, días negativos seguidos, días)."""
    by_day: dict = {}
    for start, c in rows:
        by_day.setdefault(datetime.fromtimestamp(start).date(), []).append(c)
    days = [{"day": d.strftime("%d/%m"), "clv": round(statistics.mean(v), 2), "n": len(v)}
            for d, v in sorted(by_day.items()) if len(v) >= min_n]
    streak = 0
    for d in reversed(days):
        if d["clv"] >= 0:
            break
        streak += 1
    clvs = [c for _, c in rows]
    week = statistics.mean(clvs) if len(clvs) >= CLV_WEEK_MIN_N else None
    return week, streak, days


class Semaforo:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.execute("CREATE TABLE IF NOT EXISTS paper_meta (k TEXT PRIMARY KEY, v TEXT)")
        self.conn.commit()
        self.state = self._load("semaforo") or {}
        self.paused: dict = self._load("semaforo_paused") or {}    # cuenta -> {"ts", "why"}
        self.resets: dict = self._load("semaforo_reset") or {}     # cuenta -> desde cuándo se vuelve a medir la caída
        self.manual: dict = self._load("semaforo_blocked") or {}   # casa -> cuándo la marcaste con /bloqueada
        self._bad_since: dict[str, float] = {}
        self._good_since: dict[str, float] = {}
        self._down: dict[str, dict] = {}                           # fuente -> {kind, since, status}
        self._last = 0.0

    # ------------------------------------------------------------------ persistencia
    def _load(self, k: str):
        r = self.conn.execute("SELECT v FROM paper_meta WHERE k=?", (k,)).fetchone()
        try:
            return json.loads(r[0]) if r else None
        except ValueError:
            return None

    def _save(self, **items) -> None:
        for k, v in items.items():
            self.conn.execute("INSERT OR REPLACE INTO paper_meta VALUES (?, ?)", (k, json.dumps(v, ensure_ascii=False)))
        self.conn.commit()

    # ------------------------------------------------------------------ lo que consultan el escáner y el papel
    def down_sources(self) -> set[str]:
        out: set[str] = set()
        for name in [n for n in self._down if n in CORE] + list(self.manual):
            out |= ALIASES.get(name, {name})
        return out

    def blocks(self, account: str, sources) -> str | None:
        """Motivo por el que una entrada NUEVA no debe tomarse/avisarse (None = adelante)."""
        if account in self.paused:
            return f"semáforo en rojo: {ACCOUNTS[account][0]} en pausa"
        down = self.down_sources()
        if "polymarket" in down:
            return "semáforo en rojo: Polymarket caído"
        hit = sorted(set(sources) & down)
        return f"semáforo en rojo: {', '.join(hit)} caída o bloqueada" if hit else None

    # ------------------------------------------------------------------ cada ciclo
    def step(self, sources, now: float | None = None) -> list[str]:
        """Llamado en cada ciclo: sigue el estado de las APIs y cada EVERY_S recalcula. Devuelve avisos para Telegram."""
        now = now or time.time()
        self._track_apis(sources, now)
        if now - self._last < EVERY_S:
            return []
        self._last = now
        return self.refresh(now)

    def _track_apis(self, sources, now: float) -> None:
        for src in sources:
            kind = classify(src.status)
            if kind == "skip":
                continue
            if kind in ("down", "blocked"):
                self._good_since.pop(src.name, None)
                since = self._bad_since.setdefault(src.name, now)
                if now - since >= API_DOWN_S:
                    self._down[src.name] = {"kind": kind, "since": since, "status": src.status[:120]}
                continue
            self._bad_since.pop(src.name, None)
            if src.name in self._down:
                good = self._good_since.setdefault(src.name, now)
                if kind == "off" or now - good >= API_OK_S:
                    del self._down[src.name]
                    self._good_since.pop(src.name, None)

    def refresh(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        prev = {l["id"]: l["color"] for l in self.state.get("lights", [])}
        meta = dict(self.conn.execute("SELECT k, v FROM paper_meta").fetchall())
        lights = [self._account(acc, meta, prev.get(acc), now) for acc in ACCOUNTS]
        lights.append(self._apis(now))
        overall = max((l["color"] for l in lights), key=RANK.get)
        self.state = {"ts": now, "color": overall, "lights": lights,
                      "rules": f"🟢 caída < {DD_YELLOW:g} % y CLV a favor · 🟡 caída {DD_YELLOW:g}–{DD_RED:g} % o CLV negativo "
                               f"{CLV_DAYS} días · 🔴 caída > {DD_RED:g} %, cuenta bloqueada o API caída {API_DOWN_S / 60:.0f} min"}
        self._save(semaforo=self.state, semaforo_paused=self.paused)
        first = not prev
        return [self._message(l, prev.get(l["id"])) for l in lights
                if l["color"] != prev.get(l["id"]) and not (first and l["color"] == "green")]

    def _account(self, acc: str, meta: dict, prev: str | None, now: float) -> dict:
        name, track, init_key = ACCOUNTS[acc]
        initial = float(meta.get(init_key) or meta.get("initial") or 1000)
        since = float(self.resets.get(acc) or 0)
        closes = self.conn.execute("SELECT settled_ts, pnl FROM paper_ops WHERE track=? AND status!='open' "
                                   "AND settled_ts IS NOT NULL AND pnl IS NOT NULL", (track,)).fetchall()
        dd, peak, eq = drawdown(closes, initial, since)
        parts = [("cuenta" if acc != "valor" else "valor oro", dd, peak, eq)]
        clv = None
        if acc == "valor":
            net = self._net_closes()
            if net:
                parts.append(("valor + red",) + drawdown(net[1], net[0], since))
            clv = self._clv(now)
        worst = max(parts, key=lambda p: p[1])
        dd = worst[1]
        color, reasons = "green", []
        for label, d, pk, e in parts:
            reasons.append(f"{label}: caída {d:.1f} % bajo el pico (${pk:,.0f} → ${e:,.0f})" if d >= 0.05
                           else f"{label}: en el pico (${e:,.0f})")
        if dd > DD_RED:
            color = "red"
            if acc not in self.paused:
                self.paused[acc] = {"ts": now, "why": f"caída de {dd:.1f} % ({worst[0]})"}
        elif dd >= DD_YELLOW or (prev == "yellow" and dd >= DD_BACK_GREEN):
            color = "yellow"
        if clv:
            week, streak, days = clv
            if week is not None:
                reasons.append(f"CLV de las oro en la semana {week:+.2f} % ({sum(d['n'] for d in days)} con datos)")
            if streak >= CLV_DAYS:
                color = max(color, "yellow", key=RANK.get)
                reasons.append(f"CLV negativo {streak} días seguidos")
            elif week is not None and week < 0:
                color = max(color, "yellow", key=RANK.get)
                reasons.append("CLV medio de la semana negativo")
        if acc in self.paused:
            color = "red"
            p = self.paused[acc]
            reasons.insert(0, f"EN PAUSA desde {datetime.fromtimestamp(p['ts']).strftime('%d/%m %H:%M')} "
                              f"({p['why']}): no toma ni avisa nada nuevo. /reanudar para seguir")
        if since:
            reasons.append(f"caída medida desde que reanudaste ({datetime.fromtimestamp(since).strftime('%d/%m %H:%M')})")
        return {"id": acc, "name": name, "color": color, "dd_pct": round(dd, 2), "paused": acc in self.paused,
                "reasons": reasons, "clv_days": clv[2] if clv else None}

    def _net_closes(self) -> tuple[float, list] | None:
        """Valor + red (nettrack): capital y liquidaciones (el partido se da por cerrado 2 h después del inicio)."""
        try:
            bank = self.conn.execute("SELECT v FROM net_meta WHERE k='bankroll'").fetchone()
            rows = self.conn.execute(
                "SELECT COALESCE(start_ts, created_ts) + 7200, pnl FROM net_picks WHERE COALESCE(skipped,0)=0 "
                "AND pnl IS NOT NULL AND result NOT IN ('pendiente','sin datos')").fetchall()
        except sqlite3.OperationalError:
            return None
        return (float(bank[0]) if bank else 1000.0), rows

    def _clv(self, now: float):
        """CLV de las señales oro (las que se avisan) cuyo partido empezó en los últimos 7 días."""
        try:
            rows = [(start, c) for start, first, ev, odds, c in self.conn.execute(
                "SELECT start_ts, first_seen, ev_pct, odds, clv_pct FROM value_bets WHERE clv_pct IS NOT NULL "
                "AND start_ts > ?", (now - 7 * 86400,))
                if 2 <= ev < 7 and 1.6 <= odds < 4 and 0 < start - first <= 6 * 3600]
        except sqlite3.OperationalError:
            return None
        return clv_days(rows)

    def _apis(self, now: float) -> dict:
        color, reasons = "green", []
        for name, d in sorted(self._down.items()):
            mins = (now - d["since"]) / 60
            what = "la casa nos rechaza (¿bloqueo?)" if d["kind"] == "blocked" else "API caída"
            if name in CORE:
                color = "red"
                reasons.append(f"{name}: {what} hace {mins:.0f} min ({d['status']}); pausado lo que la usa")
            else:
                color = max(color, "yellow", key=RANK.get)
                reasons.append(f"{name}: {what} hace {mins:.0f} min (solo informativa, no pausa)")
        for house, ts in sorted(self.manual.items()):
            color = "red"
            reasons.append(f"{house}: marcada como BLOQUEADA por ti el {datetime.fromtimestamp(ts).strftime('%d/%m %H:%M')}; "
                           "pausado lo que la usa hasta /reanudar")
        if not reasons:
            reasons.append("todas las casas responden")
        return {"id": "apis", "name": "APIs y cuentas de las casas", "color": color, "reasons": reasons,
                "paused": bool(self.down_sources()), "down": sorted(self.down_sources())}

    def _message(self, l: dict, prev: str | None) -> str:
        head = f"{ICON[l['color']]} Semáforo {WORD[l['color']]} · {l['name']}" + (f" (antes {WORD[prev].lower()})" if prev else "")
        body = "\n".join("• " + r for r in l["reasons"])
        if l["color"] == "red" and l["id"] == "apis":
            account = any(d["kind"] == "blocked" for d in self._down.values()) or self.manual
            tail = ("⏸ Pausado: no se avisa ni se toma nada nuevo que use esa casa (las coberturas ✅ / 🛟 de lo ya "
                    "apostado siguen). Si la API vuelve, se levanta solo."
                    + ("\n💸 Si es tu CUENTA la bloqueada (no la API): retira los fondos que no estén en juego." if account else ""))
        elif l["color"] == "red":
            tail = ("⏸ Pausado: esta cuenta no toma ni avisa nada nuevo (las coberturas ✅ / 🛟 siguen).\n"
                    "💸 Con dinero real: retira lo que no esté en juego y revisa qué pasó antes de seguir. /reanudar para volver.")
        elif l["color"] == "yellow":
            tail = "Sin pausa todavía: ojo con los montos."
        else:
            tail = "Todo en orden otra vez."
        return f"{head}\n{body}\n{tail}"

    # ------------------------------------------------------------------ comandos de Telegram
    def resume(self) -> str:
        now = time.time()
        if not self.paused and not self.manual:
            return "No había nada en pausa por el semáforo."
        for acc in self.paused:
            self.resets[acc] = now       # la caída se vuelve a medir desde aquí (si no, seguiría en rojo)
        names = [ACCOUNTS[a][0] for a in self.paused] + [f"{h} (bloqueada)" for h in self.manual]
        self.paused, self.manual = {}, {}
        self._save(semaforo_paused=self.paused, semaforo_reset=self.resets, semaforo_blocked=self.manual)
        self._last = 0.0                 # recalcular en el próximo ciclo
        return "▶️ Reanudado: " + ", ".join(names) + ". La caída se mide de nuevo desde ahora."

    def block(self, house: str) -> str:
        house = (house or "").strip().lower()
        if not house:
            return "Uso: /bloqueada 1xbet (o la casa que te limitó o cerró la cuenta)."
        self.manual[house] = time.time()
        self._save(semaforo_blocked=self.manual)
        self._last = 0.0
        return (f"🔴 {house} marcada como bloqueada: no se avisa ni se toma nada que la use. Retira los fondos que no "
                "estén en juego. /reanudar cuando vuelva a estar bien.")

    def text(self) -> str:
        if not self.state:
            return "Semáforo todavía sin calcular (espera un minuto)."
        lines = [f"{ICON[self.state['color']]} Semáforo general: {WORD[self.state['color']]}"]
        for l in self.state["lights"]:
            lines.append(f"\n{ICON[l['color']]} {l['name']}")
            lines += ["  • " + r for r in l["reasons"]]
        return "\n".join(lines)
