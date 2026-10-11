"""Futu raw facts and derived returns preserve source ownership and evidence."""
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
import json
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core import instrument_store as core
from investment_studio_instrument_core.db_models import InstrumentRegistryBase
from studio_data.services.futu import eod


NOW = datetime(2026, 9, 8, 10, tzinfo=UTC)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'registry.db'}")
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
        source_provider_currency="HKD", source_provider_security_id="100", source_price_multiplier=1, history_start_date=date(2026, 9, 1))
    monkeypatch.setattr(eod, "get_session_factory", lambda: sessions)
    monkeypatch.setattr(eod, "get_instrument", lambda identifier: core.get_instrument(sessions, identifier))
    monkeypatch.setattr(eod, "get_price_bars", lambda **kwargs: core.get_price_bars(sessions, **kwargs))
    monkeypatch.setattr(eod, "update_refresh_status", lambda **kwargs: core.update_refresh_status(sessions, **kwargs))
    yield sessions, iid
    engine.dispose()


def history_store(*, day="2026-09-08", **overrides):
    row = dict(symbol="HK.02822", date=day, stock_id=100, open=10, high=12, low=9, close=11,
               volume=1000, volume_unit="shares", source=eod.PROVIDER, ohlc_adjustment="unadjusted",
               adjusted_close_adjustment="unavailable", **overrides)
    store = Mock()
    store.latest.return_value = {"rows": [dict(symbol="HK.02822", stock_id=100, stock_type="ETF", listing_date="2012-01-01")]}
    store.latest.side_effect = lambda dataset, **kwargs: store.latest.return_value if dataset == "futu_security_directory" else {"rows": []}
    store.query.return_value = {"rows": [row], "total": 1}
    return store


def test_missing_action_evidence_keeps_atomic_raw_close_and_idempotent_partial_status(registry, monkeypatch):
    sessions, iid = registry
    from studio_market.futu import collect

    collector = Mock(side_effect=AssertionError("Injected-store replay must not acquire live prices."))
    monkeypatch.setattr(collect, "collect_history", collector)
    source = history_store()
    result = eod.refresh_futu_eod(iid, store=source, now=NOW)
    assert result["refresh_status"]["status"] == "partial"
    assert result["quote_selection_policy"] == eod.CLOSE_POLICY
    detail = core.get_instrument(sessions, iid)
    assert {point["quote_basis"] for point in detail["market_data"]} == {"close"}
    assert detail["market_data"][0]["provider"] == eod.PROVIDER
    assert core.get_price_bars(sessions, instrument_id=iid)[0]["adjustment_factor"] is None
    watermark = result["market_data_updated_at"]
    result = eod.refresh_futu_eod(iid, store=source, now=NOW)
    assert result["refresh_status"]["status"] == "partial"
    assert result["market_data_updated_at"] == watermark
    source.close.assert_not_called()
    collector.assert_not_called()


@pytest.mark.parametrize("basis", ["close", "adjusted_close"])
def test_existing_other_source_is_rejected_before_network(registry, monkeypatch, basis):
    sessions, iid = registry
    core.upsert_market_data(sessions, instrument_id=iid, metric_family="price", quote_basis=basis,
        as_of_date=date(2026, 9, 7), value="10", currency="HKD", provider="fmp:fixture", status="complete")
    construct = Mock()
    monkeypatch.setattr(eod, "NumericStore", construct)
    with pytest.raises(eod.FutuRefreshError, match="separate reviewed source migration"):
        eod.refresh_futu_eod(iid, now=NOW)
    construct.assert_not_called()


def test_missing_closed_session_remains_actionable_in_registered_refresh_status(registry, monkeypatch):
    from studio_market.futu import collect
    sessions, iid = registry
    before = core.get_instrument(sessions, iid)
    source = Mock()
    monkeypatch.setattr(eod, "NumericStore", lambda *_: source)
    monkeypatch.setattr(eod.MarketSettings, "from_environment", lambda: Mock())
    collector = Mock(side_effect=collect.FutuHistoryError(
        "Futu is missing 1 closed trading session(s): 2026-09-08. No price batch was published."))
    monkeypatch.setattr(collect, "collect_history", collector)
    with pytest.raises(eod.FutuRefreshError, match="2026-09-08"):
        eod.refresh_futu_eod(iid, now=NOW)
    after = core.get_instrument(sessions, iid)
    assert after["refresh_status"]["status"] == "failed"
    assert "2026-09-08" in after["refresh_status"]["message"]
    assert after["market_data"] == []
    assert after["market_data_updated_at"] == before["market_data_updated_at"]
    assert collector.call_args.kwargs["now"] == NOW


