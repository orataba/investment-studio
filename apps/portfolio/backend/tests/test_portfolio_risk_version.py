"""Risk-summary freshness reads published identities, never recalculates risk."""
from datetime import date

import pytest
from sqlalchemy import event, select

from investment_studio_instrument_core.db_models import Instrument
from portfolio_app.db.models import (
    ConcentrationPolicyRevisionModel, DerivativeContractRecordModel, PortfolioCalculationStateModel,
    PortfolioDailyHoldingSnapshotModel, PortfolioInstrumentEventTaskModel, PortfolioRecordModel, PortfolioTaxonomyStateModel,
)
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import daily_snapshots, holdings_workspace, instrument_registry
from portfolio_app.services import portfolio_risk_context as service
from portfolio_app.services import workspace_precompute


PID = "investment-studio"
PATH = f"/api/portfolios/{PID}/risk-context/version"


@pytest.fixture
def published(client):
    assert client.get(f"/api/portfolios/{PID}/performance").status_code == 200
    return client


def test_version_and_actual_holding_directory_need_no_calculation_registry_or_large_payload(published, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Version read must not rebuild holdings/risk or reload the registry")
    monkeypatch.setattr(holdings_workspace, "holdings_workspace", forbidden)
    monkeypatch.setattr(instrument_registry.shared_store, "get_instrument_details", forbidden)
    monkeypatch.setattr(daily_snapshots, "_run_portfolio_daily_snapshot_recalculation_synchronously", forbidden)
    statements = []
    def capture(_conn, _cursor, sql, *_args):
        statements.append(sql.lower())
    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        response = published.get(PATH)
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["available"] and data["version"] and data["instrument_ids"] and data["holdings"]
    assert all(row["detail_path"].startswith(f"/portfolios/{PID}/holdings/") for row in data["holdings"])
    assert all("terms" not in row and "quantity" not in row for row in data["holdings"])
    assert not any(sql.lstrip().startswith(("insert", "update", "delete")) for sql in statements)
    assert not any("portfolio_workspace_read_model.payload_json" in sql for sql in statements)
    assert not any("instrument_market_data" in sql for sql in statements)
    assert published.get(PATH).json() == data


@pytest.mark.parametrize("change", ["risk_policy", "targets", "concentration_limits", "same_day_republication"])
def test_same_date_policy_target_and_accounting_generation_changes_invalidate_version(published, change):
    before = published.get(PATH).json()
    with get_session_factory()() as session:
        if change == "risk_policy":
            row = session.get(PortfolioRecordModel, PID)
            row.risk_policy_json = {**(row.risk_policy_json or {}), "lookback_days": 180}
        elif change == "targets":
            row = session.get(PortfolioTaxonomyStateModel, PID)
            if row is None:
                session.add(PortfolioTaxonomyStateModel(portfolio_id=PID, current_version=1, updated_at="2026-09-27"))
            else:
                row.current_version += 1
        elif change == "concentration_limits":
            session.add(ConcentrationPolicyRevisionModel(portfolio_id=PID, revision=1,
                effective_from=date.fromisoformat(before["as_of_date"]), settings_json={"limits": []},
                created_by="test-editor", created_at="2026-09-27"))
        else:
            row = session.get(PortfolioCalculationStateModel, PID)
            row.refreshed_at = "2026-09-27T00:00:01Z"
        session.commit()
    after = published.get(PATH).json()
    assert after["available"] and after["as_of_date"] == before["as_of_date"]
    assert after["version"] != before["version"]


@pytest.mark.parametrize("change", ["market_data_updated_at", "calculation_inputs_updated_at", "dirty"])
def test_unpublished_same_day_correction_is_unavailable_without_queueing(published, change):
    before = published.get(PATH).json()
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, PID)
        if change == "dirty":
            state.daily_snapshot_status = "stale"
            state.dirty_from = date.fromisoformat(before["as_of_date"])
        else:
            row = session.get(Instrument, before["instrument_ids"][0])
            setattr(row, change, "2099-01-01T00:00:00Z")
        session.commit()
        expected_state = (state.daily_snapshot_status, state.refresh_request_id, state.dirty_from)
    result = published.get(PATH)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["available"] is False and data["status"] == "unavailable"
    assert data["version"] is None and data["instrument_ids"] == data["holdings"] == [] and data["limitations"]
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, PID)
        assert (state.daily_snapshot_status, state.refresh_request_id, state.dirty_from) == expected_state


