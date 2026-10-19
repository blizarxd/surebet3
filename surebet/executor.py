"""
Ejecutor REAL de Azuro + Polymarket, sin clics. Modo por defecto "dry": arma y anota lo que haría, sin firmar ni enviar.

Dos jugadas, las dos por API:
  A. Surebet Azuro + Polymarket (la que el automático confirma con el precio real de cada casa, surebet/execsim.py):
     primero Azuro con la cuota mínima que todavía deja ganancia; si Azuro la acepta en la cadena, Polymarket FOK con las
     shares que igualan el cobro de Azuro, leyendo el libro en ese momento.
     Pata suelta: si Polymarket no se llena o ya no deja ganancia, se reintenta cada minuto y se cubre aunque cueste hasta
     LOOSE_MAX_LOSS_PCT del total; si a 5 min del partido sigue sin cubrir, queda como apuesta de valor (Azuro tenía
     ventaja contra Polymarket) y se avisa. Una pata suelta nunca se deja sin aviso.
  B. Valor + red en Azuro (lo que la sombra da por llenado, surebet/sxshadow.py): apuesta en Azuro con la cuota mínima que
     mantiene EV ≥ 2 % contra Polymarket y, si la acepta, la red en Polymarket: la orden límite del lado contrario del
     botón 🛟 (surebet/valuenet.py), que se vigila y se cancela 10 min antes si no se llenó.

Interruptores: AUTO_VALUE_NET=0 apuesta el valor en Azuro SIN red (valor puro: más esperado, más vaivén) y AUTO_ARB=0 apaga
las surebets Azuro + Polymarket. Con los dos en 0 todo vive en Azuro: no hace falta saldo ni modo real en Polymarket.
Cobro: cada CLAIM_EVERY_S cobra solo las apuestas ganadas o anuladas (LP.withdrawPayouts, gas en POL): la plata vuelve
a la wallet y se puede volver a apostar, así hace falta menos capital.
Modos (AUTO_EXEC en .env): off | dry | live. "live" exige clave de Azuro y Polymarket en real (POLY_LIVE=1); si falta algo
queda en dry y lo dice. Monto: AUTO_AZURO_STAKE en Azuro por jugada (Polymarket va en proporción: la otra pata o la red).
Topes: AUTO_MAX_PER_OP (total comprometido por jugada, Azuro + Polymarket), AUTO_MAX_DAILY y AUTO_MAX_OPEN. Frenos: semáforo en rojo, /pausa de Telegram, el mismo partido una sola vez, nada a menos de 10 min,
CS2 y Dota2 nunca (solo observación).
Corre en segundo plano (las confirmaciones de Azuro tardan segundos y no deben frenar el escaneo).
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import httpx

from .alerts import send_telegram
from .arb import POLYMARKET, ValueBet
from .azuro_exec import ACCEPTED, AzuroClient
from .models import BookLevel, market_label
from .polybuy import CLOB, walk_shares
from .safety import observe_only
from .staking import net_limit

MODES = ("off", "dry", "live")
LOOSE_MAX_LOSS_PCT = 1.0
LOOSE_RETRY_S = 60
MIN_TO_START_S = 10 * 60
LOOSE_GIVE_UP_S = 5 * 60
VALUE_MIN_EV = 2.0
TICK = 0.01
OPEN_STATES = ("placing", "covered", "loose", "value", "dry")
CLAIM_EVERY_S = 15 * 60
CLAIM_MIN_POL = 0.02          # POL mínimo para pagar el gas del cobro
POLY_MIN_SHARES = 5
# Polymarket pide que el monto en USDC de una orden que se llena al instante tenga como mucho 2 decimales: con precio en
# centavos y shares ENTERAS siempre se cumple. Por eso el monto de Azuro se elige para que su cobro sea un entero.

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_exec (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL, kind TEXT,           -- arb (surebet Azuro + Polymarket) | value (valor + red en Azuro)
    event_key TEXT, title TEXT, market TEXT, label TEXT, start_ts REAL,
    mode TEXT,                            -- dry (solo anotado) | live (dinero real)
    status TEXT,                          -- placing | covered | loose | value | rejected | error | dry
    total REAL,                           -- comprometido (Azuro + Polymarket)
    az_ref TEXT, az_stake REAL, az_min_odds REAL, az_odds REAL, az_order TEXT, az_state TEXT, az_fee REAL,
    poly_token TEXT, poly_label TEXT, poly_shares REAL, poly_spent REAL, poly_price REAL, poly_fee_rate REAL,
    poly_fee_exp REAL, poly_status TEXT,
    net_pct REAL,                         -- arb: ganancia asegurada sobre el total · valor: EV de la apuesta en Azuro
    net_id INTEGER,                       -- valor: fila de value_nets con la red
    reason TEXT, updated_ts REAL
);
"""


