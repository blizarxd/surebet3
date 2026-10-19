"""
Valor automático EN SOMBRA (nunca envía nada) en las casas que se operan por API: SX Bet, Azuro y Cloudbet.
¿El oro de esas casas es real o es cuota vieja? ¿Alguna puede reemplazar a 1xBet en valor + red sin clics?

El escáner refresca los libros de SX cada 5 min para partidos a 1–6 h y la apuesta de valor usa solo el mejor nivel;
el automático de surebets vio que al releer el libro la cuota de SX salía ~5 % más baja (15 de 20 intentos). Esta
sombra hace lo que haría el bot de verdad con cada señal de valor ORO (de cualquier casa):
  1. busca la misma selección (mismo mercado y lo que cubre) en cada casa con API; si no está en ninguna →
     "sin casa API" (queda el aviso manual de 1xBet);
  2. pasados LATENCY_S pide el precio en ese momento al monto (Kelly/4, mínimo VALUE_MIN_TOTAL entre casa y red,
     como valor + red): SX su libro con comisión, Azuro la cuota y el máximo que acepta, Cloudbet la selección;
  3. si con esa cuota el EV contra Polymarket sigue ≥ MIN_EV → "llenada" (el bot apostaría solo);
     si no → "rechazada" con el motivo, y la señal queda para 1xBet a mano (si era de 1xBet) o se pierde.
De las llenadas se mide el CLV con la cuota REAL (contra el cierre de Polymarket) y el resultado del partido.
También se guarda cuánto aguantaba la casa con EV ≥ MIN_EV (para saber hasta qué monto se puede crecer).
La red de Polymarket iría igual que hoy (orden límite del lado contrario): su efecto ya se mide en valor + red.
Aprobada una casa para probar en real si: ≥ 100 llenadas, se llena ≥ 50 % de sus señales y CLV real ≥ +1,5 %.
Nivel "amplio" (19/10): las señales con EV 1,5–2 % se prueban igual (llenada si el EV real sigue ≥ 1,5 %) en filas aparte
(tier='amplio'); no van al ejecutor real ni cuentan en la aprobación del oro.
(El archivo se llama sxshadow porque empezó solo con SX; las columnas sx_* son de la casa con API de cada fila.)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import httpx

from .nettrack import price_path
from .pricing import fill_detail
from .sources.azuro import API as AZURO_API, ENV as AZURO_ENV
from .sources.sxbet import API as SX_API, book_levels
from .staking import net_limit, value_stake

HOUSES = ("sxbet", "azuro", "cloudbet")
LATENCY_S = 5.0
MIN_EV = 2.0
WIDE_MIN_EV = 1.5
EXPIRE_S = 120.0
PER_VERIFY = 10
SETTLE_EVERY_S = 300.0
EARLY_PRICE, EARLY_AFTER_S = 0.997, 2 * 3600     # partido decidido: como en paper.py
CLOB = "https://clob.polymarket.com"
MANUAL = {"1xbet", "onexbet"}
APPROVE = {"fills": 100, "fill_rate": 50.0, "clv": 1.5}
NAMES = {"sxbet": "SX", "azuro": "Azuro", "cloudbet": "Cloudbet"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sx_shadow (
    key TEXT PRIMARY KEY,       -- partido#mercado#lo que cubre#casa (una fila por selección y casa con API)
    created_ts REAL, start_ts REAL, title TEXT, sport TEXT, market TEXT, label TEXT, covers TEXT,
    signal_source TEXT, signal_odds REAL, signal_ev REAL,   -- la señal oro que la disparó (casa, cuota, EV)
    fair_prob REAL,             -- probabilidad justa de Polymarket al detectar
    house TEXT,                 -- casa con API donde se prueba (sxbet | azuro | cloudbet; NULL = ninguna la tiene)
    tier TEXT,                  -- oro (EV 2–7 %) | amplio (EV 1,5–2 %, solo medición)
    sx_ref TEXT,                -- referencia de la casa para apostar (SX marketHash|lado, Azuro condición|resultado...)
    sx_seen_odds REAL,          -- cuota de esa casa que tenía el escáner
    sx_seen_age_s REAL,         -- antigüedad de ese precio al detectar
    stake REAL,
    status TEXT,                -- pendiente | llenada | rechazada | sin casa API
    reason TEXT,
    checked_ts REAL,
    fill_odds REAL,             -- cuota real al monto, leída en la casa al ejecutar (neta de comisión)
    ev_real REAL,               -- EV con esa cuota contra Polymarket
    capacity REAL,              -- USDC que aguantaba la casa con EV ≥ MIN_EV
    tokens TEXT, conds TEXT,    -- Polymarket {resultado: token / condition_id} (CLV y resultado)
    p_close REAL, clv_real REAL,
    result TEXT,                -- pendiente | ganada | perdida | anulada
    pnl REAL,                   -- sin red
    last_check REAL
);
"""


