"""Public directory reads preserve listing identity without loading market data."""
import pytest
from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, event, insert
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core import instrument_store
from investment_studio_instrument_core.db_models import Instrument, InstrumentRegistryBase
from investment_studio_instrument_core.security_directory import search_directory, SecurityDirectoryError
from studio_data.db.equity_models import FmpEquityCatalog
from studio_data.db.etf_models import FmpEtfCatalog

futu_current = Table("current", MetaData(), Column("dataset", String), Column("payload", JSON))


@pytest.fixture
def directory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    InstrumentRegistryBase.metadata.create_all(engine)
    FmpEquityCatalog.__table__.create(engine)
    FmpEtfCatalog.__table__.create(engine)
    futu_current.create(engine)
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
        assert len(statements) == 5  # two FMP probes, two provider catalogs, one identity batch
        metadata = instrument_store.get_instrument_metadata(directory, [registered["instrument_id"], "missing"])
        assert metadata["missing"] is None
        identity = metadata[registered["instrument_id"]]
        assert identity["instrument_id"] == registered["instrument_id"]
        assert {"source_settings", "quote_selection_policy", "lifecycle_state"} <= identity.keys()
        assert "latest_market_data" not in identity and "coverage_state" not in identity
        assert not any("instrument_market_data" in sql or "fund_nav" in sql or "corporate_action" in sql for sql in statements)
    finally:
        event.remove(directory.kw["bind"], "before_cursor_execute", record)


def add_futu(factory, symbol="HK.02822", ticker="2822.HK", *, active=True, kind="etf", name="CSOP A50 ETF"):
    row = dict(symbol=symbol, exchange_ticker=ticker, instrument_type=kind, name=name,
               sc_name="南方 A50", currency="HKD", currency_verified=True,
               exchange_code="XHKG", active=active)
    if symbol.startswith("US."):
        row.update(currency="USD", exchange_code="XNAS")
    with factory() as session:
        session.execute(insert(futu_current), [{"dataset": "futu_security_catalog", "payload": row}])
        session.commit()


def test_futu_missing_fmp_listing_is_searchable_locally_with_its_source(directory):
    add_futu(directory)
    for query in ("2822", "2822.HK", "02822.HK", "HK.02822", "南方"):
        rows, _ = search_directory(directory, query)
        assert len(rows) == 1
        assert rows[0]["catalog_provider"] == "futu"
        assert rows[0]["catalog_symbol"] == "HK.02822"
        assert rows[0]["currency_verified"] is True
    add_futu(directory, "HK.02840", "2840.HK", active=False)
    assert search_directory(directory, "2840")[0] == []


def test_fmp_wins_overlap_but_registered_futu_choice_is_retained(directory):
    add_futu(directory, "US.GOOGL", "GOOGL", kind="equity", name="Alphabet US")
    rows, _ = search_directory(directory, "US.GOOGL", 25)
    assert len([row for row in rows if row["symbol"] == "GOOGL"]) == 1
    assert rows[0]["catalog_provider"] == "fmp"
    registered = register(directory, "GOOGL")
    instrument_store.ensure_secondary_identifier(directory, instrument_id=registered["instrument_id"],
        identifier_type="provider_symbol", identifier_value="futu:US.GOOGL")
    from investment_studio_instrument_core.db_models import Instrument
    with directory() as session:
        item = session.get(Instrument, registered["instrument_id"])
        item.source_settings_json = {**item.source_settings_json, "source_api_profile": "futu"}
        session.commit()
    rows, _ = search_directory(directory, "GOOGL", 25)
    assert rows[0]["catalog_provider"] == "futu"
    assert rows[0]["existing_instrument_id"] == registered["instrument_id"]


def test_verified_counter_currency_beats_generic_fmp_exchange_currency(directory):
    add_futu(directory)
    with directory() as session:
        session.add(FmpEtfCatalog(fmp_symbol="2822.HK", exchange_ticker="2822.HK",
            company_name="CSOP A50 ETF", exchange_code="XHKG", market="HK", currency="USD"))
        session.commit()
    rows, _ = search_directory(directory, "2822.HK")
    assert len(rows) == 1
    assert rows[0]["catalog_provider"] == "futu"
    assert rows[0]["currency"] == "HKD"


def test_conflicting_provider_and_ticker_cannot_select_the_first_registered_instrument(directory):
    register(directory, "UNRELATED", provider="GOOGL")
    register(directory, "GOOGL")
    with pytest.raises(SecurityDirectoryError, match="different registered instruments"):
        search_directory(directory, "GOOGL")


def test_registered_listing_exchange_must_match_the_catalog(directory):
    register(directory, "GOOGL", exchange="XNYS")
    with pytest.raises(SecurityDirectoryError, match="registered exchange"):
        search_directory(directory, "GOOGL")