def test_invalid_ohlc_rolls_back_prices_policy_and_watermarks(registry):
    sessions, iid = registry
    before = core.get_instrument(sessions, iid)
    source = history_store()
    source.query.return_value["rows"][0]["high"] = 8
    with pytest.raises(eod.FutuRefreshError):
        eod.refresh_futu_eod(iid, store=source, now=NOW)
    after = core.get_instrument(sessions, iid)
    assert after["market_data"] == []
    assert after["quote_selection_policy"] == before["quote_selection_policy"]
    assert after["market_data_updated_at"] == before["market_data_updated_at"]
    assert core.get_price_bars(sessions, instrument_id=iid) == []
    assert after["refresh_status"]["status"] == "failed"


@pytest.mark.parametrize("reported_quality", ["valid", "inconsistent"])
def test_inconsistent_ohlc_retains_closes_and_only_publishes_valid_bars(registry, reported_quality):
    sessions, iid = registry
    source = history_store()
    normal = {**source.query.return_value["rows"][0], "date": "2026-09-07", "ohlc_quality": "valid"}
    inconsistent = {**source.query.return_value["rows"][0], "open": 14.65, "low": 14.67,
                    "high": 14.93, "close": 14.86, "ohlc_quality": reported_quality}
    source.query.return_value = {"rows": [normal, inconsistent], "total": 2}
    result = eod.refresh_futu_eod(iid, store=source, now=NOW)
    assert result["refresh_status"]["status"] == "partial"
    assert "Omitted 1 OHLCV bars" in result["refresh_status"]["message"]
    assert "actual retained range 2026-09-07 to 2026-09-08" in result["refresh_status"]["message"]
    detail = core.get_instrument(sessions, iid)
    assert {point["as_of_date"]: str(point["value"]) for point in detail["market_data"]} == {
        "2026-09-07": "11", "2026-09-08": "14.86",
    }
    assert [bar["date"] for bar in core.get_price_bars(sessions, instrument_id=iid)] == ["2026-09-07"]
    assert inconsistent["open"] == 14.65
    assert eod.refresh_futu_eod(iid, store=source, now=NOW)["refresh_status"]["status"] == "partial"

    # A later provider correction may restore the missing bar with its actual
    # revised values; the projection never manufactures an OHLC range.
    source.query.return_value["rows"][1] = {**inconsistent, "open": 14.7, "ohlc_quality": "valid"}
    assert eod.refresh_futu_eod(iid, store=source, now=NOW)["refresh_status"]["status"] == "partial"
    assert len(core.get_price_bars(sessions, instrument_id=iid)) == 2


@pytest.mark.parametrize("close", [8, 13])
def test_contradictory_retained_close_cannot_publish_price_or_total_return(registry, close):
    sessions, iid = registry
    source = history_store()
    source.query.return_value["rows"][0]["close"] = close
    source.query.return_value["rows"][0]["ohlc_quality"] = "valid"
    before = core.get_instrument(sessions, iid)
    with pytest.raises(eod.FutuRefreshError, match="close conflicts"):
        eod.refresh_futu_eod(iid, store=source, now=NOW)
    after = core.get_instrument(sessions, iid)
    assert after["market_data"] == before["market_data"] == []
    assert after["refresh_status"]["status"] == "failed"
    assert after["market_data_updated_at"] == before["market_data_updated_at"]
    assert core.get_price_bars(sessions, instrument_id=iid) == []
    source.ingest.assert_not_called()


def test_new_ohlc_inconsistency_conflicting_with_existing_bar_aborts_all_publication(registry):
    sessions, iid = registry
    source = history_store()
    eod.refresh_futu_eod(iid, store=source, now=NOW)
    before = core.get_instrument(sessions, iid)
    before_bars = core.get_price_bars(sessions, instrument_id=iid)
    existing = source.query.return_value["rows"][0]
    source.query.return_value = {"rows": [
        {**existing, "date": "2026-09-07"},
        {**existing, "open": 8, "close": 11.5, "ohlc_quality": "inconsistent"},
    ], "total": 2}
    with pytest.raises(eod.FutuRefreshError, match="entire projection was aborted"):
        eod.refresh_futu_eod(iid, store=source, now=NOW)
    after = core.get_instrument(sessions, iid)
    assert after["refresh_status"]["status"] == "failed"
    assert after["market_data"] == before["market_data"]
    assert after["market_data_updated_at"] == before["market_data_updated_at"]
    assert core.get_price_bars(sessions, instrument_id=iid) == before_bars


