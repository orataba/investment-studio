"""Preserve the decimal scale of quote evidence separately from its value.

Revision ID: 20260714_0012
Revises: 20260713_0011
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from alembic import op
import sqlalchemy as sa


revision = "20260714_0012"
down_revision = "20260713_0011"
branch_labels = None
depends_on = None


def _drop_revision_guards(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS "
                "trg_quote_observation_revision_immutable "
                "ON quote_observation_revision"
            )
        )
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS "
                "trg_quote_observation_revision_insert_sequence "
                "ON quote_observation_revision"
            )
        )
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS "
                "trg_quote_observation_revision_current_cardinality "
                "ON quote_observation_revision"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS guard_quote_observation_revision_immutable()"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS guard_quote_observation_revision_insert_sequence()"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS guard_quote_observation_revision_current_cardinality()"
            )
        )
        return

    if connection.dialect.name == "sqlite":
        for trigger_name in (
            "trg_quote_observation_revision_update_guard",
            "trg_quote_observation_revision_insert_sequence",
            "trg_quote_observation_revision_delete_guard",
        ):
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger_name}"))


def _decimal_scale(value: object) -> int:
    try:
        resolved = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise RuntimeError(
            f"Existing quote revision value is not a decimal: {value!r}"
        ) from error
    if not resolved.is_finite():
        raise RuntimeError(f"Existing quote revision value is not finite: {value!r}")
    return max(-resolved.as_tuple().exponent, 0)


def _legacy_revision_update_statement() -> sa.Update:
    """Build a cross-dialect update that keeps the revision PK sargable."""

    revision = sa.table(
        "quote_observation_revision",
        sa.column("revision_id", sa.Uuid(as_uuid=False)),
        sa.column("value_input_scale", sa.Integer()),
        sa.column("numeric_scale_state", sa.String()),
        sa.column("payload_schema_version", sa.Integer()),
    )
    return (
        sa.update(revision)
        .where(
            revision.c.revision_id
            == sa.bindparam(
                "target_revision_id",
                type_=sa.Uuid(as_uuid=False),
            )
        )
        .values(
            payload_schema_version=1,
            value_input_scale=sa.bindparam(
                "target_value_input_scale",
                type_=sa.Integer(),
            ),
            numeric_scale_state=sa.bindparam(
                "target_numeric_scale_state",
                type_=sa.String(),
            ),
        )
    )


def _validate_legacy_numeric_lineage(connection: sa.Connection) -> None:
    invalid = (
        connection.execute(
            sa.text(
                """
                SELECT revision_id, value, status
                FROM quote_observation_revision
                WHERE (status = 'withdrawn' AND value IS NOT NULL)
                   OR (status <> 'withdrawn' AND value IS NULL)
                LIMIT 1
                """
            )
        )
        .mappings()
        .first()
    )
    if invalid is None:
        return
    if str(invalid["status"]) == "withdrawn":
        raise RuntimeError(
            "Withdrawn quote revision unexpectedly carries a numeric value."
        )
    raise RuntimeError(
        "Non-withdrawn quote revision unexpectedly has no numeric value."
    )


def _backfill_legacy_numeric_lineage(connection: sa.Connection) -> None:
    _validate_legacy_numeric_lineage(connection)
    if connection.dialect.name == "postgresql":
        # PostgreSQL stores the decimal scale on unconstrained NUMERIC values.
        # Backfill the whole table in one scan instead of issuing one UPDATE per
        # revision.  Besides being linear, this preserves values such as 1.2300.
        connection.execute(
            sa.text(
                """
                UPDATE quote_observation_revision
                SET payload_schema_version = 1,
                    value_input_scale = CASE
                        WHEN status = 'withdrawn' THEN NULL
                        ELSE scale(value)
                    END,
                    numeric_scale_state = CASE
                        WHEN status = 'withdrawn' THEN NULL
                        ELSE 'legacy_inferred'
                    END
                """
            )
        )
        return

    rows = connection.execute(
        sa.text(
            """
            SELECT revision_id, value, status
            FROM quote_observation_revision
            ORDER BY observation_id, revision_number
            """
        )
    ).mappings()
    updates: list[dict[str, object]] = []
    for row in rows:
        withdrawn = str(row["status"]) == "withdrawn"
        updates.append(
            {
                "target_revision_id": str(row["revision_id"]),
                "target_value_input_scale": (
                    None if withdrawn else _decimal_scale(row["value"])
                ),
                "target_numeric_scale_state": (
                    None if withdrawn else "legacy_inferred"
                ),
            }
        )
        if len(updates) == 5_000:
            connection.execute(_legacy_revision_update_statement(), updates)
            updates.clear()
    if updates:
        connection.execute(_legacy_revision_update_statement(), updates)


def _create_revision_guards(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                CREATE FUNCTION guard_quote_observation_revision_immutable()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        RAISE EXCEPTION 'quote_revision_immutable_violation: delete is forbidden'
                            USING ERRCODE = '55000';
                    END IF;

                    IF OLD.is_current
                       AND NOT NEW.is_current
                       AND OLD.superseded_at IS NULL
                       AND NEW.superseded_at IS NOT NULL
                       AND NEW.revision_id IS NOT DISTINCT FROM OLD.revision_id
                       AND NEW.observation_id IS NOT DISTINCT FROM OLD.observation_id
                       AND NEW.revision_number IS NOT DISTINCT FROM OLD.revision_number
                       AND NEW.value IS NOT DISTINCT FROM OLD.value
                       AND NEW.value_input_scale IS NOT DISTINCT FROM OLD.value_input_scale
                       AND NEW.numeric_scale_state IS NOT DISTINCT FROM OLD.numeric_scale_state
                       AND NEW.payload_schema_version IS NOT DISTINCT FROM OLD.payload_schema_version
                       AND NEW.source_ref IS NOT DISTINCT FROM OLD.source_ref
                       AND NEW.status IS NOT DISTINCT FROM OLD.status
                       AND NEW.source_published_at IS NOT DISTINCT FROM OLD.source_published_at
                       AND NEW.ingested_at IS NOT DISTINCT FROM OLD.ingested_at
                       AND NEW.payload_hash IS NOT DISTINCT FROM OLD.payload_hash
                    THEN
                        RETURN NEW;
                    END IF;

                    RAISE EXCEPTION 'quote_revision_immutable_violation: only current lifecycle closure is allowed'
                        USING ERRCODE = '55000';
                END;
                $$
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_observation_revision_immutable
                BEFORE UPDATE OR DELETE ON quote_observation_revision
                FOR EACH ROW
                EXECUTE FUNCTION guard_quote_observation_revision_immutable()
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE FUNCTION guard_quote_observation_revision_insert_sequence()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    previous_revision_number integer;
                    current_revision_count integer;
                BEGIN
                    EXECUTE format(
                        'SELECT MAX(revision_number), '
                        'COUNT(*) FILTER (WHERE is_current) '
                        'FROM %I.%I WHERE observation_id = $1',
                        TG_TABLE_SCHEMA,
                        TG_TABLE_NAME
                    )
                    INTO previous_revision_number, current_revision_count
                    USING NEW.observation_id;

                    IF NOT NEW.is_current OR NEW.superseded_at IS NOT NULL THEN
                        RAISE EXCEPTION 'quote_revision_insert_violation: new revision must be current'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NEW.revision_number IS DISTINCT FROM
                       COALESCE(previous_revision_number + 1, 1) THEN
                        RAISE EXCEPTION 'quote_revision_insert_violation: revision_number must be the next sequence'
                            USING ERRCODE = '55000';
                    END IF;
                    IF previous_revision_number IS NOT NULL
                       AND current_revision_count <> 0 THEN
                        RAISE EXCEPTION 'quote_revision_insert_violation: prior current must be closed first'
                            USING ERRCODE = '55000';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_observation_revision_insert_sequence
                BEFORE INSERT ON quote_observation_revision
                FOR EACH ROW
                EXECUTE FUNCTION guard_quote_observation_revision_insert_sequence()
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE FUNCTION guard_quote_observation_revision_current_cardinality()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    current_revision_count integer;
                BEGIN
                    EXECUTE format(
                        'SELECT COUNT(*) FILTER (WHERE is_current) '
                        'FROM %I.%I WHERE observation_id = $1',
                        TG_TABLE_SCHEMA,
                        TG_TABLE_NAME
                    )
                    INTO current_revision_count
                    USING NEW.observation_id;
                    IF current_revision_count <> 1 THEN
                        RAISE EXCEPTION 'quote_revision_cardinality_violation: observation must have exactly one current revision'
                            USING ERRCODE = '55000';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE CONSTRAINT TRIGGER trg_quote_observation_revision_current_cardinality
                AFTER INSERT OR UPDATE ON quote_observation_revision
                DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW
                EXECUTE FUNCTION guard_quote_observation_revision_current_cardinality()
                """
            )
        )
        return

    if connection.dialect.name == "sqlite":
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_observation_revision_update_guard
                BEFORE UPDATE ON quote_observation_revision
                FOR EACH ROW
                WHEN NOT (
                    OLD.is_current
                    AND NOT NEW.is_current
                    AND OLD.superseded_at IS NULL
                    AND NEW.superseded_at IS NOT NULL
                    AND NEW.revision_id IS OLD.revision_id
                    AND NEW.observation_id IS OLD.observation_id
                    AND NEW.revision_number IS OLD.revision_number
                    AND NEW.value IS OLD.value
                    AND NEW.value_input_scale IS OLD.value_input_scale
                    AND NEW.numeric_scale_state IS OLD.numeric_scale_state
                    AND NEW.payload_schema_version IS OLD.payload_schema_version
                    AND NEW.source_ref IS OLD.source_ref
                    AND NEW.status IS OLD.status
                    AND NEW.source_published_at IS OLD.source_published_at
                    AND NEW.ingested_at IS OLD.ingested_at
                    AND NEW.payload_hash IS OLD.payload_hash
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'quote_revision_immutable_violation: only current lifecycle closure is allowed'
                    );
                END
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_observation_revision_insert_sequence
                BEFORE INSERT ON quote_observation_revision
                FOR EACH ROW
                WHEN (
                    NOT NEW.is_current
                    OR NEW.superseded_at IS NOT NULL
                    OR NEW.revision_number <> COALESCE(
                        (
                            SELECT MAX(revision_number) + 1
                            FROM quote_observation_revision
                            WHERE observation_id = NEW.observation_id
                        ),
                        1
                    )
                    OR (
                        EXISTS (
                            SELECT 1
                            FROM quote_observation_revision
                            WHERE observation_id = NEW.observation_id
                        )
                        AND EXISTS (
                            SELECT 1
                            FROM quote_observation_revision
                            WHERE observation_id = NEW.observation_id
                              AND is_current
                        )
                    )
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'quote_revision_insert_violation: revision must be next/current after prior closure'
                    );
                END
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_observation_revision_delete_guard
                BEFORE DELETE ON quote_observation_revision
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'quote_revision_immutable_violation: delete is forbidden'
                    );
                END
                """
            )
        )


def upgrade() -> None:
    connection = op.get_bind()
    _drop_revision_guards(connection)

    op.add_column(
        "quote_observation_revision",
        sa.Column("value_input_scale", sa.Integer(), nullable=True),
    )
    op.add_column(
        "quote_observation_revision",
        sa.Column("numeric_scale_state", sa.String(), nullable=True),
    )
    op.add_column(
        "quote_observation_revision",
        sa.Column("payload_schema_version", sa.Integer(), nullable=True),
    )
    _backfill_legacy_numeric_lineage(connection)

    with op.batch_alter_table("quote_observation_revision") as batch_op:
        batch_op.alter_column(
            "payload_schema_version",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_check_constraint(
            op.f("ck_quote_observation_revision_payload_schema_version"),
            "payload_schema_version IN (1, 2)",
        )
        batch_op.create_check_constraint(
            op.f("ck_quote_observation_revision_numeric_evidence_presence"),
            "(status = 'withdrawn' AND value_input_scale IS NULL "
            "AND numeric_scale_state IS NULL) OR "
            "(status <> 'withdrawn' AND value_input_scale IS NOT NULL "
            "AND value_input_scale >= 0 AND numeric_scale_state IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            op.f("ck_quote_observation_revision_numeric_scale_state"),
            "numeric_scale_state IS NULL OR numeric_scale_state IN "
            "('declared', 'binary_inferred', 'legacy_inferred')",
        )
        batch_op.create_check_constraint(
            op.f("ck_quote_observation_revision_numeric_evidence_schema"),
            "status = 'withdrawn' OR "
            "(payload_schema_version = 1 "
            "AND numeric_scale_state = 'legacy_inferred') OR "
            "(payload_schema_version = 2 "
            "AND numeric_scale_state IN ('declared', 'binary_inferred'))",
        )

    _create_revision_guards(connection)


def downgrade() -> None:
    raise RuntimeError(
        "20260714_0012 is irreversible: removing quote numeric-evidence "
        "lineage would make reported decimal precision unauditable."
    )
