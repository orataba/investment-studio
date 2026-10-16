"""Exercise the actual migration and release backfill in a disposable database."""

from datetime import UTC, date, datetime
import importlib.util

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text
from investment_studio_instrument_core import build_return_intervals

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.read_models import InstrumentChartReadModel
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.materialization_policy import WATCHLIST_MATERIALIZATION_VERSION
from watchlist_app.services.read_models import RETURN_CHART_WINDOWS, build_sparkline_payload
from watchlist_app.services.calculation_frequency import build_calculation_frequency_context, _market_calendar_sessions

from .test_postgres_instrument_registry_constraints import BACKEND_ROOT, postgres_watchlist_env


pytestmark = pytest.mark.postgresql_integration


@pytest.mark.parametrize("postgres_watchlist_env", ["20260920_0059"], indirect=True)
@pytest.mark.parametrize("confirmed_returns", [False, True])
def test_screener_projection_migration_backfill_and_downgrade_preserve_chart(postgres_watchlist_env, confirmed_returns):
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    spec = importlib.util.spec_from_file_location(
        "pg_screener_release", BACKEND_ROOT / "scripts/refresh_release_watchlists.py",
    )
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    factory = get_session_factory()
    instrument_id = postgres_watchlist_env["instrument_id"]
    days = _market_calendar_sessions("XNYS", date(2024, 1, 2), date(2026, 2, 4))
    points = [{"date": day.isoformat(), "value": 100 + offset / 10} for offset, day in enumerate(days)]
    full_payload = {
        "selected_series": {"quote_basis": "total_return_nav", "return_kind": "total_return"},
        "latest_values": {"total_return": {"date": points[-1]["date"], "value": points[-1]["value"],
                                          "quote_basis": "total_return_nav", "metric_family": "nav"}},
        "date_range": {"start": points[0]["date"], "end": points[-1]["date"]},
        "series": [{"points": points}],
    }
    if confirmed_returns:
        frequency = build_calculation_frequency_context(
            [{**point, "as_of_date": date.fromisoformat(point["date"])} for point in points],
            expected_frequency="daily", market_calendar="XNYS")
        full_payload["return_series"] = {
            "metadata": {**full_payload["selected_series"], "return_series_status": "complete"},
            "wealth_points": points, "intervals": build_return_intervals(points), "frequency": frequency["profile"]}
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
        # This fixture exercises revision 0059, before the NAV projection column.
        session.execute(InstrumentChartReadModel.__table__.insert().values(
            instrument_id=instrument_id, payload_json=full_payload, screener_payload_json=None,
            data_freshness_status=chart.data_freshness_status,
            last_recalculated_at=chart.last_recalculated_at, source_cutoff_at=chart.source_cutoff_at,
            materialization_version=chart.materialization_version))
        session.commit()
        expected_windows = build_sparkline_payload([chart], instrument_ids=[instrument_id],
            selected_fields=list(RETURN_CHART_WINDOWS))[instrument_id]
        assert set(expected_windows) == set(RETURN_CHART_WINDOWS)
        # Backfilling a compact view never upgrades an old display series into
        # verified analytical returns. Current intervals remain usable as-is.
        assert all(window["status"] == ("ready" if confirmed_returns else "unavailable")
                   for window in expected_windows.values())

    columns = [column for column in InstrumentChartReadModel.__table__.c
               if column.name not in {"screener_payload_json", "nav_payload_json"}]
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
        assert projection == {key: full_payload[key] for key in ("latest_values", "date_range")} | {
            "selected_series": (full_payload.get("return_series") or {}).get("metadata"), "sparklines": expected_windows}
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