@pytest.mark.parametrize("stage", ["settings", "store", "collection"])
def test_acquisition_failure_is_safe_failed_and_closes_only_owned_store(registry, monkeypatch, stage):
    sessions, iid = registry
    from studio_market.futu import collect

    source = history_store()
    settings = Mock()
    construct = Mock(return_value=source)
    read_settings = Mock(return_value=settings)
    collector = Mock(side_effect=RuntimeError("credential-value-must-not-appear"))
    if stage == "settings":
        read_settings.side_effect = ValueError("credential-value-must-not-appear")
    elif stage == "store":
        construct.side_effect = RuntimeError("credential-value-must-not-appear")
    monkeypatch.setattr(eod.MarketSettings, "from_environment", read_settings)
    monkeypatch.setattr(eod, "NumericStore", construct)
    monkeypatch.setattr(collect, "collect_history", collector)
    with pytest.raises(eod.FutuRefreshError) as error:
        eod.refresh_futu_eod(iid, now=NOW)
    assert "credential-value-must-not-appear" not in str(error.value)
    result = core.get_instrument(sessions, iid)
    assert result["refresh_status"]["status"] == "failed"
    assert result["refresh_status"]["message"] == str(error.value)
    assert result["market_data"] == []
    if stage == "collection":
        source.close.assert_called_once()
    else:
        source.close.assert_not_called()
        collector.assert_not_called()


@pytest.mark.parametrize("defect", ["truncated", "wrong_type", "changed_identity", "adjusted"])
def test_unverified_source_history_never_publishes(registry, defect):
    sessions, iid = registry
    source = history_store()
    if defect == "truncated":
        source.query.return_value["total"] = 2
    elif defect == "wrong_type":
        source.latest.return_value["rows"][0]["stock_type"] = "STOCK"
    elif defect == "changed_identity":
        source.query.return_value["rows"][0]["stock_id"] = 101
    else:
        source.query.return_value["rows"][0]["adjusted_close"] = 11
    with pytest.raises(eod.FutuRefreshError):
        eod.refresh_futu_eod(iid, store=source, now=NOW)
    assert core.get_instrument(sessions, iid)["market_data"] == []


def test_missing_latest_price_is_partial_and_holidays_use_exchange_calendar(registry):
    _, iid = registry
    result = eod.refresh_futu_eod(iid, store=history_store(day="2026-09-07"), now=NOW)
    assert result["refresh_status"]["status"] == "partial"
    assert "2026-09-08" in result["refresh_status"]["message"]
    instrument = eod.get_instrument(iid)
    assert eod._expected_session(instrument, datetime(2026, 10, 1, 10, tzinfo=UTC)) == date(2026, 9, 30)
    assert eod._expected_session(instrument, datetime(2023, 9, 8, 10, tzinfo=UTC)) == date(2023, 9, 7)


def test_source_change_during_acquisition_cannot_publish(registry):
    sessions, iid = registry
    source = history_store()
    original = source.query.return_value
    def query(*args, **kwargs):
        core.upsert_source_settings(sessions, instrument_id=iid, source_mode="api", source_email=None,
            source_location=None, source_api_profile="fmp", source_email_rules=None)
        return original
    source.query.side_effect = query
    with pytest.raises(eod.FutuRefreshError, match="explicitly configured"):
        eod.refresh_futu_eod(iid, store=source, now=NOW)
    assert core.get_instrument(sessions, iid)["market_data"] == []


def test_config_requires_explicit_currency_and_history_start(registry):
    _, iid = registry
    original = eod.get_instrument(iid)
    for field in ("source_provider_currency", "source_provider_security_id", "history_start_date"):
        instrument = deepcopy(original)
        instrument["source_settings"].pop(field)
        with pytest.raises(eod.FutuRefreshError):
            eod.validate_futu_configuration(instrument)


