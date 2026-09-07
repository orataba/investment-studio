from datetime import date
from copy import deepcopy
import json

import pytest
from sqlalchemy import event, select

from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioDailySnapshotModel
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import daily_snapshots, performance, portfolio_store
from portfolio_app.services.period_calculation_state import PeriodCalculationInputs, load_period_calculation_inputs


@pytest.mark.parametrize("start", [None, date(2026, 4, 8), date(2026, 4, 12)])
def test_published_calculation_inputs_match_explicit_offline_calculation(start, monkeypatch):
    portfolio_id = "investment-studio"
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(portfolio_id)
    portfolio = portfolio_store.get_portfolio(portfolio_id)
    accounts = portfolio_store.list_accounts(portfolio_id)
    transactions = portfolio_store.list_transactions(portfolio_id)
    end = date(2026, 4, 15)
    snapshots = daily_snapshots.list_materialized_daily_snapshots(portfolio_id)
    contribution = daily_snapshots.build_materialized_contribution_report(portfolio_id, start_date=start, end_date=end)
    kwargs = dict(start_date=start, end_date=end, prebuilt_snapshots=snapshots, prebuilt_instrument_contribution_report=contribution)
    expected = performance.build_period_calculation_report(portfolio, accounts, transactions, **kwargs)
    def reject_replay(*_args, **_kwargs):
        raise AssertionError("Published period inputs must not replay history or load market series")

    for name in ("build_position_lots", "get_shared_fx_rates", "get_registry_instrument_detail", "get_registry_instrument_details", "build_daily_portfolio_snapshots"):
        monkeypatch.setattr(performance, name, reject_replay)
    inputs = load_period_calculation_inputs(portfolio_id, start_date=start, end_date=end, prebuilt_snapshots=snapshots)
    actual = performance.build_period_calculation_report(portfolio, accounts, transactions, calculation_inputs=inputs, **kwargs)
    assert actual == expected


def test_empty_period_preserves_the_existing_full_portfolio_risk_universe(monkeypatch):
    portfolio_id = "investment-studio"
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(portfolio_id)
    portfolio = portfolio_store.get_portfolio(portfolio_id)
    accounts = portfolio_store.list_accounts(portfolio_id)
    transactions = portfolio_store.list_transactions(portfolio_id)
    expected = performance._instrument_ids_for_calculation_risk_basis(
        portfolio, accounts, transactions, start_date=None, end_date=date.today(),
    )
    monkeypatch.setattr(performance, "build_position_lots", lambda *_args, **_kwargs: pytest.fail("Empty period risk must not replay lots"))
    inputs = load_period_calculation_inputs(portfolio_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 2))
    assert inputs.effective_start_date is inputs.effective_end_date is None
    assert inputs.risk_instrument_ids == expected


@pytest.mark.parametrize("suffix", ["calculation", "calculation/groups?axis=instrument", "calculation/groups?axis=currency"])
def test_calculation_get_uses_published_inputs_without_history_or_kernel(raw_client, monkeypatch, suffix):
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously("investment-studio")
    def reject(*_args, **_kwargs):
        pytest.fail("A current calculation GET must read published inputs, not replay history")
    for name in ("build_position_lots", "get_shared_fx_rates", "get_registry_instrument_detail", "get_registry_instrument_details", "build_daily_portfolio_snapshots"):
        monkeypatch.setattr(performance, name, reject)
    response = raw_client.get(f"/api/portfolios/investment-studio/performance/{suffix}")
    assert response.status_code == 200, response.text


def test_missing_period_state_queues_once_without_synchronous_fallback(raw_client, monkeypatch):
    portfolio_id = "investment-studio"
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(portfolio_id)
    with get_session_factory()() as session:
        row = session.scalar(select(PortfolioDailySnapshotModel).where(
            PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
        ).order_by(PortfolioDailySnapshotModel.as_of_date))
        row.calculation_state_json = None
        session.commit()
    monkeypatch.setattr(performance, "build_daily_portfolio_snapshots", lambda *_args, **_kwargs: pytest.fail("Missing inputs must be rebuilt by the worker"))
    request_ids = []
    for _ in range(2):
        response = raw_client.get(f"/api/portfolios/{portfolio_id}/performance/calculation")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "portfolio_calculation_pending"
        with get_session_factory()() as session:
            state = session.get(PortfolioCalculationStateModel, portfolio_id)
            assert state.daily_snapshot_status == "stale"
            request_ids.append(state.refresh_request_id)
    assert request_ids[0] == request_ids[1]


