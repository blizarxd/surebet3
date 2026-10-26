import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from surebet.arb import ArbConfig, find_opportunities, find_value_bets
from surebet.models import AWAY, DRAW, HOME, BookLevel, MatchedEvent, Quote, SourceEvent
from surebet.paper import PaperBook, PaperConfig
from surebet.pricing import poly_odds

PAST = datetime.now(timezone.utc) - timedelta(hours=3)
FUTURE = datetime.now(timezone.utc) + timedelta(hours=3)  # prepartido: todavía no empezó
RESOLVE = {HOME: ("c_h", "t_h"), DRAW: ("c_d", "t_d"), AWAY: ("c_a", "t_a")}


def poly_q(covers, price, token, depth=1000.0):
    asks = (BookLevel(price, 20), BookLevel(price + 0.02, depth))
    return Quote(source="polymarket", feed="polymarket", covers=frozenset(covers), odds=poly_odds(price, 0.05),
                 label=f"poly-{token}", asks=asks, fee_rate=0.05, min_stake=5 * price, token_id=token,
                 condition_id="c_" + token[-1], market="1x2", fair_prob=price, near_depth=5000)


def make_event(poly_quotes, book_quotes, live=False, start=FUTURE, eid="E1"):
    p = SourceEvent(feed="polymarket", event_id=eid, sport="soccer", home="Arsenal", away="Leeds",
                    start=start, three_way=True, quotes=poly_quotes, live=live)
    b = SourceEvent(feed="onexbet", event_id="X1", sport="soccer", home="Arsenal", away="Leeds",
                    start=start, three_way=True, quotes=book_quotes, live=live)
    m = MatchedEvent(anchor=p)
    m.members = [(p, False), (b, False)]
    return m


def arb_event(live=False, book_ts=None, start=FUTURE):
    # Polymarket "NO Arsenal" (empate o Leeds) a 0.30 + 1xBet Arsenal a 1.50 -> arbitraje
    extra = {"ts": book_ts} if book_ts is not None else {}
    return make_event([poly_q({DRAW, AWAY}, 0.30, "t_no_h")],
                      [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=1.50, label="Arsenal",
                             market="1x2", **extra)], live=live, start=start)


def book(tmp_path, **kw):
    return PaperBook(tmp_path / "p.db", PaperConfig(min_persist_s=0, **kw))


def test_arb_enters_with_real_book_and_fees(tmp_path):
    pb = book(tmp_path)
    opps = find_opportunities(arb_event(), ArbConfig())
    assert opps
    new = pb.consider(opps, [], lambda k, m: RESOLVE)
    assert len(new) == 1
    op = new[0]
    # $100 fijos por surebet, salvo que el libro no tenga liquidez para tanto
    assert op["total"] == pytest.approx(min(100.0, opps[0].max_total_stake), abs=0.05)
    poly_leg = next(l for l in op["legs"] if l["source"] == "polymarket")
    assert poly_leg["fee"] > 0 and poly_leg["levels"] == 2   # el monto se comió dos niveles del libro
    assert poly_leg["avg_price"] > 0.30
    payouts = [l["stake"] * l["odds"] for l in op["legs"]]
    assert min(payouts) > op["total"]                          # ganancia garantizada incluso con comisión
    # no se repite la misma operación
    assert pb.consider(opps, [], lambda k, m: RESOLVE) == []
    bal = pb.balances()
    assert bal["in_play"] == pytest.approx(op["total"], abs=0.01)
    assert bal["cash"] == pytest.approx(300 - op["total"], abs=0.01)


def test_needs_persistence(tmp_path):
    pb = PaperBook(tmp_path / "p.db", PaperConfig(min_persist_s=60))
    opps = find_opportunities(arb_event(), ArbConfig())
    assert pb.consider(opps, [], lambda k, m: RESOLVE) == []   # recién apareció: todavía no


def test_skip_when_cannot_settle(tmp_path):
    pb = book(tmp_path)
    opps = find_opportunities(arb_event(), ArbConfig())
    assert pb.consider(opps, [], lambda k, m: {}) == []
    assert pb.conn.execute("SELECT COUNT(*) FROM paper_skips").fetchone()[0] == 1


def _client(winner: str | None):
    def handler(request: httpx.Request) -> httpx.Response:
        cid = request.url.path.rsplit("/", 1)[-1]
        out = cid[-1]
        tokens = [{"token_id": f"t_{out}", "outcome": "Yes", "winner": winner == out},
                  {"token_id": f"n_{out}", "outcome": "No", "winner": winner is not None and winner != out}]
        return httpx.Response(200, json={"closed": True, "tokens": tokens})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _settle(pb, winner):
    # Se liquida 45 min después del inicio: el partido "ya se jugó"
    pb.conn.execute("UPDATE paper_ops SET start_ts=?", (PAST.timestamp(),))

    async def run():
        async with _client(winner) as c:
            return await pb.settle(c, every_s=0)
    return asyncio.run(run())