def ev_at(odds: float, fair: float) -> float:
    return 100 * (odds * fair - 1)


def capacity(levels, fair: float, min_ev: float = MIN_EV) -> float:
    """USDC que se pueden apostar en SX con EV ≥ min_ev (niveles de mejor a peor cuota)."""
    return sum(l.price * l.size for l in sorted(levels, key=lambda l: l.price) if ev_at(1 / l.price, fair) >= min_ev)


def fill_odds(levels, stake: float) -> float | None:
    fd = fill_detail(tuple(sorted(levels, key=lambda l: l.price)), stake, 0.0)
    return fd["shares"] / fd["spent"] if fd["spent"] >= stake * 0.999 else None


async def real_price(client: httpx.AsyncClient, house: str, ref: str, stake: float, fair: float,
                     cloudbet_key: str = "") -> tuple[float | None, float | None, str | None]:
    """(cuota real al monto, cuánto aguanta con EV ≥ MIN_EV, motivo si no se puede) leído en la casa AHORA."""
    name = NAMES.get(house, house)
    try:
        if house == "sxbet":
            market_hash, _, side = ref.partition("|")
            r = await client.get(f"{SX_API}/orderbook-v3/snapshot", timeout=8,
                                 params={"marketHash": market_hash, "showTakerPerspective": "true"})
            data = (r.json() or {}).get("data") or {} if r.status_code == 200 else None
            if data is None:
                return None, None, "no se pudo leer el libro de SX"
            levels = book_levels(data.get("outcomeOne" if side == "1" else "outcomeTwo") or [])
            odds = fill_odds(levels, stake)
            return odds, round(capacity(levels, fair), 2), None if odds else f"el libro de SX no llega a ${stake:,.2f}"
        if house == "azuro":
            cid, _, oid = ref.partition("|")
            r = await client.post(f"{AZURO_API}/market-manager/condition-batch", timeout=8,
                                  json={"conditionIds": [cid], "environment": AZURO_ENV})
            cond = ((r.json() or {}).get("conditions") or [None])[0] if r.status_code in (200, 201) else None
            out = next((x for x in (cond or {}).get("outcomes") or [] if str(x.get("outcomeId")) == oid), None)
            if not cond or cond.get("state") != "Active" or not out or out.get("state") not in (None, "Active"):
                return None, None, "Azuro ya no acepta apuestas en esa selección"
            odds = float(out.get("odds") or 0) or None
            lim = await client.post(f"{AZURO_API}/bet/calculation", timeout=8, json={
                "bets": [{"conditionId": cid, "outcomeId": int(oid)}], "environment": AZURO_ENV})
            max_bet = float(((lim.json() or {}).get("response") or {}).get("maxBet") or 0)
            cap = max_bet if odds and ev_at(odds, fair) >= MIN_EV else 0.0
            if not odds or stake > max_bet:
                return None, round(cap, 2), f"Azuro acepta como máximo ${max_bet:,.2f}"
            return odds, round(cap, 2), None
        if house == "cloudbet":
            from .autovivo import fresh_odds
            odds = await fresh_odds(client, {"source": "cloudbet", "stake": stake, "ref": ref}, cloudbet_key)
            return odds, None, None if odds else "Cloudbet cerró la selección o no acepta ese monto"
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None, None, f"no se pudo leer {name}"
    return None, None, f"{name}: sin lector de precio"


