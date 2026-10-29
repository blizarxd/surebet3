"""Marcar ✅ / ❌ desde el panel web: clave puesta solo desde Telegram, y las marcas quedan igual que las de los botones."""

import json
import sqlite3
import threading
import urllib.error
import urllib.request

from surebet.dashboard_server import _Handler
from surebet.manualvalor import SCHEMA, check_panel_key, mark_signal, set_panel_key
from http.server import ThreadingHTTPServer


def _db(tmp_path):
    c = sqlite3.connect(tmp_path / "p.db")
    c.executescript(SCHEMA)
    c.execute("INSERT INTO manual_value (id, key, status, odds, min_odds, title, label, stake) VALUES (7, 'valor#1', 'fuera de horario', 2.4, 2.3, 'A vs B', 'A', 30)")
    c.commit()
    return c


def test_key_and_mark(tmp_path):
    c = _db(tmp_path)
    assert not check_panel_key(c, "abcd")                                  # sin clave creada: nada pasa
    assert "al menos 4" in set_panel_key(c, "abc")
    assert "guardada" in set_panel_key(c, "mi clave")
    assert check_panel_key(c, "mi clave") and not check_panel_key(c, "otra") and not check_panel_key(c, "")
    r = mark_signal(c, 7, "ok", 2.35, "melbet")                            # también las «fuera de horario»
    assert r == {"ok": True, "id": 7, "status": "puesta", "placed_odds": 2.35, "house": "Melbet", "prev": "fuera de horario"}
    assert c.execute("SELECT status, placed_odds, house FROM manual_value WHERE id=7").fetchone() == ("puesta", 2.35, "Melbet")
    assert mark_signal(c, 7, "no", None, "x")["status"] == "no estaba"
    assert c.execute("SELECT placed_odds FROM manual_value WHERE id=7").fetchone()[0] is None
    assert not mark_signal(c, 7, "ok", 0.5)["ok"] and not mark_signal(c, 99, "no")["ok"] and not mark_signal(c, 7, "zzz")["ok"]
    assert "borrada" in set_panel_key(c, "") and not check_panel_key(c, "mi clave")


def test_http_post_needs_key(tmp_path):
    c = _db(tmp_path)
    set_panel_key(c, "secreto")
    handler = type("H", (_Handler,), {"db_path": tmp_path / "p.db", "status_path": tmp_path / "s.json"})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/api/mark"

    def post(body, key):
        req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Key": key})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert post({"id": 7, "action": "no"}, "mala")[0] == 403
        assert post({"id": 7, "action": "no"}, "")[0] == 403
        code, out = post({"id": 7, "action": "ok", "odds": 2.4, "house": "1xBet"}, "secreto")
        assert code == 200 and out["status"] == "puesta"
        assert post({"id": 7, "action": "ok", "odds": 0.9}, "secreto")[0] == 400
        assert sqlite3.connect(tmp_path / "p.db").execute("SELECT status FROM manual_value WHERE id=7").fetchone()[0] == "puesta"
    finally:
        srv.shutdown()


def test_mark_by_key_creates_row_and_hours_command(tmp_path):
    import json
    import time
    from surebet.manualvalor import ManualValor
    mv = ManualValor(tmp_path / "q.db", hours="8-20")
    mv.conn.execute("""CREATE TABLE IF NOT EXISTS paper_ops (key TEXT PRIMARY KEY, kind TEXT, track TEXT, created_ts REAL, start_ts REAL,
               title TEXT, sport TEXT, market TEXT, market_name TEXT, edge_pct REAL, legs_json TEXT, status TEXT, event_key TEXT)""")
    leg = {"source": "1xbet", "label": "Astralis", "covers": ["H"], "odds": 2.39, "fair_odds": 2.30, "url": "https://1xbet-col.com/x"}
    mv.conn.execute("INSERT INTO paper_ops VALUES ('valor#cs', 'value', 'valor', ?, ?, 'Astralis vs fnatic', 'esports:cs2', '1x2', 'Ganador', 3.8, ?, 'open', 'e1')",
                    (time.time(), time.time() + 3600, json.dumps([leg])))
    mv.conn.commit()
    r = mark_signal(mv.conn, None, "ok", 2.39, "1xBet", "valor#cs")           # CS2: nunca se avisó, no tenía fila
    assert r["ok"] and r["id"] and r["status"] == "puesta"
    assert mv.conn.execute("SELECT status, placed_odds, min_odds FROM manual_value WHERE key='valor#cs'").fetchone() == ("puesta", 2.39, 2.33)
    assert not mark_signal(mv.conn, None, "no", None, None, "valor#nope")["ok"]
    assert "12–20" in mv.set_hours("12-20") and (mv.start_h, mv.end_h) == (12, 20)
    assert "Uso" in mv.set_hours("abc") and "no válidas" in mv.set_hours("20-12")
    mv2 = ManualValor(tmp_path / "q.db", hours="8-20")                         # el horario queda guardado
    assert (mv2.start_h, mv2.end_h) == (12, 20)


