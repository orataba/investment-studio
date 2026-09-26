from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
import pytest

from portfolio_app.db.models import ResearchRunRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import research
from .test_research_api import _create_planning_taxonomy, _create_target_sets


PORTFOLIO_ID = "investment-studio"


def _research_settings(client):
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    response = client.put(f"/api/portfolios/{PORTFOLIO_ID}/research/settings", json={
        "planning_taxonomy_id": taxonomy_id, "comparator_taxonomy_node_id": nodes["Risk Assets"],
        "as_of_date": "2026-04-15", "lookback_days": 30, "capital_mode": "unit_notional",
    })
    assert response.status_code == 200, response.text


def test_disconnected_calculation_saves_failure_with_new_transactions_without_rerun(client, monkeypatch):
    _research_settings(client)
    original_execute = Session.execute
    calculation_error = OperationalError("private calculation", {}, Exception("connection lost"), connection_invalidated=True)
    calls = {"solve": 0, "failure_write": 0}
    calculation_sessions = []
    recovery_sessions = []
    waits = []
    rollback_failures = []

    def fail_solve(*args, **kwargs):
        calls["solve"] += 1
        raise calculation_error

    def execute(session, statement, *args, **kwargs):
        if getattr(statement, "is_update", False) and statement.table.name == "research_run_record":
            recovery_sessions.append(session)
            calls["failure_write"] += 1
            if calls["failure_write"] <= 2:
                raise OperationalError("terminal write", {}, Exception("recovery in progress"), connection_invalidated=True)
        return original_execute(session, statement, *args, **kwargs)

    original_commit = Session.commit
    original_rollback = Session.rollback
    def commit(session):
        if any(isinstance(row, ResearchRunRecordModel) and row.status == "running" for row in session.new):
            calculation_sessions.append(session)
        return original_commit(session)

    def rollback(session):
        if session in calculation_sessions and not rollback_failures:
            rollback_failures.append(session)
            raise OperationalError("rollback", {}, Exception("connection closed"), connection_invalidated=True)
        return original_rollback(session)

    monkeypatch.setattr(research, "solve_current_target_weights", fail_solve)
    monkeypatch.setattr(Session, "execute", execute)
    monkeypatch.setattr(Session, "commit", commit)
    monkeypatch.setattr(Session, "rollback", rollback)
    monkeypatch.setattr(research.time, "sleep", waits.append)
    with pytest.raises(OperationalError) as raised:
        research.run_portfolio_research(PORTFOLIO_ID)
    assert raised.value is calculation_error and calls == {"solve": 1, "failure_write": 3}
    assert waits == [1, 2] and len(set(recovery_sessions)) == 3
    assert rollback_failures == calculation_sessions
    assert all(session not in calculation_sessions for session in recovery_sessions)
    with get_session_factory()() as session:
        row = session.scalars(select(ResearchRunRecordModel)).one()
        assert row.status == "failed" and row.finished_at
        assert "private calculation" not in row.error_message
        assert row.detail_json is None and row.artifacts_json == []


def test_lost_publish_acknowledgement_preserves_committed_result_and_files(client, monkeypatch):
    _research_settings(client)
    original_commit = Session.commit
    lost_ack = OperationalError("publication", {}, Exception("ack lost"), connection_invalidated=True)
    committed = []
    def commit(session):
        completed = next((row for row in session.identity_map.values()
                          if isinstance(row, ResearchRunRecordModel) and row.status == "completed"), None)
        result = original_commit(session)
        if completed is not None and not committed:
            committed.append(completed.research_run_id)
            raise lost_ack
        return result
    monkeypatch.setattr(Session, "commit", commit)
    with pytest.raises(OperationalError) as raised:
        research.run_portfolio_research(PORTFOLIO_ID)
    assert raised.value is lost_ack
    with get_session_factory()() as session:
        row = session.get(ResearchRunRecordModel, committed[0])
        assert row.status == "completed" and row.detail_json and row.artifacts_json
        assert row.error_message is None
    assert (research._research_outputs_root() / PORTFOLIO_ID / committed[0] / "report.md").is_file()


