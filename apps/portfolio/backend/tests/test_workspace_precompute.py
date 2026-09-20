from collections import OrderedDict
import pytest

from fastapi.encoders import jsonable_encoder
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from portfolio_app.db.models import PortfolioWorkspaceReadModel
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import holdings_workspace, source_cache, workspace_precompute, workspace_read_models
from portfolio_app.services import instrument_registry
from portfolio_app.services.risk_model import update_portfolio_risk_policy


PORTFOLIO_ID = "investment-studio"


@pytest.fixture(autouse=True)
def published_accounting_generation(client):
    # This fixture's client drains the accounting queue before a financial GET.
    assert client.get(f"/api/portfolios/{PORTFOLIO_ID}/performance").status_code == 200


def _clear_memory(monkeypatch):
    monkeypatch.setattr(source_cache, "_cache", OrderedDict())
    monkeypatch.setattr(source_cache, "_cache_total_size_bytes", 0)


def test_background_publication_survives_empty_process_cache_and_keeps_results(client, monkeypatch):
    expected = client.get(f"/api/workspace/holdings?portfolio_id={PORTFOLIO_ID}&include_details=true").json()
    assert workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    assert not workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    with get_session_factory()() as session:
        rows = list(session.scalars(select(PortfolioWorkspaceReadModel).where(
            PortfolioWorkspaceReadModel.portfolio_id == PORTFOLIO_ID,
        )))
        assert {row.surface for row in rows} == {"holdings_analytics", "portfolio_risk_basis"}
        assert all(row.payload_json is not None and row.error_type is None for row in rows)
    _clear_memory(monkeypatch)

    def should_not_rebuild(*_args, **_kwargs):
        raise AssertionError("Latest persisted analysis must survive an API restart")

    monkeypatch.setattr(holdings_workspace, "_build_holdings_analytics_workspace", should_not_rebuild)
    monkeypatch.setattr(holdings_workspace, "_build_portfolio_calculation_frequency_profile", should_not_rebuild)
    response = client.get(f"/api/workspace/holdings?portfolio_id={PORTFOLIO_ID}&include_details=true")
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert client.get(f"/api/workspace/summary?portfolio_id={PORTFOLIO_ID}").status_code == 200


def test_publisher_discards_old_generation_and_does_not_overwrite_newer_row(client):
    assert workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    _portfolio, _as_of, keys = workspace_precompute._current_projection_inputs(PORTFOLIO_ID)
    surface = "holdings_analytics"
    current = keys[surface]
    expected = workspace_read_models.read_workspace_projection(PORTFOLIO_ID, surface, current)
    assert expected is not None
    assert not workspace_read_models.publish_workspace_projection(
        PORTFOLIO_ID, surface, ("old",), current_source_key=lambda: current, payload={"stale": True},
    )
    assert workspace_read_models.read_workspace_projection(PORTFOLIO_ID, surface, current) == expected
    assert workspace_read_models.read_workspace_projection(PORTFOLIO_ID, surface, ("old",)) is None
    assert not workspace_read_models.publish_workspace_projection(
        PORTFOLIO_ID, surface, current, current_source_key=lambda: current,
        payload=None, error_type="ValueError",
    )
    assert workspace_read_models.read_workspace_projection(PORTFOLIO_ID, surface, current) == expected


def test_changed_risk_policy_cannot_reuse_published_old_analysis(client, monkeypatch):
    assert workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    before = workspace_precompute._current_projection_inputs(PORTFOLIO_ID)
    update_portfolio_risk_policy(PORTFOLIO_ID, {"lookback_days": 180})
    after = workspace_precompute._current_projection_inputs(PORTFOLIO_ID)
    assert before[2]["holdings_analytics"] != after[2]["holdings_analytics"]
    assert workspace_read_models.read_workspace_projection(
        PORTFOLIO_ID, "holdings_analytics", after[2]["holdings_analytics"],
    ) is None
    assert workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    _clear_memory(monkeypatch)
    payload = holdings_workspace.read_holdings_analysis(PORTFOLIO_ID, after[1])
    assert jsonable_encoder(payload) == workspace_read_models.read_workspace_projection(
        PORTFOLIO_ID, "holdings_analytics", after[2]["holdings_analytics"],
    )


