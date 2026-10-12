"""Risk refresh serialization must not block durable market-data enqueue."""
import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import OperationalError

from watchlist_app.api.contracts import RecalcBulkRequest
from watchlist_app.api.routes.recalc import enqueue_bulk_recalc
from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.risk_workbench import refresh_risk_cases
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


def _seed_instrument(instrument_id):
    with get_session_factory()() as session:
        session.add(InstrumentDetail(
            instrument_id=instrument_id, instrument_type="public_fund",
            detail_view_type="public_fund", instrument_name="Risk lock test",
            metadata_json={},
        ))
        session.commit()


def test_risk_refresh_allows_bulk_recalc_foreign_key_insert(postgres_watchlist_env):
    iid = postgres_watchlist_env["instrument_id"]
    _seed_instrument(iid)
    with get_session_factory()() as refreshing, get_session_factory()() as enqueueing:
        refresh_risk_cases(refreshing, [iid])
        refreshing.flush()
        # Keep the real refresh transaction open while the HTTP route commits
        # its queue entry. No worker or market/provider call is involved.
        enqueueing.execute(text("SET LOCAL lock_timeout = '250ms'"))
        result = enqueue_bulk_recalc(RecalcBulkRequest(
            instrument_ids=[iid], job_type="all",
            trigger_type="market_data_refresh", trigger_ref_type="shared_market_data",
        ), enqueueing)
        assert result["accepted_count"] == 1
        assert result["enqueued_instrument_ids"] == [iid]
        with get_session_factory()() as observer:
            job = observer.scalar(select(RecalcJob).where(RecalcJob.instrument_id == iid))
            assert job is not None and job.job_status == "queued"
        refreshing.commit()


@pytest.mark.parametrize("competing_write", ["risk_refresh", "primary_key", "delete"])
def test_risk_refresh_still_serializes_writes_and_protects_identity(
    postgres_watchlist_env, competing_write,
):
    iid = postgres_watchlist_env["instrument_id"]
    _seed_instrument(iid)
    with get_session_factory()() as first, get_session_factory()() as second:
        refresh_risk_cases(first, [iid])
        first.flush()
        second.execute(text("SET LOCAL lock_timeout = '250ms'"))
        with pytest.raises(OperationalError) as error:
            if competing_write == "risk_refresh":
                refresh_risk_cases(second, [iid])
            elif competing_write == "primary_key":
                second.execute(update(InstrumentDetail).where(
                    InstrumentDetail.instrument_id == iid,
                ).values(instrument_id=f"{iid}-renamed"))
            else:
                second.execute(delete(InstrumentDetail).where(
                    InstrumentDetail.instrument_id == iid,
                ))
        assert error.value.orig.sqlstate == "55P03"  # lock_not_available
        second.rollback()
        first.commit()
        # A later refresh observes the committed case and does not duplicate it.
        refresh_risk_cases(second, [iid])
        second.commit()
        assert second.get(InstrumentDetail, iid) is not None
        cases = list(second.scalars(select(RiskCase).where(RiskCase.instrument_id == iid)))
        assert len(cases) == 1 and cases[0].signal == "data_coverage"