class SXShadow:
    def __init__(self, db_path: Path, bankroll: float = 1000.0, min_total: float = 30.0, cloudbet_key: str = "") -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(sx_shadow)")}
        if "house" not in cols:   # versión de una hora solo con SX y claves sin casa: se empieza de cero
            self.conn.execute("ALTER TABLE sx_shadow ADD COLUMN house TEXT")
            self.conn.execute("DELETE FROM sx_shadow")
            cols.add("house")
        if "tier" not in cols:
            self.conn.execute("ALTER TABLE sx_shadow ADD COLUMN tier TEXT DEFAULT 'oro'")
        self.conn.commit()
        self.bankroll, self.min_total = bankroll, min_total
        self.cloudbet_key = cloudbet_key
        self.houses = tuple(h for h in HOUSES if h != "cloudbet" or cloudbet_key)
        self._last_settle = 0.0
        # El ejecutor real (surebet/executor.py) recibe las llenadas: on_fill(señal, cuota de la casa, monto, cuota real, p)
        self.on_fill = None
        self._orig: dict[str, tuple] = {}   # clave -> (señal, cuota de la casa) mientras se revisa

    # ------------------------------------------------------------------ cada ciclo
    def step(self, values, is_gold, is_wide=None) -> int:
        """Anota cada selección con señal oro nueva (la de mejor EV del ciclo) en cada casa con API que la tenga.
        Con `is_wide`, también las de nivel amplio (EV 1,5–2 %), en filas aparte (clave terminada en #amplio)."""
        now = time.time()
        rows = []
        seen: set[str] = set()
        for v in sorted(values, key=lambda v: v.ev_pct, reverse=True):
            st = v.event.anchor.start
            if st is None:
                continue
            hrs = (st.timestamp() - now) / 3600
            gold = is_gold(v.event.live, v.ev_pct, v.quote.odds, hrs)
            if not gold and not (is_wide and is_wide(v.event.live, v.ev_pct, v.quote.odds, hrs)):
                continue
            tier, suffix = ("oro", "") if gold else ("amplio", "#amplio")
            covers = "".join(sorted(v.quote.covers))
            sel = f"{v.event.key}#{v.market}#{covers}"
            like = sel.replace("%", "\\%").replace("_", "\\_") + "#%"
            # oro: una vez por selección; amplio: solo si la selección no se anotó antes (ni como oro ni como amplio)
            taken = self.conn.execute("SELECT 1 FROM sx_shadow WHERE key LIKE ? ESCAPE '\\'" + (
                " AND COALESCE(tier,'oro')='oro'" if gold else ""), (like,)).fetchone()
            if sel + tier in seen or (not gold and sel + "oro" in seen) or taken:
                continue
            seen.add(sel + tier)
            best: dict = {}
            for q in v.event.quotes_for(v.market):
                if q.source in self.houses and q.covers == v.quote.covers and q.ref and q.odds > best.get(q.source, (0,))[0]:
                    best[q.source] = (q.odds, q)
            common = (now, st.timestamp(), v.event.anchor.title, v.event.anchor.sport, v.market, v.quote.label, covers,
                      v.quote.source, round(v.quote.odds, 4), round(v.ev_pct, 3), round(v.fair_prob, 5))
            polys = (json.dumps(v.poly_tokens), json.dumps(v.poly_conditions))
            if not best:
                rows.append((f"{sel}#-{suffix}", *common, None, tier, None, None, None, None, "sin casa API",
                             "ninguna casa con API tiene esta selección: queda el aviso manual", *polys))
            for house, (_, q) in best.items():
                stake = round(value_stake(q.odds, max(ev_at(q.odds, v.fair_prob), MIN_EV), self.bankroll,
                                          min_total=self.min_total, limit=net_limit(q.odds)), 2)
                rows.append((f"{sel}#{house}{suffix}", *common, house, tier, q.ref, round(q.odds, 4), round(now - q.ts, 1),
                             stake, "pendiente", None, *polys))
                if gold:
                    self._orig[f"{sel}#{house}"] = (v, q)
        self.conn.executemany(
            "INSERT OR IGNORE INTO sx_shadow (key, created_ts, start_ts, title, sport, market, label, covers, signal_source, "
            "signal_odds, signal_ev, fair_prob, house, tier, sx_ref, sx_seen_odds, sx_seen_age_s, stake, status, reason, "
            "tokens, conds) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.execute("UPDATE sx_shadow SET status='rechazada', reason='sin revisar a tiempo' "
                          "WHERE status='pendiente' AND created_ts < ?", (now - EXPIRE_S,))
        self.conn.commit()
        return len(rows)

    async def verify(self, client: httpx.AsyncClient) -> int:
        """Pasada la latencia, el precio de la casa en ese momento: cuota real al monto, EV y cuánto aguanta."""
        now = time.time()
        rows = self.conn.execute(
            "SELECT key, house, sx_ref, stake, fair_prob, signal_source, COALESCE(tier,'oro') FROM sx_shadow "
            "WHERE status='pendiente' AND created_ts <= ? ORDER BY created_ts LIMIT ?", (now - LATENCY_S, PER_VERIFY)).fetchall()
        fetched = []
        for key, house, ref, stake, fair, signal, tier in rows:   # primero todas las consultas, después las escrituras
            fetched.append((key, stake, fair, signal, tier,
                            await real_price(client, house, ref, stake, fair, self.cloudbet_key)))
        updates = []
        for key, stake, fair, signal, tier, (odds, cap, err) in fetched:
            min_ev = MIN_EV if tier == "oro" else WIDE_MIN_EV
            manual = " · queda el aviso manual de 1xBet" if signal in MANUAL else ""
            if odds is None:
                updates.append(("rechazada", (err or "sin precio") + manual, None, None, cap, None, now, key))
                continue
            ev = ev_at(odds, fair)
            if ev < min_ev:
                updates.append(("rechazada", f"cuota real {odds:.3f}: EV {ev:+.2f} % < {min_ev:g} %" + manual,
                                round(odds, 4), round(ev, 3), cap, None, now, key))
                continue
            updates.append(("llenada", f"aceptaba ${stake:,.2f} a {odds:.3f} (EV {ev:+.2f} %)", round(odds, 4),
                            round(ev, 3), cap, "pendiente", now, key))
        self.conn.executemany("UPDATE sx_shadow SET status=?, reason=?, fill_odds=?, ev_real=?, capacity=?, result=?, "
                              "checked_ts=? WHERE key=?", updates)
        self.conn.commit()
        for status, _reason, odds, _ev, _cap, _res, _ts, key in updates:
            orig = self._orig.pop(key, None)
            if status == "llenada" and orig and self.on_fill:
                stake, fair = next((s, f) for k, _h, _r, s, f, _sg, _t in rows if k == key)
                self.on_fill(orig[0], orig[1], stake, odds, fair)
        live = {k for (k,) in self.conn.execute("SELECT key FROM sx_shadow WHERE status='pendiente'")}
        self._orig = {k: v for k, v in self._orig.items() if k in live}
        return len(rows)

    async def settle(self, client: httpx.AsyncClient, every_s: float = SETTLE_EVERY_S) -> int:
        """CLV con la cuota real (cierre de Polymarket) y resultado del partido de las llenadas."""
        now = time.time()
        if now - self._last_settle < every_s:
            return 0
        self._last_settle = now
        rows = self.conn.execute(
            "SELECT key, start_ts, covers, fill_odds, stake, tokens, conds, p_close FROM sx_shadow WHERE status='llenada' "
            "AND result='pendiente' AND start_ts < ? AND (last_check IS NULL OR last_check < ?) "
            "ORDER BY COALESCE(last_check, 0) LIMIT 30", (now - 120, now - 600)).fetchall()
        markets: dict[str, dict | None] = {}
        closes: dict[tuple, float | None] = {}
        prepared = []
        for key, start, covers, odds, stake, tokens_json, conds_json, p_close in rows:
            tokens, conds = json.loads(tokens_json or "{}"), json.loads(conds_json or "{}")
            if p_close is None:   # la misma selección en dos casas comparte el cierre: se pide una vez
                ck = (tokens_json, covers, start)
                if ck not in closes:
                    path = await price_path(client, tokens, set(covers), start - 3 * 3600, start)
                    closes[ck] = path[-1][1] if path else None
                p_close = closes[ck]
            if now - start >= 45 * 60:
                for cid in set(conds.values()):
                    if cid and cid not in markets:
                        try:
                            r = await client.get(f"{CLOB}/markets/{cid}", timeout=15)
                            markets[cid] = r.json() if r.status_code == 200 else None
                        except (httpx.HTTPError, ValueError):
                            markets[cid] = None
            prepared.append((key, start, covers, odds, stake, tokens, conds, p_close))
        for key, start, covers, odds, stake, tokens, conds, p_close in prepared:
            outcome, void, decided = _outcome(tokens, conds, markets, now - start >= EARLY_AFTER_S)
            result = pnl = None
            if decided:
                result = "anulada" if void else ("ganada" if outcome in covers else "perdida")
                pnl = 0.0 if void else round(stake * (odds - 1) if outcome in covers else -stake, 2)
            self.conn.execute(
                "UPDATE sx_shadow SET p_close=?, clv_real=?, result=COALESCE(?, result), pnl=COALESCE(?, pnl), "
                "last_check=? WHERE key=?",
                (p_close, round(100 * (odds * p_close - 1), 3) if p_close else None, result, pnl, now, key))
        self.conn.commit()
        return len(rows)


