"""NAV migration preserves evidence; publication shares the chart transaction."""
from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.read_models import InstrumentChartReadModel
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.materialization_policy import WATCHLIST_MATERIALIZATION_VERSION
from watchlist_app.services.read_models import instrument_quote_payload
from .test_postgres_instrument_registry_constraints import BACKEND_ROOT, postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


def test_nav_projection_upgrade_and_downgrade_preserve_original_chart(postgres_watchlist_env):
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    factory = get_session_factory()
    iid = postgres_watchlist_env["instrument_id"]
    with factory() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
            instrument_name="NAV migration fixture", is_active=True, metadata_json={}))
        session.flush()
        session.add(InstrumentChartReadModel(instrument_id=iid, payload_json={"generation": "retained"},
            screener_payload_json={"sparklines": {}}, data_freshness_status="stale",
            source_cutoff_at=datetime(2026, 10, 1, tzinfo=UTC),
            last_recalculated_at=datetime(2026, 10, 2, tzinfo=UTC), materialization_version="retained-version"))
        session.commit()
    columns = [c for c in InstrumentChartReadModel.__table__.c if c.name != "nav_payload_json"]
    def original():
        with factory() as session:
            return session.execute(select(*columns).where(InstrumentChartReadModel.instrument_id == iid)).one()
    expected = original()
    get_engine().dispose()
    command.downgrade(config, "20261014_0074")
    assert original() == expected
    command.upgrade(config, "20261016_0075")
    assert original() == expected
    command.upgrade(config, "head")
    with factory() as session:
        assert session.scalar(text("SELECT nav_payload_json FROM watchlist.instrument_chart_read_model WHERE instrument_id=:iid"), {"iid": iid}) is None
        CanonicalRecalcService().execute_recalc(session, instrument_id=iid, job_type="all", trigger_type="test",
            trigger_ref_type=None, trigger_ref_id=None, commit=True)
        chart = session.get(InstrumentChartReadModel, iid, populate_existing=True)
        assert chart.materialization_version == WATCHLIST_MATERIALIZATION_VERSION
        assert chart.nav_payload_json is not None
        assert chart.nav_payload_json["return_series"] == chart.payload_json["return_series"]
    recalculated = original()
    get_engine().dispose()
    command.downgrade(config, "20261014_0074")
    assert original() == recalculated


def test_postgres_display_projection_preserves_null_and_avoids_return_history(postgres_watchlist_env):
    factory, iid = get_session_factory(), postgres_watchlist_env["instrument_id"]
    source = {"selected_series": None, "date_range": None, "currency": None, "series": [],
              "return_series": {"audit": ["retained"] * 100}}
    with factory() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
            instrument_name="Chart projection fixture", is_active=True, metadata_json={}))
        session.flush()
        session.add(InstrumentChartReadModel(instrument_id=iid, payload_json=source, data_freshness_status="fresh"))
        session.commit()
        repo = SQLAlchemyReadModelRepository()
        display = repo.get_chart_projection(session, iid, projection="display")
        assert display == {key: value for key, value in source.items() if key != "return_series"}
        assert instrument_quote_payload(repo.get_chart_projection(session, iid, projection="quote")) == instrument_quote_payload(source)
        assert session.get(InstrumentChartReadModel, iid).payload_json == source
