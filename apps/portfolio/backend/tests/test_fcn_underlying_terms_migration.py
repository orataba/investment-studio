from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260812_0050")

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def _insert_legacy_fcn(*, barrier_type: str, barrier_level: int | None) -> None:
    engine = get_engine()
    with engine.begin() as connection:
        account = connection.execute(
            sa.text(
                "SELECT portfolio_id, account_id, currency FROM account_record "
                "WHERE account_type = 'securities_account' ORDER BY account_id LIMIT 1"
            )
        ).mappings().one()
        connection.execute(
            sa.text(
                "INSERT INTO derivative_contract_record ("
                "derivative_contract_id, portfolio_id, account_id, contract_name, "
                "contract_type, currency, external_reference, terms_json, created_at"
                ") VALUES ("
                "'legacy-fcn', :portfolio_id, :account_id, 'Legacy FCN', "
                "'fcn', :currency, 'LEGACY-FCN', :terms_json, '2026-08-01T00:00:00Z'"
                ")"
            ),
            {
                **account,
                "terms_json": json.dumps(
                    {
                        "notional": "500000",
                        "issue_date": "2026-08-07",
                        "maturity_date": "2027-02-08",
                        "issuer": "Test Bank",
                        "counterparty": "Test Broker",
                        "underlying_instrument_ids": ["equity-us-abbv"],
                        "deliverable_instrument_ids": ["equity-us-abbv"],
                        "barrier_type": barrier_type,
                        "barrier_level": barrier_level,
                    }
                ),
            },
        )


def test_fcn_terms_migration_moves_barrier_to_each_underlying() -> None:
    _insert_legacy_fcn(barrier_type="knock_in", barrier_level=70)

    command.upgrade(_alembic_config(), "head")

    with get_engine().connect() as connection:
        terms = connection.scalar(
            sa.text(
                "SELECT terms_json FROM derivative_contract_record "
                "WHERE derivative_contract_id = 'legacy-fcn'"
            )
        )
    if isinstance(terms, str):
        terms = json.loads(terms)
    assert terms == {
        "notional": "500000",
        "annual_coupon_rate_pct": None,
        "issue_date": "2026-08-07",
        "final_observation_date": None,
        "maturity_date": "2027-02-08",
        "issuer": "Test Bank",
        "counterparty": "Test Broker",
        "underlyings": [
            {
                "instrument_id": "equity-us-abbv",
                "initial_reference_price": None,
                "strike_level_pct": None,
                "knock_in_level_pct": 70,
                "knock_out_level_pct": None,
                "deliverable": True,
            }
        ],
    }


def test_fcn_terms_migration_rejects_ambiguous_dual_barrier() -> None:
    _insert_legacy_fcn(barrier_type="dual", barrier_level=70)

    with pytest.raises(RuntimeError, match="ambiguous legacy dual barrier"):
        command.upgrade(_alembic_config(), "head")

    with get_engine().connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260812_0050"
        )
