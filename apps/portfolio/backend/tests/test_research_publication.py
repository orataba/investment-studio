from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import pytest

from portfolio_app.db.models import ResearchRunRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import research
from .test_research_api import _create_planning_taxonomy, _create_target_sets


PORTFOLIO_ID = "investment-studio"


def test_dynamic_research_date_is_resolved_after_its_snapshot_refresh(client, monkeypatch):
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    response = client.put(f"/api/portfolios/{PORTFOLIO_ID}/research/settings", json={
        "planning_taxonomy_id": taxonomy_id, "comparator_taxonomy_node_id": nodes["Risk Assets"],
        "as_of_mode": "dynamic", "lookback_days": 30,
        "target_dimension": "scope_default", "capital_mode": "unit_notional",
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
        "target_dimension": "scope_default", "capital_mode": "unit_notional",
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
    with pytest.raises(IntegrityError):
        client.post(f"/api/portfolios/{PORTFOLIO_ID}/research/runs", json={})
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
        "target_dimension": "scope_default", "capital_mode": "unit_notional",
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
