from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from .test_postgres_instrument_registry_constraints import postgres_watchlist_env
from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.db.models.read_models import InstrumentChartReadModel
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.recalc import queue_configuration_recalculation
from watchlist_app.services.read_model_freshness import calculation_input_read, recalculation_freshness_overrides
from watchlist_app.services.research_errors import ResearchInputUnavailable

pytestmark = pytest.mark.postgresql_integration


@pytest.mark.parametrize("claim_committed", [True, False])
def test_concurrent_edits_coalesce_without_waiting_for_worker(postgres_watchlist_env, claim_committed):
    iid = postgres_watchlist_env["instrument_id"]
    factory, repo, barrier = get_session_factory(), SQLAlchemyRecalcJobRepository(), Barrier(2)
    with factory() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
            instrument_name="Configuration concurrency", is_active=True, metadata_json={}))
        session.flush()
        old = repo.create(session, recalc_job_id="old-worker", instrument_id=iid, job_type="all",
            trigger_type="test", trigger_ref_type=None, trigger_ref_id=None, job_status="queued",
            priority=100, dedupe_key=f"all:{iid}", payload_json={})
        if claim_committed:
            repo.mark_running(session, old)
        session.commit()
    def edit():
        with factory() as session:
            barrier.wait(timeout=5)
            result = queue_configuration_recalculation(session, instrument_id=iid,
                trigger_type="instrument_settings_update", trigger_ref_type="settings", trigger_ref_id=None)
            session.commit()
            return result["recalc_job_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        with factory() as worker:
            assert repo.acquire_instrument_lock(worker, instrument_id=iid, wait=False)
            old = worker.scalar(select(RecalcJob).where(RecalcJob.recalc_job_id == "old-worker").with_for_update())
            if not claim_committed:
                # Another connection still observes queued, but must skip this
                # row while its worker claims it, not wait for a full calculation.
                repo.mark_running(worker, old)
            futures = [pool.submit(edit) for _ in range(2)]
            try:
                ids = [future.result(timeout=5) for future in futures]
                assert ids[0] == ids[1] != "old-worker"
            finally:
                worker.rollback()
    with factory() as session:
        successors = list(session.scalars(select(RecalcJob).where(RecalcJob.instrument_id == iid,
            RecalcJob.recalc_job_id != "old-worker", RecalcJob.job_status == "queued")))
        assert len(successors) == 1
        assert recalculation_freshness_overrides(session, [iid], configuration_only=True)[iid]["data_freshness_status"] == "pending_recalc"


def test_completed_configuration_during_read_is_rejected_without_blocking_writer(postgres_watchlist_env):
    """A READ COMMITTED reader can see old rows and a new completed receipt."""
    iid = postgres_watchlist_env["instrument_id"]
    factory, repo = get_session_factory(), SQLAlchemyRecalcJobRepository()
    with factory() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
            instrument_name="Read consistency", is_active=True, metadata_json={}))
        session.flush()
        session.add(InstrumentChartReadModel(instrument_id=iid, payload_json={"generation": "old"},
            data_freshness_status="fresh"))
        session.commit()

    def publish_successor():
        with factory() as writer:
            queued = queue_configuration_recalculation(writer, instrument_id=iid,
                trigger_type="instrument_settings_update", trigger_ref_type="settings", trigger_ref_id=None)
            record = writer.get(RecalcJob, queued["recalc_job_id"])
            repo.mark_running(writer, record)
            writer.get(InstrumentChartReadModel, iid).payload_json = {"generation": "new"}
            assert repo.mark_completed(writer, record, lease_token=record.lease_token,
                payload_json={"published": True})
            writer.commit()
            return record.recalc_job_id

    with ThreadPoolExecutor(max_workers=1) as pool, factory() as reader:
        assert reader.connection().get_isolation_level() == "READ COMMITTED"
        with pytest.raises(ResearchInputUnavailable, match="读取期间"):
            with calculation_input_read(reader, [iid]):
                retained = reader.get(InstrumentChartReadModel, iid)
                assert retained.payload_json["generation"] == "old"
                # The independent writer finishes while the read transaction
                # stays open: this guard must not hold a configuration lock.
                job_id = pool.submit(publish_successor).result(timeout=5)
                assert reader.scalar(select(InstrumentChartReadModel.payload_json)
                    .where(InstrumentChartReadModel.instrument_id == iid))["generation"] == "new"
                assert retained.payload_json["generation"] == "old"
        reader.rollback()
        with calculation_input_read(reader, [iid]):
            assert reader.get(InstrumentChartReadModel, iid, populate_existing=True).payload_json["generation"] == "new"
            completed = reader.get(RecalcJob, job_id)
            assert completed.job_status == "completed"
            assert completed.payload_json["configuration_changed"] is True
