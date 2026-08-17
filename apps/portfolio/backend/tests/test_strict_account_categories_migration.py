from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260816_0051")

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def _seed_mixed_account() -> None:
    option_terms = {
        "underlying_instrument_id": "equity-us-abbv",
        "option_type": "call",
        "expiry_date": "2026-12-18",
        "strike": 220,
        "contract_multiplier": 100,
    }
    fcn_terms = {
        "notional": 100000,
        "annual_coupon_rate_pct": 10,
        "issue_date": "2026-01-01",
        "final_observation_date": "2026-06-29",
        "maturity_date": "2026-06-30",
        "issuer": "Test Bank",
        "counterparty": "Test Broker",
        "underlyings": [
            {
                "instrument_id": "equity-us-abbv",
                "initial_reference_price": 200,
                "strike_level_pct": 100,
                "knock_in_level_pct": 70,
                "knock_out_level_pct": None,
                "deliverable": True,
            }
        ],
    }
    with get_engine().begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE account_record "
                "SET allowed_instrument_types_json = :scope "
                "WHERE account_id = 'broker-us-core'"
            ),
            {"scope": json.dumps(["equity", "fcn", "option"])},
        )
        for contract_id, contract_type, terms in (
            ("legacy-option", "option", option_terms),
            ("legacy-fcn", "fcn", fcn_terms),
        ):
            connection.execute(
                sa.text(
                    "INSERT INTO derivative_contract_record ("
                    "derivative_contract_id, portfolio_id, account_id, contract_name, "
                    "contract_type, currency, external_reference, terms_json, created_at"
                    ") VALUES ("
                    ":contract_id, 'portfolio-ops', 'broker-us-core', :contract_id, "
                    ":contract_type, 'USD', :external_reference, :terms, "
                    "'2026-01-01T00:00:00Z')"
                ),
                {
                    "contract_id": contract_id,
                    "contract_type": contract_type,
                    "external_reference": contract_id.upper(),
                    "terms": json.dumps(terms),
                },
            )
        for transaction_id, contract_id in (
            ("txn-0003", "legacy-option"),
            ("txn-0004", "legacy-fcn"),
        ):
            connection.execute(
                sa.text(
                    "UPDATE transaction_record "
                    "SET instrument_id = NULL, instrument_ref_json = NULL, "
                    "    derivative_contract_id = :contract_id "
                    "WHERE transaction_id = :transaction_id"
                ),
                {
                    "transaction_id": transaction_id,
                    "contract_id": contract_id,
                },
            )
        connection.execute(
            sa.text(
                "INSERT INTO portfolio_daily_snapshot ("
                "portfolio_id, as_of_date, coverage_state, valuation_coverage_state, "
                "return_coverage_state, book_pnl_coverage_state, "
                "attribution_coverage_state, snapshot_json, calculated_at"
                ") VALUES ("
                "'portfolio-ops', '2026-04-15', 'complete', 'complete', "
                "'complete', 'complete', 'complete', '{}', "
                "'2026-04-15T23:59:59Z')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO portfolio_calculation_state ("
                "portfolio_id, daily_snapshot_status"
                ") VALUES ('portfolio-ops', 'fresh')"
            )
        )


def test_migration_splits_mixed_accounts_and_moves_derivative_facts() -> None:
    _seed_mixed_account()

    command.upgrade(_alembic_config(), "head")

    with get_engine().connect() as connection:
        categories = connection.execute(
            sa.text(
                "SELECT account_category FROM account_record "
                "WHERE portfolio_id = 'portfolio-ops' "
                "AND (account_id = 'broker-us-core' "
                "     OR account_id LIKE 'broker-us-core-%')"
            )
        ).scalars().all()
        assert sorted(categories) == ["fcn", "option", "security"]

        contract_categories = connection.execute(
            sa.text(
                "SELECT contract.contract_type, account.account_category "
                "FROM derivative_contract_record contract "
                "JOIN account_record account "
                "  ON account.portfolio_id = contract.portfolio_id "
                " AND account.account_id = contract.account_id "
                "WHERE contract.derivative_contract_id IN "
                "('legacy-fcn', 'legacy-option') "
                "ORDER BY contract.contract_type"
            )
        ).all()
        assert contract_categories == [("fcn", "fcn"), ("option", "option")]

        transaction_categories = connection.execute(
            sa.text(
                "SELECT contract.contract_type, account.account_category "
                "FROM transaction_record txn "
                "JOIN derivative_contract_record contract "
                "  ON contract.portfolio_id = txn.portfolio_id "
                " AND contract.derivative_contract_id = txn.derivative_contract_id "
                "JOIN account_record account "
                "  ON account.portfolio_id = txn.portfolio_id "
                " AND account.account_id = txn.account_id "
                "WHERE txn.transaction_id IN ('txn-0003', 'txn-0004') "
                "ORDER BY contract.contract_type"
            )
        ).all()
        assert transaction_categories == [("fcn", "fcn"), ("option", "option")]

        account_columns = {
            str(column["name"])
            for column in sa.inspect(connection).get_columns("account_record")
        }
        assert "account_category" in account_columns
        assert "allowed_instrument_types_json" not in account_columns
        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM portfolio_daily_snapshot "
                "WHERE portfolio_id = 'portfolio-ops'"
            )
        ) == 0
        state = connection.execute(
            sa.text(
                "SELECT daily_snapshot_status, dirty_from "
                "FROM portfolio_calculation_state "
                "WHERE portfolio_id = 'portfolio-ops'"
            )
        ).one()
        assert state[0] == "stale"
        assert str(state[1]) == "2026-01-02"
