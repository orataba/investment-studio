from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, time
import json
import os
from pathlib import Path
from threading import Barrier
from typing import Iterator
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from portfolio_app.db.models import (
    AccountRecordModel,
    PortfolioRecordModel,
    TransactionCurrentModel,
    TransactionIdentityRecordModel,
    TransactionRevisionGroupRecordModel,
    TransactionRevisionRecordModel,
)
from portfolio_app.services.transaction_revisions import (
    CreateTransactionRevision,
    DeleteTransactionRevision,
    TransactionFactPayload,
    TransactionRevisionContext,
    append_transaction_revision_batch,
    tombstone_payload_hash,
    transaction_payload_hash,
)


pytestmark = pytest.mark.postgresql_integration

BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
DEFAULT_POSTGRES_URL = (
    "postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops"
)
PAYLOAD_SCHEMA_VERSION = "transaction-revision.v1"


def _admin_database_url(database_url: str) -> str:
    return make_url(database_url).set(database="postgres").render_as_string(
        hide_password=False
    )


def _is_server_unavailable(error: OperationalError) -> bool:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate is not None:
        return str(sqlstate).startswith("08") or sqlstate == "57P03"
    return "connection refused" in str(error).lower()


def _run_migrations(database_url: str) -> None:
    instrument_config = Config(
        str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic.ini")
    )
    instrument_config.set_main_option(
        "script_location",
        str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic"),
    )
    command.upgrade(instrument_config, "head")

    portfolio_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    portfolio_config.set_main_option(
        "script_location", str(BACKEND_ROOT / "alembic")
    )
    portfolio_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(portfolio_config, "head")


@pytest.fixture(scope="module")
def postgres_ledger_env() -> Iterator[dict[str, object]]:
    base_url = os.getenv("PORTFOLIO_OPS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)
    database_name = f"portfolio_ops_ledger_{uuid4().hex[:8]}"
    database_url = make_url(base_url).set(database=database_name).render_as_string(
        hide_password=False
    )
    admin_engine = create_engine(
        _admin_database_url(base_url), isolation_level="AUTOCOMMIT"
    )
    database_created = False
    ledger_engine: Engine | None = None
    previous_environment = {
        name: os.environ.get(name)
        for name in (
            "PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE",
            "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL",
            "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA",
            "PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL",
            "PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL",
            "PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA",
        )
    }
    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except OperationalError as exc:  # pragma: no cover - environment dependent
            if _is_server_unavailable(exc):
                pytest.skip(f"PostgreSQL server is unavailable: {exc}")
            raise

        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        database_created = True

        os.environ.update(
            {
                "PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE": database_name,
                "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL": database_url,
                "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA": "instrument_registry",
                "PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL": database_url,
                "PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL": database_url,
                "PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA": "portfolio",
            }
        )

        from portfolio_app.core import settings as settings_module
        from portfolio_app.db import session as session_module

        settings_module.get_settings.cache_clear()
        session_module.get_engine.cache_clear()
        session_module.get_session_factory.cache_clear()
        _run_migrations(database_url)

        ledger_engine = session_module.get_engine()
        yield {
            "engine": ledger_engine,
            "session_factory": session_module.get_session_factory(),
        }
    finally:
        if ledger_engine is not None:
            ledger_engine.dispose()
        try:
            from portfolio_app.core import settings as settings_module
            from portfolio_app.db import session as session_module

            session_module.get_engine.cache_clear()
            session_module.get_session_factory.cache_clear()
            settings_module.get_settings.cache_clear()
        except ImportError:  # pragma: no cover - setup failed before imports
            pass

        for name, previous_value in previous_environment.items():
            if previous_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous_value

        if database_created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
                )
        admin_engine.dispose()


def _context(reason: str) -> TransactionRevisionContext:
    return TransactionRevisionContext(
        source_kind="system",
        change_reason=reason,
        actor_type="service",
        actor_id="test:postgres-ledger",
        actor_display_name="PostgreSQL ledger constraint test",
        actor_source="trusted_service",
        recorded_at=datetime(2026, 7, 13, 9, 30, tzinfo=UTC),
    )


