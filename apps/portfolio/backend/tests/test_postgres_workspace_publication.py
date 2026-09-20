from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Event, current_thread
from time import monotonic, sleep

from fastapi.encoders import jsonable_encoder
import pytest
from sqlalchemy import delete, event, select, text

from portfolio_app.db.models import PortfolioRecordModel, PortfolioWorkspaceReadModel
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services.workspace_read_models import (
    publish_workspace_projection,
    read_workspace_projection,
)
from .test_postgres_instrument_registry_constraints import postgres_portfolio_env


pytestmark = pytest.mark.postgresql_integration


PORTFOLIO_ID = "workspace-publication"
SURFACE = "holdings_analytics"


def _seed_portfolio():
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(
            portfolio_id=PORTFOLIO_ID, portfolio_name="current-generation",
            base_currency="USD", valuation_timezone="UTC",
            valuation_cutoff_policy="close", inception_date=date(2026, 1, 1),
        ))
        session.commit()


def _current_source_key():
    # A committed database fact, read again after the publisher obtains its
    # portfolio lock. Each publisher uses a separate connection/transaction.
    with get_session_factory()() as session:
        return (session.scalar(select(PortfolioRecordModel.portfolio_name).where(
            PortfolioRecordModel.portfolio_id == PORTFOLIO_ID,
        )),)


def _wait_for_database_lock(backend_pid):
    deadline = monotonic() + 10
    with get_engine().connect() as connection:
        while monotonic() < deadline:
            blockers = connection.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": backend_pid})
            if blockers:
                return
            sleep(0.01)
    pytest.fail("The late publisher did not wait on the portfolio row lock")


@pytest.mark.parametrize("late_result", ["older_source", "same_source_failure"])
def test_late_publisher_cannot_replace_success_after_waiting_for_portfolio_lock(
    postgres_portfolio_env, late_result,
):
    _seed_portfolio()
    current_key = _current_source_key()
    latest_payload = {"rows": [{"line_id": "current", "weight": 0.75}], "as_of_date": date(2026, 9, 17)}
    newest_locked, release_newest, late_lock_started, late_source_checked = (Event() for _ in range(4))
    late_backend_pids = []

    def hold_newest_source_check():
        newest_locked.set()
        assert release_newest.wait(timeout=10)
        return _current_source_key()

    def late_source_check():
        late_source_checked.set()
        return _current_source_key()

    def observe_lock(connection, _cursor, statement, _parameters, _context, _many):
        if current_thread().name.startswith("late-publisher") and "FOR UPDATE" in statement:
            late_backend_pids.append(connection.connection.driver_connection.info.backend_pid)
            late_lock_started.set()

    event.listen(get_engine(), "before_cursor_execute", observe_lock)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="newest-publisher") as newest_pool, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="late-publisher") as late_pool:
            newest = newest_pool.submit(
                publish_workspace_projection, PORTFOLIO_ID, SURFACE, current_key,
                current_source_key=hold_newest_source_check, payload=latest_payload,
            )
            late = None
            try:
                assert newest_locked.wait(timeout=10)
                late = late_pool.submit(
                    publish_workspace_projection, PORTFOLIO_ID, SURFACE,
                    ("older-generation",) if late_result == "older_source" else current_key,
                    current_source_key=late_source_check,
                    payload={"rows": [{"line_id": "obsolete"}]} if late_result == "older_source" else None,
                    error_type=None if late_result == "older_source" else "OperationalError",
                )
                assert late_lock_started.wait(timeout=10)
                _wait_for_database_lock(late_backend_pids[0])
                assert not late_source_checked.is_set()
            finally:
                release_newest.set()
            assert newest.result(timeout=10) is True
            assert late is not None and late.result(timeout=10) is False
    finally:
        event.remove(get_engine(), "before_cursor_execute", observe_lock)

    assert late_source_checked.is_set()
    assert read_workspace_projection(PORTFOLIO_ID, SURFACE, current_key) == jsonable_encoder(latest_payload)
    assert read_workspace_projection(PORTFOLIO_ID, SURFACE, ("older-generation",)) is None
    with get_session_factory()() as session:
        rows = list(session.scalars(select(PortfolioWorkspaceReadModel)))
        assert len(rows) == 1
        assert rows[0].error_type is None


def test_workspace_projection_json_roundtrip_and_portfolio_delete_cascade(postgres_portfolio_env):
    _seed_portfolio()
    source_key = _current_source_key()
    payload = {
        "as_of_date": date(2026, 9, 17),
        "calculated_at": datetime(2026, 9, 17, 12, 30, tzinfo=UTC),
        "rows": [{"label": "现金与证券", "value": -123.25, "coverage": None, "eligible": False}],
        "series": [(date(2026, 9, 16), 0.125), (date(2026, 9, 17), 0.25)],
    }
    assert publish_workspace_projection(
        PORTFOLIO_ID, SURFACE, source_key, current_source_key=_current_source_key, payload=payload,
    )
    assert read_workspace_projection(PORTFOLIO_ID, SURFACE, source_key) == jsonable_encoder(payload)
    with get_session_factory()() as session:
        # Execute the actual database delete, without ORM relationship cleanup.
        session.execute(delete(PortfolioRecordModel).where(PortfolioRecordModel.portfolio_id == PORTFOLIO_ID))
        session.commit()
    with get_session_factory()() as session:
        assert session.get(PortfolioWorkspaceReadModel, (PORTFOLIO_ID, SURFACE)) is None
    assert not publish_workspace_projection(
        PORTFOLIO_ID, SURFACE, source_key, current_source_key=lambda: source_key, payload=payload,
    )
