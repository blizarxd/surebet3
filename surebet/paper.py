"""
Cuenta en papel: entra en las oportunidades que el escáner considera buenas, con dinero ficticio,
como si las ejecutara de verdad, y las liquida con el resultado oficial de Polymarket.

Para que se parezca lo más posible a la realidad:
  - Polymarket: se compra recorriendo el ORDER BOOK REAL al monto exacto (si el monto se come
    varios niveles, el precio medio empeora) y se descuenta la COMISIÓN taker. Se respeta el
    mínimo de 5 shares por orden.
  - 1xBet / casas: a la cuota mostrada en ese momento.
  - Una oportunidad tiene que seguir ahí al menos PAPER_MIN_PERSIST_S segundos (un humano no
    apuesta en el mismo instante en que aparece). Las de margen sospechoso no se toman.
  - Capital PAPER_BANKROLL (1000); cada surebet usa PAPER_ARB_STAKE (100) en total, repartido para
    que todos los lados paguen igual. El dinero apostado queda "en juego" hasta liquidar.

Dos carriles (columna `track`):
  - 'real': la cuenta de $1000. Solo surebets de PREPARTIDO (las de en vivo casi siempre son el
    retraso de un gol y la casa las rechaza).
  - 'medicion': se anota todo lo demás con el mismo monto fijo, sin tocar el saldo, para saber si
    rinde: surebets en vivo, apuestas de valor (prepartido y vivo) y surebets de prepartido que
    no entraron en la real por falta de saldo.

Lo que NO simula (avisar siempre): que la casa rechace la apuesta o cambie la cuota al confirmar,
límites de cuenta, ni el retraso de Polymarket en vivo.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .arb import POLYMARKET, ValueBet, size_legs
from .models import Opportunity, market_label
from .pricing import effective_odds, fill_detail

CLOB = "https://clob.polymarket.com"
SX_API = "https://api.sx.bet"

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE,
    kind TEXT,              -- 'arb' (surebet) | 'value' (apuesta de valor)
    created_ts REAL,
    event_key TEXT,
    title TEXT,
    sport TEXT,
    market TEXT,
    market_name TEXT,
    start_ts REAL,
    live INTEGER,
    edge_pct REAL,          -- margen del arbitraje o EV de la apuesta de valor al entrar
    total_stake REAL,
    fees REAL,              -- comisiones de Polymarket pagadas
    expected_profit REAL,   -- arbitraje: ganancia garantizada; valor: ganancia esperada
    legs_json TEXT,
    resolve_json TEXT,      -- {resultado: [condition_id, token]} para liquidar
    status TEXT,            -- open | won | lost | void
    settled_ts REAL,
    outcome TEXT,
    payout REAL,
    pnl REAL,
    last_check REAL,
    track TEXT DEFAULT 'real'  -- 'real' (cuenta de $1000) | 'medicion' (solo se mide, no toca el saldo)
);
CREATE TABLE IF NOT EXISTS paper_skips (
    ts REAL, kind TEXT, reason TEXT, title TEXT, market TEXT, edge_pct REAL
);
CREATE TABLE IF NOT EXISTS paper_meta (k TEXT PRIMARY KEY, v TEXT);
"""


@dataclass
class PaperConfig:
    bankroll: float = 300.0
    arb_stake: float = 100.0         # total por surebet (repartido entre los lados)
    value_max_pct: float = 0.03      # máximo por apuesta de valor: 3% del capital
    kelly_fraction: float = 0.25
    min_stake: float = 1.0           # mínimo por pata (las casas no aceptan centavos)
    min_persist_s: float = 5.0
    min_margin_pct: float = 0.5
    suspicious_margin_pct: float = 8.0
    value_min_ev_pct: float = 2.0
    value_max_ev_pct: float = 15.0
    include_live: bool = False       # la cuenta real solo toma prepartido
    take_value: bool = False         # por defecto la cuenta real solo opera surebets
    measure: bool = True             # anotar en 'medicion' lo que la cuenta real no toma
    measure_stake: float = 100.0     # monto fijo de cada anotación de medición
    # La cuenta real solo toma partidos que empiezan pronto: el dinero apostado queda atado hasta el
    # final, y una surebet de +1 % que empieza en 20 h bloquea $100 todo un día (el papel se quedó sin
    # saldo y dejó pasar cientos). Las lejanas se siguen midiendo.
    real_max_hours: float = 6.0
    # Ganancia NETA mínima (con comisión y libro recorrido) para usar saldo de la cuenta real; con poco
    # saldo libre sube a 1 % y 1,5 % (ver _required_net_pct)
    real_min_net_pct: float = 0.5
    # En vivo la casa tarda en aceptar y rechaza si la cuota cambió: la señal solo cuenta si sigue en
    # pie con cuotas de la casa pedidas al menos este tiempo después de verla (y se entra a ESAS cuotas)
    live_accept_delay_s: float = 8.0


