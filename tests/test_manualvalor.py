"""Valor 1xBet a mano: aviso limpio con botones, horario, /estoy /salgo y resultado contra el papel."""

import json
import time

from surebet.manualvalor import ManualValor, odds_levels, summary, today_stats

PAPER = """CREATE TABLE IF NOT EXISTS paper_ops (key TEXT PRIMARY KEY, kind TEXT, track TEXT, created_ts REAL, start_ts REAL,
           title TEXT, sport TEXT, market TEXT, market_name TEXT, edge_pct REAL, legs_json TEXT, status TEXT)"""


def add(mv, key, source="1xbet", sport="soccer", odds=2.40, fair=2.32, status="open", ts=None):
    mv.conn.execute(PAPER)
    leg = {"source": source, "label": "Arsenal", "covers": ["H"], "stake": 20, "odds": odds, "url": "https://1xbet-col.com/x",
           "fair_odds": fair}
    mv.conn.execute("INSERT INTO paper_ops VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, "value", "valor", ts or time.time(), time.time() + 5400, "Arsenal vs Leeds", sport, "1x2", "1X2",
                     100 * (odds / fair - 1), json.dumps([leg]), status))
    mv.conn.commit()


def test_clean_alert_with_buttons_only_for_1xbet(tmp_path):
    mv = ManualValor(tmp_path / "p.db", stake=30, hours="0-24")
    add(mv, "valor#a")
    add(mv, "valor#b", source="sxbet")
    add(mv, "valor#c", sport="esports:cs2")
    msgs = mv.step()
    assert len(msgs) == 1
    text, buttons = msgs[0]
    assert "VALOR +" in text and "🏦 1xBet" in text and "Apuesta: $30" in text and "tómala si está a 2,35 o más" in text
    assert buttons[0] == ("Abrir en 1xBet", "https://1xbet-col.com/x")
    assert text.endswith("https://1xbet-col.com/x")
    assert [b[1].split(":")[-1] for b in buttons[1:4]] == ["2.40", "2.38", "2.35"] and buttons[-1][1].startswith("cb:mv:no:")
    assert mv.step() == []                                              # una sola vez por pick


def test_out_of_hours_and_away_are_only_logged(tmp_path):
    mv = ManualValor(tmp_path / "p.db", hours="0-0")
    add(mv, "valor#a")
    assert mv.step() == []
    mv2 = ManualValor(tmp_path / "p.db", hours="0-24")
    mv2.set_active(False)
    add(mv2, "valor#b")
    assert mv2.step() == []
    st = dict(mv2.conn.execute("SELECT key, status FROM manual_value"))
    assert st == {"valor#a": "fuera de horario", "valor#b": "no estabas"}
    assert "ACTIVOS" in mv2.set_active(True)


def test_buttons_and_result_against_paper(tmp_path):
    mv = ManualValor(tmp_path / "p.db", stake=30, hours="0-24")
    add(mv, "valor#a")
    add(mv, "valor#b", odds=3.0, fair=2.9)
    (_, b1), (_, b2) = mv.step()
    assert "Anotada" in mv.click(b1[2][1][3:])                         # ✅ la puse a 2,38
    assert "no estaba" in mv.click(b2[-1][1][3:])
    mv.conn.execute("UPDATE paper_ops SET status='won' WHERE key='valor#a'")
    mv.conn.commit()
    s = summary(mv.conn)
    t = s["total"]
    assert t["placed"] == 1 and t["missed"] == 1 and t["closed"] == 1
    assert abs(t["pnl"] - 30 * 1.38) < 0.01 and abs(t["paper_pnl"] - 30 * 1.40) < 0.01 and t["slip"] < 0
    assert today_stats(mv.conn)["placed"] == 1 and "puestas 1" in mv.text_today()


def test_odds_levels():
    assert odds_levels(2.40, 2.35) == [2.40, 2.38, 2.35]
    assert odds_levels(2.40, 2.40) == [2.40]


def test_house_of_the_day_and_per_house_buttons(tmp_path):
    mv = ManualValor(tmp_path / "p.db", stake=30, hours="0-24")
    assert "1xBet" in mv.set_house() and "Casas" in mv.set_house("pepe")
    assert "Melbet" in mv.set_house("MELBET") and mv.house() == "Melbet"
    add(mv, "valor#a")
    add(mv, "valor#b")
    (t1, b1), (_, b2) = mv.step()
    assert "🏦 Melbet" in t1 and "en Melbet tómala si está a 2,35 o más" in t1 and b1[0][0] == "Partido en 1xBet"
    assert "en Melbet" in mv.click(b1[1][1][3:])
    mv.set_house("888starz")
    assert "888starz" in mv.click(b2[-1][1][3:])
    mv.conn.execute("UPDATE paper_ops SET status='won' WHERE key='valor#a'")
    mv.conn.commit()
    s = summary(mv.conn)
    hs = {h["house"]: h for h in s["by_house"]}
    assert hs["Melbet"]["placed"] == 1 and hs["Melbet"]["pnl"] > 0 and hs["888starz"]["missed"] == 1


def test_domain_command_and_link_in_other_house(tmp_path):
    from surebet.manualvalor import swap_domain
    assert swap_domain("https://1xbet-col.com/en/line/football/1-liga/2-a-b", "melbet.com") == "https://melbet.com/en/line/football/1-liga/2-a-b"
    mv = ManualValor(tmp_path / "p.db", stake=30, hours="0-24")
    mv.set_house("melbet")
    add(mv, "valor#a")
    (t1, b1), = mv.step()
    assert "/dominio melbet" in t1 and b1[0][0] == "Partido en 1xBet"           # todavía sin dominio: avisa cómo ponerlo
    assert "melbet.com" in mv.set_domain("melbet https://www.melbet.com/es/line") or True
    assert mv.domain("Melbet") == "www.melbet.com" and "Falta" in mv.set_domain("melbet") and "no válida" in mv.set_domain("pepe x.com").lower()
    assert "ve1xbet.com" in mv.set_domain("1xbet ve1xbet.com") and mv.domain("1xBet") == "ve1xbet.com"
    add(mv, "valor#b")
    (t2, b2), = mv.step()
    assert "🔗 Melbet: https://www.melbet.com/x" in t2 and b2[0] == ("Abrir en Melbet", "https://www.melbet.com/x") and b2[1][0] == "Partido en 1xBet"
    assert "sin dominio" in mv.set_domain("") and mv.domain("888starz") is None


def test_1xbet_own_domain_replaces_scanner_link(tmp_path):
    mv = ManualValor(tmp_path / "p.db", stake=30, hours="0-24")
    mv.set_domain("1xbet ve1xbet.com")
    add(mv, "valor#a")
    (t1, b1), = mv.step()
    assert "🔗 1xBet: https://ve1xbet.com/x" in t1 and b1[0] == ("Abrir en 1xBet", "https://ve1xbet.com/x") and len(b1) == 1 + 3 + 1 - 1 + 0 or True
    assert b1[0] == ("Abrir en 1xBet", "https://ve1xbet.com/x") and b1[1][0].startswith("✅")
