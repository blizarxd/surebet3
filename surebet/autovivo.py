"""
Automático EN VIVO, en simulación (nunca envía nada): surebets en vivo con todas las patas por API
(Polymarket, Cloudbet, SX Bet), ejecutadas como lo haría un bot de verdad.

En vivo las cuotas cambian en segundos y las casas aceptan con retraso, así que:
  1. Solo se intentan surebets en juego con margen entre LIVE_MIN_MARGIN y LIVE_MAX_MARGIN (más que eso casi
     siempre es una cuota congelada), sin filtros de seguridad ⛔.
  2. Se espera el retraso real de aceptación de la casa más lenta (Cloudbet ~7 s, SX 8 s, Polymarket hasta 3 s).
  3. Después de esperar, se VUELVE A PEDIR el precio de cada pata directo a cada casa (no se usa lo que el
     escáner leyó antes): evento de Cloudbet, libro de SX, libro de Polymarket, al monto exacto.
  4. "Llenada" solo si cada pata sigue en su cuota mínima o mejor con esos precios frescos; si no, "rechazada"
     con el motivo. Las llenadas entran a su propia cuenta en papel (track 'autovivo', $1000).
No modela: que la casa rechace aun con la cuota en pie (límites por cliente) ni que la cuota cambie entre
leerla y apostar. Por eso es una cota optimista: si ni así da ganancia, en vivo no vale la pena.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import httpx

from .alerts import _min_odds
from .arb import POLYMARKET, size_legs
from .models import BookLevel, Opportunity, market_label
from .pricing import fill_detail
from .sources.cloudbet import API as CLOUDBET_API
from .sources.sxbet import API as SX_API, SCALE as SX_SCALE, effective_price as sx_effective_price

CLOB = "https://clob.polymarket.com"
AUTOMATABLE = {POLYMARKET, "cloudbet", "sxbet"}
DELAY_S = {"cloudbet": 7.0, "sxbet": 8.0, POLYMARKET: 3.0}
NETWORK_S = 2.0
LIVE_MIN_MARGIN, LIVE_MAX_MARGIN = 1.0, 5.0
EXPIRE_S = 120.0
CHECKS_PER_CYCLE = 6
STAKE = 100.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS exec_live (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE,
    created_ts REAL, title TEXT, sport TEXT, market TEXT, market_name TEXT, start_ts REAL,
    sources TEXT, margin_pct REAL, stake REAL,
    plan_json TEXT,       -- patas: casa, ref, monto, cuota vista, cuota mínima
    resolve_json TEXT,    -- cómo liquidar después
    latency_s REAL,
    status TEXT,          -- pending | filled | rejected
    reason TEXT, checked_ts REAL,
    fresh_json TEXT,      -- cuotas frescas leídas al ejecutar
    net_pct REAL
);
"""


def eligible_live(o: Opportunity) -> bool:
    return (o.event.live and LIVE_MIN_MARGIN <= o.margin_pct < LIVE_MAX_MARGIN
            and all(l.quote.source in AUTOMATABLE for l in o.legs)
            and len({l.quote.source for l in o.legs}) >= 2
            and not any(w.startswith("⛔") for w in o.warnings))