def _seed_portfolio(
    session_factory: sessionmaker,
    *,
    portfolio_id: str,
) -> tuple[str, str]:
    outbound_account_id = f"{portfolio_id}-out"
    inbound_account_id = f"{portfolio_id}-in"
    with session_factory() as session:
        session.add(
            PortfolioRecordModel(
                portfolio_id=portfolio_id,
                portfolio_name=portfolio_id,
                base_currency="USD",
                valuation_timezone="UTC",
                valuation_cutoff_policy="latest_complete_eod",
            )
        )
        for account_id in (outbound_account_id, inbound_account_id):
            session.add(
                AccountRecordModel(
                    account_id=account_id,
                    portfolio_id=portfolio_id,
                    account_name=account_id,
                    account_type="deposit_account",
                    currency="USD",
                    status="active",
                )
            )
        session.commit()
    return outbound_account_id, inbound_account_id


def _cash_leg(
    *,
    transaction_type: str,
    account_id: str,
    counterparty_account_id: str,
    transfer_group_id: str,
    gross_amount: str = "100.00000000",
    note: str = "数据库转账校验",
) -> TransactionFactPayload:
    return TransactionFactPayload(
        transaction_type=transaction_type,
        trade_date=date(2026, 7, 13),
        trade_time=time(9, 30),
        trade_at=datetime(2026, 7, 13, 9, 30, tzinfo=UTC),
        trade_timezone="UTC",
        trade_time_is_estimated=False,
        settlement_date=date(2026, 7, 13),
        account_id=account_id,
        gross_amount=gross_amount,
        fees="0.00000000",
        taxes="0.00000000",
        currency="USD",
        transfer_scope="internal_portfolio",
        transfer_object_type="cash",
        transfer_group_id=transfer_group_id,
        counterparty_account_id=counterparty_account_id,
        note=note,
    )


def _deposit(*, account_id: str, note: str = "数据库 hash 校验") -> TransactionFactPayload:
    return TransactionFactPayload(
        transaction_type="deposit",
        trade_date=date(2026, 7, 13),
        trade_time=time(9, 30, 0, 123456),
        trade_at=datetime(2026, 7, 13, 9, 30, 0, 123456, tzinfo=UTC),
        trade_timezone="UTC",
        trade_time_is_estimated=False,
        settlement_date=date(2026, 7, 13),
        account_id=account_id,
        gross_amount="123.45670000",
        fees="0.00000000",
        taxes="0.00000000",
        currency="USD",
        note=note,
    )


def _create_pair(
    session_factory: sessionmaker,
    *,
    portfolio_id: str,
    outbound_id: str,
    inbound_id: str,
    transfer_group_id: str,
    outbound_account_id: str,
    inbound_account_id: str,
) -> tuple[TransactionFactPayload, TransactionFactPayload]:
    outbound = _cash_leg(
        transaction_type="transfer_out",
        account_id=outbound_account_id,
        counterparty_account_id=inbound_account_id,
        transfer_group_id=transfer_group_id,
    )
    inbound = _cash_leg(
        transaction_type="transfer_in",
        account_id=inbound_account_id,
        counterparty_account_id=outbound_account_id,
        transfer_group_id=transfer_group_id,
    )
    context = _context("Create a valid service transfer pair")
    with session_factory() as session:
        append_transaction_revision_batch(
            session,
            portfolio_id=portfolio_id,
            context=context,
            mutations=(
                CreateTransactionRevision(
                    transaction_id=outbound_id,
                    facts=outbound,
                    created_at=context.recorded_at,
                ),
                CreateTransactionRevision(
                    transaction_id=inbound_id,
                    facts=inbound,
                    created_at=context.recorded_at,
                ),
            ),
        )
        session.commit()
    return outbound, inbound