def test_configured_and_security_refresh_use_futu_without_fmp(registry, monkeypatch):
    _, iid = registry
    from studio_data.services import market_data_ops
    from studio_data.services import futu
    from studio_data.services.etfs import service
    instrument = eod.get_instrument(iid)
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda _: instrument)
    monkeypatch.setattr(service, "get_instrument", lambda _: instrument)
    refresh = Mock(return_value=instrument)
    monkeypatch.setattr(futu, "refresh_futu_eod", refresh)
    assert market_data_ops.refresh_market_data(instrument_id=iid, updated_by="test") == instrument
    assert service.refresh_etf_eod(iid) == instrument
    assert refresh.call_count == 2
    with pytest.raises(eod.FutuRefreshError, match="requested source differs"):
        market_data_ops.refresh_market_data(instrument_id=iid, updated_by="test", source="fmp")
    assert market_data_ops._matches_batch_source(instrument, "futu")
    assert not market_data_ops._matches_batch_source(instrument, "fmp")


def test_configured_collection_failure_is_recorded_at_the_scheduled_item_boundary(registry, monkeypatch):
    _, iid = registry
    from studio_data.services import futu, market_data_ops

    instrument = eod.get_instrument(iid)
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda _: instrument)
    monkeypatch.setattr(futu, "refresh_futu_eod", Mock(side_effect=eod.FutuRefreshError("Futu quotes unavailable.")))
    status = Mock(return_value=instrument)
    monkeypatch.setattr(market_data_ops, "update_refresh_status", status)
    assert market_data_ops.refresh_market_data_with_timeout(instrument_id=iid, updated_by="scheduler") == {
        **instrument, "canonical_changed": False,
    }
    status.assert_called_once_with(instrument_id=iid, status="failed", message="Futu quotes unavailable.",
                                   updated_by="scheduler", mode="api")


def test_partial_futu_batch_notifies_only_when_canonical_watermarks_advance(registry, monkeypatch):
    from contextlib import nullcontext
    from studio_data.commands import instruments
    from studio_data.contracts import StudioBulkRefreshRequest
    from studio_data.services import market_data_ops

    sessions, iid = registry
    source = history_store()
    source.query.return_value["rows"][0].update(open=14.65, low=14.67, high=14.93, close=14.86)
    monkeypatch.setattr(market_data_ops, "get_settings", Mock())
    monkeypatch.setattr(market_data_ops, "get_instrument", eod.get_instrument)
    monkeypatch.setattr(market_data_ops, "list_instruments", lambda **_: [eod.get_instrument(iid)])
    monkeypatch.setattr(market_data_ops, "market_data_item_timeout", lambda _: nullcontext())
    monkeypatch.setattr(market_data_ops, "refresh_market_data",
                        lambda **_: eod.refresh_futu_eod(iid, store=source, now=NOW))
    notify = Mock()
    monkeypatch.setattr(instruments, "notify_market_data_downstream_refresh", notify)
    payload = StudioBulkRefreshRequest(source="futu")

    first = instruments.refresh_instrument_market_data_batch(payload).model_dump(mode="json")
    assert first["refreshed_count"] == 0
    assert first["results"][0]["status"] == "partial"
    assert first["results"][0]["canonical_changed"] is True
    assert "Omitted 1 OHLCV bars" in first["results"][0]["message"]
    notify.assert_called_once_with(instrument_ids=[iid], raise_on_error=True)
    assert core.get_instrument(sessions, iid)["market_data"][0]["value"] == "14.86"
    assert core.get_price_bars(sessions, instrument_id=iid) == []

    notify.reset_mock()
    replay = instruments.refresh_instrument_market_data_batch(payload).model_dump(mode="json")
    assert replay["results"][0]["status"] == "partial"
    assert replay["results"][0]["canonical_changed"] is False
    notify.assert_not_called()


@pytest.fixture
def registration(registry, monkeypatch):
    sessions, _ = registry
    from studio_data.services.futu import service

    for name in ("create_instrument", "find_instrument_by_identifier"):
        method = getattr(core, name)
        monkeypatch.setattr(service, name, lambda method=method, **kwargs: method(sessions, **kwargs))
    monkeypatch.setattr(service, "get_instrument", lambda iid: core.get_instrument(sessions, iid))
    return service, sessions


def catalog_identity(**changes):
    return dict(symbol="HK.02840", stock_id=200, stock_type="ETF", name="Verified Gold ETF",
                listing_date="2010-01-05", currency="HKD", currency_verified=True,
                exchange_code="XHKG", **changes)


