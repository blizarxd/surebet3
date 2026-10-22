"""Pestaña Dashboard valor manual: apuestas de valor de 1xBet a $30 fijos, por día (hora de Colombia)."""

import json
import sqlite3
import time

from surebet.valordash import summary

PAPER = """CREATE TABLE paper_ops (key TEXT, kind TEXT, track TEXT, created_ts REAL, event_key TEXT, market TEXT,
           title TEXT, legs_json TEXT, status TEXT)"""
VB = "CREATE TABLE value_bets (key TEXT PRIMARY KEY, clv_pct REAL)"


def test_picks_at_fixed_30(tmp_path):
    c = sqlite3.connect(tmp_path / "p.db")
    c.execute(PAPER)
    c.execute(VB)
    now = time.time()

    def add(i, source, odds, status, clv=None):
        c.execute("INSERT INTO paper_ops VALUES (?,?,?,?,?,?,?,?,?)",
                  (f"valor#{i}", "value", "valor", now + i, f"e{i}", "1x2", f"Partido {i}",
                   json.dumps([{"source": source, "label": "Arsenal", "covers": ["H"], "odds": odds, "stake": 99}]), status))
        if clv is not None:
            c.execute("INSERT INTO value_bets VALUES (?,?)", (f"e{i}#1x2#{source}:H", clv))
    add(1, "1xbet", 2.5, "won", 4.0)
    add(2, "1xbet", 2.0, "lost", 2.0)
    add(3, "sxbet", 2.0, "won")                  # otra casa: fuera
    add(4, "1xbet", 3.0, "open")
    add(5, "onexbet", 1.9, "void")
    c.commit()
    s = summary(c)
    assert [p[2] for p in s["picks"]] == [45.0, -30.0, None, 0.0]      # $30 fijos, sin importar el monto del papel
    assert [p[7] for p in s["picks"]] == ["g", "p", "o", "a"]
    assert abs(s["picks"][0][3] - 1.2) < 1e-9                           # esperado = 30 × CLV 4 %
    assert summary(sqlite3.connect(tmp_path / "vacia.db")) is None
