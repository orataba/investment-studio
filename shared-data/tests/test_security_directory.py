"""Public directory reads preserve listing identity without loading market data."""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core import instrument_store
from investment_studio_instrument_core.db_models import InstrumentRegistryBase
from investment_studio_instrument_core.security_directory import search_directory, SecurityDirectoryError
from studio_data.db.equity_models import FmpEquityCatalog
from studio_data.db.etf_models import FmpEtfCatalog


@pytest.fixture
def directory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    InstrumentRegistryBase.metadata.create_all(engine)
    FmpEquityCatalog.__table__.create(engine)
    FmpEtfCatalog.__table__.create(engine)
    factory = sessionmaker(engine)
    with factory() as session:
        session.add_all([FmpEquityCatalog(
            fmp_symbol=symbol, exchange_ticker=ticker, company_name=name,
            exchange_code=exchange, market=market, currency=currency,
        ) for symbol, ticker, name, exchange, market, currency in [
            ("GOOGL", "GOOGL", "Alphabet US", "XNAS", "US", "USD"),
            ("GOOGL.L", "GOOGL.L", "Alphabet London", "XLON", "EU", "GBP"),
            ("GOOG", "GOOG", "Alphabet Class C", "XNAS", "US", "USD"),
            ("600519.SS", "600519.SH", "Kweichow Moutai", "XSHG", "CN", "CNY"),
        ]])
        session.commit()
    yield factory
    engine.dispose()


def register(factory, ticker, *, kind="equity", exchange="XNAS", provider=None):
    identifiers = [{"identifier_type": "exchange_ticker", "identifier_value": ticker, "is_primary": True}]
    if provider:
        identifiers.append({"identifier_type": "provider_symbol", "identifier_value": f"fmp:{provider}", "is_primary": False})
    return instrument_store.create_instrument(factory, instrument_name=ticker, instrument_type=kind,
                                             currency="USD", exchange_code=exchange, identifiers=identifiers)


def test_exact_and_suffixed_listings_keep_distinct_identity_and_live_registration(directory):
    first, errors = search_directory(directory, "GOOGL", 25)
    assert [row["catalog_symbol"] for row in first] == ["GOOGL", "GOOGL.L"]
    assert first[0]["existing_instrument_id"] is None
    assert first[1]["currency_verified"] is False
    assert set(errors) == {"etf"}
    registered = register(directory, "GOOGL", provider="GOOGL")
    second, _ = search_directory(directory, "googl", 25)
    assert second[0]["existing_instrument_id"] == registered["instrument_id"]
    assert second[1]["existing_instrument_id"] is None
    suffix, _ = search_directory(directory, "600519.SH", 25)
    assert suffix[0]["catalog_symbol"] == "600519.SS"
    assert suffix[0]["symbol"] == "600519.SH"


def test_batch_lookup_excludes_archived_and_rejects_type_collision(directory):
    registered = register(directory, "GOOGL", provider="GOOGL")
    instrument_store.archive_instrument(directory, instrument_id=registered["instrument_id"], updated_by="test")
    rows, _ = search_directory(directory, "GOOGL", 25)
    assert [row["catalog_symbol"] for row in rows] == ["GOOGL.L"]
    register(directory, "GOOG", kind="etf")
    with pytest.raises(SecurityDirectoryError, match="non-equity"):
        search_directory(directory, "GOOG", 25)


def test_directory_and_metadata_never_read_price_or_event_history(directory):
    registered = register(directory, "GOOGL", provider="GOOGL")
    statements = []
    def record(_connection, _cursor, statement, *_args):
        statements.append(statement.lower())
    event.listen(directory.kw["bind"], "before_cursor_execute", record)
    try:
        results, _ = search_directory(directory, "GOOGL", 25)
        assert results[0]["existing_instrument_id"] == registered["instrument_id"]
        assert len(statements) == 4  # two existence probes, one catalog, one identity batch
        metadata = instrument_store.get_instrument_metadata(directory, [registered["instrument_id"], "missing"])
        assert metadata["missing"] is None
        identity = metadata[registered["instrument_id"]]
        assert identity["instrument_id"] == registered["instrument_id"]
        assert {"source_settings", "quote_selection_policy", "lifecycle_state"} <= identity.keys()
        assert "latest_market_data" not in identity and "coverage_state" not in identity
        assert not any("instrument_market_data" in sql or "fund_nav" in sql or "corporate_action" in sql for sql in statements)
    finally:
        event.remove(directory.kw["bind"], "before_cursor_execute", record)