def _outcome(tokens: dict, conds: dict, markets: dict, early: bool) -> tuple[str | None, bool, bool]:
    """(resultado ganador, anulado, decidido) con los mercados de Polymarket (como paper.settle)."""
    outcome, void, all_closed = None, False, bool(conds)
    for out, cid in conds.items():
        m = markets.get(cid)
        tok = str(tokens.get(out))
        if m and not m.get("closed") and early:
            px = next((float(t.get("price") or 0) for t in m.get("tokens") or [] if str(t.get("token_id")) == tok), 0.0)
            if px >= EARLY_PRICE:
                return out, False, True
        if not m or not m.get("closed"):
            all_closed = False
            continue
        toks = m.get("tokens") or []
        if any(str(t.get("token_id")) == tok and t.get("winner") for t in toks):
            outcome = out
        elif not any(t.get("winner") for t in toks):
            void = True
    return outcome, void and outcome is None, outcome is not None or (all_closed and void)


def _stats(rs: list[dict]) -> dict:
    """Llenado, caída de cuota, CLV real y resultado de un grupo de filas con casa API."""
    fills = [r for r in rs if r["status"] == "llenada"]
    clvs = sorted(r["clv_real"] for r in fills if r["clv_real"] is not None)
    settled = [r for r in fills if r["result"] in ("ganada", "perdida", "anulada")]
    drops = sorted(100 * (1 - r["fill_odds"] / r["sx_seen_odds"]) for r in rs if r["fill_odds"] and r["sx_seen_odds"])
    ages = sorted(r["sx_seen_age_s"] for r in rs if r["sx_seen_age_s"] is not None)
    caps = sorted(r["capacity"] for r in fills if r["capacity"] is not None)
    staked = sum(r["stake"] for r in settled)
    fill_rate = round(100 * len(fills) / len(rs), 1) if rs else None
    clv = round(sum(clvs) / len(clvs), 2) if clvs else None
    return {"n": len(rs), "fills": len(fills), "fill_rate": fill_rate,
            "ev_seen": round(sum(ev_at(r["sx_seen_odds"], r["fair_prob"]) for r in rs) / len(rs), 2) if rs else None,
            "ev_real": round(sum(r["ev_real"] for r in fills) / len(fills), 2) if fills else None,
            "drop_pct": round(drops[len(drops) // 2], 2) if drops else None,
            "age_med_s": ages[len(ages) // 2] if ages else None, "cap_med": caps[len(caps) // 2] if caps else None,
            "clv": clv, "clv_med": clvs[len(clvs) // 2] if clvs else None, "clv_n": len(clvs),
            "clv_pos": round(100 * sum(c > 0 for c in clvs) / len(clvs)) if clvs else None,
            "settled": len(settled), "pnl": round(sum(r["pnl"] or 0 for r in settled), 2),
            "roi": round(100 * sum(r["pnl"] or 0 for r in settled) / staked, 2) if staked else None,
            "ok": (len(fills) >= APPROVE["fills"] and fill_rate is not None and fill_rate >= APPROVE["fill_rate"]
                   and clv is not None and clv >= APPROVE["clv"])}


def summary(conn) -> dict | None:
    try:
        cols = ("key", "created_ts", "start_ts", "title", "sport", "market", "label", "signal_source", "signal_odds",
                "signal_ev", "fair_prob", "house", "sx_seen_odds", "sx_seen_age_s", "stake", "status", "reason",
                "fill_odds", "ev_real", "capacity", "clv_real", "result", "pnl", "tier")
        everything = [dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {', '.join(cols[:-1])}, COALESCE(tier,'oro') FROM sx_shadow ORDER BY created_ts DESC")]
    except sqlite3.OperationalError:
        return None
    rows = [r for r in everything if r["tier"] == "oro"]
    wide = [r for r in everything if r["tier"] == "amplio" and r["status"] != "pendiente" and r["house"]]
    wide_by: dict[str, list] = {}
    for r in wide:
        wide_by.setdefault(r["house"], []).append(r)
    done = [r for r in rows if r["status"] != "pendiente"]
    api = [r for r in done if r["house"]]
    selections = {r["key"].rsplit("#", 1)[0] for r in done}
    covered = {r["key"].rsplit("#", 1)[0] for r in api}
    filled_sel = {r["key"].rsplit("#", 1)[0] for r in api if r["status"] == "llenada"}
    reasons: dict[str, int] = {}
    for r in api:
        if r["status"] == "rechazada":
            k = (r["reason"] or "").split(" · ")[0]
            k = "cuota real con EV menor al mínimo" if k.startswith("cuota real") else k
            k = "el libro no llega al monto" if "no llega a $" in k else k
            k = "la casa no acepta ese monto" if "como máximo $" in k or "no acepta ese monto" in k else k
            reasons[f"{NAMES.get(r['house'], r['house'])}: {k}"] = reasons.get(f"{NAMES.get(r['house'], r['house'])}: {k}", 0) + 1

    def by(field: str) -> list[dict]:
        groups: dict[str, list] = {}
        for r in api:
            groups.setdefault(r[field] or "?", []).append(r)
        return [{"name": NAMES.get(k, k), **_stats(v)} for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]

    return {
        "selections": len(selections), "covered": len(covered), "no_api": len(selections) - len(covered),
        "filled_sel": len(filled_sel), "pending": len(rows) - len(done), **_stats(api),
        "approve": APPROVE, "by_house": by("house"), "by_signal": by("signal_source"), "by_sport": by("sport"),
        "reasons": sorted(({"reason": k, "n": v} for k, v in reasons.items()), key=lambda x: -x["n"]),
        "wide": {**_stats(wide), "by_house": [{"name": NAMES.get(k, k), **_stats(v)}
                                              for k, v in sorted(wide_by.items(), key=lambda kv: -len(kv[1]))]},
        "rows": rows[:150],
    }