def test_directory_and_scope_match_existing_full_risk_projection_for_zero_short_and_derivative_rows(published):
    before = published.get(PATH).json()
    as_of = date.fromisoformat(before["as_of_date"])
    with get_session_factory()() as session:
        existing = session.scalar(select(PortfolioDailyHoldingSnapshotModel).where(
            PortfolioDailyHoldingSnapshotModel.portfolio_id == PID,
            PortfolioDailyHoldingSnapshotModel.as_of_date == as_of))
        for cid in ("held/fcn", "unheld-option"):
            session.add(DerivativeContractRecordModel(portfolio_id=PID, derivative_contract_id=cid,
                account_id=existing.account_id, contract_name=cid, contract_type="fcn", currency="USD",
                terms_json={"underlyings": [{"instrument_id": "must-not-read-live-terms"}]}, created_at="2026-09-27"))
        session.flush()
        session.add(PortfolioDailyHoldingSnapshotModel(portfolio_id=PID, as_of_date=as_of,
            account_id=existing.account_id, position_reference_id="held/fcn", derivative_contract_id="held/fcn",
            holding_kind="derivative_contract", currency="USD", quantity=1, calculated_at="2026-09-27",
            holding_json={"derivative_contract": {"contract_name": "已持有 FCN", "contract_type": "fcn", "terms": {
                "underlyings": [{"instrument_id": "bound-underlying"}]}}}))
        session.add(PortfolioDailyHoldingSnapshotModel(portfolio_id=PID, as_of_date=as_of,
            account_id=existing.account_id, position_reference_id="zero-position", instrument_id="zero-position",
            holding_kind="position", currency="USD", quantity=0, calculated_at="2026-09-27",
            holding_json={"quantity": 0, "instrument_ref": {"instrument_id": "zero-position", "instrument_name": "已清仓", "instrument_type": "equity", "currency": "USD", "exchange_code": "XNAS"}}))
        session.commit()
    data = published.get(PATH).json()
    row = next(item for item in data["holdings"] if item["holding_id"] == "held/fcn")
    assert row == {"holding_id": "held/fcn", "instrument_id": None, "name": "已持有 FCN",
        "detail_path": f"/portfolios/{PID}/holdings/held%2Ffcn", "underlying_instrument_ids": ["bound-underlying"]}
    assert "bound-underlying" in data["instrument_ids"]
    assert not {"zero-position", "must-not-read-live-terms", "unheld-option"} & set(data["instrument_ids"])
    assert "zero-position" in {item["holding_id"] for item in data["holdings"]}
    assert "unheld-option" not in {item["holding_id"] for item in data["holdings"]}

    # Aggregation and derivative projection are the same primitives used by the
    # full risk context. Shorts remain monitored, net-zero securities do not;
    # a retained zero derivative row still carries its bound underlying scope.
    with get_session_factory()() as session:
        accounts = list(session.scalars(select(PortfolioDailyHoldingSnapshotModel.account_id).where(
            PortfolioDailyHoldingSnapshotModel.portfolio_id == PID,
            PortfolioDailyHoldingSnapshotModel.as_of_date == as_of).distinct()))
        assert len(accounts) >= 2
        for index, quantity in enumerate((12, -12)):
            session.add(PortfolioDailyHoldingSnapshotModel(portfolio_id=PID, as_of_date=as_of,
                account_id=accounts[index], position_reference_id="net-zero", instrument_id="net-zero",
                holding_kind="position", currency="USD", quantity=quantity, calculated_at="2026-09-27",
                holding_json={"quantity": quantity, "instrument_ref": {"instrument_id": "net-zero", "instrument_name": "净零持仓",
                    "instrument_type": "equity", "currency": "USD", "exchange_code": "XNAS"}}))
        session.add(PortfolioDailyHoldingSnapshotModel(portfolio_id=PID, as_of_date=as_of,
            account_id=accounts[0], position_reference_id="short", instrument_id="short",
            holding_kind="position", currency="USD", quantity=-2, calculated_at="2026-09-27",
            holding_json={"quantity": -2, "instrument_ref": {"instrument_id": "short", "instrument_name": "空头",
                "instrument_type": "equity", "currency": "USD", "exchange_code": "XNAS"}}))
        derivative = session.scalar(select(PortfolioDailyHoldingSnapshotModel).where(
            PortfolioDailyHoldingSnapshotModel.portfolio_id == PID,
            PortfolioDailyHoldingSnapshotModel.position_reference_id == "held/fcn"))
        derivative.quantity = 0
        derivative.holding_json = {**derivative.holding_json, "quantity": 0}
        session.commit()
        rows = list(session.scalars(select(PortfolioDailyHoldingSnapshotModel).where(
            PortfolioDailyHoldingSnapshotModel.portfolio_id == PID,
            PortfolioDailyHoldingSnapshotModel.as_of_date == as_of)))
        full_rows = daily_snapshots._aggregate_holding_rows(rows, total_nav_base=1000)
    from portfolio_app.services.portfolio_risk_derivatives import project_derivative_risk
    derivatives = project_derivative_risk({"portfolio_id": PID, "as_of_date": as_of.isoformat(), "rows": full_rows})["positions"]
    contract_ids = {item["holding_id"] for item in derivatives}
    shared = [row for row in full_rows if row["quantity"] != 0
        and (row.get("derivative_contract_id") or row["position_reference_id"]) not in contract_ids
        and row["holding_kind"] != "settled_cash" and (row.get("instrument_core") or {}).get("instrument_type") != "cash"]
    expected_ids = {row["instrument_core"]["instrument_id"] for row in shared if row.get("instrument_core")}
    expected_ids.update(item["instrument_id"] for row in derivatives for item in row["underlyings"] if item.get("instrument_id"))
    expected_holdings = contract_ids | {row["position_reference_id"] for row in full_rows if row["holding_kind"] == "position"}
    data = published.get(PATH).json()
    assert set(data["instrument_ids"]) == expected_ids
    assert {row["holding_id"] for row in data["holdings"]} == expected_holdings
    assert {"short", "bound-underlying"} <= set(data["instrument_ids"])
    assert "net-zero" not in data["instrument_ids"]