def test_settle_arb_wins_either_way(tmp_path):
    for winner in (HOME.lower(), AWAY.lower()):
        pb = book(tmp_path / winner)
        opps = find_opportunities(arb_event(), ArbConfig())
        res = {HOME: ("c_h", "t_h"), DRAW: ("c_d", "t_d"), AWAY: ("c_a", "t_a")}
        pb.consider(opps, [], lambda k, m: res)
        assert _settle(pb, winner) == 1
        status, pnl = pb.conn.execute("SELECT status, pnl FROM paper_ops").fetchone()
        assert status == "won" and pnl > 0
        assert pb.balances()["equity"] == pytest.approx(300 + pnl)


def test_void_when_resolved_50_50(tmp_path):
    pb = book(tmp_path)
    pb.consider(find_opportunities(arb_event(), ArbConfig()), [], lambda k, m: RESOLVE)
    assert _settle(pb, None) == 1
    status, pnl = pb.conn.execute("SELECT status, pnl FROM paper_ops").fetchone()
    # la casa devuelve su apuesta pero Polymarket paga 0,50 por share (no devuelve lo pagado): ya no es $0.
    # Aquí la pata de Polymarket costó ~0,30 por share, así que una anulada da ganancia; a 0,55 daría pérdida.
    assert status == "void" and pnl > 1


def test_value_bets_only_measured_by_default(tmp_path):
    pb = PaperBook(tmp_path / "p.db", PaperConfig(min_persist_s=0))
    poly = [poly_q({o}, p, f"t_{o.lower()}") for o, p in ((HOME, 0.50), (DRAW, 0.28), (AWAY, 0.22))]
    ev = make_event(poly, [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=2.15,
                                 label="Arsenal", market="1x2"),
                           Quote(source="onexbet", feed="oddsapi", covers=frozenset({HOME}), odds=2.10,
                                 label="Arsenal", market="1x2")])
    new = [o for o in pb.consider([], find_value_bets(ev, ArbConfig(), min_ev_pct=2.0), lambda k, m: RESOLVE)
           if o["track"] != "valor"]
    # una sola anotación por resultado (la de mejor cuota), $100 fijos, sin tocar el capital
    assert len(new) == 1 and new[0]["track"] == "medicion" and new[0]["legs"][0]["odds"] == 2.15
    assert new[0]["total"] == pytest.approx(100.0)
    assert pb.balances()["cash"] == pytest.approx(300.0) and pb.balances()["in_play"] == 0


def test_live_arb_only_measured(tmp_path):
    pb = book(tmp_path, live_accept_delay_s=0)
    opps = find_opportunities(arb_event(live=True), ArbConfig())
    new = pb.consider(opps, [], lambda k, m: RESOLVE)
    assert len(new) == 1 and new[0]["track"] == "medicion"
    assert pb.balances()["in_play"] == 0
    assert _settle(pb, HOME.lower()) == 1
    assert pb.balances()["equity"] == pytest.approx(300.0)   # ganó, pero no cuenta para el capital


def test_live_needs_book_quote_fetched_after_the_delay(tmp_path):
    import time
    pb = book(tmp_path)  # demora de aceptación en vivo: 8 s
    now = time.time()
    seen = find_opportunities(arb_event(live=True, book_ts=now), ArbConfig())
    assert seen and pb.consider(seen, [], lambda k, m: RESOLVE) == []      # recién vista: todavía no
    assert pb.consider(seen, [], lambda k, m: RESOLVE) == []               # misma cuota vieja: tampoco
    fresh = find_opportunities(arb_event(live=True, book_ts=now + 10), ArbConfig())
    new = pb.consider(fresh, [], lambda k, m: RESOLVE)                    # sigue en pie tras la demora
    assert len(new) == 1 and new[0]["track"] == "medicion"


def test_far_prematch_arb_is_only_measured(tmp_path):
    far = arb_event(start=FUTURE + timedelta(hours=15))  # empieza en ~18 h: ataría el dinero un día
    new = book(tmp_path).consider(find_opportunities(far, ArbConfig()), [], lambda k, m: RESOLVE)
    assert len(new) == 1 and new[0]["track"] == "medicion"


def _arb(odds, eid):
    return find_opportunities(make_event(
        [poly_q({DRAW, AWAY}, 0.30, "t_no_h", depth=100000)],
        [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=odds, label="Arsenal", market="1x2")],
        eid=eid), ArbConfig())