def test_catalog_selection_registers_once_with_automatic_source_configuration(registration):
    service, _ = registration
    first = service.materialize_futu_security("HK.02840", catalog_record=catalog_identity(), refresh_eod=False)
    second = service.materialize_futu_security("HK.02840", catalog_record=catalog_identity(), refresh_eod=False)
    assert first["instrument_id"] == second["instrument_id"]
    assert second["source_settings"]["source_api_profile"] == "futu"
    assert second["source_settings"]["history_start_date"] == "2010-01-05"
    assert second["source_settings"]["source_provider_currency"] == "HKD"
    assert second["source_settings"]["source_provider_security_id"] == "200"
    assert second["quote_selection_policy"] == eod.CLOSE_POLICY
    assert {row["identifier_value"] for row in second["identifiers"]} == {"2840.HK", "futu:HK.02840"}


def test_first_collection_cannot_attach_a_reused_symbol_to_the_catalog_identity(registration, monkeypatch):
    service, sessions = registration
    from studio_market.futu import collect

    source = history_store()
    source.latest.return_value["rows"][0].update(symbol="HK.02840", stock_id=201, listing_date="2010-01-05")
    source.query.return_value["rows"][0].update(symbol="HK.02840", stock_id=201)
    collector = Mock(return_value={"status": "ready"})
    monkeypatch.setattr(eod.MarketSettings, "from_environment", Mock(return_value=Mock()))
    monkeypatch.setattr(eod, "NumericStore", Mock(return_value=source))
    monkeypatch.setattr(collect, "collect_history", collector)

    with pytest.raises(eod.FutuRefreshError, match="differs from the registered catalog identity"):
        service.materialize_futu_security("HK.02840", catalog_record=catalog_identity())
    collector.assert_called_once()
    source.query.assert_not_called()
    record = core.find_instrument_by_identifier(sessions, identifier_type="provider_symbol", identifier_value="futu:HK.02840")
    detail = core.get_instrument(sessions, record["instrument_id"])
    assert detail["source_settings"]["source_provider_security_id"] == "200"
    assert detail["instrument_name"] == "Verified Gold ETF"
    assert detail["market_data"] == []
    assert detail["refresh_status"]["status"] == "failed"
    assert core.get_price_bars(sessions, instrument_id=record["instrument_id"]) == []

    # A retry can publish only the originally selected security, not the newer
    # identity that happened to reuse its ticker while registration was pending.
    source.latest.return_value["rows"][0]["stock_id"] = 200
    source.query.return_value["rows"][0]["stock_id"] = 200
    result = eod.refresh_futu_eod(record["instrument_id"], store=source, now=NOW)
    assert result["refresh_status"]["status"] == "partial"
    assert result["source_settings"]["source_provider_security_id"] == "200"


def test_catalog_reselection_cannot_rebind_an_existing_futu_security(registration):
    service, sessions = registration
    first = service.materialize_futu_security("HK.02840", catalog_record=catalog_identity(), refresh_eod=False)
    before = core.get_instrument(sessions, first["instrument_id"])
    replacement = {**catalog_identity(), "stock_id": 201, "name": "Replacement ETF"}
    with pytest.raises(eod.FutuRefreshError, match="stock_id differs"):
        service.materialize_futu_security("HK.02840", catalog_record=replacement, refresh_eod=False)
    after = core.get_instrument(sessions, first["instrument_id"])
    assert after["source_settings"] == before["source_settings"]
    assert after["instrument_name"] == before["instrument_name"]
    assert after["identifiers"] == before["identifiers"]


def test_interrupted_registration_already_has_its_catalog_identity_bound(registration, monkeypatch):
    service, sessions = registration
    monkeypatch.setattr(service, "refresh_futu_eod", Mock(side_effect=RuntimeError("interrupted after creation")))
    with pytest.raises(RuntimeError, match="interrupted after creation"):
        service.materialize_futu_security("HK.02840", catalog_record=catalog_identity())
    created = core.find_instrument_by_identifier(sessions, identifier_type="provider_symbol", identifier_value="futu:HK.02840")
    assert created["source_settings"]["source_api_profile"] == "futu"
    assert created["source_settings"]["source_provider_security_id"] == "200"
    with pytest.raises(eod.FutuRefreshError, match="stock_id differs"):
        service.materialize_futu_security("HK.02840", catalog_record={**catalog_identity(), "stock_id": 201}, refresh_eod=False)