def test_terminal_failure_wait_is_bounded_and_maintenance_recovers_only_interrupted_runs(client, monkeypatch):
    ids = {status: "recovery-" + status for status in ("running", "completed", "failed")}
    with get_session_factory()() as session:
        for status, run_id in ids.items():
            session.add(ResearchRunRecordModel(research_run_id=run_id, portfolio_id=PORTFOLIO_ID,
                job_type="target_weight_solve", status=status, requested_at="2026-09-27T00:00:00Z",
                detail_json={"retained": True} if status == "completed" else None,
                artifacts_json=[{"path": "kept"}] if status == "completed" else []))
            directory = research._research_outputs_root() / PORTFOLIO_ID / run_id
            directory.mkdir(parents=True)
            (directory / "report.md").write_text(status)
        session.commit()
    original_execute = Session.execute
    calls = []
    waits = []
    def unavailable(session, statement, *args, **kwargs):
        if getattr(statement, "is_update", False) and statement.table.name == "research_run_record":
            calls.append(session)
            raise OperationalError("terminal", {}, Exception("DB recovering"), connection_invalidated=True)
        return original_execute(session, statement, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Session, "execute", unavailable)
        patch.setattr(research.time, "sleep", waits.append)
        assert research._fail_research_run(PORTFOLIO_ID, ids["running"], ValueError("original calculation failure")) is None
    assert len(calls) == 6 and waits == [1, 2, 4, 8, 16]
    assert research.recover_interrupted_research_runs() == 1
    assert research.recover_interrupted_research_runs() == 0
    with get_session_factory()() as session:
        row = session.get(ResearchRunRecordModel, ids["running"])
        assert row.status == "failed" and row.finished_at and "interrupted" in row.error_message
        assert session.get(ResearchRunRecordModel, ids["completed"]).detail_json == {"retained": True}
        assert session.get(ResearchRunRecordModel, ids["failed"]).finished_at is None
    root = research._research_outputs_root() / PORTFOLIO_ID
    assert not (root / ids["running"]).exists()
    assert all((root / ids[status] / "report.md").is_file() for status in ("completed", "failed"))


@pytest.mark.parametrize("database_error", [
    IntegrityError("terminal update", {}, Exception("invalid value")),
    OperationalError("terminal update", {}, Exception("no such table: research_run_record")),
    OperationalError("terminal update", {}, Exception("database or disk is full")),
])
def test_terminal_write_does_not_retry_a_deterministic_database_error(client, monkeypatch, database_error):
    calls = []
    def invalid(session, statement, *args, **kwargs):
        calls.append(session)
        raise database_error
    monkeypatch.setattr(Session, "execute", invalid)
    monkeypatch.setattr(research.time, "sleep", lambda delay: pytest.fail("deterministic database errors must not retry"))
    assert research._fail_research_run(PORTFOLIO_ID, "missing", ValueError("calculation failure")) is None
    assert len(calls) == 1


def test_database_recovery_before_sqlstate_is_available_is_retryable():
    error = OperationalError(None, None, Exception("FATAL: the database system is not yet accepting connections"))
    assert research._research_database_recovering(error)


