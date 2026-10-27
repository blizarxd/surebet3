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
    assert ls._games(time.time()) == [("383828712", "soccer")]
    c.execute("UPDATE paper_ops SET start_ts = ?", (time.time() + 3600,))     # aún no empieza
    c.commit()
    assert ls._games(time.time()) == []
    c.execute("UPDATE paper_ops SET start_ts = ?, status='won'", (time.time() - 3600,))   # ya cerrada
    c.commit()
    assert ls._games(time.time()) == []


def test_dashboard_shows_fresh_score_only_on_open(tmp_path):
    c = _db(tmp_path)
    LiveScores(str(tmp_path / "p.db"), "https://1xbet-col.com", 285)        # crea la tabla
    c.execute("INSERT OR REPLACE INTO live_scores VALUES ('383828712', ?, '1-0 · 32''', 0)", (time.time() - 60,))
    c.commit()
    assert summary(c)["picks"][0]["lv"] == "1-0 · 32'"
    c.execute("UPDATE live_scores SET ts = ?", (time.time() - 3600,))         # dato viejo: vuelve a «abierta»
    c.commit()
    assert "lv" not in summary(c)["picks"][0]


def test_final_score_for_closed_picks(tmp_path):
    c = _db(tmp_path, status="won")
    ls = LiveScores(str(tmp_path / "p.db"), "https://1xbet-col.com", 285)
    c.execute("INSERT INTO live_scores VALUES ('383828712', ?, '0-1 · 88''', 0)", (time.time() - 300,))
    c.commit()
    assert ls._games(time.time()) == [("383828712", "soccer")]            # cerrada pero aún no se vio terminar: se consulta
    assert summary(c)["picks"][0]["fin"] == "últ. 0-1 · 88'"
    c.execute("UPDATE live_scores SET ended = 1")
    c.commit()
    assert ls._games(time.time()) == []                                    # ya terminó: no se consulta más
    P = summary(c)["picks"][0]
    assert P["fin"] == "0-1" and "lv" not in P


def test_open_but_finished_shows_final(tmp_path):
    c = _db(tmp_path)                                       # abierta (aún sin liquidar)
    LiveScores(str(tmp_path / "p.db"), "https://1xbet-col.com", 285)
    c.execute("INSERT INTO live_scores VALUES ('383828712', ?, '2-1 · 90''', 1)", (time.time() - 60,))
    c.commit()
    P = summary(c)["picks"][0]
    assert P["fin"] == "2-1" and "lv" not in P


def test_brake_pauses_polling_when_1xbet_fails(tmp_path, monkeypatch):
    import asyncio
    import surebet.livescore as L
    monkeypatch.setattr(L, "BAD_LIMIT", 1)            # el test tiene un solo partido: una falla ya activa el freno

    class Boom:
        calls = 0

        async def get(self, *a, **k):
            Boom.calls += 1
            raise __import__("httpx").ConnectError("down")

    _db(tmp_path)
    ls = LiveScores(str(tmp_path / "p.db"), "https://1xbet-col.com", 285, guard=lambda: True)
    asyncio.run(ls.update(Boom()))
    assert Boom.calls == 0 and "pausa" in ls.status                        # guard activo: ni una llamada
    ls.guard = None
    asyncio.run(ls.update(Boom()))
    assert Boom.calls >= 1 and ls._hold_until > time.time()               # fallas propias: se frena 10 min
    n = Boom.calls
    asyncio.run(ls.update(Boom()))
    assert Boom.calls == n


def test_game_finished_period_counts_as_ended():
    from surebet.livescore import is_finished
    v = {"SC": {"FS": {"S1": 90, "S2": 84}, "CPS": "game finished"}}
    assert is_finished(v) and describe("basketball", v) == "90-84 · terminado"
    assert not is_finished({"SC": {"FS": {}, "CPS": "3rd quarter"}})


def test_late_game_is_polled_faster():
    from surebet.livescore import is_late, final_score
    assert is_late("soccer", {"SC": {"TS": 80 * 60}}) and not is_late("soccer", {"SC": {"TS": 40 * 60}})
    assert is_late("basketball", {"SC": {"CPS": "4th quarter"}}) and not is_late("tennis", {"SC": {"CPS": "3rd set"}})
    assert final_score("sets 2-1 · 3.º set 6-4") == "sets 2-1" and final_score("1-0 · 88'") == "1-0"
