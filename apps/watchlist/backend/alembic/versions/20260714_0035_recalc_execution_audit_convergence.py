"""Converge published recalc source-event state on the durable inbox contract.

Revision ID: 20260714_0035
Revises: 20260714_0034

The originally published 0032 placed source-event uniqueness on ``recalc_job``
and the originally published 0034 manufactured inbox generations from
historical job audit rows.  This forward migration removes only rows that can
be proven to have that synthetic shape, rebuilds invalidation state from the
remaining durable inbox, and restores ``recalc_job`` to a non-unique execution
audit lookup.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0035"
down_revision = "20260714_0034"
branch_labels = None
depends_on = None


LEGACY_UNIQUE_INDEX = "uq_recalc_job_source_event_identity"
SOURCE_REFERENCE_INDEX = "idx_recalc_job_source_reference"
SOURCE_REFERENCE_COLUMNS = [
    "trigger_ref_type",
    "trigger_ref_id",
    "instrument_id",
    "job_type",
]
SOURCE_REFERENCE_PREDICATE = (
    "trigger_ref_type IS NOT NULL AND TRIM(trigger_ref_type) <> '' "
    "AND trigger_ref_id IS NOT NULL AND TRIM(trigger_ref_id) <> ''"
)


def _format_examples(rows: list[sa.RowMapping], *keys: str) -> str:
    return ", ".join(
        "/".join(str(row[key]) for key in keys)
        for row in rows[:10]
    )


def _validate_inbox_state_contract(connection: sa.Connection) -> None:
    duplicate_generations = connection.execute(
        sa.text(
            """
            SELECT instrument_id, job_type, generation, COUNT(*) AS row_count
            FROM recalc_source_event_inbox
            WHERE disposition = 'recalc'
            GROUP BY instrument_id, job_type, generation
            HAVING COUNT(*) <> 1
            LIMIT 10
            """
        )
    ).mappings().all()
    if duplicate_generations:
        raise RuntimeError(
            "Cannot converge recalc inbox: duplicate generation identities: "
            + _format_examples(
                duplicate_generations,
                "instrument_id",
                "job_type",
                "generation",
            )
            + "."
        )

    inconsistent_states = connection.execute(
        sa.text(
            """
            WITH inbox_state AS (
                SELECT
                    instrument_id,
                    job_type,
                    MAX(generation) AS requested_generation,
                    COALESCE(
                        MAX(
                            CASE
                                WHEN consumed_at IS NOT NULL THEN generation
                                ELSE 0
                            END
                        ),
                        0
                    ) AS completed_generation
                FROM recalc_source_event_inbox
                WHERE disposition = 'recalc'
                GROUP BY instrument_id, job_type
            )
            SELECT
                COALESCE(state.instrument_id, inbox.instrument_id) AS instrument_id,
                COALESCE(state.job_type, inbox.job_type) AS job_type,
                state.requested_generation AS state_requested_generation,
                inbox.requested_generation AS inbox_requested_generation,
                state.completed_generation AS state_completed_generation,
                inbox.completed_generation AS inbox_completed_generation
            FROM recalc_invalidation_state AS state
            LEFT JOIN inbox_state AS inbox
              ON inbox.instrument_id = state.instrument_id
             AND inbox.job_type = state.job_type
            WHERE inbox.instrument_id IS NULL
               OR state.requested_generation <> inbox.requested_generation
               OR state.completed_generation <> inbox.completed_generation
            UNION ALL
            SELECT
                inbox.instrument_id,
                inbox.job_type,
                NULL,
                inbox.requested_generation,
                NULL,
                inbox.completed_generation
            FROM inbox_state AS inbox
            LEFT JOIN recalc_invalidation_state AS state
              ON state.instrument_id = inbox.instrument_id
             AND state.job_type = inbox.job_type
            WHERE state.instrument_id IS NULL
            LIMIT 10
            """
        )
    ).mappings().all()
    if inconsistent_states:
        raise RuntimeError(
            "Cannot converge recalc inbox: invalidation state does not exactly "
            "match durable inbox generations: "
            + _format_examples(inconsistent_states, "instrument_id", "job_type")
            + "."
        )

    non_prefix_consumption = connection.execute(
        sa.text(
            """
            SELECT consumed.source_event_inbox_id,
                   consumed.instrument_id,
                   consumed.job_type,
                   consumed.generation
            FROM recalc_source_event_inbox AS consumed
            WHERE consumed.disposition = 'recalc'
              AND consumed.consumed_at IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM recalc_source_event_inbox AS pending
                  WHERE pending.instrument_id = consumed.instrument_id
                    AND pending.job_type = consumed.job_type
                    AND pending.disposition = 'recalc'
                    AND pending.generation < consumed.generation
                    AND pending.consumed_at IS NULL
              )
            LIMIT 10
            """
        )
    ).mappings().all()
    if non_prefix_consumption:
        raise RuntimeError(
            "Cannot converge recalc inbox: consumed generations are not a "
            "contiguous prefix: "
            + _format_examples(
                non_prefix_consumption,
                "instrument_id",
                "job_type",
                "generation",
            )
            + "."
        )


def _proven_synthetic_rows(connection: sa.Connection) -> list[sa.RowMapping]:
    candidates = connection.execute(
        sa.text(
            """
            WITH ranked_jobs AS (
                SELECT
                    recalc_job_id,
                    trigger_ref_type,
                    trigger_ref_id,
                    instrument_id,
                    job_type,
                    enqueued_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY instrument_id, job_type
                        ORDER BY enqueued_at, recalc_job_id
                    ) AS expected_generation
                FROM recalc_job
                WHERE trigger_ref_type IS NOT NULL
                  AND TRIM(trigger_ref_type) <> ''
                  AND trigger_ref_id IS NOT NULL
                  AND TRIM(trigger_ref_id) <> ''
            )
            SELECT
                inbox.source_event_inbox_id,
                inbox.trigger_ref_type AS inbox_trigger_ref_type,
                inbox.trigger_ref_id AS inbox_trigger_ref_id,
                inbox.instrument_id AS inbox_instrument_id,
                inbox.job_type AS inbox_job_type,
                inbox.disposition,
                inbox.generation,
                ranked.trigger_ref_type AS job_trigger_ref_type,
                ranked.trigger_ref_id AS job_trigger_ref_id,
                ranked.instrument_id AS job_instrument_id,
                ranked.job_type AS job_job_type,
                ranked.expected_generation,
                CASE
                    WHEN inbox.received_at = ranked.enqueued_at THEN 1
                    ELSE 0
                END AS timestamps_match
            FROM recalc_source_event_inbox AS inbox
            JOIN recalc_job AS job
              ON job.recalc_job_id = inbox.source_event_inbox_id
            LEFT JOIN ranked_jobs AS ranked
              ON ranked.recalc_job_id = job.recalc_job_id
            ORDER BY inbox.source_event_inbox_id
            """
        )
    ).mappings().all()
    ambiguous = [
        row
        for row in candidates
        if not (
            row["disposition"] == "recalc"
            and row["inbox_trigger_ref_type"] == row["job_trigger_ref_type"]
            and row["inbox_trigger_ref_id"] == row["job_trigger_ref_id"]
            and row["inbox_instrument_id"] == row["job_instrument_id"]
            and row["inbox_job_type"] == row["job_job_type"]
            and row["generation"] == row["expected_generation"]
            and bool(row["timestamps_match"])
        )
    ]
    if ambiguous:
        raise RuntimeError(
            "Cannot converge recalc inbox: inbox ids matching execution job ids "
            "do not have the exact published-0034 synthetic shape: "
            + _format_examples(ambiguous, "source_event_inbox_id")
            + "."
        )
    return candidates


def _remove_synthetic_rows(
    connection: sa.Connection,
    rows: list[sa.RowMapping],
) -> None:
    if not rows:
        return
    synthetic_claims = [
        {
            "target_recalc_job_id": row["source_event_inbox_id"],
            "target_generation": row["generation"],
        }
        for row in rows
    ]
    connection.execute(
        sa.text(
            """
            UPDATE recalc_job
            SET claimed_generation = NULL
            WHERE recalc_job_id = :target_recalc_job_id
              AND claimed_generation = :target_generation
            """
        ),
        synthetic_claims,
    )
    connection.execute(
        sa.text(
            """
            DELETE FROM recalc_source_event_inbox
            WHERE source_event_inbox_id = :target_source_event_inbox_id
            """
        ),
        [
            {"target_source_event_inbox_id": row["source_event_inbox_id"]}
            for row in rows
        ],
    )


def _validate_remaining_claims(connection: sa.Connection) -> None:
    invalid_claims = connection.execute(
        sa.text(
            """
            SELECT job.recalc_job_id, job.instrument_id, job.job_type,
                   job.claimed_generation
            FROM recalc_job AS job
            LEFT JOIN recalc_source_event_inbox AS inbox
              ON inbox.instrument_id = job.instrument_id
             AND inbox.job_type = job.job_type
             AND inbox.disposition = 'recalc'
             AND inbox.generation = job.claimed_generation
            WHERE job.claimed_generation IS NOT NULL
              AND inbox.source_event_inbox_id IS NULL
            LIMIT 10
            """
        )
    ).mappings().all()
    if invalid_claims:
        raise RuntimeError(
            "Cannot converge recalc inbox: execution jobs claim generations "
            "that have no remaining durable inbox event: "
            + _format_examples(invalid_claims, "recalc_job_id", "claimed_generation")
            + "."
        )


def _rebuild_invalidation_state(connection: sa.Connection) -> None:
    connection.execute(sa.text("DELETE FROM recalc_invalidation_state"))
    connection.execute(
        sa.text(
            """
            INSERT INTO recalc_invalidation_state (
                instrument_id,
                job_type,
                requested_generation,
                completed_generation,
                updated_at
            )
            SELECT
                instrument_id,
                job_type,
                MAX(generation),
                COALESCE(
                    MAX(
                        CASE
                            WHEN consumed_at IS NOT NULL THEN generation
                            ELSE 0
                        END
                    ),
                    0
                ),
                CURRENT_TIMESTAMP
            FROM recalc_source_event_inbox
            WHERE disposition = 'recalc'
            GROUP BY instrument_id, job_type
            """
        )
    )


def _validated_source_reference_indexes(
    connection: sa.Connection,
) -> dict[str, dict[str, object]]:
    indexes = {
        index["name"]: index
        for index in sa.inspect(connection).get_indexes("recalc_job")
    }
    legacy = indexes.get(LEGACY_UNIQUE_INDEX)
    if legacy is not None:
        if (
            not legacy.get("unique")
            or legacy.get("column_names") != SOURCE_REFERENCE_COLUMNS
            or not _has_source_reference_predicate(connection, legacy)
        ):
            raise RuntimeError(
                f"Unexpected {LEGACY_UNIQUE_INDEX} definition; refusing to replace it."
            )

    current = indexes.get(SOURCE_REFERENCE_INDEX)
    if current is not None:
        if (
            current.get("unique")
            or current.get("column_names") != SOURCE_REFERENCE_COLUMNS
            or not _has_source_reference_predicate(connection, current)
        ):
            raise RuntimeError(
                f"Unexpected {SOURCE_REFERENCE_INDEX} definition; refusing to use it."
            )
    return indexes


def _converge_source_reference_index(connection: sa.Connection) -> None:
    indexes = _validated_source_reference_indexes(connection)
    if LEGACY_UNIQUE_INDEX in indexes:
        op.drop_index(LEGACY_UNIQUE_INDEX, table_name="recalc_job")
    if SOURCE_REFERENCE_INDEX in indexes:
        return
    op.create_index(
        SOURCE_REFERENCE_INDEX,
        "recalc_job",
        SOURCE_REFERENCE_COLUMNS,
        unique=False,
        sqlite_where=sa.text(SOURCE_REFERENCE_PREDICATE),
        postgresql_where=sa.text(SOURCE_REFERENCE_PREDICATE),
    )


def _has_source_reference_predicate(
    connection: sa.Connection,
    index: dict[str, object],
) -> bool:
    dialect_name = connection.dialect.name
    predicate = (index.get("dialect_options") or {}).get(
        f"{dialect_name}_where"
    )
    if predicate is None:
        return False
    normalized = str(predicate).lower()
    return (
        normalized.count("is not null") >= 2
        and (normalized.count("trim(") >= 2 or normalized.count("btrim(") >= 2)
        and "trigger_ref_type" in normalized
        and "trigger_ref_id" in normalized
    )


def upgrade() -> None:
    connection = op.get_bind()
    _validated_source_reference_indexes(connection)
    _validate_inbox_state_contract(connection)
    synthetic_rows = _proven_synthetic_rows(connection)
    _remove_synthetic_rows(connection, synthetic_rows)
    _validate_remaining_claims(connection)
    _rebuild_invalidation_state(connection)
    _validate_inbox_state_contract(connection)
    _converge_source_reference_index(connection)


def downgrade() -> None:
    raise RuntimeError(
        "Recalc execution-audit convergence is intentionally irreversible: "
        "removed synthetic inbox rows cannot be reconstructed safely."
    )
