"""Normalize transaction actor provenance and enforce its closed vocabulary.

Revision ID: 20260713_0038
Revises: 20260713_0037
Create Date: 2026-07-13

Migration 0036 recorded its own baseline groups with ``actor_source=alembic``.
That implementation detail is not part of the ledger's canonical provenance
contract; the corresponding semantic source is ``migration``.  This migration
normalizes only those precisely identified 0036 rows and then closes the actor
source/type domain at the database boundary.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260713_0038"
down_revision = "20260713_0037"
branch_labels = None
depends_on = None


_LEGACY_ACTOR_ID = "system:migration:20260713_0036"
_LEGACY_ACTOR_DISPLAY_NAME = "Portfolio ledger migration 0036"
_LEGACY_REQUEST_ID = "migration:20260713_0036"
_ALLOWED_SOURCES = (
    "client_asserted",
    "authenticated_principal",
    "trusted_service",
    "migration",
)
_SOURCE_SQL = ", ".join(f"'{value}'" for value in _ALLOWED_SOURCES)
_SOURCE_TYPE_SQL = """
(
    (actor_type = 'user'
     AND actor_source IN ('client_asserted', 'authenticated_principal'))
 OR (actor_type = 'service' AND actor_source = 'trusted_service')
 OR (actor_type = 'migration' AND actor_source = 'migration')
)
""".strip()


def _schema_prefix() -> str:
    schema = op.get_context().opts.get("version_table_schema")
    return f'"{schema}".' if schema else ""


def _preflight(connection: sa.Connection) -> None:
    unsupported_count = int(
        connection.scalar(
            sa.text(
                f"""
                SELECT count(*)
                FROM transaction_revision_group_record
                WHERE actor_source NOT IN ({_SOURCE_SQL}, 'alembic')
                """
            )
        )
        or 0
    )
    if unsupported_count:
        raise RuntimeError(
            "0038 found transaction revision groups outside the canonical actor "
            "source vocabulary; refusing an implicit normalization"
        )

    malformed_legacy_count = int(
        connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM transaction_revision_group_record
                WHERE actor_source = 'alembic'
                  AND NOT (
                      source_kind = 'migration'
                      AND actor_type = 'migration'
                      AND actor_id = :actor_id
                      AND actor_display_name = :actor_display_name
                      AND request_id = :request_id
                      AND idempotency_key =
                          'migration:20260713_0036:baseline:' || portfolio_id
                  )
                """
            ),
            {
                "actor_id": _LEGACY_ACTOR_ID,
                "actor_display_name": _LEGACY_ACTOR_DISPLAY_NAME,
                "request_id": _LEGACY_REQUEST_ID,
            },
        )
        or 0
    )
    if malformed_legacy_count:
        raise RuntimeError(
            "0038 found actor_source=alembic outside the exact 0036 baseline "
            "provenance; refusing to rewrite ambiguous audit metadata"
        )


def _assert_canonical_rows(connection: sa.Connection) -> None:
    invalid_count = int(
        connection.scalar(
            sa.text(
                f"""
                SELECT count(*)
                FROM transaction_revision_group_record
                WHERE actor_source NOT IN ({_SOURCE_SQL})
                   OR NOT ({_SOURCE_TYPE_SQL})
                """
            )
        )
        or 0
    )
    if invalid_count:
        raise RuntimeError(
            "0038 actor-source normalization did not produce a canonical "
            "actor_source/actor_type pairing"
        )


def _upgrade_postgresql(connection: sa.Connection) -> None:
    schema_prefix = _schema_prefix()
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(
        sa.text(
            f"LOCK TABLE {schema_prefix}transaction_revision_group_record "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    _preflight(connection)

    connection.execute(
        sa.text(
            f"""
            DROP TRIGGER trg_transaction_revision_group_record_append_only
            ON {schema_prefix}transaction_revision_group_record
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE transaction_revision_group_record
            SET actor_source = 'migration'
            WHERE actor_source = 'alembic'
              AND source_kind = 'migration'
              AND actor_type = 'migration'
              AND actor_id = :actor_id
              AND actor_display_name = :actor_display_name
              AND request_id = :request_id
              AND idempotency_key =
                  'migration:20260713_0036:baseline:' || portfolio_id
            """
        ),
        {
            "actor_id": _LEGACY_ACTOR_ID,
            "actor_display_name": _LEGACY_ACTOR_DISPLAY_NAME,
            "request_id": _LEGACY_REQUEST_ID,
        },
    )
    _assert_canonical_rows(connection)

    # The metadata naming convention expands these logical names to the exact
    # ck_transaction_revision_group_record_* names declared by the ORM model.
    op.create_check_constraint(
        "actor_source",
        "transaction_revision_group_record",
        f"actor_source IN ({_SOURCE_SQL})",
    )
    op.create_check_constraint(
        "actor_source_type",
        "transaction_revision_group_record",
        _SOURCE_TYPE_SQL,
    )
    connection.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_transaction_revision_group_record_append_only
            BEFORE UPDATE OR DELETE
            ON {schema_prefix}transaction_revision_group_record
            FOR EACH ROW
            EXECUTE FUNCTION {schema_prefix}reject_transaction_ledger_mutation()
            """
        )
    )


def _upgrade_sqlite(connection: sa.Connection) -> None:
    _preflight(connection)
    for operation in ("update", "delete"):
        connection.execute(
            sa.text(
                "DROP TRIGGER "
                f"trg_transaction_revision_group_record_{operation}_forbidden"
            )
        )

    connection.execute(
        sa.text(
            """
            UPDATE transaction_revision_group_record
            SET actor_source = 'migration'
            WHERE actor_source = 'alembic'
              AND source_kind = 'migration'
              AND actor_type = 'migration'
              AND actor_id = :actor_id
              AND actor_display_name = :actor_display_name
              AND request_id = :request_id
              AND idempotency_key =
                  'migration:20260713_0036:baseline:' || portfolio_id
            """
        ),
        {
            "actor_id": _LEGACY_ACTOR_ID,
            "actor_display_name": _LEGACY_ACTOR_DISPLAY_NAME,
            "request_id": _LEGACY_REQUEST_ID,
        },
    )
    _assert_canonical_rows(connection)

    for operation in ("UPDATE", "DELETE"):
        connection.execute(
            sa.text(
                f"""
                CREATE TRIGGER
                    trg_transaction_revision_group_record_{operation.lower()}_forbidden
                BEFORE {operation} ON transaction_revision_group_record
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'transaction_revision_group_record is append-only'
                    );
                END
                """
            )
        )
    connection.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_transaction_revision_group_record_actor_source_insert
            BEFORE INSERT ON transaction_revision_group_record
            WHEN NEW.actor_source NOT IN ({_SOURCE_SQL})
              OR NOT (
                    (NEW.actor_type = 'user' AND NEW.actor_source IN (
                        'client_asserted', 'authenticated_principal'
                    ))
                 OR (NEW.actor_type = 'service'
                     AND NEW.actor_source = 'trusted_service')
                 OR (NEW.actor_type = 'migration'
                     AND NEW.actor_source = 'migration')
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid transaction actor_source/actor_type pairing'
                );
            END
            """
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        _upgrade_postgresql(connection)
        return
    if connection.dialect.name == "sqlite":
        _upgrade_sqlite(connection)
        return
    raise RuntimeError(
        "0038 supports only PostgreSQL production and SQLite test/bootstrap databases"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0038 is intentionally irreversible because canonical audit provenance "
        "must not be converted back to an implementation-specific source. Restore "
        "the verified pre-migration database backup instead."
    )