def _current_revision_id(
    session_factory: sessionmaker,
    *,
    portfolio_id: str,
    transaction_id: str,
) -> str:
    with session_factory() as session:
        record = session.scalar(
            select(TransactionCurrentModel).where(
                TransactionCurrentModel.portfolio_id == portfolio_id,
                TransactionCurrentModel.transaction_id == transaction_id,
            )
        )
        assert record is not None
        return record.current_revision_id


def _insert_group(
    connection,
    *,
    portfolio_id: str,
    revision_group_id: str,
    reason: str,
    actor_type: str = "service",
    actor_source: str = "trusted_service",
) -> None:
    connection.execute(
        insert(TransactionRevisionGroupRecordModel.__table__),
        {
            "revision_group_id": revision_group_id,
            "portfolio_id": portfolio_id,
            "source_kind": "system",
            "change_reason": reason,
            "actor_type": actor_type,
            "actor_id": "test:raw-sql",
            "actor_display_name": "Raw SQL constraint test",
            "actor_source": actor_source,
            "recorded_at": datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
        },
    )


def test_postgres_enforces_actor_source_domain_and_preserves_append_only_guard(
    postgres_ledger_env: dict[str, object],
) -> None:
    engine = postgres_ledger_env["engine"]
    session_factory = postgres_ledger_env["session_factory"]
    assert isinstance(engine, Engine)
    assert isinstance(session_factory, sessionmaker)
    portfolio_id = "ledger-actor-source"
    _seed_portfolio(session_factory, portfolio_id=portfolio_id)

    invalid_cases = (
        (
            "invalid-source",
            "service",
            "untrusted_client",
            "ck_transaction_revision_group_record_actor_source",
        ),
        (
            "invalid-pairing",
            "user",
            "trusted_service",
            "ck_transaction_revision_group_record_actor_source_type",
        ),
    )
    for group_id, actor_type, actor_source, expected_constraint in invalid_cases:
        with pytest.raises(IntegrityError) as exc_info:
            with engine.begin() as connection:
                _insert_group(
                    connection,
                    portfolio_id=portfolio_id,
                    revision_group_id=group_id,
                    reason="Attempt invalid actor provenance",
                    actor_type=actor_type,
                    actor_source=actor_source,
                )
        assert expected_constraint in str(exc_info.value)

    canonical_pairs = (
        ("user-client", "user", "client_asserted"),
        ("user-principal", "user", "authenticated_principal"),
        ("service", "service", "trusted_service"),
        ("migration", "migration", "migration"),
    )
    with engine.begin() as connection:
        for group_id, actor_type, actor_source in canonical_pairs:
            _insert_group(
                connection,
                portfolio_id=portfolio_id,
                revision_group_id=group_id,
                reason="Persist canonical actor provenance",
                actor_type=actor_type,
                actor_source=actor_source,
            )

    with engine.connect() as connection:
        persisted_pairs = {
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT actor_type, actor_source
                    FROM transaction_revision_group_record
                    WHERE portfolio_id = :portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            )
        }
        assert persisted_pairs == {
            (actor_type, actor_source)
            for _group_id, actor_type, actor_source in canonical_pairs
        }
        trigger_contract = tuple(
            connection.execute(
                text(
                    """
                    SELECT
                        ledger_trigger.tgenabled,
                        ledger_trigger.tgtype::integer,
                        trigger_function.proname,
                        function_namespace.nspname
                    FROM pg_trigger AS ledger_trigger
                    JOIN pg_class AS ledger_table
                      ON ledger_table.oid = ledger_trigger.tgrelid
                    JOIN pg_namespace AS table_namespace
                      ON table_namespace.oid = ledger_table.relnamespace
                    JOIN pg_proc AS trigger_function
                      ON trigger_function.oid = ledger_trigger.tgfoid
                    JOIN pg_namespace AS function_namespace
                      ON function_namespace.oid = trigger_function.pronamespace
                    WHERE table_namespace.nspname = 'portfolio'
                      AND ledger_table.relname =
                          'transaction_revision_group_record'
                      AND ledger_trigger.tgname =
                          'trg_transaction_revision_group_record_append_only'
                      AND NOT ledger_trigger.tgisinternal
                    """
                )
            ).one()
        )
    assert trigger_contract == (
        "O",
        27,
        "reject_transaction_ledger_mutation",
        "portfolio",
    )

    with pytest.raises(OperationalError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE transaction_revision_group_record
                    SET change_reason = 'Forbidden rewrite'
                    WHERE revision_group_id = 'service'
                    """
                )
            )


def _insert_identity(
    connection,
    *,
    portfolio_id: str,
    transaction_id: str,
) -> None:
    connection.execute(
        insert(TransactionIdentityRecordModel.__table__),
        {
            "transaction_id": transaction_id,
            "portfolio_id": portfolio_id,
            "created_at": datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
            "created_by": "test:raw-sql",
        },
    )


def _live_revision_values(
    *,
    portfolio_id: str,
    transaction_id: str,
    revision_id: str,
    revision_group_id: str,
    facts: TransactionFactPayload,
    payload_hash: str | None = None,
    revision_number: int = 1,
    revision_kind: str = "create",
    supersedes_revision_id: str | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "revision_id": revision_id,
        "portfolio_id": portfolio_id,
        "transaction_id": transaction_id,
        "revision_number": revision_number,
        "revision_group_id": revision_group_id,
        "revision_kind": revision_kind,
        "is_tombstone": False,
        "supersedes_revision_id": supersedes_revision_id,
        "supersedes_revision_number": revision_number - 1
        if revision_number > 1
        else None,
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
        "payload_hash": payload_hash or transaction_payload_hash(facts),
        **facts.as_record_values(),
    }
    # Omitting this JSON column persists SQL NULL; binding Python None would
    # intentionally mean JSON null, which the ledger rejects.
    if values["instrument_snapshot_json"] is None:
        values.pop("instrument_snapshot_json")
    return values


def _tombstone_revision_values(
    *,
    portfolio_id: str,
    transaction_id: str,
    revision_id: str,
    revision_group_id: str,
    supersedes_revision_id: str,
) -> dict[str, object]:
    return {
        "revision_id": revision_id,
        "portfolio_id": portfolio_id,
        "transaction_id": transaction_id,
        "revision_number": 2,
        "revision_group_id": revision_group_id,
        "revision_kind": "delete",
        "is_tombstone": True,
        "supersedes_revision_id": supersedes_revision_id,
        "supersedes_revision_number": 1,
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
        "payload_hash": tombstone_payload_hash(),
    }


def test_postgres_recomputes_hash_and_accepts_a_valid_service_pair(
    postgres_ledger_env: dict[str, object],
) -> None:
    engine = postgres_ledger_env["engine"]
    session_factory = postgres_ledger_env["session_factory"]
    assert isinstance(engine, Engine)
    assert isinstance(session_factory, sessionmaker)
    portfolio_id = "ledger-hash-pair"
    outbound_account_id, inbound_account_id = _seed_portfolio(
        session_factory, portfolio_id=portfolio_id
    )

    _create_pair(
        session_factory,
        portfolio_id=portfolio_id,
        outbound_id="valid-out",
        inbound_id="valid-in",
        transfer_group_id="valid-pair",
        outbound_account_id=outbound_account_id,
        inbound_account_id=inbound_account_id,
    )
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                """
                SELECT count(*)
                FROM transaction_revision_record AS revision
                WHERE revision.portfolio_id = :portfolio_id
                  AND revision.payload_hash = transaction_revision_payload_hash_v1(revision)
                """
            ),
            {"portfolio_id": portfolio_id},
        ) == 2
        json_edge_cases = {
            "control": "line one\nline two",
            "float_boundaries": [
                1.0,
                -0.0,
                1e15,
                1e16,
                1e-7,
                6.516451664693965e-278,
                1.7976931348623157e308,
                0.0001,
                1234567890123456.8,
            ],
            "nested": {"bool": True, "integer": 7, "null": None},
            "unicode": "均成 CTA",
        }
        database_canonical_json = connection.scalar(
            text(
                "SELECT canonical_transaction_json_v1(CAST(:payload AS json))"
            ),
            {
                "payload": json.dumps(
                    json_edge_cases,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            },
        )
        assert database_canonical_json == json.dumps(
            json_edge_cases,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    bad_transaction_id = "forged-hash"
    with pytest.raises(IntegrityError, match="payload_hash does not match"):
        with engine.begin() as connection:
            _insert_identity(
                connection,
                portfolio_id=portfolio_id,
                transaction_id=bad_transaction_id,
            )
            _insert_group(
                connection,
                portfolio_id=portfolio_id,
                revision_group_id="forged-hash-group",
                reason="Attempt a forged payload hash",
            )
            connection.execute(
                insert(TransactionRevisionRecordModel.__table__),
                _live_revision_values(
                    portfolio_id=portfolio_id,
                    transaction_id=bad_transaction_id,
                    revision_id="forged-hash-revision",
                    revision_group_id="forged-hash-group",
                    facts=_deposit(account_id=outbound_account_id),
                    payload_hash="sha256:" + "0" * 64,
                ),
            )


def test_postgres_rejects_raw_single_leg_mismatch_and_single_leg_delete(
    postgres_ledger_env: dict[str, object],
) -> None:
    engine = postgres_ledger_env["engine"]
    session_factory = postgres_ledger_env["session_factory"]
    assert isinstance(engine, Engine)
    assert isinstance(session_factory, sessionmaker)
    portfolio_id = "ledger-transfer-attacks"
    outbound_account_id, inbound_account_id = _seed_portfolio(
        session_factory, portfolio_id=portfolio_id
    )

    single_facts = _cash_leg(
        transaction_type="transfer_out",
        account_id=outbound_account_id,
        counterparty_account_id=inbound_account_id,
        transfer_group_id="raw-single-leg",
    )
    with pytest.raises(IntegrityError, match="exactly one historical in leg"):
        with engine.begin() as connection:
            _insert_identity(
                connection,
                portfolio_id=portfolio_id,
                transaction_id="raw-single-out",
            )
            _insert_group(
                connection,
                portfolio_id=portfolio_id,
                revision_group_id="raw-single-create-group",
                reason="Attempt a singleton transfer",
            )
            connection.execute(
                insert(TransactionRevisionRecordModel.__table__),
                _live_revision_values(
                    portfolio_id=portfolio_id,
                    transaction_id="raw-single-out",
                    revision_id="raw-single-out-r1",
                    revision_group_id="raw-single-create-group",
                    facts=single_facts,
                ),
            )

    mismatched_out = _cash_leg(
        transaction_type="transfer_out",
        account_id=outbound_account_id,
        counterparty_account_id=inbound_account_id,
        transfer_group_id="raw-mismatched-pair",
    )
    mismatched_in = _cash_leg(
        transaction_type="transfer_in",
        account_id=inbound_account_id,
        counterparty_account_id=outbound_account_id,
        transfer_group_id="raw-mismatched-pair",
        gross_amount="101.00000000",
    )
    with pytest.raises(IntegrityError, match="non-mirrored facts"):
        with engine.begin() as connection:
            _insert_group(
                connection,
                portfolio_id=portfolio_id,
                revision_group_id="raw-mismatch-create-group",
                reason="Attempt mismatched transfer facts",
            )
            for transaction_id, revision_id, facts in (
                ("raw-mismatch-out", "raw-mismatch-out-r1", mismatched_out),
                ("raw-mismatch-in", "raw-mismatch-in-r1", mismatched_in),
            ):
                _insert_identity(
                    connection,
                    portfolio_id=portfolio_id,
                    transaction_id=transaction_id,
                )
                connection.execute(
                    insert(TransactionRevisionRecordModel.__table__),
                    _live_revision_values(
                        portfolio_id=portfolio_id,
                        transaction_id=transaction_id,
                        revision_id=revision_id,
                        revision_group_id="raw-mismatch-create-group",
                        facts=facts,
                    ),
                )

    _create_pair(
        session_factory,
        portfolio_id=portfolio_id,
        outbound_id="delete-attack-out",
        inbound_id="delete-attack-in",
        transfer_group_id="delete-attack-pair",
        outbound_account_id=outbound_account_id,
        inbound_account_id=inbound_account_id,
    )
    delete_attack_out_revision_id = _current_revision_id(
        session_factory,
        portfolio_id=portfolio_id,
        transaction_id="delete-attack-out",
    )
    with pytest.raises(IntegrityError, match="live or deleted as a complete pair"):
        with engine.begin() as connection:
            _insert_group(
                connection,
                portfolio_id=portfolio_id,
                revision_group_id="single-delete-group",
                reason="Attempt a single-leg transfer delete",
            )
            connection.execute(
                insert(TransactionRevisionRecordModel.__table__),
                _tombstone_revision_values(
                    portfolio_id=portfolio_id,
                    transaction_id="delete-attack-out",
                    revision_id="delete-attack-out-r2",
                    revision_group_id="single-delete-group",
                    supersedes_revision_id=delete_attack_out_revision_id,
                ),
            )

    with session_factory() as session:
        assert {
            row.transaction_id
            for row in session.scalars(
                select(TransactionCurrentModel).where(
                    TransactionCurrentModel.portfolio_id == portfolio_id,
                    TransactionCurrentModel.transfer_group_id == "delete-attack-pair",
                )
            )
        } == {"delete-attack-out", "delete-attack-in"}


def test_postgres_rejects_transfer_amend_and_historical_group_reuse(
    postgres_ledger_env: dict[str, object],
) -> None:
    engine = postgres_ledger_env["engine"]
    session_factory = postgres_ledger_env["session_factory"]
    assert isinstance(engine, Engine)
    assert isinstance(session_factory, sessionmaker)
    portfolio_id = "ledger-transfer-history"
    outbound_account_id, inbound_account_id = _seed_portfolio(
        session_factory, portfolio_id=portfolio_id
    )
    outbound, _inbound = _create_pair(
        session_factory,
        portfolio_id=portfolio_id,
        outbound_id="history-out",
        inbound_id="history-in",
        transfer_group_id="history-pair",
        outbound_account_id=outbound_account_id,
        inbound_account_id=inbound_account_id,
    )
    history_out_revision_id = _current_revision_id(
        session_factory,
        portfolio_id=portfolio_id,
        transaction_id="history-out",
    )

    with pytest.raises(IntegrityError, match="cannot be amended"):
        with engine.begin() as connection:
            _insert_group(
                connection,
                portfolio_id=portfolio_id,
                revision_group_id="raw-transfer-amend-group",
                reason="Attempt to amend one transfer leg",
            )
            connection.execute(
                insert(TransactionRevisionRecordModel.__table__),
                _live_revision_values(
                    portfolio_id=portfolio_id,
                    transaction_id="history-out",
                    revision_id="history-out-r2-amend",
                    revision_group_id="raw-transfer-amend-group",
                    facts=replace(outbound, note="forged amendment"),
                    revision_number=2,
                    revision_kind="amend",
                    supersedes_revision_id=history_out_revision_id,
                ),
            )

    with session_factory() as session:
        current_rows = session.scalars(
            select(TransactionCurrentModel).where(
                TransactionCurrentModel.portfolio_id == portfolio_id,
                TransactionCurrentModel.transfer_group_id == "history-pair",
            )
        ).all()
        current_by_id = {row.transaction_id: row for row in current_rows}
        delete_context = _context("Delete a valid pair before testing group reuse")
        append_transaction_revision_batch(
            session,
            portfolio_id=portfolio_id,
            context=delete_context,
            mutations=tuple(
                DeleteTransactionRevision(
                    transaction_id=transaction_id,
                    expected_revision_id=current_by_id[transaction_id].current_revision_id,
                    expected_revision_number=current_by_id[
                        transaction_id
                    ].current_revision_number,
                )
                for transaction_id in ("history-out", "history-in")
            ),
        )
        session.commit()

    reused_out = replace(outbound, transfer_group_id="history-pair")
    reused_in = _cash_leg(
        transaction_type="transfer_in",
        account_id=inbound_account_id,
        counterparty_account_id=outbound_account_id,
        transfer_group_id="history-pair",
    )
    with pytest.raises(IntegrityError, match="exactly one historical in leg"):
        with engine.begin() as connection:
            _insert_group(
                connection,
                portfolio_id=portfolio_id,
                revision_group_id="raw-reused-transfer-create-group",
                reason="Attempt to reuse a deleted transfer group",
            )
            for transaction_id, revision_id, facts in (
                ("reused-out", "reused-out-r1", reused_out),
                ("reused-in", "reused-in-r1", reused_in),
            ):
                _insert_identity(
                    connection,
                    portfolio_id=portfolio_id,
                    transaction_id=transaction_id,
                )
                connection.execute(
                    insert(TransactionRevisionRecordModel.__table__),
                    _live_revision_values(
                        portfolio_id=portfolio_id,
                        transaction_id=transaction_id,
                        revision_id=revision_id,
                        revision_group_id="raw-reused-transfer-create-group",
                        facts=facts,
                    ),
                )


def test_postgres_serializes_concurrent_transfer_group_reuse(
    postgres_ledger_env: dict[str, object],
) -> None:
    engine = postgres_ledger_env["engine"]
    session_factory = postgres_ledger_env["session_factory"]
    assert isinstance(engine, Engine)
    assert isinstance(session_factory, sessionmaker)
    portfolio_id = "ledger-transfer-concurrency"
    outbound_account_id, inbound_account_id = _seed_portfolio(
        session_factory, portfolio_id=portfolio_id
    )
    barrier = Barrier(2)

    def attempt_pair(prefix: str) -> str:
        outbound = _cash_leg(
            transaction_type="transfer_out",
            account_id=outbound_account_id,
            counterparty_account_id=inbound_account_id,
            transfer_group_id="concurrent-group",
        )
        inbound = _cash_leg(
            transaction_type="transfer_in",
            account_id=inbound_account_id,
            counterparty_account_id=outbound_account_id,
            transfer_group_id="concurrent-group",
        )
        try:
            with engine.begin() as connection:
                revision_group_id = f"{prefix}-create-group"
                _insert_group(
                    connection,
                    portfolio_id=portfolio_id,
                    revision_group_id=revision_group_id,
                    reason="Race a transfer group create",
                )
                for suffix in ("out", "in"):
                    _insert_identity(
                        connection,
                        portfolio_id=portfolio_id,
                        transaction_id=f"{prefix}-{suffix}",
                    )
                barrier.wait(timeout=5)
                for suffix, facts in (("out", outbound), ("in", inbound)):
                    connection.execute(
                        insert(TransactionRevisionRecordModel.__table__),
                        _live_revision_values(
                            portfolio_id=portfolio_id,
                            transaction_id=f"{prefix}-{suffix}",
                            revision_id=f"{prefix}-{suffix}-r1",
                            revision_group_id=revision_group_id,
                            facts=facts,
                        ),
                    )
            return "committed"
        except IntegrityError as exc:
            assert "exactly one historical in leg" in str(exc)
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(attempt_pair, ("race-a", "race-b")))

    assert outcomes == ["committed", "rejected"]
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                """
                SELECT count(*)
                FROM transaction_revision_record
                WHERE portfolio_id = :portfolio_id
                  AND transfer_group_id = 'concurrent-group'
                """
            ),
            {"portfolio_id": portfolio_id},
        ) == 2
