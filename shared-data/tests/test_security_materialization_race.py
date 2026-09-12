"""Concurrent directory selection must resolve the winner, never duplicate it."""
from importlib import import_module

import pytest
from sqlalchemy.exc import IntegrityError


@pytest.mark.parametrize("kind,package", [("equity", "equities"), ("etf", "etfs")])
@pytest.mark.parametrize("failure", [ValueError("identifier already belongs to an instrument"), IntegrityError("insert", {}, Exception("unique identifier"))])
def test_registration_race_resolves_same_listing(monkeypatch, kind, package, failure):
    module = import_module(f"studio_data.services.{package}.service")
    record = {"instrument_id": "shv", "instrument_type": kind, "exchange_code": "XNAS", "currency": "USD"}
    monkeypatch.setattr(module, f"get_catalog_{kind}", lambda symbol: {"fmp_symbol": "SHV", "exchange_ticker": "SHV.US", "exchange_code": "XNAS", "currency": "USD", "company_name": "SHV"})
    reads = iter([None, record])
    monkeypatch.setattr(module, f"_existing_{kind}", lambda **kwargs: next(reads))
    def failed_insert(**kwargs):
        raise failure
    monkeypatch.setattr(module, "create_instrument", failed_insert)
    monkeypatch.setattr(module, "ensure_secondary_identifier", lambda **kwargs: record)
    monkeypatch.setattr(module, "_source_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "get_instrument", lambda instrument_id: record)
    assert getattr(module, f"materialize_{kind}")("SHV", refresh_eod=False, client=object()) == record


@pytest.mark.parametrize("kind,package", [("equity", "equities"), ("etf", "etfs")])
def test_unrelated_create_failure_is_not_hidden(monkeypatch, kind, package):
    module = import_module(f"studio_data.services.{package}.service")
    monkeypatch.setattr(module, f"get_catalog_{kind}", lambda symbol: {"fmp_symbol": "SHV", "exchange_ticker": "SHV.US", "exchange_code": "XNAS", "currency": "USD", "company_name": "SHV"})
    monkeypatch.setattr(module, f"_existing_{kind}", lambda **kwargs: None)
    def failed_insert(**kwargs):
        raise ValueError("unrelated invalid data")
    monkeypatch.setattr(module, "create_instrument", failed_insert)
    with pytest.raises(ValueError, match="unrelated invalid data"):
        getattr(module, f"materialize_{kind}")("SHV", refresh_eod=False, client=object())


@pytest.mark.parametrize("kind,package", [("equity", "equities"), ("etf", "etfs")])
def test_archived_match_does_not_block_search_or_allow_registration(monkeypatch, kind, package):
    module = import_module(f"studio_data.services.{package}.service")
    rows = [{"fmp_symbol": symbol, "exchange_ticker": symbol + ".US", "exchange_code": "XNAS", "currency": "USD", "company_name": symbol} for symbol in ["ARCHIVED", "ACTIVE"]]
    monkeypatch.setattr(module, f"search_{kind}_catalog", lambda query, limit: rows)
    monkeypatch.setattr(module, f"get_catalog_{kind}", lambda symbol: rows[0])
    monkeypatch.setattr(module, "find_instrument_by_identifier", lambda **kwargs: {"instrument_id": "archived", "instrument_type": kind, "lifecycle_state": {"status": "archived"}} if "ARCHIVED" in kwargs["identifier_value"] else None)
    matches = getattr(module, "search_equities" if kind == "equity" else "search_etfs")("A")
    assert [row["catalog_symbol"] for row in matches] == ["ACTIVE"]
    monkeypatch.setattr(module, "create_instrument", lambda **kwargs: pytest.fail("Archived listing was recreated"))
    with pytest.raises(ValueError, match="archived"):
        getattr(module, f"materialize_{kind}")("ARCHIVED", refresh_eod=False, client=object())
