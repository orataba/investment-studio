from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

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


def test_european_fx_masters_refresh_from_fmp(isolated_fx_store: None) -> None:
    client = FakeFmpClient()

    for instrument_id, symbol, quote_currency in (
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
        assert point["metric_family"] == "fx"
        assert point["quote_basis"] == "spot"
        assert point["currency"] == quote_currency
        assert point["value"] == "1.2345"
        assert instrument["source_settings"]["source_api_profile"] == "fmp"

    assert client.calls == ["USDEUR", "USDGBP", "USDCHF"]