def _observed(event) -> bool:
    """CS2 y Dota2: solo observación, nunca en el automático (decisión del 18/10, ver safety.OBSERVE_SPORTS)."""
    return bool(getattr(event, "members", None)) and observe_only(event)


def _odds(x) -> float | None:
    """Cuota que devuelve Azuro: decimal ("2.35") o cruda con 12 decimales."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v / 10 ** 12 if v > 1000 else v


class Executor:
    def __init__(self, db_path: Path, buyer, nets, gate=None, mode: str = "dry", max_per_op: float = 100.0,
                 max_daily: float = 600.0, max_open: int = 8, azuro_key: str = "", affiliate: str = "",
                 telegram: tuple[str, str] = ("", ""), azuro_stake: float = 30.0, value_net: bool = True,
                 arb: bool = True) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.buyer, self.nets, self.gate = buyer, nets, gate
        self.max_per_op, self.max_daily, self.max_open = max_per_op, max_daily, int(max_open)
        self.azuro_stake = azuro_stake
        self.value_net, self.arb = bool(value_net), bool(arb)
        self.telegram = telegram
        self.azuro = AzuroClient(azuro_key, affiliate)
        mode = mode if mode in MODES else "dry"
        missing = []
        if mode == "live":
            if not self.azuro.address:
                missing.append(self.azuro.error or "falta la clave de Azuro (AZURO_PRIVATE_KEY o POLY_PRIVATE_KEY)")
            if not buyer.live and (self.value_net or self.arb):   # solo Azuro (sin red ni surebets): no hace falta
                missing.append("Polymarket sigue en demo (POLY_LIVE=1 y POLY_PRIVATE_KEY)")
        self.mode = "dry" if missing else mode
        self.status = (f"{self.mode}" + (f" (pediste live pero {'; '.join(missing)})" if missing else "")
                       + f" · ${azuro_stake:g} en Azuro por jugada (tope ${max_per_op:g} con Polymarket), "
                         f"${max_daily:g}/día, {self.max_open} abiertas · valor {'con' if self.value_net else 'SIN'} red"
                         f" · surebets {'sí' if self.arb else 'no'}")
        self._queue: list[tuple] = []
        self._last_loose = 0.0
        self._last_claim = 0.0
        self._claiming: dict[int, float] = {}   # apuesta -> cuándo se mandó su cobro (no repetir mientras confirma)
        self._warned_pol = False
        self._warned_funds = False
        self.conn.execute("CREATE TABLE IF NOT EXISTS paper_meta (k TEXT PRIMARY KEY, v TEXT)")
        self.conn.execute("INSERT OR REPLACE INTO paper_meta VALUES ('executor', ?)", (json.dumps({
            "mode": self.mode, "status": self.status, "azuro_stake": azuro_stake, "max_per_op": max_per_op,
            "max_daily": max_daily, "value_net": self.value_net, "arb": self.arb,
            "max_open": self.max_open,   # el panel es público: la wallet va recortada
            "wallet": f"{self.azuro.address[:6]}…{self.azuro.address[-4:]}" if self.azuro.address else None}),))
        self.conn.commit()

    # ------------------------------------------------------------------ entradas (desde el ciclo del escáner)
    def queue_arb(self, o, plan: list[dict]) -> None:
        if self.mode != "off" and self.arb and len(plan) == 2 and {p["source"] for p in plan} == {"azuro", POLYMARKET}:
            self._queue.append(("arb", o, plan))

    def queue_value(self, v, quote, stake: float, odds: float, fair: float) -> None:
        if self.mode != "off" and quote is not None and quote.source == "azuro":
            self._queue.append(("value", v, quote, stake, odds, fair))

    async def run(self) -> None:
        """Segundo plano: ejecuta la cola de a una jugada (nunca dos a la vez) y reintenta las patas sueltas."""
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "surebet3/0.2"}) as client:
            while True:
                try:
                    await self.step(client)
                except Exception as e:      # el ejecutor nunca tumba al escáner
                    self.status = f"{self.mode} · error: {e!r}"[:200]
                await asyncio.sleep(2)

    async def step(self, client: httpx.AsyncClient) -> None:
        while self._queue:
            job = self._queue.pop(0)
            try:
                if job[0] == "arb":
                    await self._arb(client, *job[1:])
                else:
                    await self._value(client, *job[1:])
            except Exception as e:
                await self._say(client, f"⚠️ Ejecutor: error inesperado ({e!r})"[:300])
                self.status = f"{self.mode} · error: {e!r}"[:200]
        if time.time() - self._last_loose >= LOOSE_RETRY_S:
            self._last_loose = time.time()
            await self._retry_loose(client)
        if self.mode == "live" and time.time() - self._last_claim >= CLAIM_EVERY_S:
            self._last_claim = time.time()
            try:
                await self._claim(client)
            except Exception as e:      # un cobro fallido se reintenta en 15 min; nunca frena las apuestas
                self.status = f"{self.mode} · cobro falló: {e!r}"[:200]

    async def _claim(self, client: httpx.AsyncClient) -> None:
        """Cobra las apuestas ganadas o anuladas de Azuro: la plata vuelve a la wallet para las próximas."""
        now = time.time()
        self._claiming = {k: t for k, t in self._claiming.items() if now - t < 600}
        bets = [b for b in await self.azuro.redeemable(client) if b["bet_id"] not in self._claiming]
        if not bets:
            return
        if await self.azuro.pol_balance(client) < CLAIM_MIN_POL:
            if not self._warned_pol:
                self._warned_pol = True
                await self._say(client, f"⚠️ Hay {len(bets)} apuestas de Azuro para cobrar (${sum(b['payout'] for b in bets):,.2f}) "
                                        "pero falta POL para el gas. Manda ~0,5 POL a la wallet del bot.")
            return
        self._warned_pol = False
        groups: dict[tuple, list] = {}
        for b in bets:
            groups.setdefault((b["core"], b["lp"]), []).append(b)
        for (core, lp), bs in groups.items():
            bs = bs[:20]
            ids = [b["bet_id"] for b in bs]
            try:
                tx = await self.azuro.claim(client, core, lp, ids)
            except Exception as e:
                await self._say(client, f"⚠️ No pude cobrar en Azuro: {e}"[:300])
                continue
            for i in ids:
                self._claiming[i] = now
            await self._say(client, f"💰 Cobré {len(ids)} apuestas de Azuro (${sum(b['payout'] for b in bs):,.2f}): "
                                    f"https://polygonscan.com/tx/{tx}")

    async def _say(self, client: httpx.AsyncClient, text: str) -> None:
        if self.mode == "live":
            await send_telegram(client, self.telegram[0], self.telegram[1], text)

    # ------------------------------------------------------------------ frenos
    def _day0(self) -> float:
        return time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))

    def committed_today(self) -> float:
        return self.conn.execute("SELECT COALESCE(SUM(total),0) FROM live_exec WHERE created_ts >= ? AND mode=? "
                                 "AND status NOT IN ('rejected','error')", (self._day0(), self.mode)).fetchone()[0]

    def open_count(self) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM live_exec WHERE mode=? AND status IN {OPEN_STATES} "
                                 "AND COALESCE(start_ts,0) + 3*3600 > ?", (self.mode, time.time())).fetchone()[0]

    def _blocked(self, kind: str, event_key: str, start_ts: float | None, sources: set, total: float) -> str | None:
        now = time.time()
        if start_ts is None or start_ts - now < MIN_TO_START_S:
            return "empieza en menos de 10 min"
        if self.conn.execute("SELECT 1 FROM live_exec WHERE event_key=? AND mode=? AND status NOT IN ('rejected','error')",
                             (event_key, self.mode)).fetchone():
            return "ese partido ya tiene una jugada"
        if self.gate:
            why = self.gate.blocks("auto" if kind == "arb" else "valor", sources)
            if why:
                return why
        if self.buyer._paused():
            return "pausado con /pausa"
        if self.committed_today() + total > self.max_daily + 1e-9:
            return f"llegó al tope del día (${self.max_daily:g})"
        if self.open_count() >= self.max_open:
            return f"ya hay {self.max_open} jugadas abiertas"
        return None

    def _insert(self, **kw) -> int:
        kw.update(created_ts=time.time(), updated_ts=time.time(), mode=self.mode)
        cur = self.conn.execute(f"INSERT INTO live_exec ({', '.join(kw)}) VALUES ({', '.join('?' * len(kw))})",
                                tuple(kw.values()))
        self.conn.commit()
        return cur.lastrowid

    def _update(self, row_id: int, **kw) -> None:
        kw["updated_ts"] = time.time()
        self.conn.execute(f"UPDATE live_exec SET {', '.join(f'{k}=?' for k in kw)} WHERE id=?", (*kw.values(), row_id))
        self.conn.commit()     # siempre antes del próximo await (la base no puede quedar tomada)

    # ------------------------------------------------------------------ Azuro
    async def _bet_azuro(self, client, row_id: int, ref: str, stake: float, min_odds: float, seen_odds: float):
        """Apuesta en Azuro. Devuelve la cuota aceptada o None (y deja la fila en rejected/error con el motivo)."""
        cond, _, outcome = ref.partition("|")
        fee_raw = await self.azuro.fee(client)
        self._update(row_id, az_fee=round(fee_raw / 1e6, 4))
        if self.mode == "dry":
            odds, max_bet = await self.azuro.quote(client, cond, outcome)
            if not odds or odds < min_odds - 1e-9:
                self._update(row_id, status="rejected", az_state="(dry) cuota bajó",
                             reason=f"Azuro da {odds or 0:.3f} < mínima {min_odds:.3f}")
                return None
            if stake > max_bet:
                self._update(row_id, status="rejected", az_state="(dry) máximo", reason=f"Azuro acepta como máximo ${max_bet:,.2f}")
                return None
            self._update(row_id, az_odds=odds, az_state="(dry) se habría aceptado")
            return odds
        need = stake + fee_raw / 1e6
        try:   # sin saldo o sin permiso, Azuro la rechaza igual: mejor no mandarla y avisar una vez
            balance, allowed = await self.azuro.usdt_balance(client), await self.azuro.allowance(client)
        except Exception:
            balance = allowed = None
        short = (f"saldo insuficiente en la wallet (${balance:,.2f} USDT)" if balance is not None and balance < need
                 else "falta aprobar USDT al relayer (tools/azuro_wallet.py --approve)" if allowed is not None
                 and allowed < need else None)
        if short:
            self._update(row_id, status="rejected", az_state="no enviada", reason=short)
            if not self._warned_funds:
                self._warned_funds = True
                await self._say(client, f"⚠️ Ejecutor: no apuesto en Azuro: {short}. Las próximas se saltan hasta que se arregle.")
            return None
        self._warned_funds = False
        body = self.azuro.build(cond, outcome, stake, min_odds, fee_raw)
        try:
            resp = await self.azuro.place(client, body)
        except Exception as e:
            self._update(row_id, status="rejected", az_state="no enviada", reason=f"Azuro no tomó la orden: {e}"[:300])
            return None
        order_id = str(resp.get("id") or "")
        self._update(row_id, az_order=order_id, az_state=str(resp.get("state") or "Created"))
        st = await self.azuro.wait(client, order_id)
        state = str(st.get("state"))
        if state not in ACCEPTED:
            why = st.get("errorMessage") or st.get("error") or ("sin respuesta en 90 s" if st.get("timeout") else state)
            # Sin respuesta no es lo mismo que rechazada: se marca para revisar a mano (puede aceptarse después)
            self._update(row_id, status="error" if st.get("timeout") else "rejected", az_state=state,
                         reason=f"Azuro: {why}"[:300])
            return None
        odds = _odds(st.get("odds")) or seen_odds
        self._update(row_id, az_state=state, az_odds=round(odds, 4))
        return odds

    # ------------------------------------------------------------------ Polymarket (cubrir la surebet)
    async def _cover(self, client, row_id: int, max_loss_pct: float) -> tuple[bool, str]:
        """Compra en Polymarket las shares que igualan el cobro de Azuro, si con el libro de AHORA la pérdida no pasa
        de max_loss_pct (0 = solo con ganancia). FOK: se llena entera o nada."""
        row = self.conn.execute("SELECT az_stake, az_fee, poly_token, poly_shares, poly_fee_rate, poly_fee_exp FROM live_exec "
                                "WHERE id=?", (row_id,)).fetchone()
        az_stake, az_fee, token, shares, fee_rate, fee_exp = row
        try:
            book = (await client.get(f"{CLOB}/book", params={"token_id": token}, timeout=10)).json()
        except (httpx.HTTPError, ValueError):
            return False, "no se pudo leer el libro de Polymarket"
        asks = sorted((BookLevel(float(a["price"]), float(a["size"])) for a in book.get("asks") or []), key=lambda l: l.price)
        tick = float(book.get("tick_size") or TICK)
        plan = walk_shares(asks, shares, fee_rate or 0.0, fee_exp or 1.0)
        if not plan:
            return False, f"no hay {shares:,.2f} shares en el libro de Polymarket"
        total = az_stake + (az_fee or 0) + plan["spent"]
        net = 100 * (shares / total - 1)
        if net < -max_loss_pct:
            return False, f"cubrir ahora daría {net:+.2f} %"
        cap = math.ceil(plan["top"] / tick - 1e-9) * tick
        if self.mode == "live":
            try:
                resp = await asyncio.to_thread(self.buyer.post_buy, token, cap, shares, "FOK")
            except Exception as e:
                return False, f"Polymarket rechazó la orden: {e}"[:200]
            if not (resp or {}).get("success", False):
                return False, f"Polymarket no llenó la orden entera (FOK): {str(resp)[:120]}"
        self._update(row_id, status="covered", poly_spent=round(plan["spent"], 4), poly_price=round(cap, 4),
                     poly_status="llenada" if self.mode == "live" else "(dry) se habría llenado",
                     net_pct=round(net, 3), total=round(total, 2))
        return True, f"{net:+.2f} %"

    # ------------------------------------------------------------------ A. surebet Azuro + Polymarket
    async def _arb(self, client, o, plan: list[dict]) -> None:
        az = next(p for p in plan if p["source"] == "azuro")
        pm = next(p for p in plan if p["source"] == POLYMARKET)
        total_plan = az["stake"] + pm["stake"]
        seen = az.get("fill_odds") or az["odds"]
        # $AUTO_AZURO_STAKE en Azuro (o lo que aguantaba el plan, si es menos); el total no pasa de AUTO_MAX_PER_OP
        target = min(self.azuro_stake, az["stake"], self.max_per_op * az["stake"] / total_plan) if total_plan else 0.0
        shares = math.floor(target * seen)                       # cobro de Azuro = shares enteras de Polymarket
        az_stake = round(shares / seen, 2) if seen else 0.0
        pm_stake = shares * pm["stake"] / (az["stake"] * seen) if az["stake"] else 0.0   # mismo precio del plan
        ev = o.event.anchor
        start = ev.start.timestamp() if ev.start else None
        why = self._blocked("arb", f"{o.event.key}#{o.market}", start, {"azuro", POLYMARKET}, az_stake + pm_stake)
        if not why and _observed(o.event):
            why = "CS2/Dota2: solo observación"
        if shares < POLY_MIN_SHARES or az_stake < 1:
            why = why or "monto muy chico para el mínimo de Polymarket (5 shares)"
        row_id = self._insert(kind="arb", event_key=f"{o.event.key}#{o.market}", title=ev.title, market=market_label(o.market),
                              label=az["label"], start_ts=start, status="rejected" if why else "placing",
                              total=round(az_stake + pm_stake, 2), az_ref=az["ref"], az_stake=az_stake,
                              az_min_odds=round(az["min_odds"], 4), poly_token=pm["token"], poly_label=pm["label"],
                              poly_shares=shares, poly_fee_rate=pm.get("fee_rate"), poly_fee_exp=pm.get("fee_exp"),
                              reason=why)
        if why:
            return
        try:   # mercados con tick de 0,001: el monto no queda en 2 decimales con shares enteras → no se arranca
            tick = float((await client.get(f"{CLOB}/book", params={"token_id": pm["token"]}, timeout=10)).json()
                         .get("tick_size") or TICK)
        except (httpx.HTTPError, ValueError):
            tick = TICK
        if tick < TICK - 1e-9:
            self._update(row_id, status="rejected", reason=f"Polymarket con tick {tick:g}: no se arranca")
            return
        odds = await self._bet_azuro(client, row_id, az["ref"], az_stake, az["min_odds"], seen)
        if odds is None:
            return
        self._update(row_id, poly_shares=max(shares, round(az_stake * odds)))   # si Azuro dio mejor cuota, cubre ese cobro
        ok, info = await self._cover(client, row_id, 0.0)
        if ok:
            if self.mode == "dry":
                self._update(row_id, status="dry")
            await self._say(client, f"🤖✅ REAL surebet {ev.title} ({market_label(o.market)}): Azuro ${az_stake:,.2f} a "
                                    f"«{az['label']}» @ {odds:.3f} + Polymarket «{pm['label']}». Asegurado {info}.")
            return
        self._update(row_id, status="loose" if self.mode == "live" else "dry", poly_status=info,
                     reason=f"pata suelta: {info}" if self.mode == "live" else f"(dry) Polymarket: {info}")
        await self._say(client, f"🤖⚠️ PATA SUELTA {ev.title}: Azuro aceptó ${az_stake:,.2f} a «{az['label']}» @ {odds:.3f} "
                                f"pero Polymarket no ({info}). Reintento cada minuto y cubro si la pérdida no pasa de "
                                f"{LOOSE_MAX_LOSS_PCT:g} %; si no, queda como apuesta de valor. /pausa para frenar el resto.")

    async def _retry_loose(self, client) -> None:
        now = time.time()
        for row_id, title, start in self.conn.execute(
                "SELECT id, title, start_ts FROM live_exec WHERE status='loose' AND mode=?", (self.mode,)).fetchall():
            if start and now >= start - LOOSE_GIVE_UP_S:
                self._update(row_id, status="value", reason="no se pudo cubrir antes del partido: queda como valor en Azuro")
                await self._say(client, f"🤖 {title}: no se pudo cubrir en Polymarket sin perder más de "
                                        f"{LOOSE_MAX_LOSS_PCT:g} %. Queda como apuesta de valor en Azuro.")
                continue
            ok, info = await self._cover(client, row_id, LOOSE_MAX_LOSS_PCT)
            if ok:
                await self._say(client, f"🤖✅ {title}: pata suelta cubierta en Polymarket ({info}).")

    # ------------------------------------------------------------------ B. valor + red en Azuro
    async def _value(self, client, v, quote, stake: float, odds: float, fair: float) -> None:
        limit = net_limit(odds)
        if limit <= 0:
            return
        if self.value_net:
            stake = round(min(self.azuro_stake, self.max_per_op / (1 + odds * limit)), 2)   # monto fijo; Azuro + red ≤ tope
            total = round(stake + stake * odds * limit, 2)
        else:
            stake = total = round(min(self.azuro_stake, self.max_per_op), 2)                # valor puro: solo Azuro
        ev = v.event.anchor
        start = ev.start.timestamp() if ev.start else None
        min_odds = round(max((1 + VALUE_MIN_EV / 100) / fair, 1.01), 3)
        why = self._blocked("value", f"{v.event.key}#{v.market}", start, {"azuro", POLYMARKET}, total)
        if not why and _observed(v.event):
            why = "CS2/Dota2: solo observación"
        if not why and min_odds > odds + 1e-9:
            why = f"la cuota {odds:.3f} ya no deja EV ≥ {VALUE_MIN_EV:g} %"
        row_id = self._insert(kind="value", event_key=f"{v.event.key}#{v.market}", title=ev.title,
                              market=market_label(v.market), label=quote.label, start_ts=start,
                              status="rejected" if why else "placing", total=total, az_ref=quote.ref, az_stake=stake,
                              az_min_odds=min_odds, net_pct=round(100 * (odds * fair - 1), 3), reason=why)
        if why:
            return
        got = await self._bet_azuro(client, row_id, quote.ref, stake, min_odds, odds)
        if got is None:
            return
        if self.mode == "dry":
            self._update(row_id, status="dry", net_pct=round(100 * (got * fair - 1), 3),
                         reason=f"(dry) red: orden límite a {100 * limit:.0f}¢ por {stake * got:,.0f} shares"
                         if self.value_net else "(dry) sin red: solo la apuesta en Azuro")
            return
        if not self.value_net:
            self._update(row_id, status="value", net_pct=round(100 * (got * fair - 1), 3), reason="sin red (AUTO_VALUE_NET=0)")
            await self._say(client, f"🤖✅ REAL valor {ev.title}: Azuro ${stake:,.2f} a «{quote.label}» @ {got:.3f} "
                                    f"(EV {100 * (got * fair - 1):+.1f} %), sin red.")
            return
        # La red de 🛟 con la apuesta de Azuro (cuota aceptada): la vigila valuenet.tick y avisa por Telegram
        vb = ValueBet(v.event, replace(quote, odds=got), fair, v.poly_tokens, v.market, v.poly_conditions)
        nid = self.nets.offer(vb, stake)
        text = await self.nets.click(client, nid) if nid else "no hay red posible en Polymarket"
        self._update(row_id, status="value", net_id=nid, net_pct=round(100 * (got * fair - 1), 3), reason=text[:300])
        await self._say(client, f"🤖✅ REAL valor {ev.title}: Azuro ${stake:,.2f} a «{quote.label}» @ {got:.3f} "
                                f"(EV {100 * (got * fair - 1):+.1f} %).\n{text}")


def summary(conn) -> dict | None:
    cols = ("id", "created_ts", "kind", "title", "market", "label", "start_ts", "mode", "status", "total", "az_stake",
            "az_min_odds", "az_odds", "az_state", "az_fee", "poly_label", "poly_shares", "poly_spent", "poly_status",
            "net_pct", "reason")
    try:
        meta = conn.execute("SELECT v FROM paper_meta WHERE k='executor'").fetchone()
        rows = [dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {', '.join(cols)} FROM live_exec ORDER BY created_ts DESC LIMIT 80")]
        day0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        by = {}
        for mode, kind, status, n, total, net in conn.execute(
                "SELECT mode, kind, status, COUNT(*), SUM(total), AVG(net_pct) FROM live_exec GROUP BY mode, kind, status"):
            by.setdefault(mode, []).append({"kind": kind, "status": status, "n": n, "total": round(total or 0, 2),
                                            "net": round(net, 2) if net is not None else None})
        today = dict(conn.execute("SELECT mode, SUM(total) FROM live_exec WHERE created_ts >= ? AND status NOT IN "
                                  "('rejected','error') GROUP BY mode", (day0,)).fetchall())
    except sqlite3.OperationalError:
        return None
    return {"config": json.loads(meta[0]) if meta else None, "rows": rows, "by": by,
            "today": {k: round(v or 0, 2) for k, v in today.items()}}
