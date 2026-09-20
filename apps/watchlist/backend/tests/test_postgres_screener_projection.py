"""Exercise the actual migration and release backfill in a disposable database."""

from datetime import UTC, date, datetime, timedelta
import importlib.util

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.read_models import InstrumentChartReadModel
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.materialization_policy import WATCHLIST_MATERIALIZATION_VERSION
from watchlist_app.services.read_models import RETURN_CHART_WINDOWS, build_sparkline_payload

from .test_postgres_instrument_registry_constraints import BACKEND_ROOT, postgres_watchlist_env


pytestmark = pytest.mark.postgresql_integration


def test_screener_projection_migration_backfill_and_downgrade_preserve_chart(postgres_watchlist_env):
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    spec = importlib.util.spec_from_file_location(
        "pg_screener_release", BACKEND_ROOT / "scripts/refresh_release_watchlists.py",
    )
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    factory = get_session_factory()
    instrument_id = postgres_watchlist_env["instrument_id"]
    points = [{"date": (date(2025, 1, 1) + timedelta(days=offset)).isoformat(), "value": 100 + offset / 10}
              for offset in range(400)]
    full_payload = {
        "selected_series": {"quote_basis": "total_return_nav", "return_kind": "total_return"},
        "latest_values": {"total_return": {"date": points[-1]["date"], "value": points[-1]["value"],
                                          "quote_basis": "total_return_nav", "metric_family": "nav"}},
        "date_range": {"start": points[0]["date"], "end": points[-1]["date"]},
        "series": [{"points": points}],
    }
    with factory() as session:
        # Archived charts must be ready too, without making the asset active.
        session.add(InstrumentDetail(instrument_id=instrument_id, instrument_type="public_fund",
            detail_view_type="public_fund", instrument_name="Archived projection fixture",
            is_active=False, metadata_json={}))
        session.flush()
        chart = InstrumentChartReadModel(instrument_id=instrument_id, payload_json=full_payload,
            screener_payload_json=None, data_freshness_status="stale",
            last_recalculated_at=datetime(2026, 2, 5, 1, 2, 3, tzinfo=UTC),
            source_cutoff_at=datetime(2026, 2, 4, 5, 6, 7, tzinfo=UTC),
            materialization_version=WATCHLIST_MATERIALIZATION_VERSION)
        session.add(chart)
        session.commit()
        expected_windows = build_sparkline_payload([chart], instrument_ids=[instrument_id],
            selected_fields=list(RETURN_CHART_WINDOWS))[instrument_id]
        assert set(expected_windows) == set(RETURN_CHART_WINDOWS)

    columns = [column for column in InstrumentChartReadModel.__table__.c if column.name != "screener_payload_json"]
    def original_rows():
        with factory() as session:
            return session.execute(select(*columns).order_by(InstrumentChartReadModel.instrument_id)).all()

    before = original_rows()
    get_engine().dispose()
    command.downgrade(config, "20260914_0058")
    assert original_rows() == before
    command.upgrade(config, "20260920_0059")
    with factory() as session:
        assert session.scalar(text("SELECT version_num FROM watchlist.alembic_version")) == "20260920_0059"
        assert session.scalar(text("SELECT count(*) FROM watchlist.instrument_chart_read_model "
                                   "WHERE screener_payload_json IS NULL")) == len(before)
        # PostgreSQL JSON stays JSON; the rejected JSONB experiment is not a migration.
        assert session.execute(text("SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='watchlist' AND table_name='instrument_chart_read_model' "
            "AND column_name IN ('payload_json', 'screener_payload_json') ORDER BY column_name")).all() == [
                ("payload_json", "json"), ("screener_payload_json", "json")]
    with factory() as session, session.begin():
        assert release._backfill_screener_projections(session) == len(before)
    assert original_rows() == before
    with factory() as session:
        assert session.scalar(text("SELECT count(*) FROM watchlist.instrument_chart_read_model "
                                   "WHERE screener_payload_json IS NULL")) == 0
        projection = session.get(InstrumentChartReadModel, instrument_id).screener_payload_json
        assert projection == {key: full_payload[key] for key in ("selected_series", "latest_values", "date_range")} | {
            "sparklines": expected_windows}
        assert session.get(InstrumentDetail, instrument_id).is_active is False
    with factory() as session, session.begin():
        assert release._backfill_screener_projections(session) == 0
    assert original_rows() == before
    get_engine().dispose()
    command.downgrade(config, "20260914_0058")
    assert original_rows() == before
    with factory() as session:
        assert session.scalar(text("SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema='watchlist' AND table_name='instrument_chart_read_model' "
            "AND column_name='screener_payload_json'")) == 0