def test_web_text_matches_telegram_notice(tmp_path):
    c = _db(tmp_path)
    from surebet.manualvalor import ManualValor
    mv = ManualValor(tmp_path / "p.db")
    r = mark_signal(mv.conn, 7, "ok", 2.35, "1xBet")
    assert r["prev"] == "fuera de horario"
    txt = mv.web_text(7, r["prev"])
    assert "Anotada desde el panel en 1xBet" in txt and "2,35" in txt and "A vs B" in txt and "corregida" not in txt
    r = mark_signal(mv.conn, 7, "ok", 2.30, "1xBet")                      # editar la cuota
    assert r["prev"] == "puesta" and "(corregida)" in mv.web_text(7, r["prev"])
    r = mark_signal(mv.conn, 7, "no", None, "1xBet")                      # «en verdad no la tomé»
    assert "no la tomaste" in mv.web_text(7, r["prev"]) and "(corregida)" in mv.web_text(7, r["prev"])


def test_wrong_pick_is_recorded_with_own_result(tmp_path):
    c = _db(tmp_path)
    from surebet.manualvalor import ManualValor
    mv = ManualValor(tmp_path / "p.db")
    r = mark_signal(mv.conn, 7, "wrong", 1.98, "1xBet", None, "W1 · 1T", 30, "perdida")
    assert r["ok"] and r["status"] == "mal jugada" and r["wp"] == "W1 · 1T" and r["ws"] == 30 and r["wr"] == "perdida"
    assert mv.conn.execute("SELECT status, placed_odds, wrong_pick, wrong_stake, wrong_result FROM manual_value WHERE id=7").fetchone() == \
        ("mal jugada", 1.98, "W1 · 1T", 30, "perdida")
    txt = mv.web_text(7, r["prev"])
    assert "jugaste W1 · 1T a 1,98" in txt and "la señal era A" in txt and "❌ perdida" in txt
    assert not mark_signal(mv.conn, 7, "wrong", 1.98, "1xBet", None, "", 30)["ok"]                 # sin elegir qué jugó
    assert not mark_signal(mv.conn, 7, "wrong", 1.98, "1xBet", None, "X", 30, "rara")["ok"]
    assert not mark_signal(mv.conn, 7, "wrong", 0.9, "1xBet", None, "X", 30)["ok"]
    mark_signal(mv.conn, 7, "ok", 2.4, "1xBet")                                                    # volver a «la puse» borra lo mal jugado
    assert mv.conn.execute("SELECT status, wrong_pick FROM manual_value WHERE id=7").fetchone() == ("puesta", None)


def test_real_stake_is_saved_per_mark_and_monto_command(tmp_path):
    from surebet.manualvalor import ManualValor, get_real_stake
    mv = ManualValor(tmp_path / "r.db")
    mv.conn.execute("INSERT INTO manual_value (id, key, status, odds, min_odds, title, label, stake) VALUES (1, 'a', 'avisada', 2.0, 1.9, 'A vs B', 'A', 30)")
    mv.conn.execute("INSERT INTO manual_value (id, key, status, odds, min_odds, title, label, stake) VALUES (2, 'b', 'avisada', 2.0, 1.9, 'C vs D', 'C', 30)")
    mv.conn.commit()
    assert get_real_stake(mv.conn) == 5.0 and "$5" in mv.set_real_stake([])
    mark_signal(mv.conn, 1, "ok", 2.0)
    assert "$5 → $10" in mv.set_real_stake(["10"]) and get_real_stake(mv.conn) == 10.0
    mark_signal(mv.conn, 2, "ok", 2.0)                                      # lo nuevo guarda el monto nuevo; lo viejo no cambia
    assert [r[0] for r in mv.conn.execute("SELECT real_stake FROM manual_value ORDER BY id")] == [5.0, 10.0]
    assert "Uso" in mv.set_real_stake(["abc"]) and get_real_stake(mv.conn) == 10.0