def test_catalog_reports_identity_conflict_without_disguising_it_as_invalid_json(directory):
    from investment_studio_instrument_core.security_catalog import SecurityCatalogError, search_catalog
    register(directory, "GOOGL", exchange="XNYS")
    with pytest.raises(SecurityCatalogError, match="identity conflict.*registered exchange.*reconcile"):
        search_catalog("GOOGL", 10, session_factory=directory)


def test_registered_currency_is_authoritative_over_generic_catalog_currency(directory):
    registered = instrument_store.create_instrument(directory, instrument_name="London dollar counter",
        instrument_type="equity", exchange_code="XLON", currency="USD", identifiers=[
            {"identifier_type":"exchange_ticker", "identifier_value":"GOOGL.L", "is_primary":True}])
    rows, _ = search_directory(directory, "GOOGL.L")
    assert rows[0]["existing_instrument_id"] == registered["instrument_id"]
    assert rows[0]["currency"] == "USD"
    assert rows[0]["currency_verified"] is True


def register_source(factory, ticker, provider):
    registered = register(factory, ticker, provider=ticker if provider == "fmp" else None)
    if provider == "futu":
        instrument_store.ensure_secondary_identifier(factory, instrument_id=registered["instrument_id"],
            identifier_type="provider_symbol", identifier_value=f"futu:US.{ticker}")
    with factory() as session:
        item = session.get(Instrument, registered["instrument_id"])
        item.source_settings_json = {**item.source_settings_json, "source_api_profile": provider}
        session.commit()
    return registered


@pytest.mark.parametrize("provider,query", [("futu", "Alphabet US"), ("fmp", "谷歌")])
def test_registered_source_survives_a_name_found_only_in_the_other_catalog(directory, provider, query):
    add_futu(directory, "US.GOOGL", "GOOGL", kind="equity", name="谷歌 A 类")
    registered = register_source(directory, "GOOGL", provider)
    rows, _ = search_directory(directory, query)
    assert len(rows) == 1
    assert rows[0]["existing_instrument_id"] == registered["instrument_id"]
    assert rows[0]["catalog_provider"] == provider
    assert rows[0]["catalog_symbol"] == ("US.GOOGL" if provider == "futu" else "GOOGL")
    assert rows[0]["name"] == ("谷歌 A 类" if provider == "futu" else "Alphabet US")
    assert not any(key.startswith("_") for key in rows[0])


@pytest.mark.parametrize("provider", ["futu", "fmp"])
def test_registered_source_survives_its_catalog_search_limit(directory, provider):
    with directory() as session:
        session.get(FmpEquityCatalog, "GOOGL").company_name = "Zulu Shared" if provider == "fmp" else "0 Shared target"
        if provider == "fmp":
            session.add(FmpEquityCatalog(fmp_symbol="AAA", exchange_ticker="AAA", company_name="Aardvark Shared",
                                        exchange_code="XNAS", market="US", currency="USD"))
        session.commit()
    add_futu(directory, "US.GOOGL", "GOOGL", kind="equity",
             name="Zulu Shared" if provider == "futu" else "0 Shared target")
    if provider == "futu":
        add_futu(directory, "US.AAA", "AAA", kind="equity", name="Aardvark Shared")
    registered = register_source(directory, "GOOGL", provider)
    rows, _ = search_directory(directory, "Shared", limit=1)
    assert len(rows) == 1
    assert rows[0]["existing_instrument_id"] == registered["instrument_id"]
    assert rows[0]["catalog_provider"] == provider
    assert rows[0]["name"] == "Zulu Shared"


def test_registered_source_completion_batches_multiple_listings(directory):
    for ticker in ("GOOGL", "GOOG"):
        add_futu(directory, f"US.{ticker}", ticker, kind="equity", name=f"谷歌 {ticker}")
        register_source(directory, ticker, "futu")
    statements = []
    def record(_connection, _cursor, statement, *_args):
        statements.append(statement.lower())
    event.listen(directory.kw["bind"], "before_cursor_execute", record)
    try:
        one, _ = search_directory(directory, "Alphabet US")
        single_count = len(statements)
        statements.clear()
        many, _ = search_directory(directory, "Alphabet")
        assert len([row for row in one if row["catalog_provider"] == "futu"]) == 1
        assert len([row for row in many if row["catalog_provider"] == "futu"]) == 2
        assert len(statements) == single_count
        assert not any("instrument_market_data" in sql or "fund_nav" in sql or "corporate_action" in sql for sql in statements)
    finally:
        event.remove(directory.kw["bind"], "before_cursor_execute", record)


@pytest.mark.parametrize("condition", ["missing", "inactive", "wrong_ticker"])
def test_registered_source_completion_cannot_fallback_or_accept_conflicting_identity(directory, condition):
    register_source(directory, "GOOGL", "futu")
    if condition != "missing":
        add_futu(directory, "US.GOOGL", "WRONG" if condition == "wrong_ticker" else "GOOGL",
                 active=condition != "inactive", kind="equity", name="谷歌")
    with pytest.raises(SecurityDirectoryError, match="Registered source"):
        search_directory(directory, "Alphabet US")
