"""Registered, active Invested instruments for API tests; never bypass admission."""
from datetime import UTC, datetime

from investment_studio_instrument_core.db_models import Instrument
from watchlist_app.core.settings import get_settings
from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail
from watchlist_app.db.session import get_session_factory


def seed_invested_research(monkeypatch, instrument_ids):
    from .conftest import canonical_quote_policy
    monkeypatch.setattr(get_settings(), "research_automatic_enabled", True)
    with get_session_factory()() as session:
        for iid in instrument_ids:
            detail = session.get(InstrumentDetail, iid)
            assert detail is not None
            if session.get(Instrument, iid) is None:
                session.add(Instrument(instrument_id=iid, instrument_name=detail.instrument_name,
                    instrument_type=detail.instrument_type, currency="USD",
                    exchange_code="XNYS" if detail.instrument_type in {"equity", "etf"} else None,
                    quote_selection_policy_json=canonical_quote_policy(detail.instrument_type),
                    source_settings_json={}, lifecycle_state_json={"status": "active"}))
            session.add(InstrumentAttributeValue(instrument_id=iid, attribute_key="coverage_status",
                value_json="Invested", adopted_at=datetime.now(UTC)))
        session.commit()
