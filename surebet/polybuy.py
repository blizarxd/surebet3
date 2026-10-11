"""
Botón de Telegram "✅ Ya aposté en 1xBet → comprar en Polymarket".

Flujo:
  1. Llega el aviso de una surebet 1xBet + Polymarket con el botón (callback "pb:<id>").
  2. Apuestas en 1xBet a mano el monto del aviso y tocas el botón.
  3. El bot pide el libro de Polymarket EN ESE MOMENTO y compra las shares exactas que igualan el cobro
     de 1xBet, solo si el costo total (con comisión) todavía deja ganancia. Si no, no compra y avisa.

Seguridad:
  - Solo obedece a TELEGRAM_CHAT_ID (tu chat). Los demás se ignoran.
  - Modo DEMO por defecto (POLY_LIVE=0): calcula y anota la orden exacta pero NO la envía.
  - En real: orden límite FOK (se llena entera ya o se cancela) con precio tope = el peor nivel que
    todavía deja ganancia. Topes POLY_MAX_ORDER_USD por orden y POLY_MAX_DAILY_USD por día.
  - /pausa apaga las compras (interruptor), /activar las vuelve a encender, /estado muestra el modo.
  - Un botón vale OFFER_TTL_S (10 min) y se usa una sola vez.
  - La clave privada vive solo en .env (POLY_PRIVATE_KEY); nunca se escribe en logs ni en la base.
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from pathlib import Path

import httpx

from .arb import POLYMARKET, size_legs
from .models import BookLevel, Opportunity, market_label
from .pricing import poly_cost_per_share, poly_fee_per_share

CLOB = "https://clob.polymarket.com"
OFFER_TTL_S = 10 * 60
MANUAL_BOOKS = {"1xbet", "onexbet", "stake", "rollbit"}  # se apuestan a mano; Polymarket va después

SCHEMA = """
CREATE TABLE IF NOT EXISTS poly_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL, title TEXT, market TEXT, start_ts REAL,
    token TEXT, label TEXT, tick REAL, fee_rate REAL, fee_exp REAL,
    shares REAL,            -- shares que igualan el cobro de la casa
    planned_cost REAL,      -- costo en Polymarket al aparecer (con comisión)
    book_source TEXT, book_label TEXT, book_stake REAL, book_odds REAL, book_payout REAL,
    status TEXT,            -- offered | demo | filled | rejected | expired | error
    clicked_ts REAL, price_cap REAL, avg_price REAL, spent REAL, fee REAL, net_pct REAL,
    mode TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS poly_meta (k TEXT PRIMARY KEY, v TEXT);
