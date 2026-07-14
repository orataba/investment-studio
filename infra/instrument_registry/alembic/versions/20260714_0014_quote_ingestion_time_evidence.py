"""Make canonical quote ingestion time complete and auditable.

Revision ID: 20260714_0014
Revises: 20260714_0013
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260714_0014"
down_revision = "20260714_0013"
branch_labels = None
depends_on = None


INGESTION_TIME_STATES = (
    "observed",
    "legacy_series_upper_bound",
    "legacy_instrument_upper_bound",
    "legacy_migration_upper_bound",
)
_CANONICAL_UTC_WATERMARK = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)
OUTBOX_EVENT_TYPE = "watchlist_market_data_refresh_requested"
WATCHLIST_INSTRUMENT_TYPES = "'fund', 'etf', 'index'"


def _parse_canonical_utc_watermark(value: object, *, source: str) -> datetime:
    normalized = str(value or "").strip()
    if not _CANONICAL_UTC_WATERMARK.fullmatch(normalized):
        raise RuntimeError(
            f"Legacy quote {source} is not a canonical UTC watermark: "
            f"{normalized!r}."
        )
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError(
            f"Legacy quote {source} is not a valid UTC watermark: "
            f"{normalized!r}."
        ) from error
    canonical = parsed.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    if canonical != normalized:
        raise RuntimeError(
            f"Legacy quote {source} is not a canonical UTC watermark: "
            f"{normalized!r}."
        )
    return parsed.astimezone(UTC)


def _legacy_watermarks(
    connection: sa.Connection,
    *,
    source: str,
    migration_upper_bound: datetime,
) -> dict[str, datetime]:
    if source == "series.data_updated_at":
        statement = sa.text(
            """
            SELECT DISTINCT series.data_updated_at AS watermark
            FROM quote_observation_revision AS revision
            JOIN quote_observation AS observation
              ON observation.observation_id = revision.observation_id
            JOIN quote_series AS series
              ON series.quote_series_id = observation.quote_series_id
            WHERE revision.ingested_at IS NULL
              AND revision.payload_schema_version = 1
              AND NULLIF(trim(series.data_updated_at), '') IS NOT NULL
            """
        )
    elif source == "instrument.market_data_updated_at":
        statement = sa.text(
            """
            SELECT DISTINCT instrument.market_data_updated_at AS watermark
            FROM quote_observation_revision AS revision
            JOIN quote_observation AS observation
              ON observation.observation_id = revision.observation_id
            JOIN quote_series AS series
              ON series.quote_series_id = observation.quote_series_id
            JOIN instrument
              ON instrument.instrument_id = series.instrument_id
            WHERE revision.ingested_at IS NULL
              AND revision.payload_schema_version = 1
              AND NULLIF(trim(series.data_updated_at), '') IS NULL
              AND NULLIF(trim(instrument.market_data_updated_at), '') IS NOT NULL
            """
        )
    else:  # pragma: no cover - internal migration programming error
        raise AssertionError(f"unsupported legacy watermark source: {source}")

    parsed: dict[str, datetime] = {}
    for raw in connection.scalars(statement):
        resolved = _parse_canonical_utc_watermark(raw, source=source)
        if resolved > migration_upper_bound:
            raise RuntimeError(
                f"Legacy quote {source} is after the locked migration clock: "
                f"{str(raw)!r}."
            )
        parsed[str(raw)] = resolved
    return parsed


def _migration_clock(connection: sa.Connection) -> datetime:
    if connection.dialect.name == "postgresql":
        value = connection.scalar(sa.text("SELECT clock_timestamp()"))
        if not isinstance(value, datetime):  # pragma: no cover - driver invariant
            raise RuntimeError("PostgreSQL did not return a migration timestamp.")
        return value.astimezone(UTC)
    return datetime.now(UTC)


def _preflight_legacy_rows(connection: sa.Connection) -> None:
    corrupt = (
        connection.execute(
            sa.text(
                """
                SELECT revision_id, payload_schema_version
                FROM quote_observation_revision
                WHERE ingested_at IS NULL
                  AND payload_schema_version <> 1
                LIMIT 1
                """
            )
        )
        .mappings()
        .first()
    )
    if corrupt is not None:
        raise RuntimeError(
            "Only schema-v1 revisions created by the legacy flat-data migration "
            "may lack ingestion time; found corrupt revision "
            f"{corrupt['revision_id']} with payload_schema_version="
            f"{corrupt['payload_schema_version']}."
        )


def _drop_revision_guards(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        for trigger_name in (
            "trg_quote_observation_revision_immutable",
            "trg_quote_observation_revision_insert_sequence",
            "trg_quote_observation_revision_current_cardinality",
        ):
            op.execute(
                sa.text(
                    f"DROP TRIGGER IF EXISTS {trigger_name} "
                    "ON quote_observation_revision"
                )
            )
        for function_name in (
            "guard_quote_observation_revision_immutable",
            "guard_quote_observation_revision_insert_sequence",
            "guard_quote_observation_revision_current_cardinality",
        ):
            op.execute(sa.text(f"DROP FUNCTION IF EXISTS {function_name}()"))
        return

    if connection.dialect.name == "sqlite":
        for trigger_name in (
            "trg_quote_observation_revision_update_guard",
            "trg_quote_observation_revision_insert_sequence",
            "trg_quote_observation_revision_delete_guard",
        ):
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger_name}"))


def _drop_sqlite_revision_outbox_trigger(connection: sa.Connection) -> None:
    if connection.dialect.name == "sqlite":
        op.execute(
            sa.text("DROP TRIGGER IF EXISTS trg_quote_revision_market_data_outbox")
        )


def _backfill_ingestion_time_evidence(
    connection: sa.Connection,
    *,
    migration_upper_bound: datetime,
    series_watermarks: dict[str, datetime],
    instrument_watermarks: dict[str, datetime],
) -> None:

    connection.execute(
        sa.text(
            """
            UPDATE quote_observation_revision
            SET ingestion_time_state = 'observed'
            WHERE ingested_at IS NOT NULL
            """
        )
    )

    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                """
                UPDATE quote_observation_revision AS revision
                SET ingested_at = CASE
                        WHEN NULLIF(btrim(series.data_updated_at), '') IS NOT NULL
                        THEN CAST(series.data_updated_at AS timestamptz)
                        WHEN NULLIF(btrim(instrument.market_data_updated_at), '')
                             IS NOT NULL
                        THEN CAST(instrument.market_data_updated_at AS timestamptz)
                        ELSE :migration_upper_bound
                    END,
                    ingestion_time_state = CASE
                        WHEN NULLIF(btrim(series.data_updated_at), '') IS NOT NULL
                        THEN 'legacy_series_upper_bound'
                        WHEN NULLIF(btrim(instrument.market_data_updated_at), '')
                             IS NOT NULL
                        THEN 'legacy_instrument_upper_bound'
                        ELSE 'legacy_migration_upper_bound'
                    END
                FROM quote_observation AS observation
                JOIN quote_series AS series
                  ON series.quote_series_id = observation.quote_series_id
                JOIN instrument
                  ON instrument.instrument_id = series.instrument_id
                WHERE revision.observation_id = observation.observation_id
                  AND revision.ingested_at IS NULL
                  AND revision.payload_schema_version = 1
                """
            ).bindparams(
                sa.bindparam(
                    "migration_upper_bound",
                    migration_upper_bound,
                    type_=sa.DateTime(timezone=True),
                )
            )
        )
    else:
        series_update = sa.text(
            """
            UPDATE quote_observation_revision
            SET ingested_at = :resolved_ingested_at,
                ingestion_time_state = 'legacy_series_upper_bound'
            WHERE ingested_at IS NULL
              AND payload_schema_version = 1
              AND observation_id IN (
                  SELECT observation.observation_id
                  FROM quote_observation AS observation
                  JOIN quote_series AS series
                    ON series.quote_series_id = observation.quote_series_id
                  WHERE series.data_updated_at = :raw_watermark
              )
            """
        ).bindparams(
            sa.bindparam(
                "resolved_ingested_at",
                type_=sa.DateTime(timezone=True),
            )
        )
        for raw_watermark, parsed_watermark in series_watermarks.items():
            connection.execute(
                series_update,
                {
                    "raw_watermark": raw_watermark,
                    "resolved_ingested_at": parsed_watermark,
                },
            )

        instrument_update = sa.text(
            """
            UPDATE quote_observation_revision
            SET ingested_at = :resolved_ingested_at,
                ingestion_time_state = 'legacy_instrument_upper_bound'
            WHERE ingested_at IS NULL
              AND payload_schema_version = 1
              AND observation_id IN (
                  SELECT observation.observation_id
                  FROM quote_observation AS observation
                  JOIN quote_series AS series
                    ON series.quote_series_id = observation.quote_series_id
                  JOIN instrument
                    ON instrument.instrument_id = series.instrument_id
                  WHERE NULLIF(trim(series.data_updated_at), '') IS NULL
                    AND instrument.market_data_updated_at = :raw_watermark
              )
            """
        ).bindparams(
            sa.bindparam(
                "resolved_ingested_at",
                type_=sa.DateTime(timezone=True),
            )
        )
        for raw_watermark, parsed_watermark in instrument_watermarks.items():
            connection.execute(
                instrument_update,
                {
                    "raw_watermark": raw_watermark,
                    "resolved_ingested_at": parsed_watermark,
                },
            )

        connection.execute(
            sa.text(
                """
                UPDATE quote_observation_revision
                SET ingested_at = :migration_upper_bound,
                    ingestion_time_state = 'legacy_migration_upper_bound'
                WHERE ingested_at IS NULL
                """
            ).bindparams(
                sa.bindparam(
                    "migration_upper_bound",
                    migration_upper_bound,
                    type_=sa.DateTime(timezone=True),
                )
            )
        )

    invalid = (
        connection.execute(
            sa.text(
                """
                SELECT revision_id
                FROM quote_observation_revision
                WHERE ingested_at IS NULL
                   OR ingestion_time_state IS NULL
                   OR ingestion_time_state NOT IN (
                       'observed',
                       'legacy_series_upper_bound',
                       'legacy_instrument_upper_bound',
                       'legacy_migration_upper_bound'
                   )
                LIMIT 1
                """
            )
        )
        .mappings()
        .first()
    )
    if invalid is not None:
        raise RuntimeError(
            "Quote ingestion-time evidence backfill left an invalid revision: "
            f"{invalid['revision_id']}."
        )


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
                       AND NEW.ingestion_time_state IS NOT DISTINCT FROM OLD.ingestion_time_state
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
                    IF NEW.ingestion_time_state IS DISTINCT FROM 'observed' THEN
                        RAISE EXCEPTION 'quote_revision_insert_violation: new revision ingestion time must be observed'
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
                    AND NEW.ingestion_time_state IS OLD.ingestion_time_state
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
                    OR NEW.ingestion_time_state IS NOT 'observed'
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


def _create_sqlite_revision_outbox_trigger(connection: sa.Connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_quote_revision_market_data_outbox
            AFTER INSERT ON quote_observation_revision
            FOR EACH ROW
            BEGIN
                INSERT INTO market_data_outbox_event (
                    event_id,
                    event_type,
                    instrument_id,
                    quote_revision_id,
                    source_kind,
                    source_version,
                    status,
                    attempt_count,
                    max_attempts,
                    available_at,
                    created_at,
                    updated_at
                )
                SELECT
                    NEW.revision_id,
                    '{OUTBOX_EVENT_TYPE}',
                    series.instrument_id,
                    NEW.revision_id,
                    'quote_revision',
                    NEW.revision_id,
                    'pending',
                    0,
                    8,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                FROM quote_observation AS observation
                JOIN quote_series AS series
                  ON series.quote_series_id = observation.quote_series_id
                JOIN instrument AS instrument
                  ON instrument.instrument_id = series.instrument_id
                WHERE observation.observation_id = NEW.observation_id
                  AND lower(instrument.instrument_type) IN (
                      {WATCHLIST_INSTRUMENT_TYPES}
                  );
            END
            """
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "LOCK TABLE instrument, quote_series, quote_observation, "
                "quote_observation_revision IN ACCESS EXCLUSIVE MODE"
            )
        )
    _preflight_legacy_rows(connection)
    migration_upper_bound = _migration_clock(connection)
    series_watermarks = _legacy_watermarks(
        connection,
        source="series.data_updated_at",
        migration_upper_bound=migration_upper_bound,
    )
    instrument_watermarks = _legacy_watermarks(
        connection,
        source="instrument.market_data_updated_at",
        migration_upper_bound=migration_upper_bound,
    )
    _drop_revision_guards(connection)
    _drop_sqlite_revision_outbox_trigger(connection)

    op.add_column(
        "quote_observation_revision",
        sa.Column("ingestion_time_state", sa.String(), nullable=True),
    )
    _backfill_ingestion_time_evidence(
        connection,
        migration_upper_bound=migration_upper_bound,
        series_watermarks=series_watermarks,
        instrument_watermarks=instrument_watermarks,
    )

    with op.batch_alter_table("quote_observation_revision") as batch_op:
        batch_op.alter_column(
            "ingested_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.alter_column(
            "ingestion_time_state",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.create_check_constraint(
            op.f("ck_quote_observation_revision_ingestion_time_state"),
            "ingestion_time_state IN ("
            "'observed', "
            "'legacy_series_upper_bound', "
            "'legacy_instrument_upper_bound', "
            "'legacy_migration_upper_bound'"
            ")",
        )
        batch_op.create_check_constraint(
            op.f("ck_quote_observation_revision_legacy_ingestion_time_schema"),
            "ingestion_time_state = 'observed' OR payload_schema_version = 1",
        )

    _create_revision_guards(connection)
    _create_sqlite_revision_outbox_trigger(connection)


def downgrade() -> None:
    raise RuntimeError(
        "20260714_0014 is irreversible: removing ingestion-time evidence would "
        "re-open temporal look-ahead ambiguity for canonical quote revisions."
    )
