from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine


pytestmark = pytest.mark.migration_base_revision("20260728_0041")


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_REVISION = "20260804_0042"
MIGRATION_PARENT = "20260728_0041"
NEW_COLUMNS = {
    "lifecycle_event_type",
    "source_system",
    "external_reference",
    "event_group_id",
    "related_instrument_id",
}
NEW_INDEXES = {
    "ix_transaction_record_portfolio_event_group",
    "ix_transaction_record_portfolio_related_instrument",
}
NEW_CHECKS = {
    "ck_transaction_record_source_identity",
    "ck_transaction_record_lifecycle_event_type",
}
SOURCE_UNIQUE = "uq_transaction_record_portfolio_source_external"


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    return config


def test_structured_transaction_migration_is_additive_constrained_and_reversible() -> None:
    engine = get_engine()
    config = _alembic_config()

    with engine.connect() as connection:
        columns_before = {
            str(column["name"])
            for column in sa.inspect(connection).get_columns("transaction_record")
        }
        transaction_count = int(
            connection.scalar(sa.text("SELECT COUNT(*) FROM transaction_record")) or 0
        )
    assert transaction_count > 0
    assert NEW_COLUMNS.isdisjoint(columns_before)

    try:
        command.upgrade(config, MIGRATION_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            columns_after = {
                str(column["name"])
                for column in inspector.get_columns("transaction_record")
            }
            indexes_after = {
                str(index["name"])
                for index in inspector.get_indexes("transaction_record")
            }
            checks_after = {
                str(check["name"])
                for check in inspector.get_check_constraints("transaction_record")
            }
            uniques_after = {
                str(unique["name"])
                for unique in inspector.get_unique_constraints("transaction_record")
            }
            assert int(
                connection.scalar(sa.text("SELECT COUNT(*) FROM transaction_record"))
                or 0
            ) == transaction_count

        assert NEW_COLUMNS.issubset(columns_after)
        assert NEW_INDEXES.issubset(indexes_after)
        assert NEW_CHECKS.issubset(checks_after)
        assert SOURCE_UNIQUE in uniques_after

        command.downgrade(config, MIGRATION_PARENT)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            columns_downgraded = {
                str(column["name"])
                for column in inspector.get_columns("transaction_record")
            }
            indexes_downgraded = {
                str(index["name"])
                for index in inspector.get_indexes("transaction_record")
            }
        assert NEW_COLUMNS.isdisjoint(columns_downgraded)
        assert NEW_INDEXES.isdisjoint(indexes_downgraded)
    finally:
        command.upgrade(config, "head")


def test_structured_transaction_migration_refuses_to_drop_populated_identity() -> None:
    engine = get_engine()
    config = _alembic_config()
    command.upgrade(config, MIGRATION_REVISION)

    try:
        with engine.begin() as connection:
            transaction_id = connection.scalar(
                sa.text(
                    "SELECT transaction_id FROM transaction_record "
                    "ORDER BY created_at, transaction_id LIMIT 1"
                )
            )
            assert transaction_id is not None
            connection.execute(
                sa.text(
                    "UPDATE transaction_record "
                    "SET lifecycle_event_type = 'fcn_knock_in', "
                    "source_system = 'migration-test', "
                    "external_reference = 'migration-test-1' "
                    "WHERE transaction_id = :transaction_id"
                ),
                {"transaction_id": transaction_id},
            )

        with pytest.raises(RuntimeError, match="lifecycle or external-source"):
            command.downgrade(config, MIGRATION_PARENT)

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE transaction_record "
                    "SET lifecycle_event_type = NULL, source_system = NULL, "
                    "external_reference = NULL "
                    "WHERE transaction_id = :transaction_id"
                ),
                {"transaction_id": transaction_id},
            )
        command.downgrade(config, MIGRATION_PARENT)
    finally:
        command.upgrade(config, "head")
