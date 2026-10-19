"""Pestaña Azuro: valor a $30 por pick, día por día, con y sin red, y CLV por filtro."""

import json
import time

from surebet.azuro_panel import summary
from surebet.nettrack import NetTracker
from surebet.paper import PaperBook, PaperConfig
from surebet.store import OpportunityLog


def add(pb, key, ts, odds, status, sport="soccer", source="azuro", ev=3.0):
    legs = [{"source": source, "label": "A", "covers": ["H"], "stake": 14, "odds": odds}]
    pb.conn.execute("INSERT INTO paper_ops (key, kind, created_ts, start_ts, event_key, title, sport, market, market_name, "
                    "live, edge_pct, total_stake, legs_json, resolve_json, status, pnl, track) VALUES "
                    "(?, 'value', ?, ?, ?, 'A vs B', ?, '1x2', '1X2', 0, ?, 14, ?, '{}', ?, 0, 'valor')",
                    (key, ts, ts + 3600, f"polymarket:{key}", sport, ev, json.dumps(legs), status))


def test_azuro_tab_at_30(tmp_path):
    db = tmp_path / "p.db"
    pb = PaperBook(db, PaperConfig())
    OpportunityLog(db)
    NetTracker(db)
    t = time.time() - 2 * 86400
    add(pb, "e1", t, 2.0, "won")
    add(pb, "e2", t + 60, 3.0, "lost")
    add(pb, "e3", t + 120, 2.5, "lost", sport="esports:cs2")     # solo observación
    add(pb, "e4", t + 180, 2.2, "won", source="1xbet")           # otra casa: no cuenta
    pb.conn.execute("INSERT INTO value_bets (key, first_seen, last_seen, source, ev_pct, odds, start_ts, sport, clv_pct) "
                    "VALUES ('polymarket:e1#1x2#azuro:H', ?, ?, 'azuro', 3, 2.0, ?, 'soccer', 4.0)", (t, t, t + 3600))
    pb.conn.commit()
    s = summary(pb.conn)
    assert s["picks"] == 3 and s["bot_picks"] == 2
    assert s["pnl"] == 30.0 - 30.0 and s["won"] == 1 and s["lost"] == 1
    assert s["exp"] == 1.2                                         # $30 × 4 % de CLV
    assert s["days_bot"][0]["n"] == 2 and s["days_all"][0]["n"] == 3
    assert s["rows"][0]["bot"] is False and s["limits"]["ev"][0]["n"] == 1