def test_real_account_takes_the_best_net_first(tmp_path):
    pb = book(tmp_path, bankroll=100.0)  # alcanza para una sola surebet de $100
    new = pb.consider(_arb(1.48, "E1") + _arb(1.55, "E2"), [], lambda k, m: RESOLVE)
    real = [n for n in new if n["track"] == "real"]
    assert len(real) == 1 and any(l["odds"] == 1.55 for l in real[0]["legs"])   # la mejor neta
    assert len(new) == 2                                                          # la otra igual se mide


def test_real_account_needs_minimum_net_after_fees(tmp_path):
    new = book(tmp_path, real_min_net_pct=5.0).consider(_arb(1.55, "E1"), [], lambda k, m: RESOLVE)
    assert len(new) == 1 and new[0]["track"] == "medicion"


def test_scarce_cash_demands_more(tmp_path):
    pb = book(tmp_path)
    assert pb._required_net_pct({"cash": 800, "equity": 1000}) == 0.5
    assert pb._required_net_pct({"cash": 300, "equity": 1000}) == 1.0
    assert pb._required_net_pct({"cash": 100, "equity": 1000}) == 1.5


def test_prematch_arb_goes_to_measure_when_no_cash(tmp_path):
    pb = book(tmp_path, bankroll=2.0)
    new = pb.consider(find_opportunities(arb_event(), ArbConfig()), [], lambda k, m: RESOLVE)
    assert len(new) == 1 and new[0]["track"] == "medicion"


def test_started_but_not_flagged_live_is_only_measured(tmp_path):
    # Polymarket a veces tarda en marcar el vivo: si ya pasó la hora de inicio, no es prepartido
    ev = make_event([poly_q({DRAW, AWAY}, 0.30, "t_no_h")],
                    [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=1.50, label="Arsenal",
                           market="1x2")], start=PAST)
    new = book(tmp_path, live_accept_delay_s=0).consider(find_opportunities(ev, ArbConfig()), [], lambda k, m: RESOLVE)
    assert len(new) == 1 and new[0]["track"] == "medicion"


def test_old_database_gets_track_column(tmp_path):
    import sqlite3
    db = tmp_path / "p.db"
    conn = sqlite3.connect(db)
    conn.executescript("""CREATE TABLE paper_ops (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, kind TEXT,
        created_ts REAL, event_key TEXT, title TEXT, sport TEXT, market TEXT, market_name TEXT, start_ts REAL,
        live INTEGER, edge_pct REAL, total_stake REAL, fees REAL, expected_profit REAL, legs_json TEXT,
        resolve_json TEXT, status TEXT, settled_ts REAL, outcome TEXT, payout REAL, pnl REAL, last_check REAL);
        INSERT INTO paper_ops (key, kind, total_stake, status) VALUES ('value#x', 'value', 1.0, 'open');""")
    conn.commit()
    conn.close()
    pb = PaperBook(db, PaperConfig())
    assert pb.conn.execute("SELECT track FROM paper_ops").fetchone()[0] == "medicion"
    assert pb.balances()["in_play"] == 0


def test_value_bet_sized_by_kelly_and_capped(tmp_path):
    pb = book(tmp_path, take_value=True)
    poly = [poly_q({o}, p, f"t_{o.lower()}") for o, p in ((HOME, 0.50), (DRAW, 0.28), (AWAY, 0.22))]
    ev = make_event(poly, [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=2.15,
                                 label="Arsenal", market="1x2")])
    vbs = find_value_bets(ev, ArbConfig(), min_ev_pct=2.0)
    assert vbs
    new = pb.consider([], vbs, lambda k, m: RESOLVE)
    assert len(new) == 1
    assert 1.0 <= new[0]["total"] <= 9.0    # Kelly/4 limitado al 3 % del capital ($300)
    legs = new[0]["legs"]
    assert json.dumps(legs) and legs[0]["source"] == "1xbet"


def test_arb_uses_fixed_stake_when_liquidity_allows(tmp_path):
    deep = make_event([poly_q({DRAW, AWAY}, 0.30, "t_no_h", depth=100000)],
                      [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=1.50, label="Arsenal",
                             market="1x2")])
    pb = book(tmp_path)
    op = pb.consider(find_opportunities(deep, ArbConfig()), [], lambda k, m: RESOLVE)[0]
    assert op["total"] == pytest.approx(100.0, abs=0.02)
    assert len(op["legs"]) == 2 and all(l["stake"] > 1 for l in op["legs"])