def test_source_update_cannot_overwrite_an_identity_bound_by_another_registration(registry):
    sessions, iid = registry
    before = core.get_instrument(sessions, iid)
    with pytest.raises(ValueError, match="provider security identity is already bound"):
        core.upsert_source_settings(sessions, instrument_id=iid, source_mode="api", source_email=None,
            source_location="Replacement source", source_api_profile="futu", source_email_rules=None,
            source_provider_security_id="101")
    after = core.get_instrument(sessions, iid)
    assert after["source_settings"] == before["source_settings"]
    assert after["calculation_inputs_updated_at"] == before["calculation_inputs_updated_at"]


@pytest.mark.parametrize("field,value", [("currency_verified", False), ("currency", ""),
                                        ("stock_type", "WARRANT"), ("exchange_code", "XNYS")])
def test_unverified_catalog_listing_does_not_create_instrument(registration, field, value):
    service, sessions = registration
    identity = catalog_identity()
    identity[field] = value
    with pytest.raises(eod.FutuRefreshError):
        service.materialize_futu_security("HK.02840", catalog_record=identity, refresh_eod=False)
    assert core.find_instrument_by_identifier(sessions, identifier_type="exchange_ticker", identifier_value="2840.HK") is None


def test_catalog_selection_cannot_take_over_fmp_history(registration):
    service, sessions = registration
    instrument = core.create_instrument(sessions, instrument_name="Existing ETF", instrument_type="etf",
        currency="HKD", exchange_code="XHKG", identifiers=[
            {"identifier_type": "exchange_ticker", "identifier_value": "2840.HK", "is_primary": True}])
    iid = instrument["instrument_id"]
    core.upsert_market_data(sessions, instrument_id=iid, metric_family="price", quote_basis="close",
        as_of_date=date(2026, 9, 7), value="10", currency="HKD", provider="fmp:fixture", status="complete")
    before = core.get_instrument(sessions, iid)
    with pytest.raises(eod.FutuRefreshError, match="separate reviewed source migration"):
        service.materialize_futu_security("HK.02840", catalog_record=catalog_identity(), refresh_eod=False)
    after = core.get_instrument(sessions, iid)
    assert after["source_settings"] == before["source_settings"]
    assert after["identifiers"] == before["identifiers"]
    assert after["market_data"] == before["market_data"]


def test_catalog_selection_cannot_take_over_another_source_before_first_price(registration):
    service, sessions = registration
    instrument = core.create_instrument(sessions, instrument_name="Existing ETF", instrument_type="etf",
        currency="HKD", exchange_code="XHKG", identifiers=[
            {"identifier_type": "exchange_ticker", "identifier_value": "2840.HK", "is_primary": True}],
        source_settings={"source_mode": "api", "source_api_profile": "fmp"})
    iid = instrument["instrument_id"]
    before = core.get_instrument(sessions, iid)
    with pytest.raises(eod.FutuRefreshError, match="separate reviewed source migration"):
        service.materialize_futu_security("HK.02840", catalog_record=catalog_identity(), refresh_eod=False)
    after = core.get_instrument(sessions, iid)
    assert after["source_settings"] == before["source_settings"]
    assert after["identifiers"] == before["identifiers"]
    assert after["calculation_inputs_updated_at"] == before["calculation_inputs_updated_at"]


def test_catalog_reselection_does_not_overwrite_concurrent_source_configuration(registration, monkeypatch):
    service, sessions = registration
    first = service.materialize_futu_security("HK.02840", catalog_record=catalog_identity(), refresh_eod=False)
    original = service.validate_futu_configuration

    def concurrently_update(instrument):
        result = original(instrument)
        core.upsert_source_settings(sessions, instrument_id=instrument["instrument_id"],
            source_mode="api", source_email="", source_location="reviewed source configuration",
            source_api_profile="futu", source_email_rules=[], history_start_date=date(2020, 1, 1))
        return result

    monkeypatch.setattr(service, "validate_futu_configuration", concurrently_update)
    result = service.materialize_futu_security("HK.02840", catalog_record=catalog_identity(), refresh_eod=False)
    assert result["instrument_id"] == first["instrument_id"]
    assert result["source_settings"]["history_start_date"] == "2020-01-01"
    assert result["source_settings"]["source_location"] == "reviewed source configuration"