def test_research_recovery_only_maintenance_does_not_start_snapshot_or_research_calculation(client, monkeypatch):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "scripts" / "refresh_release_snapshots.py"
    spec = importlib.util.spec_from_file_location("research_recovery_maintenance", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with get_session_factory()() as session:
        session.add(ResearchRunRecordModel(research_run_id="interrupted-maintenance", portfolio_id=PORTFOLIO_ID,
            job_type="target_weight_solve", status="running", requested_at="2026-09-27T00:00:00Z"))
        session.commit()
    def forbidden(*args, **kwargs):
        pytest.fail("Research-only recovery must not refresh snapshots or solve")
    for name in ("_recover_interrupted_refreshes", "_run_portfolio_daily_snapshot_recalculation_synchronously", "precompute_portfolio_workspace"):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(research, "solve_current_target_weights", forbidden)
    assert module.main(recover_research_only=True) == 0
    with get_session_factory()() as session:
        assert session.get(ResearchRunRecordModel, "interrupted-maintenance").status == "failed"


def test_dynamic_research_date_is_resolved_after_its_snapshot_refresh(client, monkeypatch):
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    response = client.put(f"/api/portfolios/{PORTFOLIO_ID}/research/settings", json={
        "planning_taxonomy_id": taxonomy_id, "comparator_taxonomy_node_id": nodes["Risk Assets"],
        "as_of_mode": "dynamic", "lookback_days": 30,
        "capital_mode": "unit_notional",
    })
    assert response.status_code == 200, response.text
    original_read = research.get_portfolio
    original_refresh = research._run_portfolio_daily_snapshot_recalculation_synchronously
    refreshed = False

    def read_portfolio(portfolio_id):
        value = original_read(portfolio_id)
        return {**value, "as_of_date": "2026-04-15" if refreshed else "2026-04-14"}

    def refresh(portfolio_id):
        nonlocal refreshed
        value = original_refresh(portfolio_id)
        refreshed = True
        return value

    monkeypatch.setattr(research, "get_portfolio", read_portfolio)
    monkeypatch.setattr(research, "_run_portfolio_daily_snapshot_recalculation_synchronously", refresh)
    response = client.post(f"/api/portfolios/{PORTFOLIO_ID}/research/runs", json={})
    assert response.status_code == 200, response.text
    assert response.json()["as_of_date"] == "2026-04-15"


def test_research_pruning_keeps_inflight_and_later_requests_and_waits_for_commit():
    root = research._research_outputs_root() / PORTFOLIO_ID
    records = [("retired", "completed", "2026-06-01"), ("running", "running", "2026-06-01"),
               ("current", "completed", "2026-06-02"), ("newer", "completed", "2026-06-03")]
    with get_session_factory()() as session:
        for run_id, status, requested_at in records:
            session.add(ResearchRunRecordModel(research_run_id=run_id, portfolio_id=PORTFOLIO_ID,
                status=status, requested_at=requested_at))
            directory = root / run_id
            directory.mkdir(parents=True)
            (directory / "report.md").write_text(run_id)
        session.commit()
        retired = research._prune_portfolio_research_runs(session, PORTFOLIO_ID, keep_run_id="current")
        assert retired == ["retired"]
        assert (root / "retired" / "report.md").read_text() == "retired"
        session.rollback()
        assert session.get(ResearchRunRecordModel, "retired") is not None
        retired = research._prune_portfolio_research_runs(session, PORTFOLIO_ID, keep_run_id="current")
        session.commit()
        research._remove_pruned_research_artifacts(PORTFOLIO_ID, retired)
        assert {row.research_run_id for row in session.scalars(select(ResearchRunRecordModel))} == {"running", "current", "newer"}
    assert not (root / "retired").exists()
    assert all((root / run_id / "report.md").exists() for run_id in ["running", "current", "newer"])


def test_failed_research_publish_rolls_back_pruning_and_preserves_previous_report(client, monkeypatch):
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    response = client.put(f"/api/portfolios/{PORTFOLIO_ID}/research/settings", json={
        "planning_taxonomy_id": taxonomy_id, "comparator_taxonomy_node_id": nodes["Risk Assets"],
        "as_of_date": "2026-04-15", "lookback_days": 30,
        "capital_mode": "unit_notional",
    })
    assert response.status_code == 200, response.text
    first = client.post(f"/api/portfolios/{PORTFOLIO_ID}/research/runs", json={})
    assert first.status_code == 200, first.text
    first_id = first.json()["research_run_id"]
    first_report = research._research_outputs_root() / PORTFOLIO_ID / first_id / "report.md"
    saved_text = first_report.read_text()
    original_commit = Session.commit
    interrupted = False

    def fail_publish(session):
        nonlocal interrupted
        if not interrupted and any(isinstance(row, ResearchRunRecordModel)
                and row.research_run_id != first_id and row.status == "completed"
                for row in session.identity_map.values()):
            interrupted = True
            # A real uniqueness failure aborts the publication transaction.
            session.execute(text("INSERT INTO research_run_record SELECT * FROM research_run_record WHERE research_run_id = :run_id"),
                            {"run_id": next(row.research_run_id for row in session.identity_map.values()
                                if isinstance(row, ResearchRunRecordModel) and row.research_run_id != first_id)})
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_publish)
    # Validate the domain transaction directly: HTTP diagnostics intentionally
    # redact unexpected database exception details at the request boundary.
    with pytest.raises(IntegrityError):
        research.run_portfolio_research(PORTFOLIO_ID)
    assert interrupted
    with get_session_factory()() as session:
        rows = list(session.scalars(select(ResearchRunRecordModel)))
        assert next(row for row in rows if row.research_run_id == first_id).status == "completed"
        failed = next(row for row in rows if row.research_run_id != first_id)
        assert failed.status == "failed"
        assert failed.detail_json is None and failed.artifacts_json == []
        assert not (research._research_outputs_root() / PORTFOLIO_ID / failed.research_run_id).exists()
    assert first_report.read_text() == saved_text

