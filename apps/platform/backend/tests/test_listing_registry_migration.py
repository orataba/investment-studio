from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
REGISTRY_MIGRATIONS_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"


def _upgrade(database_url: str, revision: str) -> None:
    config = Config(str(REGISTRY_MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(REGISTRY_MIGRATIONS_ROOT / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


def test_listing_migration_backfills_etf_exchange_and_corrects_calendar(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'listing.db'}"
    _upgrade(database_url, "20260818_0025")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO instrument "
                "(instrument_id, instrument_name, instrument_type, currency, "
                "exchange_code, quote_selection_policy_json, source_settings_json, "
                "refresh_status_json, lifecycle_state_json, market_data_updated_at, "
                "calculation_inputs_updated_at) VALUES "
                "(:instrument_id, :instrument_name, 'etf', 'CNY', NULL, :policy, "
                ":settings, :refresh_status, :lifecycle, NULL, NULL)"
            ),
            {
                "instrument_id": "159919-sz",
                "instrument_name": "CSI 300 ETF Shenzhen",
                "policy": json.dumps(
                    {
                        "trading": ["close"],
                        "valuation": ["close"],
                        "total_return": ["adjusted_close"],
                        "chart": ["adjusted_close"],
                        "reference": ["close"],
                    }
                ),
                "settings": json.dumps(
                    {
                        "source_mode": "api",
                        "source_api_profile": "tushare",
                        "expected_frequency": "daily",
                        "market_calendar": "XSHG",
                    }
                ),
                "refresh_status": json.dumps({"status": "idle", "mode": "api"}),
                "lifecycle": json.dumps({"status": "active"}),
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO instrument_identifier "
                "(instrument_id, identifier_type, identifier_value, is_primary) "
                "VALUES ('159919-sz', 'exchange_ticker', '159919.SZ', true)"
            )
        )
    engine.dispose()

    _upgrade(database_url, "head")

    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        row = connection.execute(
            sa.text(
                "SELECT exchange_code, source_settings_json FROM instrument "
                "WHERE instrument_id = '159919-sz'"
            )
        ).mappings().one()
        settings = row["source_settings_json"]
        if isinstance(settings, str):
            settings = json.loads(settings)
        assert row["exchange_code"] == "XSHE"
        assert settings["market_calendar"] == "XSHE"

        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO instrument "
                    "(instrument_id, instrument_name, instrument_type, currency, "
                    "exchange_code, quote_selection_policy_json, source_settings_json, "
                    "refresh_status_json, lifecycle_state_json) VALUES "
                    "('invalid-etf', 'Invalid ETF', 'etf', 'EUR', NULL, '{}', '{}', '{}', '{}')"
                )
            )
