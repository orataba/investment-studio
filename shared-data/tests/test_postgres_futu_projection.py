"""Exercise Futu publication/retraction atomically on disposable PostgreSQL."""
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core import instrument_store as core
from investment_studio_instrument_core.db_models import InstrumentRegistryBase
from studio_data.services.futu import eod
from test_futu_history_projection import (
    retained_evidence,
    test_action_revision_and_withdrawal_rebuild_old_returns_factors_and_watermarks,
    test_withdrawal_and_factor_clear_roll_back_with_canonical_transaction,
    test_source_change_during_acquisition_cannot_publish,
    test_source_update_cannot_overwrite_an_identity_bound_by_another_registration,
    test_new_ohlc_inconsistency_conflicting_with_existing_bar_aborts_all_publication,
    test_actions_from_failed_price_capture_cannot_revise_retained_returns,
)

pytestmark = pytest.mark.postgresql_integration


@pytest.fixture
def registry(monkeypatch):
    target = os.getenv("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not target:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")
    name = f"investment_studio_futu_test_{uuid4().hex[:8]}"
    url = make_url(target)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    engine = create_engine(url.set(database=name))
    try:
        InstrumentRegistryBase.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        record = core.create_instrument(sessions, instrument_name="Verified Hong Kong ETF", instrument_type="etf",
            currency="HKD", exchange_code="XHKG", identifiers=[
                {"identifier_type": "exchange_ticker", "identifier_value": "2822.HK", "is_primary": True},
                {"identifier_type": "provider_symbol", "identifier_value": "futu:HK.02822", "is_primary": False}])
        iid = record["instrument_id"]
        core.upsert_source_settings(sessions, instrument_id=iid, source_mode="api", source_email="",
            source_location="Futu quotes", source_api_profile="futu", source_email_rules=[],
            expected_frequency="daily", market_calendar="XHKG", return_semantics="price_return",
            source_provider_currency="HKD", source_provider_security_id="100", source_price_multiplier=1,
            history_start_date=date(2026, 9, 1))
        monkeypatch.setattr(eod, "get_session_factory", lambda: sessions)
        monkeypatch.setattr(eod, "get_instrument", lambda identifier: core.get_instrument(sessions, identifier))
        monkeypatch.setattr(eod, "get_price_bars", lambda **kwargs: core.get_price_bars(sessions, **kwargs))
        monkeypatch.setattr(eod, "update_refresh_status", lambda **kwargs: core.update_refresh_status(sessions, **kwargs))
        yield sessions, iid
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()
