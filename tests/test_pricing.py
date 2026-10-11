import pytest

from surebet.models import BookLevel, Quote
from surebet.pricing import (book_capacity, effective_odds, fill_budget, odds_after_commission,
                             poly_fee_per_share, poly_odds)


def test_fee_matches_polymarket_docs_table():
    # Tabla oficial deportes (100 shares): 0.50 -> $1.25 ; 0.30 -> $1.05 ; 0.90 -> $0.45
    assert 100 * poly_fee_per_share(0.50, 0.05) == pytest.approx(1.25)
    assert 100 * poly_fee_per_share(0.30, 0.05) == pytest.approx(1.05)
    assert 100 * poly_fee_per_share(0.90, 0.05) == pytest.approx(0.45)


def test_poly_odds_include_fee():
    assert poly_odds(0.50, 0.0) == pytest.approx(2.0)
    assert poly_odds(0.50, 0.05) == pytest.approx(1 / 0.5125)  # 1.951, no 2.00


def test_fill_budget_walks_levels():
    asks = (BookLevel(0.40, 100), BookLevel(0.50, 100))
    shares, spent = fill_budget(asks, 60, fee_rate=0.0)
    # 100 shares a 0.40 = $40, luego $20 a 0.50 = 40 shares
    assert shares == pytest.approx(140)
    assert spent == pytest.approx(60)


def test_effective_odds_worsens_with_size_and_none_without_depth():
    q = Quote(source="polymarket", covers=frozenset({"H"}), odds=2.5, label="x",
              asks=(BookLevel(0.40, 100), BookLevel(0.50, 100)))
    assert effective_odds(q, 10) == pytest.approx(2.5)
    assert effective_odds(q, 60) == pytest.approx(140 / 60)
    assert effective_odds(q, book_capacity(q.asks, 0.0) + 1) is None


def test_bookie_quote_respects_max_stake():
    q = Quote(source="casa", covers=frozenset({"A"}), odds=2.1, label="y", max_stake=50)
    assert effective_odds(q, 50) == 2.1
    assert effective_odds(q, 51) is None


def test_exchange_commission_on_profit():
    assert odds_after_commission(3.0, 0.05) == pytest.approx(2.9)