class PaperBook:
    def __init__(self, db_path: Path, cfg: PaperConfig) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(paper_ops)")}
        if "track" not in cols:
            # Base vieja: las de valor eran de la regla anterior (ahora solo se miden)
            self.conn.execute("ALTER TABLE paper_ops ADD COLUMN track TEXT DEFAULT 'real'")
            self.conn.execute("UPDATE paper_ops SET track='medicion' WHERE kind='value'")
        self.cfg = cfg
        if self.conn.execute("SELECT v FROM paper_meta WHERE k='started'").fetchone() is None:
            self.conn.execute("INSERT INTO paper_meta VALUES ('started', ?)", (str(time.time()),))
        # El capital y el monto por surebet salen de la configuración (se pueden cambiar en .env)
        self.conn.execute("INSERT OR REPLACE INTO paper_meta VALUES ('initial', ?)", (str(cfg.bankroll),))
        self.conn.execute("INSERT OR REPLACE INTO paper_meta VALUES ('arb_stake', ?)", (str(cfg.arb_stake),))
        self.conn.execute("INSERT OR IGNORE INTO paper_meta VALUES ('auto_initial', ?)", (str(cfg.bankroll),))
        self.conn.commit()
        self.initial = float(self.conn.execute("SELECT v FROM paper_meta WHERE k='initial'").fetchone()[0])
        self._seen: dict[str, float] = {}
        self._last_settle = 0.0

    # ------------------------------------------------------------------ saldo
    def balances(self) -> dict[str, float]:
        settled = self.conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM paper_ops WHERE status!='open' AND track='real'").fetchone()[0]
        in_play = self.conn.execute(
            "SELECT COALESCE(SUM(total_stake),0) FROM paper_ops WHERE status='open' AND track='real'").fetchone()[0]
        equity = self.initial + settled
        return {"initial": self.initial, "equity": equity, "in_play": in_play, "cash": equity - in_play}

    def _taken(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM paper_ops WHERE key=?", (key,)).fetchone() is not None

    def _skip(self, kind: str, reason: str, title: str, market: str, edge: float) -> None:
        self.conn.execute("INSERT INTO paper_skips VALUES (?,?,?,?,?,?)", (time.time(), kind, reason, title, market, edge))

    def auto_balances(self) -> dict[str, float]:
        """Cuenta aparte del bot automático simulado (track 'auto')."""
        initial = float(self.conn.execute("SELECT v FROM paper_meta WHERE k='auto_initial'").fetchone()[0])
        settled = self.conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM paper_ops WHERE status!='open' AND track='auto'").fetchone()[0]
        in_play = self.conn.execute(
            "SELECT COALESCE(SUM(total_stake),0) FROM paper_ops WHERE status='open' AND track='auto'").fetchone()[0]
        return {"initial": initial, "equity": initial + settled, "in_play": in_play, "cash": initial + settled - in_play}

    def enter_auto(self, o: Opportunity, plan: list[dict], resolve: dict) -> dict | None:
        """La simulación dice que el bot habría llenado las patas: se anota en la cuenta automática."""
        key = f"auto#{o.event.key}#{o.market}"
        if self._taken(key) or not _resolvable(resolve, o):
            return None
        by_cov = {tuple(sorted(l.quote.covers)): l.quote for l in o.legs}
        rows = []
        for p in plan:
            q = by_cov.get(tuple(p["covers"]))
            if q is None:
                return None
            rows.append(_leg_row(q, p["stake"], effective_odds(q, p["stake"]) or q.odds))
        total = sum(r["stake"] for r in rows)
        if total > self.auto_balances()["cash"]:
            return None
        guaranteed = min(r["stake"] * r["odds"] for r in rows) - total
        op = self._insert(key, "arb", o.event, o.market, o.margin_pct, rows, sum(r.get("fee", 0.0) for r in rows),
                          guaranteed, resolve, "auto")
        self.conn.commit()
        return op

    def eligible_real(self, o: Opportunity) -> bool:
        """¿Entraría en la cuenta real? (prepartido pronto, margen en rango, sin bloqueos de seguridad)"""
        now = time.time()
        return (self.cfg.min_margin_pct <= o.margin_pct < self.cfg.suspicious_margin_pct
                and not _in_play(o.event, now) and self._soon(o.event, now)
                and not any(w.startswith("⛔") for w in o.warnings))

    def _ready(self, key: str, now: float, in_play: bool, book_ts: float) -> bool:
        """¿Un humano llegaría a tiempo? Prepartido: que dure unos segundos. En vivo: aceptación diferida."""
        first = self._seen.setdefault(key, now)
        if in_play:
            delay = self.cfg.live_accept_delay_s
            return now - first >= self.cfg.min_persist_s and (delay <= 0 or book_ts >= first + delay)
        return now - first >= self.cfg.min_persist_s

    def _soon(self, event, now: float) -> bool:
        start = event.anchor.start
        return start is not None and start.timestamp() - now <= self.cfg.real_max_hours * 3600

    # ------------------------------------------------------------------ entradas
    def consider(self, opps: list[Opportunity], values: list[ValueBet], resolution) -> list[dict]:
        """
        Llamado en cada ciclo. `resolution(event_key, market)` devuelve {resultado: (cid, token)}.
        Devuelve las operaciones nuevas (para avisar).
        """
        now = time.time()
        present: set[str] = set()
        new: list[dict] = []
        cands = []
        for o in opps:
            key = f"arb#{o.event.key}#{o.market}"
            present.add(key)
            if self._taken(key) or not (self.cfg.min_margin_pct <= o.margin_pct < self.cfg.suspicious_margin_pct):
                continue
            in_play = _in_play(o.event, now)
            risky = any(w.startswith("⛔") for w in o.warnings)  # ver surebet/safety.py
            track = "medicion" if (in_play and not self.cfg.include_live) or not self._soon(o.event, now) or risky else "real"
            if track == "medicion" and not self.cfg.measure:
                continue
            book_ts = min((l.quote.ts for l in o.legs if l.quote.source != POLYMARKET), default=now)
            if not self._ready(key, now, in_play, book_ts):
                continue
            cands.append((self._net_pct(o), key, o, track))
        # Las mejores del momento primero, por ganancia NETA: comisión de Polymarket y libro recorrido al monto real
        for _net, key, o, track in sorted(cands, key=lambda c: c[0], reverse=True):
            op = self._enter_arb(key, o, resolution(o.event, o.market), track)
            if op:
                new.append(op)
        if self.cfg.take_value or self.cfg.measure:
            for v in sorted(values, key=lambda v: v.ev_pct, reverse=True):
                # Una sola anotación por resultado (1xBet llega por dos fuentes): la de mejor cuota, que va primero
                key = f"value#{v.event.key}#{v.market}#{''.join(sorted(v.quote.covers))}"
                present.add(key)
                if self._taken(key) or not (self.cfg.value_min_ev_pct <= v.ev_pct < self.cfg.value_max_ev_pct):
                    continue
                in_play = _in_play(v.event, now)
                real = self.cfg.take_value and (self.cfg.include_live or not in_play) and self._soon(v.event, now)
                if not real and not self.cfg.measure:
                    continue
                if not self._ready(key, now, in_play, v.quote.ts):
                    continue
                op = self._enter_value(key, v, resolution(v.event, v.market), "real" if real else "medicion")
                if op:
                    new.append(op)
        self._seen = {k: t for k, t in self._seen.items() if k in present}
        self.conn.commit()
        return new

    def _enter_arb(self, key: str, o: Opportunity, resolve: dict, track: str = "real") -> dict | None:
        title, market = o.event.anchor.title, o.market
        if not _resolvable(resolve, o):
            self._skip("arb", "sin forma de liquidar con Polymarket", title, market, o.margin_pct)
            return None
        bal = self.balances()
        if track == "real" and bal["cash"] < self.cfg.arb_stake * 0.05 and self.cfg.measure:
            track = "medicion"  # sin saldo en la real: igual se mide
        # Monto fijo por surebet; menos solo si no alcanza el disponible o la liquidez del libro
        target = min(self.cfg.arb_stake, bal["cash"]) if track == "real" else self.cfg.measure_stake
        plan = self._plan(o, target)
        if plan and track == "real" and 100 * plan[3] / plan[2] < self._required_net_pct(bal):
            if not self.cfg.measure:
                self._skip("arb", "no está entre las mejores para el saldo libre", title, market, o.margin_pct)
                return None
            track = "medicion"  # el saldo se guarda para las mejores; esta igual se mide
            plan = self._plan(o, self.cfg.measure_stake)
        if plan is None:
            reason = ("sin saldo disponible (todo en juego)" if bal["cash"] < self.cfg.arb_stake * 0.05
                      else "liquidez insuficiente para el mínimo por pata")
            self._skip("arb", reason, title, market, o.margin_pct)
            return None
        rows, fees, _total, guaranteed = plan
        if guaranteed <= 0:
            self._skip("arb", "al monto real el libro ya no deja ganancia", title, market, o.margin_pct)
            return None
        return self._insert(key, "arb", o.event, market, o.margin_pct, rows, fees, guaranteed, resolve, track)

    def _plan(self, o: Opportunity, target: float) -> tuple[list[dict], float, float, float] | None:
        """Las patas tal como se llenarían a ese monto: (filas, comisiones, total, ganancia garantizada)."""
        if o.max_total_stake:
            target = min(target, o.max_total_stake)
        legs = size_legs([l.quote for l in o.legs], round(target, 2)) if target > 0 else None
        if legs is None or any(l.stake < self.cfg.min_stake for l in legs):
            return None
        rows = [_leg_row(l.quote, l.stake, l.odds) for l in legs]
        total = sum(r["stake"] for r in rows)
        return rows, sum(r.get("fee", 0.0) for r in rows), total, min(r["stake"] * r["odds"] for r in rows) - total

    def _net_pct(self, o: Opportunity) -> float:
        plan = self._plan(o, self.cfg.arb_stake)
        return 100 * plan[3] / plan[2] if plan else float("-inf")

    def _required_net_pct(self, bal: dict) -> float:
        """Con poco saldo libre se exige más: los últimos huecos se guardan para las mejores."""
        free = bal["cash"] / bal["equity"] if bal["equity"] > 0 else 0.0
        base = self.cfg.real_min_net_pct
        if free >= 0.5:
            return base
        return max(base, 1.0 if free >= 0.2 else 1.5)

    def _enter_value(self, key: str, v: ValueBet, resolve: dict, track: str = "real") -> dict | None:
        title, market = v.event.anchor.title, v.market
        if not resolve or set(resolve) != set(v.poly_tokens):
            self._skip("value", "sin forma de liquidar con Polymarket", title, market, v.ev_pct)
            return None
        if track == "real":
            bal = self.balances()
            stake = bal["equity"] * v.kelly * self.cfg.kelly_fraction
            stake = min(max(stake, self.cfg.min_stake), bal["equity"] * self.cfg.value_max_pct, bal["cash"])
            if stake < self.cfg.min_stake:
                self._skip("value", "saldo insuficiente", title, market, v.ev_pct)
                return None
        else:
            stake = self.cfg.measure_stake  # monto fijo: así el ROI de cada tipo se compara directo
        row = _leg_row(v.quote, round(stake, 2), v.quote.odds)
        expected = row["stake"] * (v.quote.odds * v.fair_prob - 1)
        return self._insert(key, "value", v.event, market, v.ev_pct, [row], 0.0, expected, resolve, track)

    def _insert(self, key, kind, event, market, edge, rows, fees, expected, resolve, track="real") -> dict:
        ev = event.anchor
        total = round(sum(r["stake"] for r in rows), 2)
        data = (key, kind, time.time(), event.key, ev.title, ev.sport, market, market_label(market),
                ev.start.timestamp() if ev.start else None, int(event.live), edge, total, round(fees, 4),
                round(expected, 4), json.dumps(rows, ensure_ascii=False),
                json.dumps({o: list(t) for o, t in resolve.items()}), "open", track)
        self.conn.execute(
            """INSERT INTO paper_ops (key, kind, created_ts, event_key, title, sport, market, market_name,
               start_ts, live, edge_pct, total_stake, fees, expected_profit, legs_json, resolve_json, status, track)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", data)
        return {"kind": kind, "title": ev.title, "market": market_label(market), "edge": edge,
                "total": total, "expected": expected, "legs": rows, "track": track}

    # ------------------------------------------------------------------ liquidación
    async def settle(self, client: httpx.AsyncClient, every_s: float = 300) -> int:
        """Liquida las operaciones cuyo mercado ya resolvió en Polymarket. Devuelve cuántas cerró."""
        now = time.time()
        if now - self._last_settle < every_s:
            return 0
        self._last_settle = now
        rows = self.conn.execute(
            "SELECT id, legs_json, resolve_json, total_stake FROM paper_ops "
            "WHERE status='open' AND start_ts IS NOT NULL AND start_ts < ? "
            "AND (last_check IS NULL OR last_check < ?) ORDER BY COALESCE(last_check, 0) LIMIT 80",
            (now - 45 * 60, now - 600)).fetchall()  # por tandas: con cientos abiertas no frena el escaneo
        # Primero todas las consultas a Polymarket y DESPUÉS las escrituras, en una sola transacción
        # corta: escribir entre consultas dejaba la base bloqueada y tumbaba al escáner ("database is locked")
        cache: dict[str, dict | None] = {}
        for _id, _legs, resolve_json, _total in rows:
            for cid, _token in json.loads(resolve_json).values():
                if cid not in cache:
                    try:
                        if cid.startswith("sx:"):   # partido sin Polymarket: resultado oficial de SX Bet
                            r = await client.get(f"{SX_API}/markets/find", params={"marketHashes": cid[3:]}, timeout=15)
                            data = r.json().get("data") if r.status_code == 200 else None
                            cache[cid] = data[0] if isinstance(data, list) and data else None
                        else:
                            r = await client.get(f"{CLOB}/markets/{cid}", timeout=15)
                            cache[cid] = r.json() if r.status_code == 200 else None
                    except (httpx.HTTPError, ValueError):
                        cache[cid] = None
        closed = 0
        for op_id, legs_json, resolve_json, total in rows:
            resolve = json.loads(resolve_json)
            outcome, void = None, False
            all_closed = True
            for out, (cid, token) in resolve.items():
                m = cache[cid]
                if cid.startswith("sx:"):
                    if not m or m.get("outcome") is None:
                        all_closed = False
                    elif str(m["outcome"]) == str(token):
                        outcome = out
                    elif str(m["outcome"]) == "0":
                        void = True  # SX anuló el mercado
                    continue
                if not m or not m.get("closed"):
                    all_closed = False
                    continue
                toks = m.get("tokens") or []
                if any(str(t.get("token_id")) == str(token) and t.get("winner") for t in toks):
                    outcome = out
                elif not any(t.get("winner") for t in toks):
                    void = True  # resuelto 50/50 (partido cancelado, etc.)
            self.conn.execute("UPDATE paper_ops SET last_check=? WHERE id=?", (now, op_id))
            if outcome is None and not (all_closed and void):
                continue
            legs = json.loads(legs_json)
            if outcome is None:
                payout, status = total, "void"
            else:
                payout = sum(l["stake"] * l["odds"] for l in legs if outcome in l["covers"])
                status = "won" if payout > total else "lost"
            self.conn.execute(
                "UPDATE paper_ops SET status=?, settled_ts=?, outcome=?, payout=?, pnl=? WHERE id=?",
                (status, now, outcome, round(payout, 4), round(payout - total, 4), op_id))
            closed += 1
        self.conn.commit()
        return closed


def _in_play(event, now: float) -> bool:
    """En vivo, o ya pasó la hora de inicio (aunque la fuente no lo marque todavía)."""
    start = event.anchor.start
    return event.live or (start is not None and start.timestamp() <= now)


def _resolvable(resolve: dict, o: Opportunity) -> bool:
    needed = set().union(*(l.quote.covers for l in o.legs))
    return bool(resolve) and needed <= set(resolve)


def _leg_row(q, stake: float, odds: float) -> dict:
    """Una pata tal como se ejecutaría: en Polymarket, llenada contra el libro real con comisión."""
    row = {"source": q.source, "label": q.label, "covers": sorted(q.covers), "stake": round(stake, 2),
           "odds": round(odds, 4), "url": q.url}
    if q.source == POLYMARKET and q.asks:
        fd = fill_detail(q.asks, stake, q.fee_rate, q.fee_exponent)
        row.update({"shares": round(fd["shares"], 2), "avg_price": round(fd["avg_price"], 4),
                    "fee": round(fd["fee"], 4), "levels": fd["levels"],
                    "odds": round(fd["shares"] / fd["spent"], 4) if fd["spent"] else row["odds"]})
    return row