def test_failed_projection_is_not_retried_in_a_hot_loop_or_published_as_success(client, monkeypatch):
    def fail(*_args, **_kwargs):
        raise ValueError("invalid market history")

    monkeypatch.setattr(workspace_precompute, "read_holdings_analysis", fail)
    assert workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    assert not workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    with get_session_factory()() as session:
        row = session.get(PortfolioWorkspaceReadModel, (PORTFOLIO_ID, "holdings_analytics"))
        assert row.error_type == "ValueError"
        assert row.payload_json is None
    key = workspace_precompute._current_projection_inputs(PORTFOLIO_ID)[2]["holdings_analytics"]
    assert workspace_read_models.read_workspace_projection(PORTFOLIO_ID, "holdings_analytics", key) is None


def test_transient_database_failure_does_not_disable_the_generation(client, monkeypatch):
    def disconnected(*_args, **_kwargs):
        raise OperationalError("SELECT", {}, Exception("connection closed"))

    monkeypatch.setattr(workspace_precompute, "read_holdings_analysis", disconnected)
    with pytest.raises(OperationalError):
        workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    with get_session_factory()() as session:
        assert session.get(PortfolioWorkspaceReadModel, (PORTFOLIO_ID, "holdings_analytics")) is None


@pytest.mark.parametrize("cause", [
    OperationalError("SELECT", {}, Exception("connection closed")),
    OSError("connection interrupted"),
    ValueError("invalid market history"),
])
def test_registry_wrapped_failure_preserves_transient_or_data_error_policy(client, monkeypatch, cause):
    _clear_memory(monkeypatch)

    def failed_read(*_args, **_kwargs):
        raise cause

    # Exercise the real registry -> holdings HTTPException wrapping chain.
    monkeypatch.setattr(instrument_registry.shared_store, "get_instrument_details", failed_read)
    if isinstance(cause, (OperationalError, OSError)):
        with pytest.raises(HTTPException) as failure:
            workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
        assert isinstance(failure.value.__cause__, instrument_registry.InstrumentRegistryError)
        assert failure.value.__cause__.__cause__ is cause
        with get_session_factory()() as session:
            assert session.get(PortfolioWorkspaceReadModel, (PORTFOLIO_ID, "holdings_analytics")) is None
    else:
        assert workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
        assert not workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
        with get_session_factory()() as session:
            row = session.get(PortfolioWorkspaceReadModel, (PORTFOLIO_ID, "holdings_analytics"))
            assert row.error_type == "HTTPException"
            assert row.payload_json is None


def test_unchanged_background_scan_does_not_reload_financial_inputs(client):
    assert workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement.lower())

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert not workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) <= 6
    assert not any("transaction_record" in sql or "instrument_data" in sql for sql in statements)


def test_live_operational_overlays_are_not_saved_in_page_analysis(client, monkeypatch):
    monkeypatch.setattr(holdings_workspace, "instrument_event_task_quality_warnings", lambda _pid: ["live task"])
    assert workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    before = client.get(f"/api/workspace/holdings?portfolio_id={PORTFOLIO_ID}").json()
    monkeypatch.setattr(holdings_workspace, "instrument_event_task_quality_warnings", lambda _pid: ["changed task"])
    _clear_memory(monkeypatch)
    after = client.get(f"/api/workspace/holdings?portfolio_id={PORTFOLIO_ID}").json()
    assert "live task" in before["quality_warnings"]
    assert "live task" not in after["quality_warnings"]
    assert "changed task" in after["quality_warnings"]