@pytest.mark.parametrize("changed_input", ["transaction", "deletion", "market_source", "base_currency"])
def test_research_fingerprint_detects_historical_financial_input_changes(client, changed_input):
    from datetime import date
    from investment_studio_instrument_core.db_models import Instrument
    from portfolio_app.db.models import PortfolioRecordModel, TransactionRecordModel
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    def fingerprint():
        with get_session_factory()() as session:
            return research._planning_state_fingerprint(session, portfolio_id=PORTFOLIO_ID,
                planning_taxonomy_id=taxonomy_id, as_of_date=date(2026, 4, 15))
    original = fingerprint()
    with get_session_factory()() as session:
        if changed_input in {"transaction", "deletion"}:
            transaction = session.scalars(select(TransactionRecordModel).where(
                TransactionRecordModel.portfolio_id == PORTFOLIO_ID,
                TransactionRecordModel.trade_date <= date(2026, 4, 15))).first()
            if changed_input == "transaction":
                transaction.row_version += 1
                transaction.gross_amount += 1
            else:
                session.delete(transaction)
        elif changed_input == "market_source":
            instrument = session.get(Instrument, 'equity-us-abbv')
            instrument.market_data_updated_at = '2026-09-13T08:00:00+00:00'
        else:
            session.get(PortfolioRecordModel, PORTFOLIO_ID).base_currency = 'CNY'
        session.commit()
    assert fingerprint() != original


def test_research_input_change_during_calculation_preserves_previous_run(client, monkeypatch):
    from portfolio_app.db.models import TransactionRecordModel
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    response = client.put(f"/api/portfolios/{PORTFOLIO_ID}/research/settings", json={
        "planning_taxonomy_id": taxonomy_id, "comparator_taxonomy_node_id": nodes["Risk Assets"],
        "as_of_date": "2026-04-15", "lookback_days": 30,
        "capital_mode": "unit_notional",
    })
    assert response.status_code == 200
    first = client.post(f"/api/portfolios/{PORTFOLIO_ID}/research/runs", json={})
    assert first.status_code == 200, first.text
    first_id = first.json()["research_run_id"]
    original = research.solve_current_target_weights
    def mutate_after_solve(*args, **kwargs):
        result = original(*args, **kwargs)
        with get_session_factory()() as session:
            row = session.scalars(select(TransactionRecordModel).where(TransactionRecordModel.portfolio_id == PORTFOLIO_ID)).first()
            row.row_version += 1
            session.commit()
        return result
    monkeypatch.setattr(research, "solve_current_target_weights", mutate_after_solve)
    response = client.post(f"/api/portfolios/{PORTFOLIO_ID}/research/runs", json={})
    assert response.status_code == 400
    assert "inputs changed" in response.json()["detail"]
    with get_session_factory()() as session:
        assert session.get(ResearchRunRecordModel, first_id).status == 'completed'
    assert (research._research_outputs_root() / PORTFOLIO_ID / first_id / 'report.md').exists()


@pytest.mark.parametrize('legacy', [False, True])
def test_saved_benchmark_comparison_does_not_depend_on_current_taxonomy(client, monkeypatch, legacy):
    from datetime import date
    from portfolio_app.services import research_solver

    monkeypatch.setattr(research_solver, 'capture_current_target_configuration',
                        lambda *args, **kwargs: pytest.fail('Archived comparison must not read current targets'))
    with get_session_factory()() as session:
        session.add(ResearchRunRecordModel(
            research_run_id='archived-comparison', portfolio_id=PORTFOLIO_ID,
            planning_taxonomy_id=None, status='completed', requested_at='2026-04-15T00:00:00Z',
            as_of_date=date(2026, 4, 15),
            request_payload_json={} if legacy else {
                'target_configuration': 'current_snapshot',
                'target_configuration_snapshot': {'base_currency': 'USD', 'taxonomy': {'taxonomy_id': 'deleted-taxonomy'}},
            },
            detail_json={'backtest': {'points': [
                {'date': '2026-04-13', 'value': 1.0},
                {'date': '2026-04-14', 'value': 1.02},
                {'date': '2026-04-15', 'value': 1.03},
            ]}},
        ))
        session.commit()
    result = research.get_research_backtest_benchmark_comparison(
        PORTFOLIO_ID, research_run_id='archived-comparison', benchmark_instrument_id='fund-hk-2800')
    assert result['backtest_benchmark']['points']
    assert result['backtest_relative_metrics'] is not None
    currency_warnings = [value for value in result['backtest_benchmark']['warnings'] if 'reporting currency' in value]
    assert bool(currency_warnings) is legacy


