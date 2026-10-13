"""Coverage reactivation keeps its identity under competing refreshes."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from watchlist_app.db.models import InstrumentSummaryReadModel
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.risk_workbench import refresh_risk_cases
from . import test_risk_coverage_recurrence as fixture
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


def test_same_gap_reactivation_is_serialized(postgres_watchlist_env, monkeypatch):
    iid = postgres_watchlist_env["instrument_id"]
    monkeypatch.setattr(fixture, "IID", iid)
    fixture.observe()
    original = fixture.cases()[0]
    fixture.clear()
    with get_session_factory()() as session:
        session.get(InstrumentSummaryReadModel, iid).payload_json = {"freshness": fixture.FRESHNESS}
        session.commit()
    with get_session_factory()() as first, get_session_factory()() as second:
        refresh_risk_cases(first, [iid])
        first.flush()
        second.execute(text("SET LOCAL lock_timeout = '250ms'"))
        with pytest.raises(OperationalError) as error:
            refresh_risk_cases(second, [iid])
        assert error.value.orig.sqlstate == "55P03"
        second.rollback()
        first.commit()
        refresh_risk_cases(second, [iid])
        second.commit()
    [case] = fixture.cases()
    assert case.case_id == original.case_id and case.trigger_active and case.resolved_at is None
    assert [item["action"] for item in case.history_json] == ["triggered", "cleared", "reactivated"]