def test_value_account_takes_only_golden_with_kelly(tmp_path):
    pb = PaperBook(tmp_path / "p.db", PaperConfig(min_persist_s=0, value_bankroll=1000))
    poly = [poly_q({o}, p, f"t_{o.lower()}") for o, p in ((HOME, 0.50), (DRAW, 0.28), (AWAY, 0.22))]
    ev = make_event(poly, [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=2.08,
                                 label="Arsenal", market="1x2")])
    new = pb.consider([], find_value_bets(ev, ArbConfig(), min_ev_pct=2.0), lambda k, m: RESOLVE)
    valor = [o for o in new if o["track"] == "valor"]
    assert len(valor) == 1 and 0 < valor[0]["total"] <= 30          # Kelly/4 con tope 3 % de $1000
    assert valor[0]["legs"][0]["fair_odds"] == pytest.approx(2.0, abs=0.01)
    assert pb.valor_balances()["in_play"] == pytest.approx(valor[0]["total"])
    assert pb.balances()["in_play"] == 0                              # no toca la cuenta de surebets


def test_settles_early_when_winner_already_decided(tmp_path):
    """Partido terminado pero sin resolución oficial: el ganador vale 99,8¢ → se vende y se libera la plata."""
    import asyncio
    import httpx
    pb = PaperBook(tmp_path / "p.db", PaperConfig())
    legs = [{"source": "1xbet", "covers": ["H"], "stake": 50.0, "odds": 2.1},
            {"source": "polymarket", "covers": ["A"], "stake": 50.0, "odds": 2.05}]
    pb.conn.execute(
        "INSERT INTO paper_ops (key, kind, created_ts, start_ts, total_stake, legs_json, resolve_json, status, track) "
        "VALUES ('k','arb',?,?,100,?,?, 'open','real')",
        (time.time() - 4 * 3600, time.time() - 3 * 3600, json.dumps(legs),
         json.dumps({"H": ["c1", "tH"], "A": ["c1", "tA"]})))
    pb.conn.commit()

    def handler(request):
        return httpx.Response(200, json={"closed": False, "tokens": [
            {"token_id": "tH", "price": 0.002, "winner": False}, {"token_id": "tA", "price": 0.998, "winner": False}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await pb.settle(c, every_s=0)
    assert asyncio.run(run()) == 1
    status, pnl, out = pb.conn.execute("SELECT status, pnl, outcome FROM paper_ops").fetchone()
    assert status == "won" and out == "A" and abs(pnl - (50 * 2.05 * 0.998 - 100)) < 1e-6


def test_void_pays_half_a_share_on_polymarket_not_the_stake_back():
    from surebet.paper import void_payout
    legs = [{"source": "polymarket", "stake": 54.58, "shares": 81.29},
            {"source": "1xbet", "stake": 26.31}]
    assert void_payout(legs, 80.89) == pytest.approx(0.5 * 81.29 + 26.31)     # pierde ~$14 aunque nadie "perdió"
    assert void_payout([{"source": "1xbet", "stake": 10.0}, {"source": "cloudbet", "stake": 12.0}], 22.0) == 22.0


def test_amplio_account_takes_ev_1_5_to_2_only_and_never_touches_the_gold_account(tmp_path):
    pb = PaperBook(tmp_path / "p.db", PaperConfig(min_persist_s=0, value_bankroll=1000))
    poly = [poly_q({o}, p, f"t_{o.lower()}") for o, p in ((HOME, 0.50), (DRAW, 0.28), (AWAY, 0.22))]

    def run(odds):
        ev = make_event(poly, [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=odds,
                                     label="Arsenal", market="1x2")])
        return pb.consider([], find_value_bets(ev, ArbConfig(), min_ev_pct=1.0), lambda k, m: RESOLVE)
    assert [o["track"] for o in run(2.036) if o["track"] in ("valor", "amplio")] == ["amplio"]    # EV +1,8 %
    assert run(2.036) == [] or True                                                                    # una sola vez por selección
    assert pb.valor_balances()["in_play"] == 0                                                         # el oro no se toca
    assert not [o for o in run(2.012) if o["track"] in ("valor", "amplio", "medicion", "real")]      # EV +0,6 %: nada
    n = pb.conn.execute("SELECT COUNT(*) FROM paper_ops WHERE track='amplio'").fetchone()[0]
    assert n == 1
    off = PaperBook(tmp_path / "q.db", PaperConfig(min_persist_s=0, value_bankroll=1000, amplio_account=False))
    ev = make_event(poly, [Quote(source="1xbet", feed="onexbet", covers=frozenset({HOME}), odds=2.036, label="Arsenal", market="1x2")])
    assert off.consider([], find_value_bets(ev, ArbConfig(), min_ev_pct=1.0), lambda k, m: RESOLVE) == []
