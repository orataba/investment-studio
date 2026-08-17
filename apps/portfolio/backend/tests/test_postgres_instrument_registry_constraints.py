from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT_STR = str(BACKEND_ROOT)
if BACKEND_ROOT_STR in sys.path:
    sys.path.remove(BACKEND_ROOT_STR)
sys.path.insert(0, BACKEND_ROOT_STR)

WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
INSTRUMENT_CORE_PYTHON = WORKSPACE_ROOT / "packages" / "instrument-core" / "python"
INSTRUMENT_CORE_PYTHON_STR = str(INSTRUMENT_CORE_PYTHON)
if INSTRUMENT_CORE_PYTHON_STR in sys.path:
    sys.path.remove(INSTRUMENT_CORE_PYTHON_STR)
sys.path.insert(0, INSTRUMENT_CORE_PYTHON_STR)

from portfolio_app.db.models import (
    AccountRecordModel,
    PortfolioRecordModel,
    TransactionRecordModel,
)
from portfolio_ops_instrument_core import instrument_store as shared_store


pytestmark = pytest.mark.postgresql_integration

def _run_instrument_registry_upgrade() -> None:
    config = Config(str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic"),
    )
    command.upgrade(config, "head")


def _run_portfolio_upgrade(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _admin_database_url(database_url: str) -> str:
    url = make_url(database_url)
    return url.set(database="postgres").render_as_string(hide_password=False)


@pytest.fixture
def postgres_portfolio_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    base_database_url = os.getenv("PORTFOLIO_OPS_TEST_POSTGRES_URL")
    if not base_database_url:
        pytest.skip("PORTFOLIO_OPS_TEST_POSTGRES_URL is not explicitly configured.")
    database_name = f"portfolio_ops_portfolio_fk_{uuid4().hex[:8]}"
    database_url = make_url(base_database_url).set(database=database_name).render_as_string(hide_password=False)
    admin_engine = create_engine(_admin_database_url(base_database_url), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as exc:  # pragma: no cover - environment-dependent skip
        pytest.skip(f"PostgreSQL is not available for integration test: {exc}")
    finally:
        admin_engine.dispose()

    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "instrument_registry")
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA", "portfolio")

    from portfolio_app.core import settings as settings_module
    from portfolio_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    _run_instrument_registry_upgrade()
    _run_portfolio_upgrade(database_url)

    session_factory = session_module.get_session_factory()
    identifier_value = f"PORTFK{uuid4().hex[:8].upper()}"
    instrument = shared_store.create_instrument(
        session_factory,
        instrument_name="Portfolio FK Integration Asset",
        instrument_type="equity",
        currency="USD",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": identifier_value,
                "is_primary": True,
            }
        ],
    )

    yield {
        "database_url": database_url,
        "database_schema": "portfolio",
        "instrument_id": str(instrument["instrument_id"]),
    }

    cleanup_engine = create_engine(_admin_database_url(base_database_url), isolation_level="AUTOCOMMIT")
    try:
        with cleanup_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name
                      AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    finally:
        cleanup_engine.dispose()

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


def test_transaction_record_instrument_registry_fk_is_enforced(
    postgres_portfolio_env: dict[str, str],
) -> None:
    from portfolio_app.db import session as session_module

    engine = create_engine(postgres_portfolio_env["database_url"])
    try:
        with engine.connect() as connection:
            constraint = connection.execute(
                text(
                    """
                    SELECT
                        con.conname AS constraint_name,
                        ref_ns.nspname AS referred_schema,
                        ref_cls.relname AS referred_table
                    FROM pg_constraint con
                    JOIN pg_class cls
                        ON cls.oid = con.conrelid
                    JOIN pg_namespace cls_ns
                        ON cls_ns.oid = cls.relnamespace
                    JOIN pg_class ref_cls
                        ON ref_cls.oid = con.confrelid
                    JOIN pg_namespace ref_ns
                        ON ref_ns.oid = ref_cls.relnamespace
                    WHERE cls_ns.nspname = :schema
                      AND cls.relname = 'transaction_record'
                      AND con.conname = 'fk_transaction_record_instrument_id_instrument'
                    """
                ),
                {"schema": postgres_portfolio_env["database_schema"]},
            ).mappings().first()
    finally:
        engine.dispose()

    assert constraint is not None
    assert constraint["referred_schema"] == "instrument_registry"
    assert constraint["referred_table"] == "instrument"

    session_factory = session_module.get_session_factory()
    portfolio_id = f"portfolio-fk-{uuid4().hex[:8]}"
    valid_transaction_id = f"tx-valid-{uuid4().hex[:8]}"
    invalid_transaction_id = f"tx-invalid-{uuid4().hex[:8]}"
    orphan_account_transaction_id = f"tx-orphan-account-{uuid4().hex[:8]}"

    with session_factory() as session:
        session.add(
            PortfolioRecordModel(
                portfolio_id=portfolio_id,
                portfolio_name="Constraint Verification Portfolio",
                base_currency="USD",
                valuation_timezone="UTC",
                valuation_cutoff_policy="close",
            )
        )
        session.add(
            AccountRecordModel(
                account_id="account-fk-cash",
                portfolio_id=portfolio_id,
                account_name="Constraint Verification Cash",
                account_type="deposit_account",
                account_category="cash",
                currency="USD",
                status="active",
            )
        )
        session.add(
            AccountRecordModel(
                account_id="account-fk-valid",
                portfolio_id=portfolio_id,
                account_name="Constraint Verification Account",
                account_type="securities_account",
                account_category="security",
                currency="USD",
                default_settlement_cash_account_id="account-fk-cash",
                cost_basis_method="fifo",
                status="active",
            )
        )
        session.commit()

    with session_factory() as session:
        session.add(
            TransactionRecordModel(
                transaction_id=valid_transaction_id,
                transaction_sequence=1,
                portfolio_id=portfolio_id,
                transaction_type="buy",
                trade_date=date(2026, 4, 21),
                trade_time="09:30",
                trade_at="2026-04-21T09:30:00Z",
                trade_timezone="UTC",
                settlement_date=date(2026, 4, 23),
                account_id="account-fk-valid",
                instrument_id=postgres_portfolio_env["instrument_id"],
                gross_amount=1000.0,
                currency="USD",
            )
        )
        session.commit()

    with session_factory() as session:
        session.add(
            TransactionRecordModel(
                transaction_id=invalid_transaction_id,
                transaction_sequence=2,
                portfolio_id=portfolio_id,
                transaction_type="buy",
                trade_date=date(2026, 4, 21),
                trade_time="09:45",
                trade_at="2026-04-21T09:45:00Z",
                trade_timezone="UTC",
                settlement_date=date(2026, 4, 23),
                account_id="account-fk-valid",
                instrument_id=f"missing-{uuid4().hex[:8]}",
                gross_amount=1200.0,
                currency="USD",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with session_factory() as session:
        session.add(
            TransactionRecordModel(
                transaction_id=orphan_account_transaction_id,
                transaction_sequence=3,
                portfolio_id=portfolio_id,
                transaction_type="buy",
                trade_date=date(2026, 4, 21),
                trade_time="10:00",
                trade_at="2026-04-21T10:00:00Z",
                trade_timezone="UTC",
                settlement_date=date(2026, 4, 23),
                account_id="missing-account",
                instrument_id=postgres_portfolio_env["instrument_id"],
                gross_amount=1200.0,
                currency="USD",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