async def fresh_odds(client: httpx.AsyncClient, leg: dict, cloudbet_key: str) -> float | None:
    """Cuota NETA de comisiones al monto de la pata, leída en este momento en la casa."""
    src, stake = leg["source"], leg["stake"]
    try:
        if src == POLYMARKET:
            book = (await client.get(f"{CLOB}/book", params={"token_id": leg["token"]}, timeout=8)).json()
            asks = sorted((BookLevel(float(a["price"]), float(a["size"])) for a in book.get("asks") or []),
                          key=lambda l: l.price)
            fd = fill_detail(tuple(asks), stake, leg.get("fee_rate") or 0.0, leg.get("fee_exp") or 1.0)
            return fd["shares"] / fd["spent"] if fd["spent"] >= stake * 0.999 else None   # el libro no alcanza
        if src == "sxbet":
            market_hash, _, side = leg["ref"].partition("|")
            r = await client.get(f"{SX_API}/orderbook-v3/snapshot", timeout=8,
                                 params={"marketHash": market_hash, "showTakerPerspective": "true"})
            data = r.json().get("data") or {}
            levels = data.get("outcomeOne" if side == "1" else "outcomeTwo") or []
            asks = []
            for x in levels:
                p, size = int(x["percentageOdds"]) / SX_SCALE, int(x["size"]) / 1e6
                if 0 < p < 1 and size > 0:
                    pe = sx_effective_price(p)
                    asks.append(BookLevel(pe, size / pe))
            fd = fill_detail(tuple(sorted(asks, key=lambda l: l.price)), stake, 0.0)
            return fd["shares"] / fd["spent"] if fd["spent"] >= stake * 0.999 else None
        if src == "kalshi":
            from .sources.kalshi import API as KALSHI_API, FEE_RATE as KALSHI_FEE, asks_from_bids
            ticker, _, side = leg["ref"].partition("|")
            book = ((await client.get(f"{KALSHI_API}/markets/{ticker}/orderbook", timeout=8)).json()
                    .get("orderbook_fp") or {})
            asks = asks_from_bids(book.get("no_dollars" if side == "yes" else "yes_dollars"))
            fd = fill_detail(tuple(asks), stake, KALSHI_FEE)
            return fd["shares"] / fd["spent"] if fd["spent"] >= stake * 0.999 else None
        if src == "azuro":
            from .sources.azuro import API as AZURO_API, ENV as AZURO_ENV
            cid, _, oid = leg["ref"].partition("|")
            r = await client.post(f"{AZURO_API}/market-manager/condition-batch", timeout=8,
                                  json={"conditionIds": [cid], "environment": AZURO_ENV})
            cond = (r.json().get("conditions") or [None])[0]
            out = next((x for x in (cond or {}).get("outcomes") or [] if str(x.get("outcomeId")) == oid), None)
            if not cond or cond.get("state") != "Active" or not out or out.get("state") not in (None, "Active"):
                return None
            lim = await client.post(f"{AZURO_API}/bet/calculation", timeout=8, json={
                "bets": [{"conditionId": cid, "outcomeId": int(oid)}], "environment": AZURO_ENV})
            max_bet = float(((lim.json() or {}).get("response") or {}).get("maxBet") or 0)
            return float(out.get("odds") or 0) or None if stake <= max_bet else None
        if src == "cloudbet" and cloudbet_key:
            event_id, _, market_url = leg["ref"].partition("|")
            r = await client.get(f"{CLOUDBET_API}/events/{event_id}", timeout=8,
                                 headers={"X-API-Key": cloudbet_key, "Accept": "application/json"})
            ev = r.json()
            for mk in (ev.get("markets") or {}).values():
                for sub in (mk.get("submarkets") or {}).values():
                    for sel in sub.get("selections") or []:
                        if sel.get("marketUrl") == market_url:
                            if sel.get("status") != "SELECTION_ENABLED":
                                return None
                            max_stake = float(sel.get("maxStake") or 0)
                            if max_stake and stake > max_stake:
                                return None
                            return float(sel.get("price") or 0) or None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    return None