@pytest.fixture
def retained_evidence(tmp_path):
    from studio_market.config import MarketSettings
    from studio_market.numeric import NumericStore

    market = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'evidence.db'}", tmp_path / "evidence"))
    market.create_schema_for_testing()
    identity = dict(symbol="HK.02822", stock_id=100, stock_type="ETF", listing_date="2012-01-01")
    market.ingest("futu_security_directory", [[identity]], source="futu:stock-basicinfo")
    raw = [dict(symbol="HK.02822", stock_id=100, date=day, open=close, high=close + 1,
                low=close - 1, close=close, volume=1000, volume_unit="shares",
                ohlc_adjustment="unadjusted", adjusted_close_adjustment="unavailable",
                raw_ref=f"numeric/raw/futu/{day}.json")
           for day, close in [("2026-09-07", 100), ("2026-09-08", 99)]]
    def actions(amount=1, *, unknown=False, publish_prices=True):
        events = [] if unknown else [{"ex_div_date": "2026-09-08", "action_types": ["DIVIDEND"],
                  "per_cash_div": amount, "desc_sc": f"1股派股息{amount}HKD"}]
        action_result = market.ingest("futu_corporate_actions", [[{
            **identity, "coverage_start": "2012-01-01", "coverage_end": "2026-09-08",
            "coverage_status": "unknown_empty_etf" if unknown else "provider_reported",
            "coverage_basis": "provider_reported_since_listing", "event_count": len(events),
            "events_json": json.dumps(events), "raw_ref": "numeric/raw/futu/actions.json",
        }]], source=eod.ACTION_SOURCE)
        if publish_prices:
            # Every completed price acquisition binds the full actions response,
            # including captures in which the raw prices themselves are unchanged.
            market.ingest("futu_raw_eod_daily", [raw], source=eod.PROVIDER,
                          details={"corporate_actions_batch_id": action_result["batch_id"]})
    actions()
    yield market, actions
    market.close()


def test_action_revision_and_withdrawal_rebuild_old_returns_factors_and_watermarks(registry, retained_evidence):
    sessions, iid = registry
    market, revise = retained_evidence
    eod.refresh_futu_eod(iid, store=market, now=NOW)
    first = core.get_instrument(sessions, iid)
    assert first["refresh_status"]["status"] == "partial"
    assert "Requested history starts on market session 2026-09-01" in first["refresh_status"]["message"]
    def values(record, basis):
        return {point["as_of_date"]: Decimal(str(point["value"])) for point in record["market_data"]
                if point["quote_basis"] == basis}
    assert values(first, "adjusted_close")["2026-09-08"] == pytest.approx(Decimal(100))
    assert first["source_settings"]["return_semantics"] == "price_return"
    assert first["source_settings"]["total_return_segments"] == [
        {"start_date": "2026-09-07", "end_date": "2026-09-08"}]
    raw_before = values(first, "close")
    # The completed capture revises the action snapshot while raw values remain
    # unchanged; every earlier derived price must still be corrected.
    revise(.5)
    eod.refresh_futu_eod(iid, store=market, now=NOW)
    changed = core.get_instrument(sessions, iid)
    assert values(changed, "adjusted_close")["2026-09-08"] == pytest.approx(Decimal("99.5"))
    assert values(changed, "close") == raw_before
    assert changed["market_data_updated_at"] != first["market_data_updated_at"]
    assert changed["calculation_inputs_updated_at"] != first["calculation_inputs_updated_at"]
    factor = core.get_price_bars(sessions, instrument_id=iid)[-1]["adjustment_factor"]
    assert Decimal(factor) == pytest.approx(Decimal("99.5") / 99)
    revise(unknown=True)
    eod.refresh_futu_eod(iid, store=market, now=NOW)
    withdrawn = core.get_instrument(sessions, iid)
    assert withdrawn["refresh_status"]["status"] == "partial"
    assert values(withdrawn, "adjusted_close") == {}
    assert values(withdrawn, "close") == raw_before
    assert withdrawn["source_settings"]["total_return_segments"] == []
    assert all(bar["adjustment_factor"] is None for bar in core.get_price_bars(sessions, instrument_id=iid))
    assert withdrawn["market_data_updated_at"] != changed["market_data_updated_at"]
    retained = market.query("futu_return_daily", symbols=["HK.02822"])["rows"]
    assert len(retained) == 2 and all(row["adjusted_close"] is None for row in retained)
    assert all(row["corporate_actions_source_id"] and row["raw_source_id"] for row in retained)
    # Replaying precisely the same evidence preserves both canonical watermarks.
    replay = eod.refresh_futu_eod(iid, store=market, now=NOW)
    assert replay["market_data_updated_at"] == withdrawn["market_data_updated_at"]
    assert replay["calculation_inputs_updated_at"] == withdrawn["calculation_inputs_updated_at"]


