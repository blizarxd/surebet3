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
    assert r == {"ok": True, "status": "puesta", "placed_odds": 2.35, "house": "Melbet"}
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
