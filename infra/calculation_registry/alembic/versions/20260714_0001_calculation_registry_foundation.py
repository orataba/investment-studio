"""Create the exact calculation publication registry foundation.

Revision ID: 20260714_0001
Revises:
Create Date: 2026-07-14 00:00:00

This chain is deliberately PostgreSQL-only.  Besides relational constraints,
the migration installs state-machine, immutability, generation-CAS, manifest
sealing, dependency locking, lease-fencing, and output-write guards.  Producer
schemas own typed dependencies and outputs and attach the public guard trigger
functions created here.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260714_0001"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA = "calculation_registry"
RUN_STATES = (
    "capturing",
    "queued",
    "running",
    "succeeded",
    "published",
    "superseded",
    "failed",
)
JOB_STATES = (
    "queued",
    "leased",
    "retry_wait",
    "succeeded",
    "failed",
    "superseded",
)
INTENT_STATES = ("pending", "materialized", "superseded", "failed")


def _states(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _create_numeric_functions() -> None:
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.round_half_even(
                p_value numeric,
                p_scale integer
            )
            RETURNS numeric
            LANGUAGE plpgsql
            IMMUTABLE
            STRICT
            PARALLEL SAFE
            AS $$
            DECLARE
                v_factor numeric;
                v_quantum numeric;
                v_scaled numeric;
                v_floor numeric;
                v_fraction numeric;
                v_rounded numeric;
            BEGIN
                IF p_scale < 0 OR p_scale > 1000 THEN
                    RAISE EXCEPTION 'calculation_round_half_even_scale_out_of_range: %',
                        p_scale USING ERRCODE = '22003';
                END IF;
                IF p_value::text IN ('NaN', 'Infinity', '-Infinity') THEN
                    RAISE EXCEPTION 'calculation_round_half_even_nonfinite'
                        USING ERRCODE = '22003';
                END IF;

                v_factor := power(10::numeric, p_scale);
                v_quantum := ('1e-' || p_scale::text)::numeric;
                v_scaled := abs(p_value) * v_factor;
                v_floor := trunc(v_scaled);
                v_fraction := v_scaled - v_floor;
                v_rounded := CASE
                    WHEN v_fraction < 0.5 THEN v_floor
                    WHEN v_fraction > 0.5 THEN v_floor + 1
                    WHEN mod(v_floor, 2) = 0 THEN v_floor
                    ELSE v_floor + 1
                END;
                RETURN (CASE WHEN p_value < 0 THEN -v_rounded ELSE v_rounded END)
                    * v_quantum;
            END;
            $$;

            CREATE FUNCTION {SCHEMA}.round_significant_half_even(
                p_value numeric,
                p_significant_digits integer
            )
            RETURNS numeric
            LANGUAGE plpgsql
            IMMUTABLE
            STRICT
            PARALLEL SAFE
            AS $$
            DECLARE
                v_normalized numeric;
                v_magnitude integer := 0;
                v_scale integer;
                v_iterations integer := 0;
                v_shift numeric;
                v_quantum numeric;
            BEGIN
                IF p_significant_digits < 1 OR p_significant_digits > 1000 THEN
                    RAISE EXCEPTION
                        'calculation_round_significant_digits_out_of_range: %',
                        p_significant_digits USING ERRCODE = '22003';
                END IF;
                IF p_value::text IN ('NaN', 'Infinity', '-Infinity') THEN
                    RAISE EXCEPTION 'calculation_round_significant_nonfinite'
                        USING ERRCODE = '22003';
                END IF;
                IF p_value = 0 THEN
                    RETURN 0;
                END IF;

                v_normalized := abs(p_value);
                IF v_normalized >= 1 THEN
                    WHILE v_normalized >= 10 LOOP
                        v_normalized := v_normalized * 0.1;
                        v_magnitude := v_magnitude + 1;
                        v_iterations := v_iterations + 1;
                        IF v_iterations > 1000 THEN
                            RAISE EXCEPTION
                                'calculation_round_significant_magnitude_out_of_range'
                                USING ERRCODE = '22003';
                        END IF;
                    END LOOP;
                ELSE
                    WHILE v_normalized < 1 LOOP
                        v_normalized := v_normalized * 10;
                        v_magnitude := v_magnitude - 1;
                        v_iterations := v_iterations + 1;
                        IF v_iterations > 1000 THEN
                            RAISE EXCEPTION
                                'calculation_round_significant_magnitude_out_of_range'
                                USING ERRCODE = '22003';
                        END IF;
                    END LOOP;
                END IF;

                v_scale := p_significant_digits - 1 - v_magnitude;
                IF v_scale >= 0 THEN
                    RETURN {SCHEMA}.round_half_even(p_value, v_scale);
                END IF;
                v_shift := ('1e' || v_scale::text)::numeric;
                v_quantum := ('1e' || (-v_scale)::text)::numeric;
                RETURN {SCHEMA}.round_half_even(p_value * v_shift, 0)
                    * v_quantum;
            END;
            $$;

            CREATE FUNCTION {SCHEMA}.divide_significant_half_even(
                p_numerator numeric,
                p_denominator numeric,
                p_significant_digits integer
            )
            RETURNS numeric
            LANGUAGE plpgsql
            IMMUTABLE
            STRICT
            PARALLEL SAFE
            AS $$
            DECLARE
                v_numerator numeric;
                v_denominator numeric;
                v_magnitude integer := 0;
                v_iterations integer := 0;
                v_factor numeric;
                v_scaled_numerator numeric;
                v_floor numeric;
                v_remainder numeric;
                v_rounded numeric;
                v_quantum numeric;
                v_negative boolean;
            BEGIN
                IF p_significant_digits < 1 OR p_significant_digits > 1000 THEN
                    RAISE EXCEPTION
                        'calculation_divide_significant_digits_out_of_range: %',
                        p_significant_digits USING ERRCODE = '22003';
                END IF;
                IF p_numerator::text IN ('NaN', 'Infinity', '-Infinity')
                   OR p_denominator::text IN ('NaN', 'Infinity', '-Infinity') THEN
                    RAISE EXCEPTION 'calculation_divide_significant_nonfinite'
                        USING ERRCODE = '22003';
                END IF;
                IF p_denominator = 0 THEN
                    RAISE EXCEPTION 'calculation_divide_significant_zero_denominator'
                        USING ERRCODE = '22012';
                END IF;
                IF p_numerator = 0 THEN
                    RETURN 0;
                END IF;

                v_negative := (p_numerator < 0) <> (p_denominator < 0);
                v_numerator := abs(p_numerator);
                v_denominator := abs(p_denominator);

                -- Normalize the exact rational into [1, 10) using decimal
                -- shifts only.  No PostgreSQL numeric division participates
                -- in the rounding decision.
                IF v_numerator >= v_denominator THEN
                    WHILE v_numerator >= v_denominator * 10 LOOP
                        v_denominator := v_denominator * 10;
                        v_magnitude := v_magnitude + 1;
                        v_iterations := v_iterations + 1;
                        IF v_iterations > 1000 THEN
                            RAISE EXCEPTION
                                'calculation_divide_significant_magnitude_out_of_range'
                                USING ERRCODE = '22003';
                        END IF;
                    END LOOP;
                ELSE
                    WHILE v_numerator < v_denominator LOOP
                        v_numerator := v_numerator * 10;
                        v_magnitude := v_magnitude - 1;
                        v_iterations := v_iterations + 1;
                        IF v_iterations > 1000 THEN
                            RAISE EXCEPTION
                                'calculation_divide_significant_magnitude_out_of_range'
                                USING ERRCODE = '22003';
                        END IF;
                    END LOOP;
                END IF;

                v_factor := ('1e' || (p_significant_digits - 1)::text)::numeric;
                v_scaled_numerator := v_numerator * v_factor;
                v_floor := div(v_scaled_numerator, v_denominator);
                v_remainder := mod(v_scaled_numerator, v_denominator);
                v_rounded := CASE
                    WHEN v_remainder * 2 < v_denominator THEN v_floor
                    WHEN v_remainder * 2 > v_denominator THEN v_floor + 1
                    WHEN mod(v_floor, 2) = 0 THEN v_floor
                    ELSE v_floor + 1
                END;
                v_quantum := (
                    '1e' || (v_magnitude - p_significant_digits + 1)::text
                )::numeric;
                RETURN (CASE WHEN v_negative THEN -v_rounded ELSE v_rounded END)
                    * v_quantum;
            END;
            $$;
            """
        )
    )


