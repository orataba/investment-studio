from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from platform_app.services.fmp.fx import refresh_fmp_fx_eod
from platform_app.services.instrument_store import get_instrument


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
REGISTRY_MIGRATIONS_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"


@pytest.fixture()
def isolated_fx_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fx.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA", "")
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA", "")

    from platform_app.core import settings as settings_module
    from platform_app.db import session as session_module

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
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    monkeypatch.setenv(
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL",
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
