"""Semáforo de riesgo: colores por caída, CLV y APIs; pausa en rojo; CS2/Dota2 solo en observación."""

import time
from types import SimpleNamespace

from surebet.arb import ArbConfig, find_opportunities
from surebet.semaforo import API_DOWN_S, API_OK_S, Semaforo, classify, clv_days, drawdown
from test_paper import RESOLVE, arb_event, book


def settle(pb, track, pnls, t0=None):
    t0 = t0 or time.time() - 86400
    for i, pnl in enumerate(pnls):
        pb.conn.execute("INSERT INTO paper_ops (key, kind, created_ts, total_stake, status, settled_ts, pnl, track) "
                        "VALUES (?, 'arb', ?, 100, ?, ?, ?, ?)",
                        (f"{track}{i}{t0}", t0 + i, "won" if pnl > 0 else "lost", t0 + i + 1, pnl, track))
    pb.conn.commit()


def src(name, status):
    return SimpleNamespace(name=name, status=status)


def test_classify_statuses():
    assert classify("1105 partidos (45 en vivo), libros pedidos para 426") == "ok"
    assert classify("error: ConnectError()") == "down"
    assert classify("HTTP 500: boom") == "down"
    assert classify("1xBet no responde (¿IP bloqueada temporalmente?): en pausa 10 min") == "blocked"
    assert classify("Cloudbet rechazó la clave (HTTP 401)") == "blocked"
    assert classify("cupo del día usado (8/10); sigue mañana") == "off"
    assert classify("sin ODDS_API_KEY (desactivada)") == "off"
    assert classify("error en papel (maker): ValueError()") == "skip"   # falla nuestra, no de Polymarket


def test_drawdown_restarts_from_resume():
    closes = [(1, 100), (2, -300), (3, 50)]                  # 1000 → 1100 → 800 → 850
    dd, peak, eq = drawdown(closes, 1000)
    assert peak == 1100 and eq == 850 and round(dd, 1) == 22.7
    dd2, peak2, _ = drawdown(closes, 1000, since=2.5)         # reanudado con 800: pico nuevo 850
    assert peak2 == 850 and dd2 == 0


def test_clv_three_negative_days_in_a_row():
    day = 86400
    t = time.time() - 4 * day
    rows = ([(t, 2.0)] * 5 + [(t + day, -1.0)] * 5 + [(t + 2 * day, -0.5)] * 5 + [(t + 3 * day, -2.0)] * 5
            + [(t + 3 * day, 1.0)] * 2)                      # el último día sigue negativo en promedio
    week, streak, days = clv_days(rows)
    assert streak == 3 and len(days) == 4 and week is not None


def test_red_drawdown_pauses_account_until_resume(tmp_path):
    pb = book(tmp_path, bankroll=1000)
    settle(pb, "real", [+100, -350])                         # 1100 → 750: caída 31,8 %
    sem = Semaforo(tmp_path / "p.db")
    msgs = sem.refresh()
    real = next(l for l in sem.state["lights"] if l["id"] == "real")
    assert real["color"] == "red" and real["paused"]
    assert any("ROJO" in m and "retira" in m for m in msgs)
    assert sem.blocks("real", {"1xbet", "polymarket"})
    assert sem.blocks("auto", {"sxbet", "polymarket"}) is None   # las otras cuentas siguen
    # El papel manda a medición lo que iba a la real (se sigue midiendo, no toca el saldo)
    pb.gate = sem
    new = pb.consider(find_opportunities(arb_event(), ArbConfig()), [], lambda k, m: RESOLVE)
    assert new and new[0]["track"] == "medicion"
    assert "Reanudado" in sem.resume()
    sem.refresh()
    real = next(l for l in sem.state["lights"] if l["id"] == "real")
    assert real["color"] == "green" and not sem.blocks("real", {"1xbet"})
    assert Semaforo(tmp_path / "p.db").resets.get("real")    # sobrevive a un reinicio


def test_yellow_between_10_and_20_without_pause(tmp_path):
    pb = book(tmp_path, bankroll=1000)
    settle(pb, "auto", [-150])                               # auto: inicial 1000 (auto_initial) → 850
    sem = Semaforo(tmp_path / "p.db")
    sem.refresh()
    auto = next(l for l in sem.state["lights"] if l["id"] == "auto")
    assert auto["color"] == "yellow" and not auto["paused"] and sem.blocks("auto", {"sxbet"}) is None


def test_api_down_five_minutes_pauses_its_source_then_recovers(tmp_path):
    book(tmp_path)
    sem = Semaforo(tmp_path / "p.db")
    t = time.time()
    sem.step([src("sxbet", "error: ReadTimeout()")], now=t)
    assert sem.blocks("auto", {"sxbet"}) is None                       # un error suelto no pausa
    msgs = sem.step([src("sxbet", "error: ReadTimeout()")], now=t + API_DOWN_S + 1)
    assert sem.blocks("auto", {"sxbet", "polymarket"}) and sem.blocks("real", {"1xbet"}) is None
    assert any("APIs" in m and "ROJO" in m for m in msgs)
    sem.step([src("sxbet", "335 mercados")], now=t + API_DOWN_S + 60)
    assert sem.blocks("auto", {"sxbet"})                               # tiene que responder 10 min seguidos
    sem.step([src("sxbet", "335 mercados")], now=t + API_DOWN_S + 61 + API_OK_S)
    assert sem.blocks("auto", {"sxbet"}) is None


def test_polymarket_down_pauses_everything(tmp_path):
    book(tmp_path)
    sem = Semaforo(tmp_path / "p.db")
    t = time.time()
    for dt in (0, API_DOWN_S + 1):
        sem.step([src("polymarket", "error en ciclo: boom")], now=t + dt)
    assert sem.blocks("real", {"1xbet"}) and sem.blocks("valor", {"sxbet"})


def test_manual_block_of_1xbet(tmp_path):
    book(tmp_path)
    sem = Semaforo(tmp_path / "p.db")
    assert "bloqueada" in sem.block("1xBet")
    assert sem.blocks("real", {"1xbet", "polymarket"}) and sem.blocks("auto", {"sxbet"}) is None
    sem.refresh()
    assert next(l for l in sem.state["lights"] if l["id"] == "apis")["color"] == "red"
    sem.resume()
    assert sem.blocks("real", {"1xbet"}) is None


def test_cs2_dota2_only_observation(tmp_path):
    pb = book(tmp_path)
    ev = arb_event()
    for m, _ in ev.members:
        m.sport = "esports:cs2"
    opps = find_opportunities(ev, ArbConfig())
    assert opps and not pb.eligible_auto(opps[0])
    new = pb.consider(opps, [], lambda k, m: RESOLVE)
    assert new and new[0]["track"] == "medicion"
    soccer = find_opportunities(arb_event(), ArbConfig())
    assert pb.eligible_auto(soccer[0])
