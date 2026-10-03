"""Same-currency transfers conserve NAV and the realized/unrealized FX bridge."""
from datetime import date

import pytest

from portfolio_app.services.ledger import _replay_settled_monetary_postings


def replay(source, target, amount, *, missing_day=None):
    def posting(key, account, day, value, kind, group=None):
        return dict(transaction_id=key, account_id=account, currency="EUR",
                    trade_date=day, effective_date=day, cash_amount_delta=value,
                    source_transaction_type=kind, transfer_group_id=group)
    rows = [posting("source", "cash", "2026-01-01", source, "deposit"),
            posting("target", "loan", "2026-01-02", target, "deposit"),
            posting("out", "cash", "2026-01-03", -amount, "transfer_out", "pair"),
            posting("in", "loan", "2026-01-03", amount, "transfer_in", "pair")]
    def fx(*, as_of_date, **_kwargs):
        return None if as_of_date.day == missing_day else {"rate": {1: .9, 2: 1.2, 3: 1}[as_of_date.day], "stale": False}
    arguments = dict(as_of_date=date(2026, 1, 3), base_currency="USD", direct_fx_instruments={},
                     instrument_detail_cache={}, resolve_fx_rate_on=fx, impact_transaction_ids={"out", "in"})
    before, _ = _replay_settled_monetary_postings(postings=rows[:2], **arguments)
    after, impacts = _replay_settled_monetary_postings(postings=rows, **arguments)
    return before, after, impacts


@pytest.mark.parametrize("source,target,amount,source_basis,target_basis,realized", [
    (100, 0, 40, 54, 36, 0),             # move a monetary asset
    (-20, -50, 30, -54, -24, 0),        # move historical debt, no netting
    (100, -50, 25, 67.5, -30, 7.5),     # partial repayment
    (100, -50, 50, 45, 0, 15),          # exact debt retirement
    (100, -50, 150, -50, 95, 15),       # both balances cross zero
    (20, -50, 30, -12, -24, 6),         # source crosses into debt
    (20, -50, 70, -56, 20, 6),          # transfer debt then newly borrowed cash
    (0, -50, 70, -80, 20, 0),           # debt relocation and new borrowing
])
def test_transfer_preserves_value_and_fx_bridge(source, target, amount, source_basis, target_basis, realized):
    before, after, impacts = replay(source, target, amount)
    assert after[("cash", "EUR")]["amount"] == source - amount
    assert after[("loan", "EUR")]["amount"] == target + amount
    assert after[("cash", "EUR")]["historical_cost_basis_base"] == pytest.approx(source_basis)
    assert after[("loan", "EUR")]["historical_cost_basis_base"] == pytest.approx(target_basis)
    assert sum(row["realized_cash_fx_pnl_base"] for row in impacts) == pytest.approx(realized)
    nav_before = sum(row["amount"] for row in before.values())
    nav_after = sum(row["amount"] for row in after.values())
    assert nav_after == pytest.approx(nav_before)
    unrealized_before = nav_before - sum(row["historical_cost_basis_base"] for row in before.values())
    unrealized_after = nav_after - sum(row["historical_cost_basis_base"] for row in after.values())
    assert unrealized_after + realized == pytest.approx(unrealized_before)
    if realized:
        assert len(impacts) == 1
        assert impacts[0]["transaction_id"] == "in"
        netted = min(max(source, 0), max(-target, 0), amount)
        assert impacts[0]["local_exposure_released"] == netted
        assert impacts[0]["source_cost_basis_base"] == pytest.approx(netted * .9)
        assert impacts[0]["liability_cost_basis_base"] == pytest.approx(netted * 1.2)
        assert impacts[0]["fair_value_base"] == 0  # Equal asset/debt fair values cancel.
        assert impacts[0]["liability_cost_basis_base"] - impacts[0]["source_cost_basis_base"] == pytest.approx(realized)


@pytest.mark.parametrize("missing_day", [1, 2])
def test_netting_with_missing_historical_basis_is_unavailable(missing_day):
    _, _, impacts = replay(100, -50, 50, missing_day=missing_day)
    assert len(impacts) == 1
    assert impacts[0]["realized_cash_fx_pnl_base"] is None
    assert impacts[0]["fx_coverage_status"] == "unavailable"


def test_netting_does_not_need_current_fx_because_equal_exposures_cancel():
    _, after, impacts = replay(100, -50, 50, missing_day=3)
    assert after[("cash", "EUR")]["historical_cost_basis_base"] == 45
    assert impacts[0]["realized_cash_fx_pnl_base"] == pytest.approx(15)


def test_original_financing_repayment_keeps_ten_dollars_in_fx_bridge():
    rows = []
    for key, account, day, amount, kind, group in (
        ("deposit", "cash", 1, 100, "deposit", None),
        ("borrow-out", "loan", 2, -50, "transfer_out", "borrow"),
        ("borrow-in", "cash", 2, 50, "transfer_in", "borrow"),
        ("repay-out", "cash", 3, -50, "transfer_out", "repay"),
        ("repay-in", "loan", 3, 50, "transfer_in", "repay"),
    ):
        rows.append(dict(transaction_id=key, account_id=account, currency="EUR",
            trade_date=f"2026-01-0{day}", effective_date=f"2026-01-0{day}",
            cash_amount_delta=amount, source_transaction_type=kind, transfer_group_id=group))
    states, impacts = _replay_settled_monetary_postings(postings=rows, as_of_date=date(2026, 1, 3),
        base_currency="USD", direct_fx_instruments={}, instrument_detail_cache={},
        resolve_fx_rate_on=lambda **kw: {"rate": {1: .9, 2: 1.2, 3: 1}[kw["as_of_date"].day], "stale": False},
        impact_transaction_ids={"repay-out", "repay-in"})
    assert sum(state["amount"] for state in states.values()) == 100
    assert states[("loan", "EUR")]["amount"] == 0
    assert states[("cash", "EUR")]["historical_cost_basis_base"] == 100
    assert sum(row["realized_cash_fx_pnl_base"] for row in impacts) == 10