def test_withdrawal_and_factor_clear_roll_back_with_canonical_transaction(registry, retained_evidence, monkeypatch):
    sessions, iid = registry
    market, revise = retained_evidence
    eod.refresh_futu_eod(iid, store=market, now=NOW)
    before = core.get_instrument(sessions, iid)
    before_bars = core.get_price_bars(sessions, instrument_id=iid)
    revise(unknown=True)
    monkeypatch.setattr(eod.shared_store, "_upsert_price_bars", Mock(side_effect=ValueError("simulated publication failure")))
    with pytest.raises(eod.FutuRefreshError):
        eod.refresh_futu_eod(iid, store=market, now=NOW)
    after = core.get_instrument(sessions, iid)
    assert after["market_data"] == before["market_data"]
    assert after["source_settings"] == before["source_settings"]
    assert after["market_data_updated_at"] == before["market_data_updated_at"]
    assert after["calculation_inputs_updated_at"] == before["calculation_inputs_updated_at"]
    assert core.get_price_bars(sessions, instrument_id=iid) == before_bars


def test_actions_from_failed_price_capture_cannot_revise_retained_returns(registry, retained_evidence):
    sessions, iid = registry
    market, revise = retained_evidence
    eod.refresh_futu_eod(iid, store=market, now=NOW)
    before = core.get_instrument(sessions, iid)
    # Simulate a collection that persisted new actions, then failed before the
    # raw-price commit. Merely being the latest action snapshot is insufficient.
    revise(50, publish_prices=False)
    latest = market.latest("futu_corporate_actions", symbols=["HK.02822"])["rows"][0]
    assert json.loads(latest["events_json"])[0]["per_cash_div"] == 50
    eod.refresh_futu_eod(iid, store=market, now=NOW)
    replay = core.get_instrument(sessions, iid)
    assert replay["market_data"] == before["market_data"]
    assert replay["source_settings"] == before["source_settings"]
    assert replay["market_data_updated_at"] == before["market_data_updated_at"]
    assert replay["calculation_inputs_updated_at"] == before["calculation_inputs_updated_at"]


def test_complete_retained_history_does_not_require_a_holiday_price(registry, retained_evidence):
    sessions, iid = registry
    market, _ = retained_evidence
    core.upsert_source_settings(sessions, instrument_id=iid, source_mode="api", source_email="",
        source_location="Futu quotes", source_api_profile="futu", source_email_rules=[],
        history_start_date=date(2026, 9, 5))  # Saturday; first requested session is Monday.
    result = eod.refresh_futu_eod(iid, store=market, now=NOW)
    assert result["refresh_status"]["status"] == "refreshed"
    assert "Requested history starts" not in result["refresh_status"]["message"]


def test_missing_bound_action_batch_cannot_fall_back_to_another_snapshot(registry, retained_evidence):
    from sqlalchemy import update
    from studio_market.numeric.schema import batches

    sessions, iid = registry
    market, revise = retained_evidence
    eod.refresh_futu_eod(iid, store=market, now=NOW)
    before = core.get_instrument(sessions, iid)
    bound = before["source_settings"]["total_return_evidence"]["corporate_actions_batch_id"]
    revise(50, publish_prices=False)
    with market.engine.begin() as connection:
        connection.execute(update(batches).where(batches.c.id == bound).values(status="failed"))
    with pytest.raises(eod.FutuRefreshError, match="lacks its bound corporate-action snapshot"):
        eod.refresh_futu_eod(iid, store=market, now=NOW)
    after = core.get_instrument(sessions, iid)
    assert after["market_data"] == before["market_data"]
    assert after["source_settings"] == before["source_settings"]
    assert after["market_data_updated_at"] == before["market_data_updated_at"]
