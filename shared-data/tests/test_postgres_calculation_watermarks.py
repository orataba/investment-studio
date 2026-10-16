"""Configuration generations must retain commit ordering across instruments."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
import os
from threading import Event, current_thread
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core import instrument_store
from investment_studio_instrument_core.db_models import Instrument, InstrumentRegistryBase, RegistryMetadata


pytestmark = pytest.mark.postgresql_integration


@pytest.fixture()
def registry_factory():
    target = os.getenv("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not target:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")
    name = f"investment_studio_config_clock_{uuid4().hex[:8]}"
    url = make_url(target)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    engine = create_engine(url.set(database=name))
    try:
        InstrumentRegistryBase.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(RegistryMetadata(registry_key="shared", registry_name="Test registry"))
            for instrument_id in ("a", "b"):
                session.add(Instrument(
                    instrument_id=instrument_id, instrument_name=instrument_id,
                    instrument_type="equity", currency="USD", exchange_code="XNYS",
                    quote_selection_policy_json={role: ["adjusted_close"] if role == "total_return" else ["close"] for role in
                                                 ("valuation", "trading", "chart", "reference", "total_return")},
                    source_settings_json={}, refresh_status_json={}, lifecycle_state_json={},
                    calculation_inputs_updated_at="2099-01-01T00:00:00.000000Z",
                ))
            session.commit()
        yield factory
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def test_configuration_writers_allocate_under_one_lock_until_commit(registry_factory, monkeypatch):
    allocated, second_started, second_finished, release = (Event() for _ in range(4))
    original = instrument_store._next_calculation_input_watermark
    allocations = {}
    monkeypatch.setattr(instrument_store, "_utcnow_iso", lambda: "2099-01-01T00:00:01.000000Z")

    def reserve(session):
        name = current_thread().name
        if name.endswith("_1"):
            second_started.set()
        result = original(session)
        allocations[name] = result
        if name.endswith("_0"):
            allocated.set()
            assert release.wait(5), "The first writer must be released by the test."
        return result

    monkeypatch.setattr(instrument_store, "_next_calculation_input_watermark", reserve)

    def update(instrument_id):
        with registry_factory() as session:
            policy = deepcopy(session.get(Instrument, instrument_id).quote_selection_policy_json)
        policy["valuation"] = ["last", "close"]
        result = instrument_store.upsert_quote_selection_policy(
            registry_factory, instrument_id=instrument_id, quote_selection_policy=policy,
        )
        if instrument_id == "b":
            second_finished.set()
        return result["calculation_inputs_updated_at"]

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="configuration") as executor:
        first = executor.submit(update, "a")
        try:
            assert allocated.wait(5)
            second = executor.submit(update, "b")
            assert second_started.wait(5)
            assert not second_finished.wait(.1)
        finally:
            release.set()
        first_watermark, second_watermark = first.result(timeout=5), second.result(timeout=5)
    assert second_watermark > first_watermark
    assert len(set(allocations.values())) == 2
    with registry_factory() as session:
        assert session.get(RegistryMetadata, "shared").market_data_updated_at is None


def test_configuration_and_market_writer_keep_instrument_then_registry_lock_order(registry_factory, monkeypatch):
    market_holds_instrument, configuration_started, configuration_allocated, release = (Event() for _ in range(4))
    market_clock = instrument_store._next_market_data_watermark
    configuration_clock = instrument_store._next_calculation_input_watermark

    def pause_market(session):
        market_holds_instrument.set()
        assert release.wait(5)
        return market_clock(session)

    def allocate_configuration(session):
        result = configuration_clock(session)
        configuration_allocated.set()
        return result

    monkeypatch.setattr(instrument_store, "_next_market_data_watermark", pause_market)
    monkeypatch.setattr(instrument_store, "_next_calculation_input_watermark", allocate_configuration)

    def update_configuration():
        configuration_started.set()
        return instrument_store.upsert_quote_selection_policy(
            registry_factory, instrument_id="a",
            quote_selection_policy={role: ["adjusted_close"] if role == "total_return" else ["last", "close"] for role in
                                    ("valuation", "trading", "chart", "reference", "total_return")},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        market = executor.submit(
            instrument_store.upsert_market_data, registry_factory, instrument_id="a",
            metric_family="price", quote_basis="close", as_of_date=date(2026, 9, 8),
            value="100", currency="USD", provider="test", status="complete",
        )
        try:
            assert market_holds_instrument.wait(5)
            configuration = executor.submit(update_configuration)
            assert configuration_started.wait(5)
            assert not configuration_allocated.wait(.1)
        finally:
            release.set()
        assert market.result(timeout=5)["market_data_updated_at"] is not None
        assert configuration.result(timeout=5)["quote_selection_policy"]["valuation"] == ["last", "close"]


def test_calculation_read_cannot_mix_nav_publications(registry_factory):
    record = instrument_store.create_instrument(
        registry_factory, instrument_name="Publication snapshot fund",
        instrument_type="public_fund", currency="USD",
        identifiers=[{"identifier_type": "ticker", "identifier_value": "SNAPSHOT", "is_primary": True}],
    )
    iid = record["instrument_id"]

    def publish(value, fingerprint, watermark):
        return instrument_store.publish_fund_nav_history(
            registry_factory, instrument_id=iid,
            rows=[{"as_of_date": "2026-07-15", "nav": value, "nav_status": "complete",
                   "nav_source_provider": "test", "currency": "USD",
                   "nav_lineage": {"kind": "provider_explicit", "evidence": {"source_field": "unit_nav"}}}],
            projection_run={
                "source_observation_fingerprint": fingerprint * 64,
                "projection_kind": "event_derived", "projection_status": "unavailable",
                "method_version": "dividend_reinvestment/v2", "anchor_date": None,
                "source_provider": "test", "created_by": "pytest",
                "evidence": {"unavailable_reason": "reinvestment_evidence_not_observed",
                             "source_row_count": 1, "published_total_return_dates": [],
                             "missing_total_return_dates": ["2026-07-15"]},
            },
            current_fund_nav_event_ids=[], current_fund_nav_reinvestment_evidence_ids=[],
            expected_market_data_updated_at=watermark, refresh_status="imported",
            updated_by="pytest", message="Snapshot isolation regression",
        )

    first = publish("1.25", "a", record["market_data_updated_at"])
    expected = instrument_store.get_instrument_calculation_inputs(registry_factory, [iid])[iid]
    published = []

    def publish_between_identity_and_history(_conn, _cursor, statement, _parameters, _context, _many):
        if not published and "FROM instrument_market_data" in statement:
            # The reader has captured its identity/watermarks. A separate
            # connection commits a new NAV generation before its history read.
            published.append(True)
            published[0] = publish("1.50", "b", first["market_data_updated_at"])

    engine = registry_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", publish_between_identity_and_history)
    try:
        actual = instrument_store.get_instrument_calculation_inputs(registry_factory, [iid])[iid]
    finally:
        event.remove(engine, "before_cursor_execute", publish_between_identity_and_history)
    assert len(published) == 1
    assert actual == expected
    current = instrument_store.get_instrument_calculation_inputs(registry_factory, [iid])[iid]
    assert current["market_data"][0]["value"] == "1.50"
    assert current["market_data_updated_at"] == published[0]["market_data_updated_at"]
    assert current["current_fund_nav_projection_run_id"] == published[0]["published_projection_run_id"]
    assert current["current_fund_nav_projection_run_id"] != expected["current_fund_nav_projection_run_id"]
    # The stronger isolation belongs only to this read transaction; returning
    # its connection to the pool must not change ordinary writer isolation.
    with registry_factory() as session:
        assert session.connection().get_isolation_level() == "READ COMMITTED"
