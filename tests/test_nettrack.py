"""Valor + red en papel: con el historial de Polymarket se ve si la red convirtió la apuesta en surebet."""

import asyncio
import json
import time

import httpx

from surebet.nettrack import NetTracker, kelly_stake, limit_for, net_label, summary
from surebet.paper import PaperBook, PaperConfig


def add_value(pb, key, odds=2.0, ev=3.0, status="open", hours=3, created_ago=7200):
    now = time.time()
    created = now - created_ago
    start = created + hours * 3600
    pb.conn.execute(
        "INSERT INTO paper_ops (key, kind, created_ts, start_ts, title, sport, market, market_name, live, edge_pct, "
        "total_stake, legs_json, resolve_json, status, track) VALUES (?, 'value', ?, ?, 'A vs B', 'soccer', 'ml', 'Ganador', "
        "0, ?, 100, ?, ?, ?, 'medicion')",
        (key, created, start, ev, json.dumps([{"source": "1xbet", "label": "A", "covers": ["H"], "stake": 100, "odds": odds}]),
         json.dumps({"H": ["c", "tH"], "A": ["c", "tA"]}), status))
    pb.conn.commit()
    return created, start


def run(tr, prices):
    """prices: token -> lista de (segundos desde la entrada, precio)."""
    def handler(request):
        tok, a = request.url.params["market"], int(request.url.params["startTs"])
        return httpx.Response(200, json={"history": [{"t": a + s, "p": p} for s, p in prices[tok]]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await tr.update(c)
    return asyncio.run(go())


def test_helpers():
    assert limit_for(2.0) == 0.49                                   # 1/1.01 − 1/2 = 0.490
    assert net_label("ml", ["H"], "A", "A vs B") == "B"
    assert net_label("ou_2.5", ["O"], "Más 2.5", "x") == "Menos 2.5"
    assert 0 < kelly_stake(2.0, 3.0, 1000) <= 30


def test_locks_when_opposite_side_gets_cheap(tmp_path):
    pb = PaperBook(tmp_path / "p.db", PaperConfig())
    add_value(pb, "v1", hours=1.5, created_ago=7200)                 # el partido ya empezó: se decide entero
    tr = NetTracker(tmp_path / "p.db")
    tr.activated = time.time()
    # A sube de 0.50 a 0.52 → "no A" baja a 0.48: cruza la red de 0.49 con margen
    run(tr, {"tH": [(60, 0.50), (120, 0.52)], "tA": [(60, 0.50), (120, 0.48)]})
    st, = tr.conn.execute("SELECT status FROM net_picks").fetchone()
    assert st == "asegurada"
    tr._settle(); tr.conn.commit()
    r = summary(tr.conn)
    assert r["all"]["locked"] == 1 and r["all"]["pnl"] > 0 and r["backtest"]["picks"] == 1


def test_kept_as_value_then_uses_real_result(tmp_path):
    pb = PaperBook(tmp_path / "p.db", PaperConfig())
    add_value(pb, "v2", status="lost", hours=1.5, created_ago=7200)
    tr = NetTracker(tmp_path / "p.db")
    run(tr, {"tH": [(60, 0.50), (120, 0.49)], "tA": [(60, 0.50), (120, 0.51)]})
    st, res, pnl = tr.conn.execute("SELECT status, result, pnl FROM net_picks").fetchone()
    assert st == "quedó como valor" and res == "perdida" and pnl < 0


def test_levels_in_cents(tmp_path):
    pb = PaperBook(tmp_path / "p.db", PaperConfig())
    add_value(pb, "v3", status="won", hours=1.5, created_ago=7200)
    tr = NetTracker(tmp_path / "p.db")
    # lado contrario: 0.50 al entrar → baja a 0.475: llena la red a 2¢ (0.48), no la de 3¢ (0.47)
    run(tr, {"tH": [(60, 0.50), (120, 0.525)], "tA": [(60, 0.50), (120, 0.475)]})
    lv = json.loads(tr.conn.execute("SELECT levels FROM net_picks").fetchone()[0])
    assert lv["2"]["ts"] and not lv["3"]["ts"] and lv["2"]["limit"] == 0.48
    rows = {l["name"]: l for l in summary(tr.conn)["by_level"]}
    assert rows["Red a 2¢ bajo la entrada"]["fills"] == 1 and rows["Red a 5¢ bajo la entrada"]["fills"] == 0


def test_no_history_is_retried_not_counted(tmp_path):
    pb = PaperBook(tmp_path / "p.db", PaperConfig())
    add_value(pb, "v4", hours=1.5, created_ago=7200)
    tr = NetTracker(tmp_path / "p.db")
    run(tr, {"tH": [], "tA": []})
    st, tries = tr.conn.execute("SELECT status, tries FROM net_picks").fetchone()
    assert st == "valor" and tries == 1                       # no se asume "quedó como valor"
