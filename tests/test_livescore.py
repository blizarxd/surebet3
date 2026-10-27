"""Marcador en vivo de las apuestas abiertas: texto por deporte, juegos a consultar y lectura en el dashboard."""

import json
import sqlite3
import time

from surebet.livescore import LiveScores, describe, game_id
from surebet.valordash import summary

PAPER = """CREATE TABLE paper_ops (key TEXT, kind TEXT, track TEXT, created_ts REAL, start_ts REAL, event_key TEXT, market TEXT,
           market_name TEXT, sport TEXT, edge_pct REAL, title TEXT, legs_json TEXT, status TEXT)"""
VB = "CREATE TABLE value_bets (key TEXT PRIMARY KEY, clv_pct REAL, first_seen REAL, last_seen REAL)"
URL = "https://1xbet-col.com/en/line/football/1-liga/383828712-beijing-guoan-jeonbuk"


def test_describe_by_sport():
    soccer = {"SC": {"FS": {"S2": 1}, "CPS": "1st half", "TS": 1949}}
    assert describe("soccer", soccer) == "0-1 · 32'"
    assert describe("soccer", {"SC": {"FS": {}, "CPS": "Half time", "TS": 2700}}) == "0-0 · descanso"
    tennis = {"SC": {"FS": {"S1": 1, "S2": 1}, "CPS": "3rd set", "PS": [{"Key": 1, "Value": {"S1": 3, "S2": 6}},
                                                                     {"Key": 3, "Value": {"S1": 4, "S2": 2}}]}}
    assert describe("tennis", tennis) == "sets 1-1 · 3.º set 4-2"
    cs = {"SC": {"FS": {}, "CPS": "1st map", "PS": [{"Key": 1, "Value": {"S1": 10, "S2": 10}}]}}
    assert describe("esports:cs2", cs) == "mapas 0-0 · mapa 1 10-10"
    assert describe("basketball", {"SC": {"FS": {"S1": 62, "S2": 57}, "CPS": "3rd quarter"}}) == "62-57 · Q3"
    assert describe("soccer", {}) == "" and describe("soccer", {"SC": None}) == ""


def test_bet_symbol():
    from surebet.valordash import symbol
    assert symbol("ou_2.5", ["O"]) == "over" and symbol("ou_3.5", ["U"]) == "under"
    assert symbol("1x2", ["H"]) == "W1" and symbol("ml", ["A"]) == "W2" and symbol("1x2", ["D"]) == "X"
    assert symbol("1x2", ["D", "H"]) == "1X" and symbol("1x2", ["A", "D"]) == "X2" and symbol("1x2", ["A", "H"]) == "12"
    assert symbol("ou_h1_1.5", ["O"]) == "over · 1T" and symbol("1x2_h1", ["H"]) == "W1 · 1T" and symbol("x", []) == ""


def test_game_id_from_url():
    assert game_id(URL) == "383828712" and game_id("https://x/y") is None and game_id("") is None


def _db(tmp_path, start_offset=-3600, status="open"):
    c = sqlite3.connect(tmp_path / "p.db")
    c.execute(PAPER)
    c.execute(VB)
    now = time.time()
    c.execute("INSERT INTO paper_ops VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("valor#1", "value", "valor", now - 7200, now + start_offset, "e1", "1x2", "1X2", "soccer", 3.1, "Beijing vs Jeonbuk",
               json.dumps([{"source": "1xbet", "label": "Beijing", "covers": ["H"], "odds": 2.4, "url": URL}]), status))
    c.commit()
    return c


def test_only_started_open_games_are_polled(tmp_path):
    c = _db(tmp_path)
    ls = LiveScores(str(tmp_path / "p.db"), "https://1xbet-col.com", 285)
    assert ls._open_games(time.time()) == [("383828712", "soccer")]
    c.execute("UPDATE paper_ops SET start_ts = ?", (time.time() + 3600,))     # aún no empieza
    c.commit()
    assert ls._open_games(time.time()) == []
    c.execute("UPDATE paper_ops SET start_ts = ?, status='won'", (time.time() - 3600,))   # ya cerrada
    c.commit()
    assert ls._open_games(time.time()) == []


def test_dashboard_shows_fresh_score_only_on_open(tmp_path):
    c = _db(tmp_path)
    LiveScores(str(tmp_path / "p.db"), "https://1xbet-col.com", 285)        # crea la tabla
    c.execute("INSERT OR REPLACE INTO live_scores VALUES ('383828712', ?, '1-0 · 32''')", (time.time() - 60,))
    c.commit()
    assert summary(c)["picks"][0]["lv"] == "1-0 · 32'"
    c.execute("UPDATE live_scores SET ts = ?", (time.time() - 3600,))         # dato viejo: vuelve a «abierta»
    c.commit()
    assert "lv" not in summary(c)["picks"][0]
