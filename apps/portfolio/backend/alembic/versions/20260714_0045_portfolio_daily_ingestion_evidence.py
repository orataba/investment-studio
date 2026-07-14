"""Persist quote ingestion evidence semantics in sealed daily manifests.

Revision ID: 20260714_0045
Revises: 20260714_0044
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import re

from alembic import op
import sqlalchemy as sa


revision = "20260714_0045"
down_revision = "20260714_0044"
branch_labels = None
depends_on = None


INGESTION_STATE_CHECK = (
    "ingestion_time_state IN ("
    "'observed', "
    "'legacy_series_upper_bound', "
    "'legacy_instrument_upper_bound', "
    "'legacy_migration_upper_bound'"
    ")"
)

_SOURCE_CHECK_DEFINITIONS = {
    "ck_quote_observation_revision_ingestion_time_state": (
        "CHECK (ingestion_time_state::text = ANY (ARRAY["
        "'observed'::character varying, "
        "'legacy_series_upper_bound'::character varying, "
        "'legacy_instrument_upper_bound'::character varying, "
        "'legacy_migration_upper_bound'::character varying]::text[]))"
    ),
    "ck_quote_observation_revision_legacy_ingestion_time_schema": (
        "CHECK (ingestion_time_state::text = 'observed'::text OR "
        "payload_schema_version = 1)"
    ),
}
_SOURCE_INSERT_TRIGGER_DEFINITION = (
    "CREATE TRIGGER trg_quote_observation_revision_insert_sequence BEFORE "
    "INSERT ON instrument_registry.quote_observation_revision FOR EACH ROW "
    "EXECUTE FUNCTION "
    "instrument_registry.guard_quote_observation_revision_insert_sequence()"
)
_SOURCE_OBSERVED_INSERT_GUARD = (
    "IF NEW.ingestion_time_state IS DISTINCT FROM 'observed' THEN "
    "RAISE EXCEPTION 'quote_revision_insert_violation: new revision ingestion "
    "time must be observed' USING ERRCODE = '55000'; END IF;"
)
_SOURCE_IMMUTABLE_TRIGGER_DEFINITION = (
    "CREATE TRIGGER trg_quote_observation_revision_immutable BEFORE DELETE OR "
    "UPDATE ON instrument_registry.quote_observation_revision FOR EACH ROW "
    "EXECUTE FUNCTION "
    "instrument_registry.guard_quote_observation_revision_immutable()"
)
_SOURCE_IMMUTABLE_INGESTED_AT_GUARD = (
    "AND NEW.ingested_at IS NOT DISTINCT FROM OLD.ingested_at"
)
_SOURCE_IMMUTABLE_STATE_GUARD = (
    "AND NEW.ingestion_time_state IS NOT DISTINCT FROM "
    "OLD.ingestion_time_state"
)
_SOURCE_IMMUTABLE_REJECTION = (
    "RAISE EXCEPTION 'quote_revision_immutable_violation: only current "
    "lifecycle closure is allowed' USING ERRCODE = '55000';"
)


def _normalized_ddl(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@contextmanager
def _deterministic_deparser_search_path(
    connection: sa.Connection,
) -> Iterator[None]:
    previous = str(
        connection.scalar(sa.text("SELECT current_setting('search_path')"))
    )
    connection.execute(
        sa.text("SELECT set_config('search_path', 'pg_catalog, public', true)")
    )
    try:
        yield
    finally:
        connection.execute(
            sa.text("SELECT set_config('search_path', :search_path, true)"),
            {"search_path": previous},
        )


def _require_instrument_ingestion_contract(connection: sa.Connection) -> None:
    version = connection.scalar(
        sa.text(
            "SELECT version_num FROM instrument_registry.alembic_version"
        )
    )
    columns = {
        row["column_name"]: row
        for row in connection.execute(
            sa.text(
                """
                SELECT column_name, is_nullable, data_type
                FROM information_schema.columns
                WHERE table_schema = 'instrument_registry'
                  AND table_name = 'quote_observation_revision'
                  AND column_name IN ('ingested_at', 'ingestion_time_state')
                """
            )
        ).mappings()
    }
    contract_error = (
        "20260714_0045 requires the complete instrument-registry ingestion "
        "evidence contract introduced by 20260714_0014 before Portfolio "
        f"migration (current registry revision: {version!r})."
    )
    ingested_at = columns.get("ingested_at")
    ingestion_state = columns.get("ingestion_time_state")
    if not (
        ingested_at is not None
        and ingested_at["is_nullable"] == "NO"
        and ingested_at["data_type"] == "timestamp with time zone"
        and ingestion_state is not None
        and ingestion_state["is_nullable"] == "NO"
        and ingestion_state["data_type"] == "character varying"
    ):
        raise RuntimeError(contract_error)

    with _deterministic_deparser_search_path(connection):
        constraints = {
            row["conname"]: row
            for row in connection.execute(
                sa.text(
                    """
                    SELECT conname, convalidated,
                           pg_get_constraintdef(oid, true) AS definition
                    FROM pg_constraint
                    WHERE conrelid =
                        'instrument_registry.quote_observation_revision'::regclass
                      AND conname IN (
                          'ck_quote_observation_revision_ingestion_time_state',
                          'ck_quote_observation_revision_legacy_ingestion_time_schema'
                      )
                    """
                )
            ).mappings()
        }
        insert_guard = connection.execute(
            sa.text(
                """
                SELECT trigger_row.tgenabled,
                       pg_get_triggerdef(trigger_row.oid, true)
                           AS trigger_definition,
                       pg_get_functiondef(trigger_row.tgfoid)
                           AS function_definition
                FROM pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid =
                    'instrument_registry.quote_observation_revision'::regclass
                  AND trigger_row.tgname =
                      'trg_quote_observation_revision_insert_sequence'
                  AND NOT trigger_row.tgisinternal
                """
            )
        ).mappings().first()
        immutable_guard = connection.execute(
            sa.text(
                """
                SELECT trigger_row.tgenabled,
                       pg_get_triggerdef(trigger_row.oid, true)
                           AS trigger_definition,
                       pg_get_functiondef(trigger_row.tgfoid)
                           AS function_definition
                FROM pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid =
                    'instrument_registry.quote_observation_revision'::regclass
                  AND trigger_row.tgname =
                      'trg_quote_observation_revision_immutable'
                  AND NOT trigger_row.tgisinternal
                """
            )
        ).mappings().first()
    invalid_source_rows = int(
        connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM instrument_registry.quote_observation_revision
                WHERE ingested_at IS NULL
                   OR ingestion_time_state IS NULL
                   OR ingestion_time_state NOT IN (
                       'observed',
                       'legacy_series_upper_bound',
                       'legacy_instrument_upper_bound',
                       'legacy_migration_upper_bound'
                   )
                   OR (
                       ingestion_time_state <> 'observed'
                       AND payload_schema_version <> 1
                   )
                """
            )
        )
        or 0
    )

    constraint_contract_ok = all(
        (row := constraints.get(name)) is not None
        and bool(row["convalidated"])
        and row["definition"] == definition
        for name, definition in _SOURCE_CHECK_DEFINITIONS.items()
    )
    insert_guard_ok = (
        insert_guard is not None
        and insert_guard["tgenabled"] == "O"
        and _normalized_ddl(insert_guard["trigger_definition"])
        == _SOURCE_INSERT_TRIGGER_DEFINITION
        and _SOURCE_OBSERVED_INSERT_GUARD
        in _normalized_ddl(insert_guard["function_definition"])
    )
    immutable_function = _normalized_ddl(
        immutable_guard["function_definition"]
        if immutable_guard is not None
        else None
    )
    immutable_guard_ok = (
        immutable_guard is not None
        and immutable_guard["tgenabled"] == "O"
        and _normalized_ddl(immutable_guard["trigger_definition"])
        == _SOURCE_IMMUTABLE_TRIGGER_DEFINITION
        and _SOURCE_IMMUTABLE_INGESTED_AT_GUARD in immutable_function
        and _SOURCE_IMMUTABLE_STATE_GUARD in immutable_function
        and _SOURCE_IMMUTABLE_REJECTION in immutable_function
    )
    if not (
        constraint_contract_ok
        and insert_guard_ok
        and immutable_guard_ok
        and invalid_source_rows == 0
    ):
        raise RuntimeError(contract_error)