def _create_tables() -> None:
    op.create_table(
        "calculation_scope_generation",
        sa.Column("calculation_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(calculation_kind) <> '' AND calculation_kind = btrim(calculation_kind)",
            name="ck_calc_scope_generation_kind",
        ),
        sa.CheckConstraint(
            "btrim(scope_kind) <> '' AND scope_kind = btrim(scope_kind)",
            name="ck_calc_scope_generation_scope_kind",
        ),
        sa.CheckConstraint(
            "btrim(scope_id) <> '' AND scope_id = btrim(scope_id)",
            name="ck_calc_scope_generation_scope_id",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_calc_scope_generation_nonnegative"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_calc_scope_generation_timestamps"),
        sa.PrimaryKeyConstraint(
            "calculation_kind",
            "scope_kind",
            "scope_id",
            name="pk_calculation_scope_generation",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_calc_scope_generation_kind_generation",
        "calculation_scope_generation",
        ["calculation_kind", "generation"],
        schema=SCHEMA,
    )

    op.create_table(
        "calculation_run",
        sa.Column(
            "run_id",
            _uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("calculation_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("requested_as_of", sa.Date(), nullable=False),
        sa.Column("effective_as_of", sa.Date(), nullable=False),
        sa.Column(
            "cutoff_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("transaction_timestamp()"),
            nullable=False,
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("methodology_version", sa.String(length=128), nullable=False),
        sa.Column("input_schema_version", sa.String(length=64), nullable=False),
        sa.Column("output_schema_version", sa.String(length=64), nullable=False),
        sa.Column("captured_generation", sa.BigInteger(), nullable=False),
        sa.Column("dedupe_key", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'capturing'"),
            nullable=False,
        ),
        sa.Column("status_reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "status_reason_context",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest_id", _uuid(), nullable=True),
        sa.Column("published_output_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("superseded_by_run_id", _uuid(), nullable=True),
        sa.CheckConstraint(
            f"status IN ({_states(RUN_STATES)})",
            name="ck_calculation_run_status",
        ),
        sa.CheckConstraint(
            "captured_generation >= 0",
            name="ck_calculation_run_generation",
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_run_dedupe_hash",
        ),
        sa.CheckConstraint(
            "published_output_hash IS NULL OR "
            "published_output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_run_output_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(status_reason_context) = 'object'",
            name="ck_calculation_run_reason_context",
        ),
        sa.CheckConstraint(
            "status_reason_code IS NULL OR "
            "(btrim(status_reason_code) <> '' AND "
            " status_reason_code = btrim(status_reason_code))",
            name="ck_calculation_run_reason_code",
        ),
        sa.CheckConstraint(
            "effective_as_of <= requested_as_of",
            name="ck_calculation_run_as_of_order",
        ),
        sa.CheckConstraint(
            "btrim(calculation_kind) <> '' AND calculation_kind = btrim(calculation_kind) "
            "AND btrim(scope_kind) <> '' AND scope_kind = btrim(scope_kind) "
            "AND btrim(scope_id) <> '' AND scope_id = btrim(scope_id) "
            "AND btrim(timezone) <> '' AND timezone = btrim(timezone) "
            "AND btrim(methodology_version) <> '' AND methodology_version = btrim(methodology_version) "
            "AND btrim(input_schema_version) <> '' AND input_schema_version = btrim(input_schema_version) "
            "AND btrim(output_schema_version) <> '' AND output_schema_version = btrim(output_schema_version) "
            "AND btrim(requested_by) <> '' AND requested_by = btrim(requested_by)",
            name="ck_calculation_run_nonblank_fields",
        ),
        sa.CheckConstraint(
            "(started_at IS NULL OR started_at >= created_at) "
            "AND (completed_at IS NULL OR completed_at >= created_at) "
            "AND (started_at IS NULL OR completed_at IS NULL OR completed_at >= started_at)",
            name="ck_calculation_run_timestamp_order",
        ),
        sa.CheckConstraint(
            "(status = 'capturing' AND manifest_id IS NULL AND started_at IS NULL "
            "    AND completed_at IS NULL AND published_output_hash IS NULL "
            "    AND superseded_by_run_id IS NULL) "
            "OR (status = 'queued' AND manifest_id IS NOT NULL AND started_at IS NULL "
            "    AND completed_at IS NULL AND published_output_hash IS NULL "
            "    AND superseded_by_run_id IS NULL) "
            "OR (status = 'running' AND manifest_id IS NOT NULL AND started_at IS NOT NULL "
            "    AND completed_at IS NULL AND published_output_hash IS NULL "
            "    AND superseded_by_run_id IS NULL) "
            "OR (status = 'succeeded' AND manifest_id IS NOT NULL AND started_at IS NOT NULL "
            "    AND completed_at IS NOT NULL AND published_output_hash IS NULL "
            "    AND superseded_by_run_id IS NULL) "
            "OR (status = 'published' AND manifest_id IS NOT NULL AND started_at IS NOT NULL "
            "    AND completed_at IS NOT NULL AND published_output_hash IS NOT NULL "
            "    AND superseded_by_run_id IS NULL) "
            "OR (status = 'failed' AND completed_at IS NOT NULL "
            "    AND status_reason_code IS NOT NULL AND published_output_hash IS NULL "
            "    AND superseded_by_run_id IS NULL) "
            "OR (status = 'superseded' AND completed_at IS NOT NULL "
            "    AND status_reason_code IS NOT NULL AND published_output_hash IS NULL "
            "    AND superseded_by_run_id IS NOT NULL)",
            name="ck_calculation_run_state_fields",
        ),
        sa.CheckConstraint(
            "superseded_by_run_id IS NULL OR superseded_by_run_id <> run_id",
            name="ck_calculation_run_not_self_superseded",
        ),
        sa.ForeignKeyConstraint(
            ["calculation_kind", "scope_kind", "scope_id"],
            [
                f"{SCHEMA}.calculation_scope_generation.calculation_kind",
                f"{SCHEMA}.calculation_scope_generation.scope_kind",
                f"{SCHEMA}.calculation_scope_generation.scope_id",
            ],
            name="fk_calculation_run_scope_generation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_run_id"],
            [f"{SCHEMA}.calculation_run.run_id"],
            name="fk_calculation_run_superseded_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_calculation_run"),
        sa.UniqueConstraint("manifest_id", name="uq_calculation_run_manifest"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_calculation_run_active_dedupe",
        "calculation_run",
        ["dedupe_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "status IN ('capturing', 'queued', 'running', 'succeeded')"
        ),
    )
    op.create_index(
        "ix_calculation_run_scope_as_of",
        "calculation_run",
        ["calculation_kind", "scope_kind", "scope_id", sa.text("effective_as_of DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_calculation_run_status_created",
        "calculation_run",
        ["status", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "calculation_input_manifest",
        sa.Column(
            "manifest_id",
            _uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", _uuid(), nullable=False),
        sa.Column("captured_generation", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'building'"),
            nullable=False,
        ),
        sa.Column("canonical_manifest_hash", sa.CHAR(length=64), nullable=True),
        sa.Column(
            "dependency_counts",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "captured_generation >= 0",
            name="ck_calculation_manifest_generation",
        ),
        sa.CheckConstraint(
            "status IN ('building', 'sealed')",
            name="ck_calculation_manifest_status",
        ),
        sa.CheckConstraint(
            "btrim(schema_version) <> '' AND schema_version = btrim(schema_version)",
            name="ck_calculation_manifest_schema_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(dependency_counts) = 'object'",
            name="ck_calculation_manifest_dependency_counts",
        ),
        sa.CheckConstraint(
            "canonical_manifest_hash IS NULL OR "
            "canonical_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_manifest_hash",
        ),
        sa.CheckConstraint(
            "(status = 'building' AND canonical_manifest_hash IS NULL AND sealed_at IS NULL) "
            "OR (status = 'sealed' AND canonical_manifest_hash IS NOT NULL "
            "    AND sealed_at IS NOT NULL AND sealed_at >= created_at)",
            name="ck_calculation_manifest_state_fields",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{SCHEMA}.calculation_run.run_id"],
            name="fk_calculation_manifest_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("manifest_id", name="pk_calculation_input_manifest"),
        sa.UniqueConstraint("run_id", name="uq_calculation_manifest_run"),
        sa.UniqueConstraint(
            "manifest_id",
            "run_id",
            name="uq_calculation_manifest_id_run",
        ),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_calculation_run_manifest_pair",
        "calculation_run",
        "calculation_input_manifest",
        ["manifest_id", "run_id"],
        ["manifest_id", "run_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "calculation_job",
        sa.Column(
            "job_id",
            _uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", _uuid(), nullable=False),
        sa.Column("dedupe_key", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fencing_token",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_diagnostic", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"status IN ({_states(JOB_STATES)})",
            name="ck_calculation_job_status",
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_job_dedupe_hash",
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts "
            "AND fencing_token >= 0",
            name="ck_calculation_job_attempts",
        ),
        sa.CheckConstraint(
            "lease_owner IS NULL OR "
            "(btrim(lease_owner) <> '' AND lease_owner = btrim(lease_owner))",
            name="ck_calculation_job_lease_owner",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR "
            "(btrim(failure_code) <> '' AND failure_code = btrim(failure_code))",
            name="ck_calculation_job_failure_code",
        ),
        sa.CheckConstraint(
            "failure_diagnostic IS NULL OR "
            "(btrim(failure_diagnostic) <> '' AND "
            " failure_diagnostic = btrim(failure_diagnostic))",
            name="ck_calculation_job_failure_diagnostic",
        ),
        sa.CheckConstraint(
            "(heartbeat_at IS NULL OR heartbeat_at >= created_at) "
            "AND (lease_expires_at IS NULL OR lease_expires_at >= created_at) "
            "AND (heartbeat_at IS NULL OR lease_expires_at IS NULL "
            "     OR lease_expires_at > heartbeat_at) "
            "AND updated_at >= created_at "
            "AND (completed_at IS NULL OR completed_at >= created_at)",
            name="ck_calculation_job_timestamps",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND attempt = 0 AND fencing_token = 0 "
            "    AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "    AND heartbeat_at IS NULL AND failure_code IS NULL "
            "    AND failure_diagnostic IS NULL AND completed_at IS NULL) "
            "OR (status = 'leased' AND attempt >= 1 AND fencing_token = attempt "
            "    AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "    AND heartbeat_at IS NOT NULL AND failure_code IS NULL "
            "    AND failure_diagnostic IS NULL AND completed_at IS NULL) "
            "OR (status = 'retry_wait' AND attempt >= 1 AND fencing_token = attempt "
            "    AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "    AND heartbeat_at IS NULL AND failure_code IS NOT NULL "
            "    AND completed_at IS NULL) "
            "OR (status = 'succeeded' AND attempt >= 1 AND fencing_token = attempt "
            "    AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "    AND heartbeat_at IS NULL AND failure_code IS NULL "
            "    AND failure_diagnostic IS NULL AND completed_at IS NOT NULL) "
            "OR (status = 'failed' AND attempt >= 1 AND fencing_token = attempt "
            "    AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "    AND heartbeat_at IS NULL AND failure_code IS NOT NULL "
            "    AND completed_at IS NOT NULL) "
            "OR (status = 'superseded' AND lease_owner IS NULL "
            "    AND lease_expires_at IS NULL AND heartbeat_at IS NULL "
            "    AND failure_code IS NOT NULL "
            "    AND completed_at IS NOT NULL)",
            name="ck_calculation_job_state_fields",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{SCHEMA}.calculation_run.run_id"],
            name="fk_calculation_job_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("job_id", name="pk_calculation_job"),
        sa.UniqueConstraint("run_id", name="uq_calculation_job_run"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_calculation_job_active_dedupe",
        "calculation_job",
        ["dedupe_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'leased', 'retry_wait')"),
    )
    op.create_index(
        "ix_calculation_job_queue_claim",
        "calculation_job",
        ["status", "available_at", "created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'retry_wait')"),
    )
    op.create_index(
        "ix_calculation_job_expired_lease",
        "calculation_job",
        ["lease_expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'leased'"),
    )

    op.create_table(
        "calculation_publication",
        sa.Column(
            "publication_id",
            _uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", _uuid(), nullable=False),
        sa.Column("manifest_id", _uuid(), nullable=False),
        sa.Column("calculation_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("output_schema_version", sa.String(length=64), nullable=False),
        sa.Column("published_fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("canonical_output_hash", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "published_fencing_token > 0",
            name="ck_calculation_publication_fencing_token",
        ),
        sa.CheckConstraint(
            "canonical_output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_publication_output_hash",
        ),
        sa.CheckConstraint(
            "btrim(calculation_kind) <> '' AND calculation_kind = btrim(calculation_kind) "
            "AND btrim(scope_kind) <> '' AND scope_kind = btrim(scope_kind) "
            "AND btrim(scope_id) <> '' AND scope_id = btrim(scope_id) "
            "AND btrim(output_schema_version) <> '' "
            "AND output_schema_version = btrim(output_schema_version)",
            name="ck_calculation_publication_nonblank_fields",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{SCHEMA}.calculation_run.run_id"],
            name="fk_calculation_publication_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id", "run_id"],
            [
                f"{SCHEMA}.calculation_input_manifest.manifest_id",
                f"{SCHEMA}.calculation_input_manifest.run_id",
            ],
            name="fk_calculation_publication_manifest_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("publication_id", name="pk_calculation_publication"),
        sa.UniqueConstraint("run_id", name="uq_calculation_publication_run"),
        sa.UniqueConstraint("manifest_id", name="uq_calculation_publication_manifest"),
        sa.UniqueConstraint(
            "publication_id",
            "calculation_kind",
            "scope_kind",
            "scope_id",
            name="uq_calculation_publication_scope",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_calculation_publication_scope_published",
        "calculation_publication",
        ["calculation_kind", "scope_kind", "scope_id", sa.text("published_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "calculation_current_publication",
        sa.Column("calculation_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("publication_id", _uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["calculation_kind", "scope_kind", "scope_id"],
            [
                f"{SCHEMA}.calculation_scope_generation.calculation_kind",
                f"{SCHEMA}.calculation_scope_generation.scope_kind",
                f"{SCHEMA}.calculation_scope_generation.scope_id",
            ],
            name="fk_calculation_current_scope_generation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id", "calculation_kind", "scope_kind", "scope_id"],
            [
                f"{SCHEMA}.calculation_publication.publication_id",
                f"{SCHEMA}.calculation_publication.calculation_kind",
                f"{SCHEMA}.calculation_publication.scope_kind",
                f"{SCHEMA}.calculation_publication.scope_id",
            ],
            name="fk_calculation_current_publication_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "calculation_kind",
            "scope_kind",
            "scope_id",
            name="pk_calculation_current_publication",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "calculation_recompute_intent",
        sa.Column(
            "intent_id",
            _uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("calculation_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("requested_generation", sa.BigInteger(), nullable=False),
        sa.Column("dedupe_key", sa.CHAR(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column(
            "reason_context",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("run_id", _uuid(), nullable=True),
        sa.Column("status_reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"status IN ({_states(INTENT_STATES)})",
            name="ck_calculation_intent_status",
        ),
        sa.CheckConstraint(
            "requested_generation >= 0",
            name="ck_calculation_intent_generation",
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_intent_dedupe_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_context) = 'object'",
            name="ck_calculation_intent_reason_context",
        ),
        sa.CheckConstraint(
            "btrim(reason_code) <> '' AND reason_code = btrim(reason_code)",
            name="ck_calculation_intent_reason_code",
        ),
        sa.CheckConstraint(
            "status_reason_code IS NULL OR "
            "(btrim(status_reason_code) <> '' AND "
            " status_reason_code = btrim(status_reason_code))",
            name="ck_calculation_intent_status_reason_code",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at "
            "AND (completed_at IS NULL OR completed_at >= created_at)",
            name="ck_calculation_intent_timestamps",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND run_id IS NULL AND status_reason_code IS NULL "
            "    AND completed_at IS NULL) "
            "OR (status = 'materialized' AND run_id IS NOT NULL "
            "    AND status_reason_code IS NULL AND completed_at IS NOT NULL) "
            "OR (status IN ('superseded', 'failed') AND status_reason_code IS NOT NULL "
            "    AND completed_at IS NOT NULL)",
            name="ck_calculation_intent_state_fields",
        ),
        sa.ForeignKeyConstraint(
            ["calculation_kind", "scope_kind", "scope_id"],
            [
                f"{SCHEMA}.calculation_scope_generation.calculation_kind",
                f"{SCHEMA}.calculation_scope_generation.scope_kind",
                f"{SCHEMA}.calculation_scope_generation.scope_id",
            ],
            name="fk_calculation_intent_scope_generation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{SCHEMA}.calculation_run.run_id"],
            name="fk_calculation_intent_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("intent_id", name="pk_calculation_recompute_intent"),
        sa.UniqueConstraint("dedupe_key", name="uq_calculation_intent_dedupe"),
        sa.UniqueConstraint(
            "calculation_kind",
            "scope_kind",
            "scope_id",
            "requested_generation",
            name="uq_calculation_intent_scope_generation",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_calculation_intent_pending",
        "calculation_recompute_intent",
        ["created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "calculation_worker_heartbeat",
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("instance_id", _uuid(), nullable=False),
        sa.Column("worker_version", sa.String(length=128), nullable=False),
        sa.Column("supported_calculation_kinds", _jsonb(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "metadata_json",
            _jsonb(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(worker_id) <> '' AND worker_id = btrim(worker_id) "
            "AND btrim(worker_version) <> '' AND worker_version = btrim(worker_version)",
            name="ck_calculation_worker_nonblank_fields",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(supported_calculation_kinds) = 'array' "
            "AND jsonb_array_length(supported_calculation_kinds) > 0",
            name="ck_calculation_worker_supported_kinds",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'",
            name="ck_calculation_worker_metadata",
        ),
        sa.CheckConstraint(
            "heartbeat_at >= started_at",
            name="ck_calculation_worker_timestamps",
        ),
        sa.PrimaryKeyConstraint("worker_id", name="pk_calculation_worker_heartbeat"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_calculation_worker_heartbeat_at",
        "calculation_worker_heartbeat",
        ["heartbeat_at"],
        schema=SCHEMA,
    )


def _drop_tables() -> None:
    op.drop_constraint(
        "fk_calculation_run_manifest_pair",
        "calculation_run",
        schema=SCHEMA,
        type_="foreignkey",
    )
    for table_name in (
        "calculation_worker_heartbeat",
        "calculation_recompute_intent",
        "calculation_current_publication",
        "calculation_publication",
        "calculation_job",
        "calculation_input_manifest",
        "calculation_run",
        "calculation_scope_generation",
    ):
        op.drop_table(table_name, schema=SCHEMA)


def _create_guard_functions() -> None:
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.reject_truncate()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'calculation_registry_truncate_forbidden: %.%',
                    TG_TABLE_SCHEMA, TG_TABLE_NAME
                    USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.guard_scope_generation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'calculation_scope_generation_delete_forbidden'
                        USING ERRCODE = '55000';
                END IF;

                IF TG_OP = 'UPDATE' THEN
                    IF ROW(NEW.calculation_kind, NEW.scope_kind, NEW.scope_id, NEW.created_at)
                       IS DISTINCT FROM
                       ROW(OLD.calculation_kind, OLD.scope_kind, OLD.scope_id, OLD.created_at) THEN
                        RAISE EXCEPTION 'calculation_scope_generation_identity_immutable'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NEW.generation <= OLD.generation THEN
                        RAISE EXCEPTION
                            'calculation_scope_generation_not_monotonic: old=%, new=%',
                            OLD.generation, NEW.generation
                            USING ERRCODE = '23514';
                    END IF;
                    NEW.updated_at := clock_timestamp();
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.guard_calculation_run()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_generation bigint;
                v_manifest record;
                v_replacement record;
                v_publication record;
                v_job record;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'calculation_run_delete_forbidden'
                        USING ERRCODE = '55000';
                END IF;

                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'capturing' THEN
                        RAISE EXCEPTION 'calculation_run_initial_status_must_be_capturing'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.cutoff_at IS DISTINCT FROM transaction_timestamp() THEN
                        RAISE EXCEPTION
                            'calculation_run_cutoff_must_equal_transaction_timestamp'
                            USING ERRCODE = '23514';
                    END IF;
                    SELECT generation
                    INTO v_generation
                    FROM {SCHEMA}.calculation_scope_generation
                    WHERE calculation_kind = NEW.calculation_kind
                      AND scope_kind = NEW.scope_kind
                      AND scope_id = NEW.scope_id
                    FOR KEY SHARE;
                    IF NOT FOUND OR v_generation <> NEW.captured_generation THEN
                        RAISE EXCEPTION
                            'calculation_run_generation_mismatch: captured=%, current=%',
                            NEW.captured_generation, v_generation
                            USING ERRCODE = '40001';
                    END IF;
                    RETURN NEW;
                END IF;

                IF OLD.status IN ('published', 'superseded', 'failed') THEN
                    RAISE EXCEPTION 'calculation_run_terminal_immutable: %', OLD.status
                        USING ERRCODE = '55000';
                END IF;
                IF ROW(
                    NEW.run_id, NEW.calculation_kind, NEW.scope_kind, NEW.scope_id,
                    NEW.requested_as_of, NEW.effective_as_of, NEW.cutoff_at, NEW.timezone,
                    NEW.methodology_version, NEW.input_schema_version,
                    NEW.output_schema_version, NEW.captured_generation, NEW.dedupe_key,
                    NEW.requested_by, NEW.created_at
                ) IS DISTINCT FROM ROW(
                    OLD.run_id, OLD.calculation_kind, OLD.scope_kind, OLD.scope_id,
                    OLD.requested_as_of, OLD.effective_as_of, OLD.cutoff_at, OLD.timezone,
                    OLD.methodology_version, OLD.input_schema_version,
                    OLD.output_schema_version, OLD.captured_generation, OLD.dedupe_key,
                    OLD.requested_by, OLD.created_at
                ) THEN
                    RAISE EXCEPTION 'calculation_run_identity_immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.status = OLD.status THEN
                    RAISE EXCEPTION 'calculation_run_same_state_update_forbidden: %', OLD.status
                        USING ERRCODE = '55000';
                END IF;
                IF NOT (
                    (OLD.status = 'capturing' AND NEW.status IN ('queued', 'failed', 'superseded'))
                    OR (OLD.status = 'queued' AND NEW.status IN ('running', 'failed', 'superseded'))
                    OR (OLD.status = 'running' AND NEW.status IN ('succeeded', 'failed', 'superseded'))
                    OR (OLD.status = 'succeeded' AND NEW.status IN ('published', 'superseded'))
                ) THEN
                    RAISE EXCEPTION 'calculation_run_invalid_transition: % -> %',
                        OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;

                IF NEW.status = 'queued' THEN
                    SELECT status, captured_generation, schema_version, run_id
                    INTO v_manifest
                    FROM {SCHEMA}.calculation_input_manifest
                    WHERE manifest_id = NEW.manifest_id
                    FOR UPDATE;
                    IF NOT FOUND
                       OR v_manifest.status <> 'sealed'
                       OR v_manifest.run_id <> NEW.run_id
                       OR v_manifest.captured_generation <> NEW.captured_generation
                       OR v_manifest.schema_version <> NEW.input_schema_version THEN
                        RAISE EXCEPTION 'calculation_run_requires_matching_sealed_manifest'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.status = 'running' THEN
                    SELECT status, lease_expires_at
                    INTO v_job
                    FROM {SCHEMA}.calculation_job
                    WHERE run_id = NEW.run_id
                    FOR KEY SHARE;
                    IF NOT FOUND
                       OR v_job.status <> 'leased'
                       OR v_job.lease_expires_at <= clock_timestamp() THEN
                        RAISE EXCEPTION 'calculation_run_start_requires_active_lease'
                            USING ERRCODE = '40001';
                    END IF;
                ELSIF NEW.status = 'succeeded' THEN
                    SELECT status
                    INTO v_job
                    FROM {SCHEMA}.calculation_job
                    WHERE run_id = NEW.run_id
                    FOR KEY SHARE;
                    IF NOT FOUND OR v_job.status <> 'succeeded' THEN
                        RAISE EXCEPTION 'calculation_run_success_requires_succeeded_job'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.status = 'failed' AND OLD.status <> 'capturing' THEN
                    SELECT status
                    INTO v_job
                    FROM {SCHEMA}.calculation_job
                    WHERE run_id = NEW.run_id
                    FOR KEY SHARE;
                    IF NOT FOUND
                       OR (OLD.status = 'queued' AND v_job.status <> 'superseded')
                       OR (OLD.status = 'running' AND v_job.status <> 'failed') THEN
                        RAISE EXCEPTION 'calculation_run_failure_job_state_mismatch'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.status = 'superseded' THEN
                    SELECT calculation_kind, scope_kind, scope_id, captured_generation
                    INTO v_replacement
                    FROM {SCHEMA}.calculation_run
                    WHERE run_id = NEW.superseded_by_run_id
                    FOR KEY SHARE;
                    IF NOT FOUND
                       OR ROW(v_replacement.calculation_kind, v_replacement.scope_kind,
                              v_replacement.scope_id)
                          IS DISTINCT FROM
                          ROW(NEW.calculation_kind, NEW.scope_kind, NEW.scope_id)
                       OR v_replacement.captured_generation < NEW.captured_generation THEN
                        RAISE EXCEPTION 'calculation_run_invalid_superseding_run'
                            USING ERRCODE = '23514';
                    END IF;
                    IF OLD.status <> 'capturing' THEN
                        SELECT status
                        INTO v_job
                        FROM {SCHEMA}.calculation_job
                        WHERE run_id = NEW.run_id
                        FOR KEY SHARE;
                        IF NOT FOUND
                           OR (OLD.status IN ('queued', 'running')
                               AND v_job.status <> 'superseded')
                           OR (OLD.status = 'succeeded'
                               AND v_job.status <> 'succeeded') THEN
                            RAISE EXCEPTION
                                'calculation_run_supersede_job_state_mismatch'
                                USING ERRCODE = '23514';
                        END IF;
                    END IF;
                ELSIF NEW.status = 'published' THEN
                    SELECT p.publication_id, p.canonical_output_hash,
                           cp.publication_id AS current_publication_id
                    INTO v_publication
                    FROM {SCHEMA}.calculation_publication AS p
                    LEFT JOIN {SCHEMA}.calculation_current_publication AS cp
                      ON cp.calculation_kind = p.calculation_kind
                     AND cp.scope_kind = p.scope_kind
                     AND cp.scope_id = p.scope_id
                    WHERE p.run_id = NEW.run_id;
                    IF NOT FOUND
                       OR v_publication.current_publication_id IS DISTINCT FROM
                          v_publication.publication_id
                       OR NEW.published_output_hash IS DISTINCT FROM
                          v_publication.canonical_output_hash THEN
                        RAISE EXCEPTION 'calculation_run_publication_pointer_or_hash_mismatch'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.guard_calculation_manifest()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_run record;
                v_generation bigint;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'building' THEN
                        RAISE EXCEPTION 'calculation_manifest_initial_status_must_be_building'
                            USING ERRCODE = '23514';
                    END IF;
                    SELECT status, captured_generation, input_schema_version,
                           calculation_kind, scope_kind, scope_id
                    INTO v_run
                    FROM {SCHEMA}.calculation_run
                    WHERE run_id = NEW.run_id
                    FOR UPDATE;
                    IF NOT FOUND
                       OR v_run.status <> 'capturing'
                       OR v_run.captured_generation <> NEW.captured_generation
                       OR v_run.input_schema_version <> NEW.schema_version THEN
                        RAISE EXCEPTION 'calculation_manifest_run_contract_mismatch'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END IF;

                IF TG_OP = 'DELETE' THEN
                    IF OLD.status = 'sealed' THEN
                        RAISE EXCEPTION 'calculation_manifest_sealed_immutable'
                            USING ERRCODE = '55000';
                    END IF;
                    RETURN OLD;
                END IF;

                IF OLD.status = 'sealed' THEN
                    RAISE EXCEPTION 'calculation_manifest_sealed_immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF ROW(NEW.manifest_id, NEW.run_id, NEW.captured_generation,
                       NEW.schema_version, NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.manifest_id, OLD.run_id, OLD.captured_generation,
                       OLD.schema_version, OLD.created_at) THEN
                    RAISE EXCEPTION 'calculation_manifest_identity_immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF OLD.status <> 'building' OR NEW.status <> 'sealed' THEN
                    RAISE EXCEPTION 'calculation_manifest_invalid_transition: % -> %',
                        OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM jsonb_each(NEW.dependency_counts) AS item(dependency_kind, dependency_count)
                    WHERE btrim(item.dependency_kind) = ''
                       OR item.dependency_kind <> btrim(item.dependency_kind)
                       OR jsonb_typeof(item.dependency_count) <> 'number'
                       OR (item.dependency_count #>> '{{}}')::numeric < 0
                       OR trunc((item.dependency_count #>> '{{}}')::numeric)
                          <> (item.dependency_count #>> '{{}}')::numeric
                ) THEN
                    RAISE EXCEPTION
                        'calculation_manifest_dependency_counts_must_be_nonnegative_integers'
                        USING ERRCODE = '23514';
                END IF;

                SELECT status, captured_generation, input_schema_version,
                       calculation_kind, scope_kind, scope_id
                INTO v_run
                FROM {SCHEMA}.calculation_run
                WHERE run_id = NEW.run_id
                FOR UPDATE;
                IF NOT FOUND
                   OR v_run.status <> 'capturing'
                   OR v_run.captured_generation <> NEW.captured_generation
                   OR v_run.input_schema_version <> NEW.schema_version THEN
                    RAISE EXCEPTION 'calculation_manifest_seal_run_contract_mismatch'
                        USING ERRCODE = '23514';
                END IF;
                SELECT generation
                INTO v_generation
                FROM {SCHEMA}.calculation_scope_generation
                WHERE calculation_kind = v_run.calculation_kind
                  AND scope_kind = v_run.scope_kind
                  AND scope_id = v_run.scope_id
                FOR KEY SHARE;
                IF NOT FOUND OR v_generation <> NEW.captured_generation THEN
                    RAISE EXCEPTION
                        'calculation_manifest_generation_changed_during_capture: captured=%, current=%',
                        NEW.captured_generation, v_generation
                        USING ERRCODE = '40001';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.assert_manifest_building(p_manifest_id uuid)
            RETURNS void
            LANGUAGE plpgsql
            VOLATILE
            AS $$
            DECLARE
                v_status varchar(16);
            BEGIN
                SELECT status
                INTO v_status
                FROM {SCHEMA}.calculation_input_manifest
                WHERE manifest_id = p_manifest_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'calculation_manifest_not_found: %', p_manifest_id
                        USING ERRCODE = '23503';
                END IF;
                IF v_status <> 'building' THEN
                    RAISE EXCEPTION 'calculation_manifest_not_building: % status=%',
                        p_manifest_id, v_status
                        USING ERRCODE = '55000';
                END IF;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.guard_manifest_dependency_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_old_manifest_id uuid;
                v_new_manifest_id uuid;
            BEGIN
                IF TG_OP IN ('UPDATE', 'DELETE') THEN
                    v_old_manifest_id := NULLIF(to_jsonb(OLD) ->> 'manifest_id', '')::uuid;
                    IF v_old_manifest_id IS NULL THEN
                        RAISE EXCEPTION 'manifest_dependency_requires_manifest_id_column'
                            USING ERRCODE = '23502';
                    END IF;
                    PERFORM {SCHEMA}.assert_manifest_building(v_old_manifest_id);
                END IF;
                IF TG_OP IN ('INSERT', 'UPDATE') THEN
                    v_new_manifest_id := NULLIF(to_jsonb(NEW) ->> 'manifest_id', '')::uuid;
                    IF v_new_manifest_id IS NULL THEN
                        RAISE EXCEPTION 'manifest_dependency_requires_manifest_id_column'
                            USING ERRCODE = '23502';
                    END IF;
                    IF TG_OP = 'UPDATE' AND v_new_manifest_id <> v_old_manifest_id THEN
                        RAISE EXCEPTION 'manifest_dependency_manifest_id_immutable'
                            USING ERRCODE = '55000';
                    END IF;
                    IF TG_OP = 'INSERT' THEN
                        PERFORM {SCHEMA}.assert_manifest_building(v_new_manifest_id);
                    END IF;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.guard_calculation_job()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_run record;
                v_is_heartbeat boolean;
                v_is_takeover boolean;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'calculation_job_delete_forbidden'
                        USING ERRCODE = '55000';
                END IF;

                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'queued' THEN
                        RAISE EXCEPTION 'calculation_job_initial_status_must_be_queued'
                            USING ERRCODE = '23514';
                    END IF;
                    SELECT status, dedupe_key
                    INTO v_run
                    FROM {SCHEMA}.calculation_run
                    WHERE run_id = NEW.run_id
                    FOR UPDATE;
                    IF NOT FOUND
                       OR v_run.status <> 'queued'
                       OR v_run.dedupe_key <> NEW.dedupe_key THEN
                        RAISE EXCEPTION 'calculation_job_run_contract_mismatch'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END IF;

                IF OLD.status IN ('succeeded', 'failed', 'superseded') THEN
                    RAISE EXCEPTION 'calculation_job_terminal_immutable: %', OLD.status
                        USING ERRCODE = '55000';
                END IF;
                IF ROW(NEW.job_id, NEW.run_id, NEW.dedupe_key, NEW.max_attempts,
                       NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.job_id, OLD.run_id, OLD.dedupe_key, OLD.max_attempts,
                       OLD.created_at) THEN
                    RAISE EXCEPTION 'calculation_job_identity_immutable'
                        USING ERRCODE = '55000';
                END IF;

                IF NEW.status = OLD.status THEN
                    IF OLD.status <> 'leased' THEN
                        RAISE EXCEPTION 'calculation_job_same_state_update_forbidden: %',
                            OLD.status
                            USING ERRCODE = '55000';
                    END IF;
                    v_is_heartbeat :=
                        OLD.lease_expires_at > clock_timestamp()
                        AND NEW.attempt = OLD.attempt
                        AND NEW.fencing_token = OLD.fencing_token
                        AND NEW.lease_owner = OLD.lease_owner
                        AND NEW.heartbeat_at >= OLD.heartbeat_at
                        AND NEW.heartbeat_at <= clock_timestamp()
                        AND NEW.lease_expires_at >= OLD.lease_expires_at
                        AND NEW.lease_expires_at > clock_timestamp();
                    v_is_takeover :=
                        OLD.lease_expires_at <= clock_timestamp()
                        AND NEW.attempt = OLD.attempt + 1
                        AND NEW.fencing_token = OLD.fencing_token + 1
                        AND NEW.heartbeat_at >= OLD.lease_expires_at
                        AND NEW.heartbeat_at <= clock_timestamp()
                        AND NEW.lease_expires_at > clock_timestamp();
                    IF NOT (v_is_heartbeat OR v_is_takeover) THEN
                        RAISE EXCEPTION
                            'calculation_job_invalid_heartbeat_or_takeover: job=%', NEW.job_id
                            USING ERRCODE = '40001';
                    END IF;
                ELSE
                    IF NOT (
                        (OLD.status = 'queued' AND NEW.status IN ('leased', 'superseded'))
                        OR (OLD.status = 'leased' AND NEW.status IN
                            ('retry_wait', 'succeeded', 'failed', 'superseded'))
                        OR (OLD.status = 'retry_wait' AND NEW.status IN
                            ('leased', 'failed', 'superseded'))
                    ) THEN
                        RAISE EXCEPTION 'calculation_job_invalid_transition: % -> %',
                            OLD.status, NEW.status
                            USING ERRCODE = '23514';
                    END IF;

                    IF NEW.status = 'leased' THEN
                        IF OLD.status NOT IN ('queued', 'retry_wait')
                           OR OLD.available_at > clock_timestamp()
                           OR NEW.attempt <> OLD.attempt + 1
                           OR NEW.fencing_token <> OLD.fencing_token + 1
                           OR NEW.heartbeat_at > clock_timestamp()
                           OR NEW.lease_expires_at <= clock_timestamp() THEN
                            RAISE EXCEPTION 'calculation_job_invalid_lease_claim'
                                USING ERRCODE = '40001';
                        END IF;
                    ELSIF OLD.status = 'leased' THEN
                        IF NEW.attempt <> OLD.attempt
                           OR NEW.fencing_token <> OLD.fencing_token
                           OR (NEW.status IN ('retry_wait', 'succeeded')
                               AND OLD.lease_expires_at <= clock_timestamp())
                           OR NEW.lease_owner IS NOT NULL
                           OR NEW.lease_expires_at IS NOT NULL
                           OR NEW.heartbeat_at IS NOT NULL THEN
                            RAISE EXCEPTION 'calculation_job_terminal_fence_mismatch'
                                USING ERRCODE = '40001';
                        END IF;
                    ELSE
                        IF NEW.attempt <> OLD.attempt
                           OR NEW.fencing_token <> OLD.fencing_token THEN
                            RAISE EXCEPTION 'calculation_job_fence_changed_without_lease'
                                USING ERRCODE = '40001';
                        END IF;
                    END IF;
                END IF;
                IF NEW.status IN ('leased', 'retry_wait', 'succeeded', 'failed', 'superseded') THEN
                    SELECT status
                    INTO v_run
                    FROM {SCHEMA}.calculation_run
                    WHERE run_id = NEW.run_id
                    FOR KEY SHARE;
                    IF NOT FOUND
                       OR (NEW.status = 'leased' AND OLD.status = 'queued'
                           AND v_run.status <> 'queued')
                       OR (NEW.status = 'leased' AND OLD.status <> 'queued'
                           AND v_run.status <> 'running')
                       OR (NEW.status IN ('retry_wait', 'succeeded', 'failed')
                           AND v_run.status <> 'running')
                       OR (NEW.status = 'superseded' AND OLD.status = 'queued'
                           AND v_run.status <> 'queued')
                       OR (NEW.status = 'superseded' AND OLD.status <> 'queued'
                           AND v_run.status <> 'running') THEN
                        RAISE EXCEPTION
                            'calculation_job_run_state_mismatch: job % -> %, run=%',
                            OLD.status, NEW.status, v_run.status
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                NEW.updated_at := clock_timestamp();
                RETURN NEW;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.assert_run_output_writable(
                p_run_id uuid,
                p_fencing_token bigint,
                p_lease_owner varchar
            )
            RETURNS void
            LANGUAGE plpgsql
            VOLATILE
            AS $$
            DECLARE
                v_run_status varchar(24);
                v_job_status varchar(24);
                v_fencing_token bigint;
                v_lease_owner varchar(255);
                v_lease_expires_at timestamptz;
            BEGIN
                IF p_lease_owner IS NULL OR btrim(p_lease_owner) = '' THEN
                    RAISE EXCEPTION 'calculation_output_lease_owner_required'
                        USING ERRCODE = '23502';
                END IF;
                SELECT r.status, j.status, j.fencing_token, j.lease_owner,
                       j.lease_expires_at
                INTO v_run_status, v_job_status, v_fencing_token, v_lease_owner,
                     v_lease_expires_at
                FROM {SCHEMA}.calculation_run AS r
                JOIN {SCHEMA}.calculation_job AS j ON j.run_id = r.run_id
                WHERE r.run_id = p_run_id
                FOR UPDATE OF r, j;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'calculation_output_run_or_job_not_found: %', p_run_id
                        USING ERRCODE = '23503';
                END IF;
                IF v_run_status <> 'running'
                   OR v_job_status <> 'leased'
                   OR v_fencing_token <> p_fencing_token
                   OR v_lease_owner IS DISTINCT FROM p_lease_owner
                   OR v_lease_expires_at <= clock_timestamp() THEN
                    RAISE EXCEPTION
                        'calculation_output_stale_fence: run=%, owner=%, token=%',
                        p_run_id, p_lease_owner, p_fencing_token
                        USING ERRCODE = '40001';
                END IF;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.guard_calculation_publication()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_run record;
                v_manifest record;
                v_job record;
            BEGIN
                IF TG_OP IN ('UPDATE', 'DELETE') THEN
                    RAISE EXCEPTION 'calculation_publication_immutable'
                        USING ERRCODE = '55000';
                END IF;

                SELECT status, manifest_id, calculation_kind, scope_kind, scope_id,
                       output_schema_version
                INTO v_run
                FROM {SCHEMA}.calculation_run
                WHERE run_id = NEW.run_id
                FOR UPDATE;
                IF NOT FOUND
                   OR v_run.status <> 'succeeded'
                   OR v_run.manifest_id <> NEW.manifest_id
                   OR ROW(v_run.calculation_kind, v_run.scope_kind, v_run.scope_id,
                          v_run.output_schema_version)
                      IS DISTINCT FROM
                      ROW(NEW.calculation_kind, NEW.scope_kind, NEW.scope_id,
                          NEW.output_schema_version) THEN
                    RAISE EXCEPTION 'calculation_publication_run_contract_mismatch'
                        USING ERRCODE = '23514';
                END IF;

                SELECT status, fencing_token
                INTO v_job
                FROM {SCHEMA}.calculation_job
                WHERE run_id = NEW.run_id
                FOR UPDATE;
                IF NOT FOUND
                   OR v_job.status <> 'succeeded'
                   OR v_job.fencing_token <> NEW.published_fencing_token THEN
                    RAISE EXCEPTION 'calculation_publication_requires_succeeded_job'
                        USING ERRCODE = '23514';
                END IF;

                SELECT status, run_id
                INTO v_manifest
                FROM {SCHEMA}.calculation_input_manifest
                WHERE manifest_id = NEW.manifest_id
                FOR KEY SHARE;
                IF NOT FOUND
                   OR v_manifest.status <> 'sealed'
                   OR v_manifest.run_id <> NEW.run_id THEN
                    RAISE EXCEPTION 'calculation_publication_requires_matching_sealed_manifest'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.guard_current_publication()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_generation bigint;
                v_publication record;
                v_prior_effective_as_of date;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'calculation_current_publication_delete_forbidden'
                        USING ERRCODE = '55000';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    IF ROW(NEW.calculation_kind, NEW.scope_kind, NEW.scope_id)
                       IS DISTINCT FROM
                       ROW(OLD.calculation_kind, OLD.scope_kind, OLD.scope_id) THEN
                        RAISE EXCEPTION 'calculation_current_publication_identity_immutable'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NEW.publication_id = OLD.publication_id THEN
                        RAISE EXCEPTION 'calculation_current_publication_noop_forbidden'
                            USING ERRCODE = '55000';
                    END IF;
                    NEW.updated_at := clock_timestamp();
                END IF;

                SELECT generation
                INTO v_generation
                FROM {SCHEMA}.calculation_scope_generation
                WHERE calculation_kind = NEW.calculation_kind
                  AND scope_kind = NEW.scope_kind
                  AND scope_id = NEW.scope_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'calculation_current_publication_scope_not_found'
                        USING ERRCODE = '23503';
                END IF;

                SELECT r.status, r.captured_generation, r.effective_as_of
                INTO v_publication
                FROM {SCHEMA}.calculation_publication AS p
                JOIN {SCHEMA}.calculation_run AS r ON r.run_id = p.run_id
                WHERE p.publication_id = NEW.publication_id
                  AND p.calculation_kind = NEW.calculation_kind
                  AND p.scope_kind = NEW.scope_kind
                  AND p.scope_id = NEW.scope_id
                FOR KEY SHARE OF p, r;
                IF NOT FOUND
                   OR v_publication.status NOT IN ('succeeded', 'published')
                   OR v_publication.captured_generation <> v_generation THEN
                    RAISE EXCEPTION
                        'calculation_current_publication_generation_or_state_mismatch'
                        USING ERRCODE = '40001';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    SELECT r.effective_as_of
                    INTO v_prior_effective_as_of
                    FROM {SCHEMA}.calculation_publication AS p
                    JOIN {SCHEMA}.calculation_run AS r ON r.run_id = p.run_id
                    WHERE p.publication_id = OLD.publication_id
                    FOR KEY SHARE OF p, r;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION
                            'calculation_current_publication_prior_not_found'
                            USING ERRCODE = '23503';
                    END IF;
                    IF v_publication.effective_as_of < v_prior_effective_as_of THEN
                        RAISE EXCEPTION
                            'calculation_current_publication_effective_as_of_regression: old=%, new=%',
                            v_prior_effective_as_of, v_publication.effective_as_of
                            USING ERRCODE = '40001';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.validate_current_publication_commit()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_state record;
            BEGIN
                SELECT p.canonical_output_hash, p.published_fencing_token,
                       r.status AS run_status,
                       r.published_output_hash,
                       r.captured_generation,
                       j.status AS job_status,
                       j.fencing_token AS job_fencing_token,
                       sg.generation AS current_generation
                INTO v_state
                FROM {SCHEMA}.calculation_current_publication AS cp
                JOIN {SCHEMA}.calculation_publication AS p
                  ON p.publication_id = cp.publication_id
                JOIN {SCHEMA}.calculation_run AS r ON r.run_id = p.run_id
                JOIN {SCHEMA}.calculation_job AS j ON j.run_id = r.run_id
                JOIN {SCHEMA}.calculation_scope_generation AS sg
                  ON sg.calculation_kind = cp.calculation_kind
                 AND sg.scope_kind = cp.scope_kind
                 AND sg.scope_id = cp.scope_id
                WHERE cp.calculation_kind = NEW.calculation_kind
                  AND cp.scope_kind = NEW.scope_kind
                  AND cp.scope_id = NEW.scope_id;

                IF NOT FOUND
                   OR v_state.run_status <> 'published'
                   OR v_state.job_status <> 'succeeded'
                   OR v_state.published_output_hash IS DISTINCT FROM
                      v_state.canonical_output_hash
                   OR v_state.job_fencing_token IS DISTINCT FROM
                      v_state.published_fencing_token
                   OR v_state.captured_generation IS DISTINCT FROM
                      v_state.current_generation THEN
                    RAISE EXCEPTION
                        'calculation_current_publication_incomplete_atomic_publish'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.guard_recompute_intent()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_generation bigint;
                v_run record;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'calculation_recompute_intent_delete_forbidden'
                        USING ERRCODE = '55000';
                END IF;

                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'pending' THEN
                        RAISE EXCEPTION 'calculation_intent_initial_status_must_be_pending'
                            USING ERRCODE = '23514';
                    END IF;
                    SELECT generation
                    INTO v_generation
                    FROM {SCHEMA}.calculation_scope_generation
                    WHERE calculation_kind = NEW.calculation_kind
                      AND scope_kind = NEW.scope_kind
                      AND scope_id = NEW.scope_id
                    FOR KEY SHARE;
                    IF NOT FOUND OR v_generation <> NEW.requested_generation THEN
                        RAISE EXCEPTION
                            'calculation_intent_generation_mismatch: requested=%, current=%',
                            NEW.requested_generation, v_generation
                            USING ERRCODE = '40001';
                    END IF;
                    RETURN NEW;
                END IF;

                IF OLD.status IN ('materialized', 'superseded', 'failed') THEN
                    RAISE EXCEPTION 'calculation_recompute_intent_terminal_immutable: %',
                        OLD.status
                        USING ERRCODE = '55000';
                END IF;
                IF ROW(
                    NEW.intent_id, NEW.calculation_kind, NEW.scope_kind, NEW.scope_id,
                    NEW.requested_generation, NEW.dedupe_key, NEW.reason_code,
                    NEW.reason_context, NEW.created_at
                ) IS DISTINCT FROM ROW(
                    OLD.intent_id, OLD.calculation_kind, OLD.scope_kind, OLD.scope_id,
                    OLD.requested_generation, OLD.dedupe_key, OLD.reason_code,
                    OLD.reason_context, OLD.created_at
                ) THEN
                    RAISE EXCEPTION 'calculation_recompute_intent_identity_immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF OLD.status <> 'pending'
                   OR NEW.status NOT IN ('materialized', 'superseded', 'failed') THEN
                    RAISE EXCEPTION 'calculation_recompute_intent_invalid_transition: % -> %',
                        OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;

                IF NEW.status = 'materialized' THEN
                    SELECT calculation_kind, scope_kind, scope_id, captured_generation,
                           status
                    INTO v_run
                    FROM {SCHEMA}.calculation_run
                    WHERE run_id = NEW.run_id
                    FOR KEY SHARE;
                    IF NOT FOUND
                       OR ROW(v_run.calculation_kind, v_run.scope_kind, v_run.scope_id)
                          IS DISTINCT FROM
                          ROW(NEW.calculation_kind, NEW.scope_kind, NEW.scope_id)
                       OR v_run.captured_generation <> NEW.requested_generation
                       OR v_run.status NOT IN ('capturing', 'queued') THEN
                        RAISE EXCEPTION 'calculation_intent_materialized_run_mismatch'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                NEW.updated_at := clock_timestamp();
                RETURN NEW;
            END;
            $$
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SCHEMA}.guard_worker_heartbeat()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(NEW.supported_calculation_kinds) AS item(value)
                    WHERE jsonb_typeof(item.value) <> 'string'
                       OR btrim(item.value #>> '{{}}') = ''
                       OR (item.value #>> '{{}}') <> btrim(item.value #>> '{{}}')
                ) OR (
                    SELECT count(*)
                    FROM jsonb_array_elements_text(NEW.supported_calculation_kinds)
                ) <> (
                    SELECT count(DISTINCT value)
                    FROM jsonb_array_elements_text(NEW.supported_calculation_kinds) AS item(value)
                ) THEN
                    RAISE EXCEPTION
                        'calculation_worker_supported_kinds_must_be_unique_nonblank_strings'
                        USING ERRCODE = '23514';
                END IF;

                IF TG_OP = 'UPDATE' THEN
                    IF NEW.worker_id <> OLD.worker_id THEN
                        RAISE EXCEPTION 'calculation_worker_id_immutable'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NEW.instance_id = OLD.instance_id THEN
                        IF NEW.started_at <> OLD.started_at
                           OR NEW.heartbeat_at < OLD.heartbeat_at THEN
                            RAISE EXCEPTION
                                'calculation_worker_same_instance_time_regression'
                                USING ERRCODE = '23514';
                        END IF;
                    ELSIF NEW.started_at <= OLD.started_at
                       OR NEW.heartbeat_at <= OLD.heartbeat_at THEN
                        RAISE EXCEPTION
                            'calculation_worker_stale_instance_replacement'
                            USING ERRCODE = '40001';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )


def _create_triggers() -> None:
    row_triggers = {
        "calculation_scope_generation": (
            "trg_calculation_scope_generation_guard",
            "INSERT OR UPDATE OR DELETE",
            "guard_scope_generation",
        ),
        "calculation_run": (
            "trg_calculation_run_guard",
            "INSERT OR UPDATE OR DELETE",
            "guard_calculation_run",
        ),
        "calculation_input_manifest": (
            "trg_calculation_manifest_guard",
            "INSERT OR UPDATE OR DELETE",
            "guard_calculation_manifest",
        ),
        "calculation_job": (
            "trg_calculation_job_guard",
            "INSERT OR UPDATE OR DELETE",
            "guard_calculation_job",
        ),
        "calculation_publication": (
            "trg_calculation_publication_guard",
            "INSERT OR UPDATE OR DELETE",
            "guard_calculation_publication",
        ),
        "calculation_current_publication": (
            "trg_calculation_current_publication_guard",
            "INSERT OR UPDATE OR DELETE",
            "guard_current_publication",
        ),
        "calculation_recompute_intent": (
            "trg_calculation_recompute_intent_guard",
            "INSERT OR UPDATE OR DELETE",
            "guard_recompute_intent",
        ),
        "calculation_worker_heartbeat": (
            "trg_calculation_worker_heartbeat_guard",
            "INSERT OR UPDATE",
            "guard_worker_heartbeat",
        ),
    }
    for table_name, (trigger_name, operations, function_name) in row_triggers.items():
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE {operations} ON {SCHEMA}.{table_name}
                FOR EACH ROW
                EXECUTE FUNCTION {SCHEMA}.{function_name}()
                """
            )
        )

    for table_name in row_triggers:
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_truncate_guard
                BEFORE TRUNCATE ON {SCHEMA}.{table_name}
                FOR EACH STATEMENT
                EXECUTE FUNCTION {SCHEMA}.reject_truncate()
                """
            )
        )

    op.execute(
        sa.text(
            f"""
            CREATE CONSTRAINT TRIGGER trg_calculation_current_publication_commit
            AFTER INSERT OR UPDATE
            ON {SCHEMA}.calculation_current_publication
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.validate_current_publication_commit()
            """
        )
    )


def _validate_foundation() -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            DECLARE
                v_name text;
                v_trigger text;
                v_function text;
                v_index text;
            BEGIN
                FOREACH v_name IN ARRAY ARRAY[
                    'calculation_scope_generation',
                    'calculation_run',
                    'calculation_input_manifest',
                    'calculation_job',
                    'calculation_publication',
                    'calculation_current_publication',
                    'calculation_recompute_intent',
                    'calculation_worker_heartbeat'
                ] LOOP
                    IF to_regclass('{SCHEMA}.' || v_name) IS NULL THEN
                        RAISE EXCEPTION 'calculation_registry_validation_missing_table: %', v_name;
                    END IF;
                END LOOP;

                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint AS c
                    JOIN pg_namespace AS n ON n.oid = c.connamespace
                    WHERE n.nspname = '{SCHEMA}'
                      AND NOT c.convalidated
                ) THEN
                    RAISE EXCEPTION 'calculation_registry_validation_unvalidated_constraint';
                END IF;

                FOREACH v_function IN ARRAY ARRAY[
                    '{SCHEMA}.round_half_even(numeric,integer)',
                    '{SCHEMA}.round_significant_half_even(numeric,integer)',
                    '{SCHEMA}.divide_significant_half_even(numeric,numeric,integer)',
                    '{SCHEMA}.reject_truncate()',
                    '{SCHEMA}.guard_scope_generation()',
                    '{SCHEMA}.guard_calculation_run()',
                    '{SCHEMA}.guard_calculation_manifest()',
                    '{SCHEMA}.assert_manifest_building(uuid)',
                    '{SCHEMA}.guard_manifest_dependency_mutation()',
                    '{SCHEMA}.guard_calculation_job()',
                    '{SCHEMA}.assert_run_output_writable(uuid,bigint,character varying)',
                    '{SCHEMA}.guard_calculation_publication()',
                    '{SCHEMA}.guard_current_publication()',
                    '{SCHEMA}.validate_current_publication_commit()',
                    '{SCHEMA}.guard_recompute_intent()',
                    '{SCHEMA}.guard_worker_heartbeat()'
                ] LOOP
                    IF to_regprocedure(v_function) IS NULL THEN
                        RAISE EXCEPTION
                            'calculation_registry_validation_missing_function: %', v_function;
                    END IF;
                END LOOP;

                FOREACH v_trigger IN ARRAY ARRAY[
                    'trg_calculation_scope_generation_guard',
                    'trg_calculation_run_guard',
                    'trg_calculation_manifest_guard',
                    'trg_calculation_job_guard',
                    'trg_calculation_publication_guard',
                    'trg_calculation_current_publication_guard',
                    'trg_calculation_current_publication_commit',
                    'trg_calculation_recompute_intent_guard',
                    'trg_calculation_worker_heartbeat_guard',
                    'trg_calculation_scope_generation_truncate_guard',
                    'trg_calculation_run_truncate_guard',
                    'trg_calculation_input_manifest_truncate_guard',
                    'trg_calculation_job_truncate_guard',
                    'trg_calculation_publication_truncate_guard',
                    'trg_calculation_current_publication_truncate_guard',
                    'trg_calculation_recompute_intent_truncate_guard',
                    'trg_calculation_worker_heartbeat_truncate_guard'
                ] LOOP
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_trigger AS t
                        JOIN pg_class AS c ON c.oid = t.tgrelid
                        JOIN pg_namespace AS n ON n.oid = c.relnamespace
                        WHERE n.nspname = '{SCHEMA}'
                          AND t.tgname = v_trigger
                          AND NOT t.tgisinternal
                          AND t.tgenabled = 'O'
                    ) THEN
                        RAISE EXCEPTION
                            'calculation_registry_validation_missing_trigger: %', v_trigger;
                    END IF;
                END LOOP;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_trigger AS t
                    JOIN pg_class AS c ON c.oid = t.tgrelid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = '{SCHEMA}'
                      AND c.relname = 'calculation_current_publication'
                      AND t.tgname = 'trg_calculation_current_publication_commit'
                      AND t.tgdeferrable
                      AND t.tginitdeferred
                      AND NOT t.tgisinternal
                ) THEN
                    RAISE EXCEPTION
                        'calculation_registry_validation_atomic_publish_trigger_not_deferred';
                END IF;

                FOREACH v_index IN ARRAY ARRAY[
                    'ix_calc_scope_generation_kind_generation',
                    'uq_calculation_run_active_dedupe',
                    'ix_calculation_run_scope_as_of',
                    'ix_calculation_run_status_created',
                    'uq_calculation_job_active_dedupe',
                    'ix_calculation_job_queue_claim',
                    'ix_calculation_job_expired_lease',
                    'ix_calculation_publication_scope_published',
                    'ix_calculation_intent_pending',
                    'ix_calculation_worker_heartbeat_at'
                ] LOOP
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_class AS i
                        JOIN pg_namespace AS n ON n.oid = i.relnamespace
                        JOIN pg_index AS x ON x.indexrelid = i.oid
                        WHERE n.nspname = '{SCHEMA}'
                          AND i.relname = v_index
                          AND x.indisvalid
                          AND x.indisready
                    ) THEN
                        RAISE EXCEPTION
                            'calculation_registry_validation_missing_index: %', v_index;
                    END IF;
                END LOOP;
            END;
            $$
            """
        )
    )


def _drop_guard_functions() -> None:
    signatures = (
        "guard_worker_heartbeat()",
        "guard_recompute_intent()",
        "validate_current_publication_commit()",
        "guard_current_publication()",
        "guard_calculation_publication()",
        "assert_run_output_writable(uuid, bigint, character varying)",
        "guard_calculation_job()",
        "guard_manifest_dependency_mutation()",
        "assert_manifest_building(uuid)",
        "guard_calculation_manifest()",
        "guard_calculation_run()",
        "guard_scope_generation()",
        "reject_truncate()",
    )
    for signature in signatures:
        op.execute(sa.text(f"DROP FUNCTION {SCHEMA}.{signature}"))


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError(
            "calculation registry 0001 supports PostgreSQL only; "
            "SQLite compatibility is intentionally not provided"
        )
    op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    _create_numeric_functions()
    _create_tables()
    _create_guard_functions()
    _create_triggers()
    _validate_foundation()


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("calculation registry downgrade requires PostgreSQL")
    _drop_tables()
    _drop_guard_functions()
    op.execute(
        sa.text(
            f"DROP FUNCTION {SCHEMA}.divide_significant_half_even("
            "numeric, numeric, integer)"
        )
    )
    op.execute(
        sa.text(
            f"DROP FUNCTION {SCHEMA}.round_significant_half_even(numeric, integer)"
        )
    )
    op.execute(sa.text(f"DROP FUNCTION {SCHEMA}.round_half_even(numeric, integer)"))
