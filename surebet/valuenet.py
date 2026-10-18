"""
Red de seguridad de las apuestas de valor, desde Telegram: "🛟 Ya aposté en 1xBet → poner red en Polymarket".

Flujo:
  1. Llega un aviso de valor "oro" con el botón (callback "vn:<id>").
  2. Apuestas en 1xBet a mano el monto sugerido (sin apuro: el valor dura minutos) y tocas el botón.
  3. El bot deja en Polymarket una orden LÍMITE de compra del lado contrario (maker: sin comisión), al precio
     que asegura VALUE_LOCK_PCT pase lo que pase. Si el libro ya está en ese precio, compra al instante
     solo si aun con la comisión queda ganancia segura.
  4. Cada ciclo vigila la orden: si se llena, te avisa "🛟 asegurada" (la apuesta de valor se volvió surebet).
  5. CANCEL_BEFORE_S antes del partido cancela lo que no se llenó y te avisa: queda la apuesta de valor sola
     (si se llenó una parte, esa parte queda asegurada).

Demo por defecto (POLY_LIVE=0): no envía nada; da por llenada la orden cuando el mejor vendedor baja a nuestro
precio. Mismos topes, pausa (/pausa) y chat único que el botón de surebets (surebet/polybuy.py).
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time

import httpx

from .alerts import BOOK_NAMES
from .makersim import TICK, VALUE_LOCK_PCT, value_hedge
from .models import BookLevel, market_label
from .polybuy import CLOB, PolyBuyer, walk_shares

NET_TTL_S = 30 * 60          # el botón vale media hora (el valor dura más que una surebet)
CANCEL_BEFORE_S = 600        # se cancela lo pendiente 10 min antes del partido
LIVE_POLL_S = 60             # en real, cada cuánto se consulta el estado de la orden
MIN_TAKER_LOCK_PCT = 0.5     # si el precio ya llegó, comprar al instante solo si asegura al menos esto

SCHEMA = """
CREATE TABLE IF NOT EXISTS value_nets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL, title TEXT, market TEXT, start_ts REAL,
    token TEXT, label TEXT, fee_rate REAL, fee_exp REAL,
    shares REAL,            -- shares del lado contrario = cobro de la apuesta de 1xBet
    limit_price REAL,       -- precio que asegura lock_pct (sin comisión)
    lock_pct REAL,
    book_source TEXT, book_label TEXT, book_odds REAL, book_stake REAL, ev_pct REAL, value_key TEXT,
    status TEXT,            -- offered | resting | locked | partial | kept | expired | rejected | error
    clicked_ts REAL, order_id TEXT, matched REAL DEFAULT 0, last_poll REAL,
    done_ts REAL, cost REAL, net_pct REAL, pnl REAL, mode TEXT, reason TEXT
);
"""


class ValueNet:
    def __init__(self, buyer: PolyBuyer, lock_pct: float = VALUE_LOCK_PCT) -> None:
        self.buyer, self.conn, self.lock_pct = buyer, buyer.conn, lock_pct
        self.conn.executescript(SCHEMA)
        buyer.nets = self

    @property
    def live(self) -> bool:
        return self.buyer.live

    # ------------------------------------------------------------------ oferta (al avisar)
    def offer(self, v, stake: float) -> int | None:
        h = value_hedge(v, stake, self.lock_pct)
        if not h or stake <= 0:
            return None
        ev, q = v.event.anchor, h["quote"]
        cur = self.conn.execute(
            "INSERT INTO value_nets (created_ts, title, market, start_ts, token, label, fee_rate, fee_exp, shares, "
            "limit_price, lock_pct, book_source, book_label, book_odds, book_stake, ev_pct, value_key, status, mode) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'offered', ?)",
            (time.time(), ev.title, market_label(v.market), ev.start.timestamp() if ev.start else None, h["token"],
             h["label"], q.fee_rate, q.fee_exponent, math.floor(h["shares"] * 100) / 100, h["limit"], self.lock_pct,
             v.quote.source, v.quote.label, v.quote.odds, round(stake, 2), round(v.ev_pct, 3), v.key,
             "real" if self.live else "demo"))
        self.conn.commit()
        return cur.lastrowid

    def resting(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM value_nets WHERE status='resting'").fetchone()[0]

    @staticmethod
    def button(net_id: int) -> tuple[str, str]:
        return ("🛟 Ya aposté en 1xBet → poner red en Polymarket", f"cb:vn:{net_id}")

    # ------------------------------------------------------------------ al tocar el botón
    async def click(self, client: httpx.AsyncClient, net_id: int) -> str:
        row = self.conn.execute("SELECT created_ts, title, start_ts, token, label, fee_rate, fee_exp, shares, limit_price, "
                                "book_label, book_odds, book_stake, status, book_source FROM value_nets WHERE id=?",
                                (net_id,)).fetchone()
        if not row:
            return "No encontré esa apuesta."
        created, title, start, token, label, fee_rate, fee_exp, shares, limit, blabel, bodds, bstake, status, house = row
        house = BOOK_NAMES.get(house, house or "1xBet")
        now = time.time()

        def done(st: str, reason: str, **kw) -> str:
            cols = "".join(f", {k}=?" for k in kw)
            self.conn.execute(f"UPDATE value_nets SET status=?, reason=?, clicked_ts=?{cols} WHERE id=?",
                              (st, reason, now, *kw.values(), net_id))
            self.conn.commit()
            return reason

        if status != "offered":
            return f"Esa red ya se usó ({status})."
        if now - created > NET_TTL_S:
            return done("expired", f"⌛ {title}: el botón venció. La apuesta de valor sigue igual de buena; "
                                   "si quieres la red, ponla a mano con el precio del aviso.")
        if start and now >= start - CANCEL_BEFORE_S:
            return done("expired", f"⌛ {title}: el partido empieza ya, no hay tiempo para la red.")
        if self.buyer._paused():
            return done("rejected", "⏸ Pausado (/activar). No se puso la red.")
        cost = shares * limit
        if cost > self.buyer.max_order:
            return done("rejected", f"❌ La red costaría ${cost:,.2f}: supera el tope por orden (${self.buyer.max_order:,.0f}).")
        if self.buyer._spent_today() + cost > self.buyer.max_daily:
            return done("rejected", f"❌ Superaría el tope diario (${self.buyer.max_daily:,.0f}). No se puso la red.")
        try:
            book = (await client.get(f"{CLOB}/book", params={"token_id": token}, timeout=10)).json()
        except (httpx.HTTPError, ValueError):
            return "No pude leer el libro de Polymarket ahora. Toca el botón de nuevo en unos segundos."
        asks = sorted((BookLevel(float(a["price"]), float(a["size"])) for a in book.get("asks") or []),
                      key=lambda l: l.price)
        tick = float(book.get("tick_size") or TICK)
        payout = bstake * bodds
        intro = (f"{title}: apostaste ${bstake:,.2f} a «{blabel}» @ {bodds:.2f} en {house}"
                 + (" (si fue otro monto, avísame)." if house in ("1xBet", "1xbet") else "."))

        # ¿El precio ya llegó? Entonces comprar al instante, si aun con comisión queda ganancia segura
        if asks and asks[0].price <= limit + 1e-9:
            plan = walk_shares(asks, shares, fee_rate or 0.0, fee_exp or 1.0)
            if plan:
                net = 100 * (min(payout, shares) / (bstake + plan["spent"]) - 1)
                if net >= MIN_TAKER_LOCK_PCT:
                    cap = math.ceil(plan["top"] / tick - 1e-9) * tick
                    if self.live:
                        try:
                            resp = await asyncio.to_thread(self.buyer.post_buy, token, cap, shares, "FOK")
                        except Exception as e:
                            return done("error", f"⚠️ {intro}\nPolymarket rechazó la compra ({str(e)[:120]}).")
                        if not (resp or {}).get("success", False):
                            return done("rejected", f"❌ {intro}\nLa compra no se llenó entera y se canceló.")
                    gain = min(payout, shares) - (bstake + plan["spent"])
                    return done("locked", f"🛟 {'' if self.live else '🧪 DEMO '}{intro}\nEl precio ya estaba: compré "
                                          f"{shares:,.0f} shares de «{label}» por ${plan['spent']:,.2f} (con comisión). "
                                          f"ASEGURADA {net:+.2f}% (${gain:+,.2f}) pase lo que pase.",
                                done_ts=now, cost=round(plan["spent"], 4), matched=shares,
                                net_pct=round(net, 3), pnl=round(gain, 2))
            limit = min(limit, round(asks[0].price - tick, 2))  # si no, la orden queda esperando sin cruzar
        if limit < tick:
            return done("rejected", f"❌ {intro}\nNo hay un precio de red que deje ganancia segura.")
        order_id = None
        if self.live:
            try:
                resp = await asyncio.to_thread(self.buyer.post_buy, token, limit, shares, "GTC")
                order_id = (resp or {}).get("orderID") or (resp or {}).get("orderId")
                if not order_id:
                    return done("error", f"⚠️ {intro}\nPolymarket no aceptó la orden: {str(resp)[:120]}")
            except Exception as e:
                return done("error", f"⚠️ {intro}\nPolymarket rechazó la orden ({str(e)[:120]}).")
        lock = 100 * (min(payout, shares) / (bstake + shares * limit) - 1)
        when = time.strftime("%H:%M", time.localtime(start - CANCEL_BEFORE_S)) if start else "antes del partido"
        return done("resting", f"🛟 {'' if self.live else '🧪 DEMO '}Red puesta. {intro}\n"
                               f"Orden límite: comprar {shares:,.0f} shares de «{label}» a {100 * limit:.0f}¢ "
                               f"(sin comisión). Si se llena: {lock:+.2f}% asegurado.\n"
                               f"Te aviso si se llena. Si no, la cancelo a las {when} y queda la apuesta de valor.",
                    limit_price=limit, order_id=order_id)

    # ------------------------------------------------------------------ cada ciclo
    async def tick(self, poly_quotes: dict, say) -> None:
        """Vigila las redes puestas. `say(texto)` manda el aviso a Telegram."""
        now = time.time()
        rows = self.conn.execute("SELECT id, title, start_ts, token, label, shares, limit_price, book_odds, book_stake, "
                                 "order_id, matched, last_poll FROM value_nets WHERE status='resting'").fetchall()
        for net_id, title, start, token, label, shares, limit, bodds, bstake, order_id, matched, last_poll in rows:
            if self.live and order_id:
                if now - (last_poll or 0) >= LIVE_POLL_S:
                    try:
                        info = await asyncio.to_thread(self.buyer.clob().get_order, order_id)
                        matched = float((info or {}).get("size_matched") or 0)
                    except Exception:
                        pass
                    self.conn.execute("UPDATE value_nets SET matched=?, last_poll=? WHERE id=?", (matched, now, net_id))
                    self.conn.commit()   # nunca dejar una escritura abierta durante un await (bloquea la base)
            else:
                q = poly_quotes.get(token)
                if q and q.asks and q.asks[0].price <= limit + 1e-9:
                    matched = shares   # demo: el vendedor bajó hasta nuestro precio
            payout = bstake * bodds
            if matched >= shares - 0.01:
                cost = shares * limit
                gain = min(payout, shares) - (bstake + cost)
                self._close(net_id, "locked", now, cost, shares, gain, bstake)
                await say(f"🛟✅ ASEGURADA {title}: se llenó tu red ({shares:,.0f} shares de «{label}» a "
                          f"{100 * limit:.0f}¢). Ganas {100 * gain / (bstake + cost):+.2f}% (${gain:+,.2f}) pase lo que pase.")
            elif start and now >= start - CANCEL_BEFORE_S:
                if self.live and order_id:
                    try:
                        await asyncio.to_thread(self.buyer.clob().cancel, order_id)
                    except Exception:
                        pass
                if matched > 0.01:
                    cost = matched * limit
                    self._close(net_id, "partial", now, cost, matched, None, bstake)
                    await say(f"🛟 {title}: el partido empieza; se llenó una parte de la red ({matched:,.0f} de "
                              f"{shares:,.0f} shares). Cancelé el resto: esa parte queda asegurada y el resto sigue como valor.")
                else:
                    self._close(net_id, "kept", now, 0.0, 0.0, None, bstake)
                    await say(f"🛟 {title}: la red no se llenó y la cancelé. Queda tu apuesta de valor sola "
                              f"(«{label}» no se compró). Sigue teniendo ventaja a largo plazo.")
        self.conn.commit()

    def _close(self, net_id, status, now, cost, matched, gain, bstake) -> None:
        net = None if gain is None else round(100 * gain / (bstake + cost), 3)
        self.conn.execute("UPDATE value_nets SET status=?, done_ts=?, cost=?, matched=?, net_pct=?, pnl=? WHERE id=?",
                          (status, now, round(cost, 4), matched, net, None if gain is None else round(gain, 2), net_id))
        self.conn.commit()   # antes del aviso a Telegram (await): si no, la base queda tomada mientras se envía


def summary(conn) -> dict:
    cols = ("created_ts", "title", "market", "label", "book_label", "book_odds", "book_stake", "ev_pct", "shares",
            "limit_price", "status", "clicked_ts", "done_ts", "net_pct", "pnl", "mode", "reason")
    try:
        rows = [dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {', '.join(cols)} FROM value_nets ORDER BY created_ts DESC LIMIT 60")]
        counts = dict(conn.execute("SELECT status, COUNT(*) FROM value_nets GROUP BY status").fetchall())
    except sqlite3.OperationalError:
        return {"counts": {}, "rows": []}
    return {"counts": counts, "rows": rows}