def _require_empty_pre_state_manifests(connection: sa.Connection) -> None:
    row = connection.execute(
        sa.text(
            """
            SELECT
                (SELECT count(*)
                   FROM portfolio.portfolio_daily_quote_candidate) AS quote_count,
                (SELECT count(*)
                   FROM portfolio.portfolio_daily_fx_leg) AS fx_count
            """
        )
    ).mappings().one()
    if int(row["quote_count"]) or int(row["fx_count"]):
        raise RuntimeError(
            "20260714_0045 cannot reinterpret already-captured immutable quote "
            "evidence. Rebuild pre-publication daily manifests before upgrade: "
            f"quote_candidate={row['quote_count']}, fx_leg={row['fx_count']}."
        )


def _replace_lineage_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION portfolio.pd_guard_quote_candidate_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_source record;
        BEGIN
            SELECT qs.quote_series_id, qo.observation_id, qo.as_of_date,
                   qr.revision_id, qr.revision_number, qr.value, qr.status,
                   qr.source_published_at, qr.ingested_at,
                   qr.ingestion_time_state, qr.payload_hash
            INTO v_source
            FROM instrument_registry.quote_observation_revision AS qr
            JOIN instrument_registry.quote_observation AS qo
              ON qo.observation_id = qr.observation_id
            JOIN instrument_registry.quote_series AS qs
              ON qs.quote_series_id = qo.quote_series_id
            WHERE qr.revision_id = NEW.revision_id
            FOR KEY SHARE OF qr, qo, qs;
            IF NOT FOUND OR ROW(
                    v_source.quote_series_id, v_source.observation_id,
                    v_source.as_of_date, v_source.revision_number, v_source.value,
                    v_source.status, v_source.source_published_at,
                    v_source.ingested_at, v_source.ingestion_time_state,
                    v_source.payload_hash
                ) IS DISTINCT FROM ROW(
                    NEW.quote_series_id, NEW.observation_id,
                    NEW.observation_date, NEW.revision_number, NEW.quote_value,
                    NEW.quote_status, NEW.source_published_at,
                    NEW.ingested_at, NEW.ingestion_time_state,
                    NEW.payload_hash
                ) THEN
                RAISE EXCEPTION 'portfolio_daily_quote_revision_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE OR REPLACE FUNCTION portfolio.pd_guard_fx_leg_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_source record;
        BEGIN
            IF NEW.leg_resolution_status = 'missing' THEN
                RETURN NEW;
            END IF;
            SELECT qs.quote_series_id, qo.observation_id, qo.as_of_date,
                   qr.revision_number, qr.value, qr.status,
                   qr.source_published_at, qr.ingested_at,
                   qr.ingestion_time_state, qr.payload_hash
            INTO v_source
            FROM instrument_registry.quote_observation_revision AS qr
            JOIN instrument_registry.quote_observation AS qo
              ON qo.observation_id = qr.observation_id
            JOIN instrument_registry.quote_series AS qs
              ON qs.quote_series_id = qo.quote_series_id
            WHERE qr.revision_id = NEW.revision_id
            FOR KEY SHARE OF qr, qo, qs;
            IF NOT FOUND OR ROW(
                    v_source.quote_series_id, v_source.observation_id,
                    v_source.as_of_date, v_source.revision_number, v_source.value,
                    v_source.status, v_source.source_published_at,
                    v_source.ingested_at, v_source.ingestion_time_state,
                    v_source.payload_hash
                ) IS DISTINCT FROM ROW(
                    NEW.quote_series_id, NEW.observation_id,
                    NEW.observation_date, NEW.revision_number, NEW.quoted_rate,
                    NEW.quote_status, NEW.source_published_at,
                    NEW.ingested_at, NEW.ingestion_time_state,
                    NEW.payload_hash
                ) THEN
                RAISE EXCEPTION 'portfolio_daily_fx_leg_revision_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.leg_resolution_status = 'resolved'
               AND ((NOT NEW.is_inverted
                        AND NEW.effective_rate IS DISTINCT FROM NEW.quoted_rate)
                    OR (NEW.is_inverted
                        AND NEW.effective_rate IS DISTINCT FROM
                            calculation_registry.divide_significant_half_even(
                                1,
                                NEW.quoted_rate,
                                NEW.rate_math_precision
                            ))
                    OR NEW.rate_derivation_residual_exact IS DISTINCT FROM
                       (CASE WHEN NEW.is_inverted
                             THEN NEW.effective_rate * NEW.quoted_rate - 1
                             ELSE 0 END)) THEN
                RAISE EXCEPTION 'portfolio_daily_fx_leg_effective_rate_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError(
            "Portfolio Daily exact-storage migrations require PostgreSQL."
        )
    op.execute(
        "LOCK TABLE instrument_registry.quote_observation_revision "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    _require_instrument_ingestion_contract(connection)
    op.execute(
        "LOCK TABLE portfolio.portfolio_daily_quote_candidate, "
        "portfolio.portfolio_daily_fx_leg IN ACCESS EXCLUSIVE MODE"
    )
    _require_empty_pre_state_manifests(connection)

    op.alter_column(
        "portfolio_daily_quote_candidate",
        "ingested_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        schema="portfolio",
    )
    op.add_column(
        "portfolio_daily_quote_candidate",
        sa.Column("ingestion_time_state", sa.String(length=40), nullable=False),
        schema="portfolio",
    )
    op.create_check_constraint(
        "ck_pd_quote_candidate_ingestion_time_state",
        "portfolio_daily_quote_candidate",
        INGESTION_STATE_CHECK,
        schema="portfolio",
    )

    op.add_column(
        "portfolio_daily_fx_leg",
        sa.Column("ingestion_time_state", sa.String(length=40), nullable=True),
        schema="portfolio",
    )
    op.create_check_constraint(
        "ck_pd_fx_leg_ingestion_time_state",
        "portfolio_daily_fx_leg",
        "(leg_resolution_status = 'missing' AND ingestion_time_state IS NULL) "
        "OR (leg_resolution_status <> 'missing' AND "
        + INGESTION_STATE_CHECK
        + ")",
        schema="portfolio",
    )
    _replace_lineage_guards()


def downgrade() -> None:
    raise RuntimeError(
        "20260714_0045 is irreversible: dropping ingestion-time state would "
        "make sealed quote evidence temporally ambiguous."
    )