def test_normal_snapshot_reads_defer_private_calculation_state():
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously("investment-studio")
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(get_engine(), "before_cursor_execute", record)
    try:
        with get_session_factory()() as session:
            row = session.scalar(select(PortfolioDailySnapshotModel).limit(1))
            assert "calculation_state_json" not in row.__dict__
    finally:
        event.remove(get_engine(), "before_cursor_execute", record)
    assert all("calculation_state_json" not in statement for statement in statements)


@pytest.mark.parametrize("scenario", ["funded", "funded_restart", "selected_transfer", "transfer_fifo", "moving_average", "zero_net_short", "written_option", "foreign_partial"])
@pytest.mark.parametrize("start", [None, date(2026, 6, 2)])
def test_period_state_preserves_lot_and_lifecycle_semantics(scenario, start, monkeypatch):
    from tests.test_ledger_review_repairs import ACCOUNTS, REF, fact
    from tests.test_structured_transaction_events import _derivative_contract

    accounts = deepcopy(ACCOUNTS)
    if scenario == "moving_average":
        accounts[1]["cost_basis_method"] = "moving_average"
    portfolio = {"portfolio_id": "audit", "base_currency": "USD", "inception_date": "2026-06-01",
                 "as_of_date": "2026-06-05", "valuation_timezone": "Asia/Shanghai", "valuation_cutoff_policy": "latest_complete_eod"}
    facts = [fact(1, "deposit" if scenario == "funded" else "opening_balance", "2026-06-01", 5000, account="cash")]
    facts += [fact(2, "buy", "2026-06-01", 1000, 10, settlement_cash_account_id="cash"),
              fact(3, "buy", "2026-06-02", 1100, 10, settlement_cash_account_id="cash")]
    if scenario == "funded_restart":
        facts = [fact(1, "deposit", "2026-06-01", 5000, account="cash"),
                 fact(2, "withdrawal", "2026-06-02", 5000, account="cash"),
                 fact(3, "deposit", "2026-06-03", 5000, account="cash"),
                 fact(4, "opening_balance", "2026-06-03", 1200, 10),
                 fact(5, "buy", "2026-06-03", 600, 5, settlement_cash_account_id="cash"),
                 fact(6, "sell", "2026-06-04", 560, 4, settlement_cash_account_id="cash")]
        start = date(2026, 6, 3) if start is not None else None
    elif scenario == "transfer_fifo":
        facts[2]["account_id"] = "b"
        facts += [fact(4, "transfer_out", "2026-06-03", 1000, 10, transfer_object_type="position", transfer_scope="internal", transfer_group_id="transfer", counterparty_account_id="b"),
                  fact(5, "transfer_in", "2026-06-03", 1000, 10, account="b", transfer_object_type="position", transfer_scope="internal", transfer_group_id="transfer", counterparty_account_id="a"),
                  fact(6, "sell", "2026-06-04", 1400, 10, account="b", settlement_cash_account_id="cash")]
    elif scenario in {"selected_transfer", "moving_average"}:
        transfer_date = "2026-06-04" if scenario == "selected_transfer" else "2026-06-03"
        sale_date = "2026-06-03" if scenario == "selected_transfer" else "2026-06-04"
        facts += [fact(4, "transfer_out", transfer_date, 1200, 10, transfer_object_type="position", transfer_scope="internal", transfer_group_id="transfer", counterparty_account_id="b"),
                  fact(5, "transfer_in", transfer_date, 1200, 10, account="b", transfer_object_type="position", transfer_scope="internal", transfer_group_id="transfer", counterparty_account_id="a"),
                  fact(6, "sell", sale_date, 560, 4, account="a" if scenario == "selected_transfer" else "b", settlement_cash_account_id="cash", fees=0.3, taxes=0.2,
                       lot_selections=[{"opening_transaction_id": "txn-0003", "quantity": 4}] if scenario == "selected_transfer" else [])]
    elif scenario == "zero_net_short":
        facts += [fact(4, "short_sell", "2026-06-02", 2200, 20, account="b", settlement_cash_account_id="cash"),
                  fact(5, "buy_to_cover", "2026-06-04", 700, 5, account="b", settlement_cash_account_id="cash", fees=0.2)]
    elif scenario == "written_option":
        contract = _derivative_contract("writer", "option", account_id="a", option_underlying_id=REF["instrument_id"])
        derivative = {"instrument_id": None, "instrument_ref": None, "derivative_contract_id": "writer", "derivative_contract": contract, "settlement_cash_account_id": "cash"}
        facts += [fact(4, "option_write", "2026-06-02", 300, 2, **derivative),
                  fact(5, "option_buy_to_close", "2026-06-04", 80, 1, fees=2, **derivative)]
    else:
        facts += [fact(4, "sell", "2026-06-04", 700, 5, settlement_cash_account_id="cash", fees=0.3)]
    details = {
        **REF, "quote_selection_policy": {role: ["official_nav"] for role in ("valuation", "chart", "trading", "reference", "total_return")},
        "market_data": [{"metric_family": "nav", "quote_basis": "official_nav", "as_of_date": f"2026-06-0{day}", "value": price, "currency": "USD", "price_unit": "per_unit", "price_scale": 1, "status": "complete"}
                        for day, price in enumerate((100, 110, 120, 140, 150), 1)],
    }
    if scenario == "foreign_partial":
        for tx in facts:
            if tx.get("instrument_id"):
                tx["currency"] = "EUR"
                tx["instrument_ref"] = {**REF, "currency": "EUR"}
        details["currency"] = "EUR"
        for point in details["market_data"]:
            point["currency"] = "EUR"
        original_fx = performance.valuation_fx.resolve_fx_rate_on
        def resolve_fx(**kwargs):
            if kwargs["base_currency"] == "EUR" and kwargs["quote_currency"] == "USD":
                return {"rate": 1 + kwargs["as_of_date"].day / 100, "stale": False, "as_of_date": kwargs["as_of_date"]}
            return original_fx(**kwargs)
        monkeypatch.setattr(performance.valuation_fx, "resolve_fx_rate_on", resolve_fx)
    monkeypatch.setattr(performance, "get_registry_instrument_detail", lambda key: deepcopy(details) if key == REF["instrument_id"] else None)
    monkeypatch.setattr(performance, "get_registry_instrument_details", lambda keys: {key: deepcopy(details) for key in keys if key == REF["instrument_id"]})
    monkeypatch.setattr(performance, "get_shared_fx_rates", lambda: {"supported_currencies": ["USD"], "rates": []})
    end = date(2026, 6, 5)
    snapshots = performance.build_daily_portfolio_snapshots(portfolio, accounts, facts, include_materialized_rows=True)
    assert snapshots[-1]["as_of_date"] == end
    assert not snapshots[-1].get("valuation_blocked_reason")
    states = {row["as_of_date"]: json.loads(json.dumps(daily_snapshots._json_safe(row["_calculation_state"]))) for row in snapshots}
    inputs = PeriodCalculationInputs(snapshots, states)
    for state in states.values():
        for side in ("long", "short"):
            inputs.lot_events[side].extend(state["lot_events"][side])
        inputs.option_events.extend(state["option_events"])
        inputs.corporate_actions.extend(state["corporate_actions"])
        inputs.cash_components.extend(state["cash_components"])
    if scenario == "zero_net_short":
        assert states[date(2026, 6, 2)]["open_lots"]["long"]
        assert states[date(2026, 6, 2)]["open_lots"]["short"]
    expected = performance.build_period_calculation_report(portfolio, accounts, facts, start_date=start, end_date=end)
    actual = performance.build_period_calculation_report(portfolio, accounts, facts, start_date=start, end_date=end, calculation_inputs=inputs)
    assert actual == expected
    if scenario == "transfer_fifo":
        # The transferred June 1 lot is sold first; the June 2 lot retains its
        # $110 basis ($110 also happens to be the optional start boundary).
        assert actual["summary"]["unrealized_capital_gains"] == pytest.approx(400)
