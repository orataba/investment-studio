from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

from alembic import command
from fastapi import HTTPException
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from studio_identity import Principal, principal_context

from portfolio_app.db.models import PortfolioInvestmentStanceModel as Stance, PortfolioInvestmentViewModel as Note, PortfolioInvestmentViewRevisionModel as Revision, PortfolioRecordModel
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import investment_views as views
from portfolio_app.services.portfolio_store import create_portfolio
from tests.test_postgres_schema_reconciliation import postgres_reconciliation_database, _portfolio_config, _run_registry_upgrade  # noqa: F401

pytestmark = pytest.mark.postgresql_integration
PM = Principal("pm", "Portfolio Manager", "default", "admin")


@pytest.fixture
def view_portfolio(postgres_reconciliation_database):
    # The existing reconciliation fixture starts before the Registry schema rename;
    # Portfolio's historical 0055 migration still reads that old schema.
    command.upgrade(_portfolio_config(postgres_reconciliation_database), "head")
    _run_registry_upgrade("head")
    with principal_context(PM):
        portfolio = create_portfolio("观点并发测试", base_currency="USD", inception_date=date(2026, 10, 1))
        yield portfolio["portfolio_id"]


def _draft(body="Original"):
    return {"view_date": date(2026, 10, 8), "title": "Risk budget", "body": body,
        "context": "Dated analysis, retained independently of research output files.", "source": "Dated source", "instrument_ids": []}


def _concurrent(tasks):
    def run(task):
        with principal_context(PM):
            try:
                return task()
            except HTTPException as error:
                return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, tasks))


def test_concurrent_corrections_publish_one_revision_without_lost_update(view_portfolio, monkeypatch):
    portfolio_id = view_portfolio
    original = views.save_investment_view(portfolio_id, _draft())
    barrier = Barrier(2)
    read_note = views._note
    def synchronized_read(*args):
        row = read_note(*args)
        barrier.wait(timeout=10)
        return row
    with monkeypatch.context() as patch:
        patch.setattr(views, "_note", synchronized_read)
        results = _concurrent([lambda: views.save_investment_view(portfolio_id, _draft("First"), note_id=original["note_id"], expected_revision=1),
            lambda: views.save_investment_view(portfolio_id, _draft("Second"), note_id=original["note_id"], expected_revision=1)])
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 409 in results
    history = views.investment_view_revisions(portfolio_id, original["note_id"])["revisions"]
    assert [row["revision_number"] for row in history] == [2, 1]
    assert history[1]["body"] == "Original"
    assert history[0]["body"] in {"First", "Second"}


@pytest.mark.parametrize("initial_version", [0, 1])
def test_concurrent_stance_selection_is_compare_and_swap(view_portfolio, monkeypatch, initial_version):
    portfolio_id = view_portfolio
    first = views.save_investment_view(portfolio_id, _draft("First"))
    second = views.save_investment_view(portfolio_id, _draft("Second"))
    if initial_version:
        views.select_investment_stance(portfolio_id, note_id=first["note_id"], revision_number=1, expected_version=0)
    barrier = Barrier(2)
    read_note = views._note
    def synchronized_read(*args):
        row = read_note(*args)
        if not getattr(barrier, "passed", False):
            barrier.wait(timeout=10)
            barrier.passed = True
        return row
    with monkeypatch.context() as patch:
        patch.setattr(views, "_note", synchronized_read)
        results = _concurrent([lambda: views.select_investment_stance(portfolio_id, note_id=first["note_id"], revision_number=1, expected_version=initial_version),
            lambda: views.select_investment_stance(portfolio_id, note_id=second["note_id"], revision_number=1, expected_version=initial_version)])
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 409 in results
    assert views.list_investment_views(portfolio_id)["stance_version"] == initial_version + 1


def test_postgres_stance_foreign_key_rejects_cross_portfolio_revision_and_delete_cascades(view_portfolio):
    portfolio_id = view_portfolio
    original = views.save_investment_view(portfolio_id, _draft())
    second = create_portfolio("另一个组合", base_currency="USD", inception_date=date(2026, 10, 1))["portfolio_id"]
    with pytest.raises(IntegrityError), get_session_factory()() as session:
        session.add(Stance(portfolio_id=second, note_id=original["note_id"], revision_number=1,
            selection_version=1, selected_at="2026-10-08T00:00:00Z", selected_by_user_id="pm", selected_by_name="PM"))
        session.commit()
    views.select_investment_stance(portfolio_id, note_id=original["note_id"], revision_number=1, expected_version=0)
    with get_session_factory()() as session:
        session.execute(delete(PortfolioRecordModel).where(PortfolioRecordModel.portfolio_id == portfolio_id))
        session.commit()
        for model in (Stance, Note, Revision):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_empty_postgres_migration_is_reversible(postgres_reconciliation_database):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "head")
    command.downgrade(config, "20260929_0070")
    command.upgrade(config, "head")
    from sqlalchemy import inspect
    assert {"portfolio_investment_view", "portfolio_investment_view_revision", "portfolio_investment_stance"} <= set(inspect(get_engine()).get_table_names(schema="portfolio"))