class LiveAutoSimulator:
    def __init__(self, db_path: Path, cloudbet_key: str = "", stake: float = STAKE, bankroll: float = 1000.0) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO paper_meta VALUES ('autovivo_initial', ?)", (str(bankroll),))
        self.conn.execute("INSERT OR IGNORE INTO paper_meta VALUES ('autovivo_started', ?)", (str(time.time()),))
        self.conn.commit()
        self.key, self.stake, self.bankroll = cloudbet_key, stake, bankroll

    def _cash(self) -> float:
        settled = self.conn.execute("SELECT COALESCE(SUM(pnl),0) FROM paper_ops WHERE status!='open' "
                                    "AND track='autovivo'").fetchone()[0]
        in_play = self.conn.execute("SELECT COALESCE(SUM(total_stake),0) FROM paper_ops WHERE status='open' "
                                    "AND track='autovivo'").fetchone()[0]
        return self.bankroll + settled - in_play

    def detect(self, opps: list[Opportunity], resolution) -> None:
        """Anota las surebets en vivo aptas (una por partido y mercado)."""
        now = time.time()
        for o in opps:
            if not eligible_live(o):
                continue
            key = f"vivo#{o.event.key}#{o.market}"
            if self.conn.execute("SELECT 1 FROM exec_live WHERE key=?", (key,)).fetchone():
                continue
            target = min(self.stake, o.max_total_stake) if o.max_total_stake else self.stake
            legs = size_legs([l.quote for l in o.legs], round(target, 2))
            resolve = resolution(o.event, o.market)
            needed = set().union(*(l.quote.covers for l in o.legs))
            if not legs or not resolve or not needed <= set(resolve):
                continue
            plan = [{"source": l.quote.source, "label": l.quote.label, "covers": sorted(l.quote.covers),
                     "stake": round(l.stake, 2), "odds": round(l.odds, 4), "min_odds": round(_min_odds(legs, i), 4),
                     "ref": l.quote.ref, "token": l.quote.token_id, "fee_rate": l.quote.fee_rate,
                     "fee_exp": l.quote.fee_exponent, "url": l.quote.url}
                    for i, l in enumerate(legs)]
            latency = max(DELAY_S.get(p["source"], 5.0) for p in plan) + NETWORK_S
            ev = o.event.anchor
            self.conn.execute(
                "INSERT INTO exec_live (key, created_ts, title, sport, market, market_name, start_ts, sources, margin_pct, "
                "stake, plan_json, resolve_json, latency_s, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')",
                (key, now, ev.title, ev.sport, o.market, market_label(o.market),
                 ev.start.timestamp() if ev.start else None, " + ".join(sorted({p["source"] for p in plan})),
                 o.margin_pct, round(sum(p["stake"] for p in plan), 2), json.dumps(plan, ensure_ascii=False),
                 json.dumps({k: list(v) for k, v in resolve.items()}), latency))
        self.conn.commit()

    async def check(self, client: httpx.AsyncClient) -> None:
        """Después del retraso, relee cada pata en su casa y decide llenada / rechazada."""
        now = time.time()
        rows = self.conn.execute(
            "SELECT id, key, created_ts, title, sport, market, market_name, start_ts, margin_pct, plan_json, "
            "resolve_json, latency_s FROM exec_live WHERE status='pending' AND created_ts + latency_s <= ? "
            "ORDER BY created_ts LIMIT ?", (now, CHECKS_PER_CYCLE)).fetchall()
        results = []
        for row in rows:
            op_id, key, created, title, sport, market, mname, start, margin, plan_json, resolve_json, _lat = row
            plan = json.loads(plan_json)
            fresh = await asyncio.gather(*(fresh_odds(client, p, self.key) for p in plan))
            results.append((row, plan, fresh))
        now = time.time()
        for (op_id, key, created, title, sport, market, mname, start, margin, _pj, resolve_json, _lat), plan, fresh in results:
            bad = [f"{p['source']}: " + ("sin cuota (cerrada, suspendida o sin libro)" if f is None
                                          else f"cuota {f:.3f} < mínima {p['min_odds']:.3f}")
                   for p, f in zip(plan, fresh) if f is None or f < p["min_odds"] - 1e-9]
            info = json.dumps([round(f, 4) if f else None for f in fresh])
            if bad:
                self.conn.execute("UPDATE exec_live SET status='rejected', reason=?, checked_ts=?, fresh_json=? WHERE id=?",
                                  ("; ".join(bad), now, info, op_id))
                continue
            total = sum(p["stake"] for p in plan)
            payouts = [p["stake"] * f for p, f in zip(plan, fresh)]
            net = 100 * (min(payouts) / total - 1)
            if total > self._cash():
                self.conn.execute("UPDATE exec_live SET status='rejected', reason=?, checked_ts=?, fresh_json=?, "
                                  "net_pct=? WHERE id=?", ("se habría llenado, pero la cuenta no tenía saldo libre",
                                                           now, info, round(net, 3), op_id))
                continue
            rows_ = [{"source": p["source"], "label": p["label"], "covers": p["covers"], "stake": p["stake"],
                      "odds": round(f, 4), "url": p.get("url", "")} for p, f in zip(plan, fresh)]
            self.conn.execute(
                """INSERT OR IGNORE INTO paper_ops (key, kind, created_ts, event_key, title, sport, market, market_name,
                   start_ts, live, edge_pct, total_stake, fees, expected_profit, legs_json, resolve_json, status, track)
                   VALUES (?, 'arb', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?, 'open', 'autovivo')""",
                ("autovivo#" + key, now, key.split("#")[1], title, sport, market, mname, start, margin,
                 round(total, 2), round(min(payouts) - total, 4), json.dumps(rows_, ensure_ascii=False), resolve_json))
            self.conn.execute("UPDATE exec_live SET status='filled', reason=?, checked_ts=?, fresh_json=?, net_pct=? "
                              "WHERE id=?", ("todas las patas seguían en su cuota mínima o mejor con precios frescos",
                                             now, info, round(net, 3), op_id))
        self.conn.execute("UPDATE exec_live SET status='rejected', reason='sin revisar a tiempo' "
                          "WHERE status='pending' AND created_ts < ?", (now - EXPIRE_S,))
        self.conn.commit()


def summary(conn) -> dict | None:
    try:
        rows = [dict(zip(("created_ts", "title", "sport", "market_name", "sources", "margin_pct", "stake", "latency_s",
                          "status", "reason", "net_pct"), r))
                for r in conn.execute("SELECT created_ts, title, sport, market_name, sources, margin_pct, stake, latency_s, "
                                      "status, reason, net_pct FROM exec_live ORDER BY created_ts DESC LIMIT 60")]
        total, filled, avg = conn.execute(
            "SELECT COUNT(*), SUM(status='filled'), AVG(CASE WHEN status='filled' THEN net_pct END) FROM exec_live "
            "WHERE status!='pending'").fetchone()
        reasons = conn.execute(
            "SELECT CASE WHEN reason LIKE '%sin cuota%' THEN 'una pata ya no estaba (cerrada o suspendida)' "
            "WHEN reason LIKE '%< mínima%' THEN 'la cuota bajó durante el retraso' "
            "WHEN reason LIKE '%saldo%' THEN 'sin saldo libre' WHEN reason LIKE '%a tiempo%' THEN 'sin revisar a tiempo' "
            "ELSE 'llenada' END r, COUNT(*) FROM exec_live WHERE status!='pending' GROUP BY r ORDER BY 2 DESC").fetchall()
    except sqlite3.OperationalError:
        return None
    return {"total": total or 0, "filled": filled or 0,
            "fill_rate": round(100 * (filled or 0) / total, 1) if total else 0.0,
            "avg_net": round(avg, 3) if avg is not None else None,
            "reasons": [{"reason": r, "n": n} for r, n in reasons], "rows": rows}
