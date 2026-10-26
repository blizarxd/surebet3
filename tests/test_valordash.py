"""Pestaña Dashboard valor manual: apuestas de valor de 1xBet a $30 fijos, por día (hora de Colombia), con link y tus botones."""

import json
import sqlite3
import time

from surebet.valordash import summary

PAPER = """CREATE TABLE paper_ops (key TEXT, kind TEXT, track TEXT, created_ts REAL, start_ts REAL, event_key TEXT, market TEXT,
           market_name TEXT, sport TEXT, edge_pct REAL, title TEXT, legs_json TEXT, status TEXT)"""
VB = "CREATE TABLE value_bets (key TEXT PRIMARY KEY, clv_pct REAL, first_seen REAL, last_seen REAL)"
MV = "CREATE TABLE manual_value (key TEXT, status TEXT, placed_odds REAL, min_odds REAL, house TEXT)"


def test_picks_at_fixed_30(tmp_path):
    c = sqlite3.connect(tmp_path / "p.db")
    for q in (PAPER, VB, MV):
        c.execute(q)
    now = time.time()

    def add(i, source, odds, status, clv=None):
        c.execute("INSERT INTO paper_ops VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"valor#{i}", "value", "valor", now + i, now + 3600, f"e{i}", "1x2", "1X2", "soccer", 3.1, f"Partido {i}",
                   json.dumps([{"source": source, "label": "Arsenal", "covers": ["H"], "odds": odds, "stake": 99,
                                "url": "https://1xbet-col.com/x"}]), status))
        if clv is not None:
            c.execute("INSERT INTO value_bets VALUES (?,?,?,?)", (f"e{i}#1x2#{source}:H", clv, now + i - 60, now + i + 1380))
    add(1, "1xbet", 2.5, "won", 4.0)
    add(2, "1xbet", 2.0, "lost", 2.0)
    add(3, "sxbet", 2.0, "won")                  # otra casa: fuera
    add(4, "1xbet", 3.0, "open")
    add(5, "onexbet", 1.9, "void")
    c.execute("INSERT INTO manual_value VALUES ('valor#1', 'puesta', 2.4, 2.35, 'Melbet')")
    c.commit()
    s = summary(c)
    P = s["picks"]
    assert [p["p"] for p in P] == [45.0, -30.0, None, 0.0]              # $30 fijos, sin importar el monto del papel
    assert [p["s"] for p in P] == ["g", "p", "o", "a"]
    assert abs(P[0]["e"] - 1.2) < 1e-9 and P[0]["mk"] == "1X2" and P[0]["sp"] == "soccer"   # esperado = 30 × CLV 4 %
    assert P[0]["u"] == "https://1xbet-col.com/x" and P[0]["y"] == "puesta" and P[0]["po"] == 2.4 and P[1]["y"] is None and P[0]["h"] == "Melbet" and P[1]["h"] is None
    assert P[0]["ls"] - P[0]["fs"] == 1440 and P[1]["fs"] is not None and P[2]["fs"] is None   # duración de la señal (24 min)
    assert P[0]["tr"] == "oro" and P[0]["lt"] == 4 and P[0]["n"] == 1                           # 4 señales en pocos segundos = lote; partidos distintos
    assert summary(sqlite3.connect(tmp_path / "vacia.db")) is None


def test_batch_and_nth_pick_of_the_game(tmp_path):
    c = sqlite3.connect(tmp_path / "p.db")
    for q in (PAPER, VB, MV):
        c.execute(q)
    now = time.time()
    for i, (ek, dt) in enumerate((("g1", 0), ("g2", 1), ("g3", 2), ("g1", 3000))):   # 3 juntas (lote) y un 2.º pick del partido g1 mucho después
        c.execute("INSERT INTO paper_ops VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"valor#{i}", "value", "valor", now + dt, now + 9000, ek, "1x2", "1X2", "soccer", 3.1, "P",
                   json.dumps([{"source": "1xbet", "label": "A", "covers": ["H"], "odds": 2.0, "url": "u"}]), "open"))
    c.commit()
    P = summary(c)["picks"]
    assert [p["lt"] for p in P] == [3, 3, 3, 0] and [p["n"] for p in P] == [1, 1, 1, 2]


def test_amplio_track_is_included_and_tagged(tmp_path):
    c = sqlite3.connect(tmp_path / "p.db")
    for q in (PAPER, VB, MV):
        c.execute(q)
    now = time.time()
    for i, tr in enumerate(("valor", "amplio")):
        c.execute("INSERT INTO paper_ops VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"{tr}#{i}", "value", tr, now + i * 500, now + 9000, f"e{i}", "1x2", "1X2", "soccer", 1.8, "P",
                   json.dumps([{"source": "1xbet", "label": "A", "covers": ["H"], "odds": 2.0, "url": "u"}]), "open"))
    c.commit()
    assert [p["tr"] for p in summary(c)["picks"]] == ["oro", "amplio"]
