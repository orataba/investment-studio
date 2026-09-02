from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260810_0048")


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def test_option_cash_settlement_migration_requires_manual_legacy_normalization() -> None:
    engine = get_engine()
    config = _alembic_config()
    with engine.begin() as connection:
        transaction = connection.execute(
            sa.text(
                "SELECT transaction_id, portfolio_id, account_id, currency, trade_date "
                "FROM transaction_record "
                "ORDER BY transaction_id LIMIT 1"
            )
        ).mappings().one()
        transaction_id = str(transaction["transaction_id"])
        connection.execute(
            sa.text(
                "UPDATE transaction_record "
                "SET lifecycle_event_type = 'option_long_exercise' "
                "WHERE transaction_id = :transaction_id"
            ),
            {"transaction_id": transaction_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO derivative_contract_record ("
                "derivative_contract_id, portfolio_id, account_id, contract_name, "
                "contract_type, currency, external_reference, terms_json, created_at"
                ") VALUES ("
                "'migration-option', :portfolio_id, :account_id, 'Migration Option', "
                "'option', :currency, 'MIGRATION-OPTION', :terms_json, "
                "'2026-08-10T00:00:00Z'"
                ")"
            ),
            {
                "portfolio_id": transaction["portfolio_id"],
                "account_id": transaction["account_id"],
                "currency": transaction["currency"],
                "terms_json": json.dumps(
                    {
                        "underlying_instrument_id": "equity-us-abbv",
                        "option_type": "call",
                        "expiry_date": "2026-12-18",
                        "strike": "200",
                        "contract_multiplier": "100",
                        "settlement_type": "physical",
                    }
                ),
            },
        )

    with pytest.raises(RuntimeError, match="does not infer settlement facts"):
        command.upgrade(config, "head")

    with engine.begin() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260810_0048"
        )
        connection.execute(
            sa.text(
                "UPDATE transaction_record SET "
                "lifecycle_event_type = NULL, "
                "transaction_type = 'maturity_redemption', "
                "position_effective_date = trade_date, "
                "settlement_cash_account_id = NULL, "
                "instrument_id = NULL, "
                "instrument_ref_json = NULL, "
                "derivative_contract_id = 'migration-option', "
                "quantity = 1, source_quantity = 1, "
                "price = NULL, source_price = NULL, "
                "gross_amount = 100, source_gross_amount = 100, "
                "fees = 0, source_fees = 0, taxes = 0, source_taxes = 0 "
                "WHERE transaction_id = :transaction_id"
            ),
            {"transaction_id": transaction_id},
        )

    with pytest.raises(RuntimeError, match="explicit long or writer expiry"):
        command.upgrade(config, "head")

    with engine.begin() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260810_0048"
        )
        connection.execute(
            sa.text(
                "UPDATE transaction_record SET "
                "lifecycle_event_type = 'option_long_expiry', "
                "gross_amount = 0, source_gross_amount = 0 "
                "WHERE transaction_id = :transaction_id"
            ),
            {"transaction_id": transaction_id},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        terms = connection.scalar(
            sa.text(
                "SELECT terms_json FROM derivative_contract_record "
                "WHERE derivative_contract_id = 'migration-option'"
            )
        )
        if isinstance(terms, str):
            terms = json.loads(terms)
        assert isinstance(terms, dict)
        assert "settlement_type" not in terms

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE transaction_record "
                "SET lifecycle_event_type = 'option_auto_exercise' "
                "WHERE transaction_id = :transaction_id"
            ),
            {"transaction_id": transaction_id},
        )