"""


def walk_shares(asks, shares: float, fee_rate: float, exponent: float = 1.0) -> dict | None:
    """Costo de comprar exactamente `shares` recorriendo el libro: gasto, comisión, precio tope y medio."""
    need, spent, fee, top = shares, 0.0, 0.0, 0.0
    for lvl in asks:
        if need <= 1e-9:
            break
        take = min(lvl.size, need)
        spent += take * poly_cost_per_share(lvl.price, fee_rate, exponent)
        fee += take * poly_fee_per_share(lvl.price, fee_rate, exponent)
        top = lvl.price
        need -= take
    if need > 1e-6:
        return None  # no hay tantas shares en el libro
    return {"spent": spent, "fee": fee, "top": top, "avg": (spent - fee) / shares}


class PolyBuyer:
    def __init__(self, db_path: Path, token: str, chat_id: str, live: bool = False, private_key: str = "",
                 funder: str = "", signature_type: int = 0, max_order_usd: float = 150.0,
                 max_daily_usd: float = 600.0) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.token, self.chat_id = token, str(chat_id)
        self.live = bool(live and private_key)
        self._pk, self._funder, self._sig = private_key, funder, signature_type
        self.max_order, self.max_daily = max_order_usd, max_daily_usd
        self._offset = 0
        self._clob = None
        self.nets = None   # ValueNet (red de seguridad de las apuestas de valor), si está activa
        self.status = "demo (no envía órdenes)" if not self.live else "REAL"

    # ------------------------------------------------------------------ oferta (al avisar)
    def offer(self, o: Opportunity, stake: float) -> int | None:
        """Anota la compra que haría el botón. Solo surebets de 2 patas: una casa manual + Polymarket."""
        if len(o.legs) != 2:
            return None
        srcs = {l.quote.source for l in o.legs}
        if POLYMARKET not in srcs or not (srcs - {POLYMARKET}) <= MANUAL_BOOKS:
            return None
        target = min(stake, o.max_total_stake) if o.max_total_stake else stake
        legs = size_legs([l.quote for l in o.legs], round(target, 2))
        if not legs:
            return None
        book = next(l for l in legs if l.quote.source != POLYMARKET)
        poly = next(l for l in legs if l.quote.source == POLYMARKET)
        q = poly.quote
        payout = book.stake * book.odds
        shares = math.floor(payout * 100) / 100          # mismas shares que el cobro de la casa
        plan = walk_shares(q.asks, shares, q.fee_rate, q.fee_exponent) if q.asks else None
        if not plan or not q.token_id:
            return None
        ev = o.event.anchor
        cur = self.conn.execute(
            "INSERT INTO poly_orders (created_ts, title, market, start_ts, token, label, tick, fee_rate, fee_exp, "
            "shares, planned_cost, book_source, book_label, book_stake, book_odds, book_payout, status, mode) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'offered', ?)",
            (time.time(), ev.title, market_label(o.market), ev.start.timestamp() if ev.start else None, q.token_id,
             q.label, 0.01, q.fee_rate, q.fee_exponent, shares, round(plan["spent"], 4), book.quote.source, book.quote.label,
             round(book.stake, 2), book.odds, round(payout, 4), "real" if self.live else "demo"))
        self.conn.commit()
        return cur.lastrowid

    @staticmethod
    def button(order_id: int) -> tuple[str, str]:
        return ("✅ Ya aposté en 1xBet → comprar en Polymarket", f"cb:pb:{order_id}")

    # ------------------------------------------------------------------ Telegram
    async def run(self) -> None:
        """Escucha los botones y comandos de Telegram (long polling). Corre en segundo plano."""
        if not self.token or not self.chat_id:
            self.status = "sin TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
            return
        async with httpx.AsyncClient(timeout=40) as client:
            while True:
                try:
                    r = await client.get(f"https://api.telegram.org/bot{self.token}/getUpdates",
                                         params={"timeout": 25, "offset": self._offset,
                                                 "allowed_updates": json.dumps(["callback_query", "message"])})
                    for upd in r.json().get("result", []):
                        self._offset = upd["update_id"] + 1
                        await self._handle(client, upd)
                except (httpx.HTTPError, ValueError, KeyError):
                    await asyncio.sleep(5)
                except Exception as e:  # nunca tumba al escáner
                    self.status = f"error bot: {e!r}"[:160]
                    await asyncio.sleep(10)

    async def _handle(self, client: httpx.AsyncClient, upd: dict) -> None:
        cq = upd.get("callback_query")
        msg = upd.get("message")
        chat = str(((cq or {}).get("message") or msg or {}).get("chat", {}).get("id", ""))
        if chat != self.chat_id:
            return  # solo tu chat
        if cq:
            data = cq.get("data", "")
            if data.startswith("pb:"):
                text = await self.execute(client, int(data[3:]))
            elif data.startswith("vn:") and self.nets:
                text = await self.nets.click(client, int(data[3:]))
            else:
                text = "Botón desconocido."
            await client.post(f"https://api.telegram.org/bot{self.token}/answerCallbackQuery",
                              json={"callback_query_id": cq["id"], "text": text[:190]})
            await self._say(client, text)
        elif msg and (msg.get("text") or "").startswith("/"):
            cmd = msg["text"].split()[0].lower()
            if cmd == "/pausa":
                self._set_paused(True)
                await self._say(client, "⏸ Compras en Polymarket PAUSADAS. /activar para volver.")
            elif cmd == "/activar":
                self._set_paused(False)
                await self._say(client, "▶️ Compras en Polymarket activas.")
            elif cmd == "/estado":
                spent = self._spent_today()
                await self._say(client, f"Modo: {'REAL' if self.live else 'DEMO (no envía órdenes)'}\n"
                                        f"Pausado: {'sí' if self._paused() else 'no'}\n"
                                        f"Hoy: ${spent:,.2f} de ${self.max_daily:,.0f} · máx ${self.max_order:,.0f} por orden"
                                        + (f"\nRedes esperando: {self.nets.resting()}" if self.nets else ""))

    async def _say(self, client: httpx.AsyncClient, text: str) -> None:
        try:
            await client.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True})
        except httpx.HTTPError:
            pass

    def _paused(self) -> bool:
        r = self.conn.execute("SELECT v FROM poly_meta WHERE k='paused'").fetchone()
        return bool(r and r[0] == "1")

    def _set_paused(self, on: bool) -> None:
        self.conn.execute("INSERT OR REPLACE INTO poly_meta VALUES ('paused', ?)", ("1" if on else "0",))
        self.conn.commit()

    def _spent_today(self) -> float:
        """Lo comprometido hoy en Polymarket: compras de surebets + órdenes de red (esperando o llenadas)."""
        day0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        spent = self.conn.execute("SELECT COALESCE(SUM(spent),0) FROM poly_orders WHERE status IN ('filled','demo') "
                                  "AND clicked_ts >= ?", (day0,)).fetchone()[0]
        try:
            spent += self.conn.execute(
                "SELECT COALESCE(SUM(shares * limit_price),0) FROM value_nets WHERE status IN ('resting','locked','partial') "
                "AND clicked_ts >= ?", (day0,)).fetchone()[0]
        except sqlite3.OperationalError:
            pass
        return spent

    # ------------------------------------------------------------------ ejecución
    async def execute(self, client: httpx.AsyncClient, order_id: int) -> str:
        row = self.conn.execute("SELECT created_ts, title, token, shares, book_stake, book_payout, book_label, "
                                "label, status, fee_rate, fee_exp FROM poly_orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            return "No encontré esa surebet."
        created, title, token, shares, bstake, bpayout, blabel, label, status, fee_rate, fee_exp = row
        now = time.time()

        def done(st: str, reason: str, **kw) -> str:
            cols = ", ".join(f"{k}=?" for k in kw)
            self.conn.execute(f"UPDATE poly_orders SET status=?, reason=?, clicked_ts=?{', ' + cols if cols else ''} "
                              "WHERE id=?", (st, reason, now, *kw.values(), order_id))
            self.conn.commit()
            return reason

        if status != "offered":
            return f"Esa ya se usó ({status})."
        if now - created > OFFER_TTL_S:
            return done("expired", f"⌛ {title}: el botón venció (más de {OFFER_TTL_S // 60} min). "
                                   "Revisa la cuota y compra a mano si sigue siendo surebet.")
        if self._paused():
            return done("rejected", "⏸ Compras pausadas (/activar). No se compró nada.")
        try:
            r = await client.get(f"{CLOB}/book", params={"token_id": token}, timeout=10)
            book = r.json()
        except (httpx.HTTPError, ValueError):
            return "No pude leer el libro de Polymarket ahora. Prueba de nuevo en unos segundos."
        asks = sorted((BookLevel(float(a["price"]), float(a["size"])) for a in book.get("asks") or []),
                      key=lambda l: l.price)
        tick = float(book.get("tick_size") or 0.01)
        plan = walk_shares(asks, shares, fee_rate or 0.0, fee_exp or 1.0)
        if not plan:
            return done("rejected", f"❌ {title}: no hay {shares:,.0f} shares de «{label}» en el libro. No se compró.")
        total = bstake + plan["spent"]
        net = 100 * (min(bpayout, shares) / total - 1)
        if net <= 0:
            return done("rejected", f"❌ {title}: el precio subió (medio {100 * plan['avg']:.1f}¢, tope "
                                    f"{100 * plan['top']:.1f}¢). Comprar ahora daría {net:+.2f}%, así que NO compré.\n"
                                    "Tu apuesta de 1xBet sigue sola: decide si la cubres a mano o esperas.",
                        avg_price=plan["avg"], net_pct=round(net, 3))
        if plan["spent"] > self.max_order:
            return done("rejected", f"❌ Costo ${plan['spent']:,.2f} supera el tope por orden (${self.max_order:,.0f}).")
        if self._spent_today() + plan["spent"] > self.max_daily:
            return done("rejected", f"❌ Superaría el tope diario (${self.max_daily:,.0f}). No se compró.")
        cap = math.ceil(plan["top"] / tick - 1e-9) * tick
        summary = (f"{shares:,.2f} shares de «{label}» a {100 * plan['avg']:.1f}¢ medio (tope {100 * cap:.1f}¢) · "
                   f"gasto ${plan['spent']:,.2f} con comisión ${plan['fee']:,.2f}\n"
                   f"Con tus ${bstake:,.2f} en 1xBet ({blabel}) cobras mínimo ${min(bpayout, shares):,.2f}: {net:+.2f}% asegurado.")
        if not self.live:
            return done("demo", f"🧪 DEMO {title}: habría comprado {summary}", price_cap=cap, avg_price=plan["avg"],
                        spent=round(plan["spent"], 4), fee=round(plan["fee"], 4), net_pct=round(net, 3))
        try:
            resp = await asyncio.to_thread(self._post_fok, token, cap, shares)
        except Exception as e:
            return done("error", f"⚠️ {title}: Polymarket rechazó la orden ({str(e)[:120]}). Revisa a mano.")
        if not (resp or {}).get("success", False) or (resp or {}).get("status") not in ("matched", "MATCHED", None):
            return done("rejected", f"❌ {title}: la orden no se llenó entera (FOK) y se canceló: {str(resp)[:120]}")
        return done("filled", f"✅ COMPRADO {title}: {summary}", price_cap=cap, avg_price=plan["avg"],
                    spent=round(plan["spent"], 4), fee=round(plan["fee"], 4), net_pct=round(net, 3))

    def clob(self):
        """Cliente oficial de Polymarket (solo en modo real). La clave sale de .env y no se guarda en ningún lado."""
        from py_clob_client.client import ClobClient          # pip install py-clob-client
        if self._clob is None:
            kw = {"key": self._pk, "chain_id": 137}
            if self._funder:
                kw.update(funder=self._funder, signature_type=self._sig)
            self._clob = ClobClient(CLOB, **kw)
            self._clob.set_api_creds(self._clob.create_or_derive_api_creds())
        return self._clob

    def post_buy(self, token: str, price: float, size: float, order_type: str = "FOK") -> dict:
        """Orden límite de compra. FOK: se llena entera ya o se cancela. GTC: queda esperando en el libro."""
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        c = self.clob()
        signed = c.create_order(OrderArgs(token_id=token, price=round(price, 3), size=round(size, 2), side=BUY))
        return c.post_order(signed, getattr(OrderType, order_type))

    def _post_fok(self, token: str, price: float, size: float) -> dict:
        return self.post_buy(token, price, size, "FOK")


def summary(conn) -> dict:
    rows = [dict(zip(("created_ts", "title", "market", "label", "book_label", "book_stake", "book_odds", "shares",
                      "planned_cost", "status", "clicked_ts", "avg_price", "spent", "net_pct", "mode", "reason"), r))
            for r in conn.execute("SELECT created_ts, title, market, label, book_label, book_stake, book_odds, shares, "
                                  "planned_cost, status, clicked_ts, avg_price, spent, net_pct, mode, reason "
                                  "FROM poly_orders ORDER BY created_ts DESC LIMIT 60")]
    agg = dict(conn.execute("SELECT status, COUNT(*) FROM poly_orders GROUP BY status").fetchall())
    return {"counts": agg, "rows": rows}