@pytest.mark.parametrize("change", ["market_data_updated_at", "calculation_inputs_updated_at", "event_review"])
def test_live_derivative_quotes_and_event_review_invalidate_without_changing_accounting_generation(published, change):
    initial = published.get(PATH).json()
    as_of = date.fromisoformat(initial["as_of_date"])
    with get_session_factory()() as session:
        existing = session.scalar(select(PortfolioDailyHoldingSnapshotModel).where(
            PortfolioDailyHoldingSnapshotModel.portfolio_id == PID,
            PortfolioDailyHoldingSnapshotModel.as_of_date == as_of))
        session.add(Instrument(instrument_id="live-underlying", instrument_name="底层", instrument_type="equity",
            currency="USD", exchange_code="XNAS", quote_selection_policy_json={}))
        session.add(DerivativeContractRecordModel(portfolio_id=PID, derivative_contract_id="held-option",
            account_id=existing.account_id, contract_name="已持有期权", contract_type="option", currency="USD",
            terms_json={"underlying_instrument_id": "live-underlying"}, created_at="2026-09-27"))
        session.flush()
        session.add(PortfolioDailyHoldingSnapshotModel(portfolio_id=PID, as_of_date=as_of,
            account_id=existing.account_id, position_reference_id="held-option", derivative_contract_id="held-option", holding_kind="option_obligation",
            currency="USD", quantity=-1, calculated_at="2026-09-27", holding_json={"derivative_contract": {
                "contract_name": "已持有期权", "contract_type": "option", "terms": {"underlying_instrument_id": "live-underlying"}}}))
        session.add(PortfolioInstrumentEventTaskModel(instrument_event_task_id="review-task", portfolio_id=PID,
            account_id=existing.account_id, instrument_id=initial["instrument_ids"][0], event_source="fund_nav",
            event_action_id="confirmed-action", current_event_revision_id="revision1", event_type="cash_dividend",
            source_revision_kind="original", source_event_state="active", effective_date=as_of,
            created_at="2026-09-27", updated_at="2026-09-27"))
        session.commit()
    before = published.get(PATH).json()
    generation = workspace_precompute._current_projection_inputs(PID)
    with get_session_factory()() as session:
        if change == "event_review":
            task = session.get(PortfolioInstrumentEventTaskModel, "review-task")
            task.resolution_status, task.row_version = "not_applicable", 2
        else:
            setattr(session.get(Instrument, "live-underlying"), change, "2099-01-01T00:00:00Z")
        session.commit()
    after = published.get(PATH).json()
    assert workspace_precompute._current_projection_inputs(PID) == generation
    assert before["available"] and after["available"]
    assert before["version"] != after["version"]
    assert before["instrument_ids"] == after["instrument_ids"]


def test_live_overlay_changed_during_read_is_not_labeled_current(published, monkeypatch):
    identities = iter([(("underlying", None, None), ()), (("underlying", "new", None), ())])
    monkeypatch.setattr(service, "_risk_live_overlay_identity", lambda *_args: next(identities))
    data = published.get(PATH).json()
    assert not data["available"] and data["version"] is None and data["holdings"] == []


def test_generation_changed_during_directory_read_is_not_labeled_current(published, monkeypatch):
    original = workspace_precompute._current_projection_inputs
    calls = []
    def changed(*args, **kwargs):
        calls.append(None)
        return original(*args, **kwargs) if len(calls) == 1 else None
    monkeypatch.setattr(workspace_precompute, "_current_projection_inputs", changed)
    data = published.get(PATH).json()
    assert data["available"] is False and data["version"] is None and data["holdings"] == []
    assert "读取期间" in data["limitations"][0]
