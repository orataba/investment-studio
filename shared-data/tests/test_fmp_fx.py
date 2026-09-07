from __future__ import annotations

from collections.abc import Iterator
from datetime import date
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from studio_data.services.fmp.fx import refresh_fmp_fx_eod
from studio_data.services.instrument_store import get_instrument


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
REGISTRY_MIGRATIONS_ROOT = WORKSPACE_ROOT / "shared-data" / "instruments"


@pytest.fixture()
def isolated_fx_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fx.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA", "")
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA", "")

    from studio_data.core import settings as settings_module
    from studio_data.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()
    config = Config(str(REGISTRY_MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(REGISTRY_MIGRATIONS_ROOT / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    yield

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


class FakeFmpClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def historical_fx(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, object]]:
        del start_date, end_date
        self.calls.append(symbol)
        return [{"date": "2026-08-21", "close": "1.2345"}]


def test_fmp_fx_cutover_replaces_legacy_series_without_mixing_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fx-cutover.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    monkeypatch.setenv(
        "INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL",
        database_url,
    )
    config = Config(str(REGISTRY_MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(REGISTRY_MIGRATIONS_ROOT / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260823_0028")

    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    exchange_code, quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json, market_data_updated_at,
                    calculation_inputs_updated_at
                ) VALUES
                    ('fx-usd-hkd', 'USD/HKD Spot', 'fx', 'HKD', NULL,
                     '{"valuation":["spot"]}',
                     '{"source_mode":"manual","source_location":"Legacy"}',
                     '{}', '{}', '2026-08-20T00:00:00Z', '2026-08-20T00:00:00Z'),
                    ('fx-usd-cny', 'USD/CNY Spot', 'fx', 'CNY', NULL,
                     '{"valuation":["spot"]}',
                     '{"source_mode":"api","source_api_profile":"cfets"}',
                     '{}', '{}', '2026-08-20T00:00:00Z', '2026-08-20T00:00:00Z')
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO instrument_market_data (
                    instrument_id, metric_family, quote_basis, as_of_date,
                    value, currency, price_unit, price_scale, provider, status
                ) VALUES
                    ('fx-usd-hkd', 'fx', 'spot', '2026-08-20',
                     '7.8', 'HKD', 'rate', 1, 'legacy:manual', 'complete'),
                    ('fx-usd-cny', 'fx', 'spot', '2026-08-20',
                     '7.2', 'CNY', 'rate', 1, 'cfets:reference', 'complete'),
                    ('fx-usd-eur', 'fx', 'spot', '2026-08-20',
                     '0.86', 'EUR', 'rate', 1, 'fmp:historical-price-eod:full', 'complete')
                """
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        source_rows = connection.execute(
            sa.text(
                "SELECT instrument_id, source_settings_json "
                "FROM instrument WHERE instrument_type = 'fx' ORDER BY instrument_id"
            )
        ).mappings().all()
        point_rows = connection.execute(
            sa.text(
                "SELECT instrument_id, provider FROM instrument_market_data "
                "ORDER BY instrument_id"
            )
        ).all()

    assert len(source_rows) == 5
    for row in source_rows:
        settings = row["source_settings_json"]
        if isinstance(settings, str):
            settings = json.loads(settings)
        assert settings["source_mode"] == "api"
        assert settings["source_api_profile"] == "fmp"
        assert settings["source_location"] == "FMP API"
        assert settings["expected_frequency"] == "daily"
    assert point_rows == [("fx-usd-eur", "fmp:historical-price-eod:full")]


def test_all_maintained_fx_masters_refresh_from_fmp(isolated_fx_store: None) -> None:
    client = FakeFmpClient()

    for instrument_id, symbol, quote_currency in (
        ("fx-usd-hkd", "USDHKD", "HKD"),
        ("fx-usd-cny", "USDCNY", "CNY"),
        ("fx-usd-eur", "USDEUR", "EUR"),
        ("fx-usd-gbp", "USDGBP", "GBP"),
        ("fx-usd-chf", "USDCHF", "CHF"),
    ):
        refreshed = refresh_fmp_fx_eod(instrument_id, client=client)  # type: ignore[arg-type]
        assert refreshed["refresh_status"]["status"] == "refreshed"
        instrument = get_instrument(instrument_id)
        assert instrument is not None
        assert instrument["currency"] == quote_currency
        point = instrument["market_data"][0]
        assert client.calls[-1] == symbol
        assert point["metric_family"] == "fx"
        assert point["quote_basis"] == "spot"
        assert point["currency"] == quote_currency
        assert point["value"] == "1.2345"
        assert instrument["source_settings"]["source_api_profile"] == "fmp"

    assert client.calls == ["USDHKD", "USDCNY", "USDEUR", "USDGBP", "USDCHF"]


def test_fmp_fx_calendar_migration_preserves_quotes_and_other_settings(tmp_path, monkeypatch):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fx-calendar.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL", database_url)
    config = Config(str(REGISTRY_MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REGISTRY_MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260907_0033")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        instruments = sa.Table("instrument", sa.MetaData(), autoload_with=connection)
        quotes = sa.Table("instrument_market_data", sa.MetaData(), autoload_with=connection)
        connection.execute(instruments.insert().values(instrument_id="unrelated", instrument_name="Unrelated",
            instrument_type="other", currency="USD", quote_selection_policy_json={},
            source_settings_json={"source_mode": "api", "source_api_profile": "fmp", "market_calendar": None},
            refresh_status_json={}, lifecycle_state_json={}, calculation_inputs_updated_at="2026-01-01T00:00:00Z"))
        for day, value in [(date(2026, 8, 21), 7.8), (date(2026, 8, 23), 7.9)]:
            connection.execute(quotes.insert().values(instrument_id="fx-usd-hkd", metric_family="fx", quote_basis="spot",
                as_of_date=day, value=value, currency="HKD", price_unit="rate", price_scale=1,
                provider="fmp:historical-price-eod:full", status="complete"))
        before = {row["instrument_id"]: dict(row) for row in connection.execute(sa.select(instruments)).mappings()}
        original_quotes = list(connection.execute(sa.select(quotes)).mappings())

    command.upgrade(config, "20260907_0034")
    with engine.connect() as connection:
        upgraded = {row["instrument_id"]: dict(row) for row in connection.execute(sa.select(instruments)).mappings()}
        assert upgraded["unrelated"] == before["unrelated"]
        for iid, old in before.items():
            if iid == "unrelated":
                continue
            assert upgraded[iid]["source_settings_json"] == {**old["source_settings_json"], "market_calendar": "24/5"}
            assert upgraded[iid]["calculation_inputs_updated_at"] > old["calculation_inputs_updated_at"]
            assert {key: value for key, value in upgraded[iid].items() if key not in {"source_settings_json", "calculation_inputs_updated_at"}} == {
                key: value for key, value in old.items() if key not in {"source_settings_json", "calculation_inputs_updated_at"}}
        assert list(connection.execute(sa.select(quotes)).mappings()) == original_quotes

    command.downgrade(config, "20260907_0033")
    with engine.connect() as connection:
        restored = {row["instrument_id"]: dict(row) for row in connection.execute(sa.select(instruments)).mappings()}
        for iid in before:
            assert restored[iid]["source_settings_json"] == before[iid]["source_settings_json"]
            if iid != "unrelated":
                assert restored[iid]["calculation_inputs_updated_at"] > upgraded[iid]["calculation_inputs_updated_at"]
        assert list(connection.execute(sa.select(quotes)).mappings()) == original_quotes
