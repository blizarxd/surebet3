"""
Panel web local (http://localhost:8765): cuenta en papel, semana, operaciones y señales en vivo.

Servidor de la librería estándar (sin dependencias). Lee la base SQLite y el archivo de estado
que el escáner escribe en cada ciclo, así que también funciona con el escáner apagado.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import experiments, golive
from .liveodds import leg_ref, load as load_live_odds
from .risk import from_paper_ops, risk_stats

STATIC = Path(__file__).resolve().parent.parent / "static"
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _auto_account(ops: list[dict], initial: float, started: float) -> dict:
    """Misma medición que la cuenta principal, para lo que el bot automático habría ejecutado."""
    settled = sorted((o for o in ops if o["status"] != "open"), key=lambda o: o["settled_ts"] or 0)
    open_ = [o for o in ops if o["status"] == "open"]
    pnl = sum(o["pnl"] or 0 for o in settled)
    staked = sum(o["total_stake"] for o in settled)
    in_play = sum(o["total_stake"] for o in open_)
    eq, curve = initial, [[started * 1000, initial]]
    for o in settled:
        eq += o["pnl"] or 0
        curve.append([(o["settled_ts"] or 0) * 1000, round(eq, 2)])
    return {"account": {"initial": initial, "equity": round(initial + pnl, 2), "cash": round(initial + pnl - in_play, 2),
                        "in_play": round(in_play, 2), "pnl": round(pnl, 2),
                        "roi_pct": round(100 * pnl / staked, 2) if staked else 0.0,
                        "growth_pct": round(100 * pnl / initial, 2) if initial else 0.0,
                        "ops": len(ops), "open": len(open_),
                        "won": sum(o["status"] == "won" for o in settled),
                        "lost": sum(o["status"] == "lost" for o in settled),
                        "void": sum(o["status"] == "void" for o in settled),
                        "fees": round(sum(o["fees"] or 0 for o in ops), 2),
                        "expected_open": round(sum(o["expected_profit"] or 0 for o in open_), 2)},
            "curve": curve, "ops": ops[:200], "risk": risk_stats(from_paper_ops(ops), initial)}


def _execsim(conn) -> dict:
    """Simulación de ejecución automática: cuántas se habrían llenado y por qué fallan las otras."""
    rows = [dict(r) for r in conn.execute(
        "SELECT created_ts, title, market, sources, margin_pct, status, reason, net_pct FROM exec_sim "
        "ORDER BY created_ts DESC LIMIT 40")]
    total, filled, avg = conn.execute(
        "SELECT COUNT(*), SUM(status='filled'), AVG(CASE WHEN status='filled' THEN net_pct END) FROM exec_sim "
        "WHERE status!='pending'").fetchone()
    return {"total": total or 0, "filled": filled or 0,
            "fill_rate": round(100 * (filled or 0) / total, 1) if total else 0.0,
            "avg_net": round(avg, 3) if avg is not None else None, "rows": rows}


AUTO_BOOKS = {"polymarket", "cloudbet", "sxbet"}


def _auto_funnel(conn) -> dict:
    """Todas las surebets automatizables (solo Polymarket / Cloudbet / SX) de 7 días y qué pasó con cada una."""
    since = time.time() - 7 * 86400
    execs = {r["key"]: dict(r) for r in conn.execute("SELECT key, status, reason, net_pct FROM exec_sim")}
    autos = {r[0] for r in conn.execute("SELECT key FROM paper_ops WHERE track='auto'")}
    rows, counts = [], {}
    for r in conn.execute("SELECT key, first_seen, last_seen, title, live, best_margin_pct, legs_json FROM opportunities "
                          "WHERE first_seen > ? ORDER BY first_seen DESC", (since,)):
        srcs = sorted({l.get("source") for l in json.loads(r["legs_json"] or "[]")})
        if len(srcs) < 2 or not set(srcs) <= AUTO_BOOKS:
            continue
        dur = r["last_seen"] - r["first_seen"]
        ex = execs.get(r["key"])
        parts = r["key"].split("#")
        entered = len(parts) > 1 and f"auto#{parts[0]}#{parts[1]}" in autos
        if ex and ex["status"] == "filled":
            stage = ("entró a la cuenta automática" if entered
                     else "llenada, pero no entró (saldo, partido ya tomado o sin forma de liquidar)")
        elif ex:
            stage = "intentada y rechazada: " + (ex["reason"] or "")
        elif r["live"]:
            stage = "en vivo: el bot solo toma prepartido"
        elif dur < 5:
            stage = "duró menos de 5 s"
        elif r["best_margin_pct"] >= 8:
            stage = "margen sospechoso (8 % o más): casi siempre dato malo"
        else:
            stage = "no apta: empieza en más de 6 h, neto bajo o filtro de seguridad"
        key = stage.split(":")[0]
        counts[key] = counts.get(key, 0) + 1
        if len(rows) < 80:
            rows.append({"ts": r["first_seen"], "title": r["title"], "sources": " + ".join(srcs), "live": r["live"],
                         "margin": r["best_margin_pct"], "dur": round(dur), "stage": stage,
                         "net": ex["net_pct"] if ex else None})
    return {"total": sum(counts.values()),
            "counts": sorted(({"stage": k, "n": v} for k, v in counts.items()), key=lambda x: -x["n"]), "rows": rows}


COLOMBIA_BOOKS = ("rushbet.co", "betplay")


def _colombia(conn) -> dict:
    """Surebets y valor de casas con licencia en Colombia (vía OddsPapi) contra Polymarket, 7 días."""
    since = time.time() - 7 * 86400
    like = " OR ".join(f"legs_json LIKE '%\"{b}\"%'" for b in COLOMBIA_BOOKS)
    opps = []
    for r in conn.execute(f"SELECT key, first_seen, last_seen, title, sport, live, best_margin_pct, max_total_stake, legs_json "
                          f"FROM opportunities WHERE first_seen > ? AND ({like}) ORDER BY first_seen DESC LIMIT 150", (since,)):
        legs = json.loads(r["legs_json"] or "[]")
        opps.append({"ts": r["first_seen"], "dur": round(r["last_seen"] - r["first_seen"]), "title": r["title"],
                     "sport": r["sport"], "live": r["live"], "margin": r["best_margin_pct"], "max": r["max_total_stake"],
                     "legs": [{"source": l.get("source"), "label": l.get("label"), "odds": l.get("odds")} for l in legs]})
    vb = [dict(r) for r in conn.execute(
        "SELECT first_seen, title, sport, source, label, odds, fair_prob, ev_pct, clv_pct, start_ts FROM value_bets "
        f"WHERE first_seen > ? AND source IN ({','.join('?' * len(COLOMBIA_BOOKS))}) ORDER BY first_seen DESC LIMIT 150",
        (since, *COLOMBIA_BOOKS))]
    paper = conn.execute(
        f"SELECT COUNT(*), SUM(status='won'), SUM(status='lost'), SUM(CASE WHEN status!='open' THEN pnl END), "
        f"SUM(CASE WHEN status!='open' THEN total_stake END) FROM paper_ops WHERE kind='arb' AND ({like})").fetchone()
    clvs = [v["clv_pct"] for v in vb if v["clv_pct"] is not None]
    return {"books": list(COLOMBIA_BOOKS), "opps": opps, "values": vb,
            "arbs_ok": sum(o["margin"] >= 0.5 and not o["live"] for o in opps),
            "big": sum(o["margin"] >= 5 and not o["live"] for o in opps),
            "paper": {"ops": paper[0] or 0, "won": paper[1] or 0, "lost": paper[2] or 0, "pnl": round(paper[3] or 0, 2),
                      "roi": round(100 * (paper[3] or 0) / paper[4], 2) if paper[4] else None},
            "clv": round(sum(clvs) / len(clvs), 2) if clvs else None, "clv_n": len(clvs)}


def _bucket(v: float, edges: list[float], fmt: str) -> str:
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return fmt.format(lo, hi)
    return fmt.format(edges[-1], "+") if v >= edges[-1] else fmt.format("-", edges[0])


def _value_groups(ops: list[dict]) -> dict:
    """Rendimiento de apuestas de valor por franja de EV, de cuota, casa, deporte y fase (ROI de las cerradas, CLV)."""
    def group(key) -> list[dict]:
        out: dict[str, dict] = {}
        for o in ops:
            k = key(o)
            g = out.setdefault(k, {"name": k, "n": 0, "open": 0, "won": 0, "lost": 0, "staked": 0.0, "pnl": 0.0,
                                   "clv_sum": 0.0, "clv_n": 0, "clv_pos": 0})
            g["n"] += 1
            if o.get("clv") is not None:
                g["clv_sum"] += o["clv"]
                g["clv_n"] += 1
                g["clv_pos"] += o["clv"] > 0
            if o["status"] == "open":
                g["open"] += 1
                continue
            g["won"] += o["status"] == "won"
            g["lost"] += o["status"] == "lost"
            g["staked"] += o["total_stake"]
            g["pnl"] += o["pnl"] or 0
        return [{"name": g["name"], "n": g["n"], "open": g["open"], "won": g["won"], "lost": g["lost"],
                 "staked": round(g["staked"], 2), "pnl": round(g["pnl"], 2),
                 "roi": round(100 * g["pnl"] / g["staked"], 2) if g["staked"] else None,
                 "clv": round(g["clv_sum"] / g["clv_n"], 2) if g["clv_n"] else None,
                 "clv_pos": round(100 * g["clv_pos"] / g["clv_n"]) if g["clv_n"] else None}
                for g in sorted(out.values(), key=lambda g: g["name"])]

    def odds(o):
        return (o["legs"] or [{}])[0].get("odds", 0)

    return {"ev": group(lambda o: _bucket(o["edge_pct"], [0, 2, 3, 4, 5, 7, 10], "EV {}–{} %")),
            "odds": group(lambda o: _bucket(odds(o), [1, 1.6, 2, 2.5, 3, 4, 6], "cuota {}–{}")),
            "house": group(lambda o: (o["legs"] or [{}])[0].get("source", "?")),
            "sport": group(lambda o: o["sport"] or "?"),
            "phase": group(lambda o: "en vivo" if o["live"] else "prepartido")}


def build_state(db_path: Path, status_path: Path) -> dict:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        meta = {r["k"]: r["v"] for r in conn.execute("SELECT k, v FROM paper_meta")}
        ops = [dict(r) for r in conn.execute("SELECT * FROM paper_ops ORDER BY created_ts DESC")]
        skips = conn.execute(
            "SELECT reason, COUNT(*) n FROM paper_skips WHERE ts > ? GROUP BY reason ORDER BY n DESC",
            (time.time() - 7 * 86400,)).fetchall()
    except sqlite3.OperationalError:  # aún no hay tablas (el escáner nunca corrió con papel)
        meta, ops, skips = {}, [], []
    try:
        execsim = _execsim(conn)
    except sqlite3.OperationalError:  # todavía no existe la tabla de simulación
        execsim = None
    try:
        from .makersim import summary as maker_summary
        maker = maker_summary(conn)
    except sqlite3.OperationalError:
        maker = None
    try:
        funnel = _auto_funnel(conn)
    except sqlite3.OperationalError:
        funnel = None
    try:
        from .polybuy import summary as polybuy_summary
        polybuy = polybuy_summary(conn)
    except sqlite3.OperationalError:
        polybuy = None
    from .valuenet import summary as nets_summary
    from .nettrack import summary as netpaper_summary
    nets = nets_summary(conn)
    netpaper = netpaper_summary(conn)
    from .autovivo import summary as live_exec_summary
    from .lprewards import summary as lp_summary
    live_exec = live_exec_summary(conn)
    lp = lp_summary(conn)
    try:
        colombia = _colombia(conn)
    except sqlite3.OperationalError:
        colombia = None
    buttons = {"clicked": 0, "rejected": 0}   # uso de los botones de Telegram (práctica y rechazos de 1xBet)
    for sql, k in (("SELECT COUNT(*) FROM poly_orders WHERE clicked_ts IS NOT NULL", "clicked"),
                   ("SELECT COUNT(*) FROM value_nets WHERE clicked_ts IS NOT NULL", "clicked"),
                   ("SELECT COUNT(*) FROM manual_feedback", "rejected")):
        try:
            buttons[k] += conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            pass
    try:  # CLV de cada apuesta de valor (lo calcula clv.py contra el cierre de Polymarket)
        clv = {r[0]: r[1] for r in conn.execute("SELECT key, clv_pct FROM value_bets WHERE clv_pct IS NOT NULL")}
    except sqlite3.OperationalError:
        clv = {}
    finally:
        conn.close()

    initial = float(meta.get("initial", 100))
    for o in ops:
        o["legs"] = json.loads(o.pop("legs_json") or "[]")
        o.pop("resolve_json", None)
        o["track"] = o.get("track") or "real"
    live = load_live_odds(db_path.parent / "live_odds.json")  # cuota de AHORA de cada pata abierta
    for o in ops:
        if o["status"] == "open":
            for l in o["legs"]:
                now_odds = live.get(leg_ref(l.get("source"), l.get("url", ""), o["market"], l.get("label")))
                if now_odds:
                    l["now_odds"], l["now_ts"] = now_odds
    by_key = {o["key"]: o for o in ops}
    for o in ops:
        if o["kind"] == "value" and o["legs"]:
            leg = o["legs"][0]
            o["clv"] = clv.get(f"{o['event_key']}#{o['market']}#{leg.get('source')}:{''.join(leg.get('covers') or [])}")
    if netpaper:  # cuota de ahora también en los picks de "valor + red" que siguen abiertos
        for r in netpaper["rows"]:
            o = by_key.get(r["key"])
            if o and o["status"] == "open" and o["legs"]:
                r["now_odds"], r["now_ts"] = o["legs"][0].get("now_odds"), o["legs"][0].get("now_ts")
    autovivo_ops = [o for o in ops if o["track"] == "autovivo"]  # automático en vivo simulado: cuenta aparte
    auto_ops = [o for o in ops if o["track"] == "auto"]   # bot automático simulado: cuenta aparte
    valor_ops = [o for o in ops if o["track"] == "valor"]  # cuenta de apuestas de valor "oro"
    value_measured = [o for o in ops if o["kind"] == "value" and o["track"] == "medicion"]
    ops_all = ops
    all_ops = [o for o in ops if o["track"] not in ("auto", "valor", "autovivo")]
    measure = _measure(all_ops)
    ops = [o for o in all_ops if o["track"] == "real"]  # la cuenta de $1000 (lo demás solo se mide)
    settled = [o for o in ops if o["status"] != "open"]
    open_ = [o for o in ops if o["status"] == "open"]
    pnl_total = sum(o["pnl"] or 0 for o in settled)
    in_play = sum(o["total_stake"] for o in open_)
    staked_settled = sum(o["total_stake"] for o in settled)
    won = sum(1 for o in settled if o["status"] == "won")
    lost = sum(1 for o in settled if o["status"] == "lost")

    # Curva de capital: arranca en el saldo inicial y suma cada liquidación en orden
    eq, curve = initial, [[float(meta.get("started", time.time())) * 1000, initial]]
    for o in sorted(settled, key=lambda o: o["settled_ts"] or 0):
        eq += o["pnl"] or 0
        curve.append([(o["settled_ts"] or 0) * 1000, round(eq, 2)])

    # Semana: por día de entrada (hora local)
    today = datetime.now().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    weekly = []
    for d in days:
        day_ops = [o for o in ops if datetime.fromtimestamp(o["created_ts"]).date() == d]
        day_settled = [o for o in settled if o["settled_ts"] and datetime.fromtimestamp(o["settled_ts"]).date() == d]
        weekly.append({
            "day": d.isoformat(), "label": f"{DIAS[d.weekday()]} {d.day:02d}",
            "entries": len(day_ops), "arbs": sum(1 for o in day_ops if o["kind"] == "arb"),
            "values": sum(1 for o in day_ops if o["kind"] == "value"),
            "staked": round(sum(o["total_stake"] for o in day_ops), 2),
            "expected": round(sum(o["expected_profit"] or 0 for o in day_ops), 2),
            "pnl": round(sum(o["pnl"] or 0 for o in day_settled), 2),
            "fees": round(sum(o["fees"] or 0 for o in day_ops), 2),
        })

    def group(key) -> list[dict]:
        out: dict[str, dict] = {}
        for o in ops:
            k = key(o)
            g = out.setdefault(k, {"name": k, "ops": 0, "staked": 0.0, "pnl": 0.0, "open": 0, "expected": 0.0})
            g["ops"] += 1
            g["staked"] += o["total_stake"]
            g["pnl"] += o["pnl"] or 0
            g["expected"] += o["expected_profit"] or 0
            g["open"] += o["status"] == "open"
        return sorted(({**g, "staked": round(g["staked"], 2), "pnl": round(g["pnl"], 2),
                        "expected": round(g["expected"], 2)} for g in out.values()),
                      key=lambda g: -g["ops"])

    status = {}
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass

    return {
        "now": time.time(),
        "account": {
            "initial": initial, "arb_stake": float(meta.get("arb_stake", 100)),
            "equity": round(initial + pnl_total, 2), "in_play": round(in_play, 2),
            "cash": round(initial + pnl_total - in_play, 2), "pnl": round(pnl_total, 2),
            "roi_pct": round(100 * pnl_total / staked_settled, 2) if staked_settled else 0.0,
            "growth_pct": round(100 * pnl_total / initial, 2) if initial else 0.0,
            "ops": len(ops), "open": len(open_), "won": won, "lost": lost,
            "void": sum(1 for o in settled if o["status"] == "void"),
            "fees": round(sum(o["fees"] or 0 for o in ops), 2),
            "expected_open": round(sum(o["expected_profit"] or 0 for o in open_), 2),
        },
        "curve": curve,
        "risk": risk_stats(from_paper_ops(ops), initial),
        "weekly": weekly,
        "by_kind": group(lambda o: "Arbitraje" if o["kind"] == "arb" else "Valor"),
        "by_market": group(lambda o: o["market_name"] or o["market"]),
        "by_sport": group(lambda o: o["sport"]),
        "by_source": group(lambda o: " + ".join(sorted({l["source"] for l in o["legs"]}))),
        "measure": measure,
        "execsim": execsim,
        "maker": maker,
        "auto": _auto_account(auto_ops, float(meta.get("auto_initial", initial)), float(meta.get("started", time.time()))),
        "funnel": funnel,
        "lp": lp,
        "colombia": colombia,
        "autovivo": {**_auto_account(autovivo_ops, float(meta.get("autovivo_initial", 1000)),
                                     float(meta.get("autovivo_started", time.time()))), "exec": live_exec},
        "experiments": experiments.evaluate(ops_all, float(meta.get("started", 0) or 0), live_exec),
        "golive": golive.evaluate(float(meta.get("started", 0) or 0), [o for o in ops_all if o["track"] in ("real", "medicion")],
                                  netpaper, buttons, _is_gold),
        "polybuy": polybuy,
        "nets": nets,
        "netpaper": netpaper,
        "valor": {**_auto_account(valor_ops, float(meta.get("valor_initial", 1000)),
                                  float(meta.get("valor_started", time.time()))),
                  "groups": _value_groups(valor_ops)},
        "value_measure": {"groups": _value_groups(value_measured),
                          "gold": _value_groups([o for o in value_measured if _is_gold(o)]),
                          "ops": value_measured[:200]},
        "ops": all_ops[:200],
        "skips": [{"reason": r["reason"], "n": r["n"]} for r in skips],
        "status": status,
    }


def _is_gold(o: dict) -> bool:
    from .safety import golden_value
    return bool(o["start_ts"] and o["legs"] and golden_value(
        bool(o["live"]) or o["created_ts"] >= o["start_ts"], o["edge_pct"], o["legs"][0]["odds"],
        (o["start_ts"] - o["created_ts"]) / 3600))


def _category(o: dict) -> str:
    if o["kind"] == "arb":
        if o["live"]:
            return "Surebet en vivo"
        if o["track"] == "real":
            return "Surebet prepartido"
        return "Surebet prepartido (lejana o sin saldo, solo medida)"
    return "Valor en vivo" if o["live"] else "Valor prepartido"


def _measure(ops: list[dict]) -> list[dict]:
    """Rendimiento por tipo de jugada, real y medida: para saber dónde está la ventaja."""
    out: dict[str, dict] = {}
    for o in ops:
        g = out.setdefault(_category(o), {"name": _category(o), "real": o["track"] == "real", "ops": 0, "open": 0,
                                          "won": 0, "lost": 0, "void": 0, "staked": 0.0, "pnl": 0.0, "expected": 0.0})
        g["ops"] += 1
        g["expected"] += o["expected_profit"] or 0
        if o["status"] == "open":
            g["open"] += 1
            continue
        g[o["status"]] = g.get(o["status"], 0) + 1
        g["staked"] += o["total_stake"]
        g["pnl"] += o["pnl"] or 0
    # Subconjunto: el valor que pasa el filtro de "oro" (ver surebet/safety.py:golden_value)
    from .safety import golden_value
    gold = [o for o in ops if o["kind"] == "value" and o["start_ts"] and o["legs"] and golden_value(
        bool(o["live"]) or o["created_ts"] >= o["start_ts"], o["edge_pct"], o["legs"][0]["odds"],
        (o["start_ts"] - o["created_ts"]) / 3600)]
    if gold:
        g = {"name": "Valor prepartido FILTRADO (oro)", "real": False, "ops": len(gold), "open": 0, "won": 0,
             "lost": 0, "void": 0, "staked": 0.0, "pnl": 0.0, "expected": sum(o["expected_profit"] or 0 for o in gold)}
        for o in gold:
            if o["status"] == "open":
                g["open"] += 1
            else:
                g[o["status"]] += 1
                g["staked"] += o["total_stake"]
                g["pnl"] += o["pnl"] or 0
        out[g["name"]] = g
    order = ["Surebet prepartido", "Surebet prepartido (lejana o sin saldo, solo medida)", "Surebet en vivo",
             "Valor prepartido", "Valor prepartido FILTRADO (oro)", "Valor en vivo"]
    rows = sorted(out.values(), key=lambda g: order.index(g["name"]) if g["name"] in order else 99)
    return [{**g, "staked": round(g["staked"], 2), "pnl": round(g["pnl"], 2), "expected": round(g["expected"], 2),
             "roi_pct": round(100 * g["pnl"] / g["staked"], 2) if g["staked"] else None} for g in rows]


_CACHE: dict = {"ts": 0.0, "body": None}
_CACHE_LOCK = threading.Lock()
CACHE_S = 10   # varias pestañas o personas mirando: se arma el estado una vez cada 10 s


def _cached_state(db_path: Path, status_path: Path) -> tuple[bytes, bytes]:
    with _CACHE_LOCK:
        if _CACHE["body"] is None or time.time() - _CACHE["ts"] > CACHE_S:
            raw = json.dumps(build_state(db_path, status_path), ensure_ascii=False).encode("utf-8")
            _CACHE["body"], _CACHE["ts"] = (raw, gzip.compress(raw, 6)), time.time()
        return _CACHE["body"]


class _Handler(BaseHTTPRequestHandler):
    db_path: Path
    status_path: Path

    def log_message(self, *args) -> None:  # silencioso: no ensucia la consola del escáner
        pass

    def _send(self, code: int, body: bytes, ctype: str, gzip_: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if gzip_:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/state"):
            try:
                body = _cached_state(self.db_path, self.status_path)
                if "gzip" in (self.headers.get("Accept-Encoding") or ""):
                    self._send(200, body[1], "application/json; charset=utf-8", gzip_=True)   # ~10 veces más liviano
                else:
                    self._send(200, body[0], "application/json; charset=utf-8")
            except Exception as e:  # el panel nunca debe tumbar al escáner
                self._send(500, json.dumps({"error": repr(e)}).encode(), "application/json")
            return
        page = STATIC / "index.html"
        if self.path in ("/", "/index.html") and page.exists():
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"no encontrado", "text/plain")


def serve(db_path: Path, status_path: Path, port: int = 8765, background: bool = True) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"db_path": db_path, "status_path": status_path})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    if background:
        threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    else:
        server.serve_forever()
    return server