def test_research_fingerprint_tracks_current_target_market_data_without_transaction_history():
    from datetime import date
    from sqlalchemy import delete
    from investment_studio_instrument_core.db_models import Instrument
    from portfolio_app.db.models import PortfolioInstrumentUniverseRecordModel

    configuration = {
        'target_snapshot_fingerprint': 'snapshot-test',
        'taxonomy_assignments': [{'status': 'active', 'target_scope': 'instrument', 'target_entity_id': 'fund-us-watch'}],
    }
    with get_session_factory()() as session:
        session.execute(delete(PortfolioInstrumentUniverseRecordModel).where(
            PortfolioInstrumentUniverseRecordModel.portfolio_id == PORTFOLIO_ID,
            PortfolioInstrumentUniverseRecordModel.instrument_id == 'fund-us-watch'))
        def fingerprint():
            return research._planning_state_fingerprint(session, portfolio_id=PORTFOLIO_ID,
                planning_taxonomy_id='current-targets', as_of_date=date(2026, 4, 15),
                target_configuration=configuration)
        before = fingerprint()
        session.get(Instrument, 'fund-us-watch').market_data_updated_at = '2026-09-20T00:00:00Z'
        session.flush()
        assert fingerprint() != before


@pytest.mark.parametrize(
    ("solver_version", "identity_version", "expected_state", "reason"),
    [
        (research.RESEARCH_TARGET_SOLVER_VERSION, research.RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION, "current", None),
        (None, research.RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION, "stale", "earlier target solver"),
        ("recursive_local_covariance", research.RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION, "stale", "earlier target solver"),
        ("global_leaf_scalar_targets_v2", research.RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION, "stale", "earlier target solver"),
        ("global_leaf_scalar_targets_v3", research.RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION, "stale", "earlier target solver"),
        ("global_leaf_scalar_targets_v4", research.RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION, "stale", "earlier target solver"),
        (research.RESEARCH_TARGET_SOLVER_VERSION, 5, "stale", "earlier Research input identity version"),
    ],
)
def test_research_algorithm_version_is_required_for_current_result(solver_version, identity_version, expected_state, reason):
    from copy import deepcopy
    from datetime import date

    settings = {
        "planning_taxonomy_id": "planning",
        "as_of_mode": "pinned",
        "as_of_date": "2026-04-15",
    }
    request = {
        **settings,
        "lookback_days": 90,
        "calculation_frequency": "daily",
        "missing_return_policy": research.RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
        "frozen_taxonomy_node_ids": [],
        "top_sleeve_weight_bounds": [],
        "planning_state_fingerprint": "sha256:unchanged-financial-inputs",
        "planning_state_fingerprint_version": identity_version,
        "solver_version": solver_version,
    }
    saved_detail = {"headline": "Saved result", "target_assumptions": ["Original method"], "leaf_targets": [{"member_id": "a", "target_weight": 1.0}]}
    row = ResearchRunRecordModel(
        research_run_id="archived", portfolio_id=PORTFOLIO_ID, status="completed",
        as_of_date=date(2026, 4, 15), request_payload_json=deepcopy(request), detail_json=deepcopy(saved_detail),
    )

    state, reasons = research._research_run_reliability(
        row,
        latest_portfolio_as_of_date=date(2026, 4, 15),
        settings_payload=settings,
        production_risk_model={},
        latest_transaction_date=None,
        current_planning_state_fingerprint=request["planning_state_fingerprint"],
    )

    assert state == expected_state
    if reason is None:
        assert reasons == []
    else:
        assert any(reason in item for item in reasons)
    assert row.request_payload_json == request
    assert row.detail_json == saved_detail


def test_research_fingerprint_invalidates_cached_analysis_when_solver_changes(client, monkeypatch):
    from datetime import date

    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    with get_session_factory()() as session:
        before = research._planning_state_fingerprint(
            session, portfolio_id=PORTFOLIO_ID, planning_taxonomy_id=taxonomy_id, as_of_date=date(2026, 4, 15),
        )
        monkeypatch.setattr(research, "RESEARCH_TARGET_SOLVER_VERSION", "next-target-solver")
        after = research._planning_state_fingerprint(
            session, portfolio_id=PORTFOLIO_ID, planning_taxonomy_id=taxonomy_id, as_of_date=date(2026, 4, 15),
        )
    assert after != before
