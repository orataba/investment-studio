"""Create durable Platform-private email ingestion state.

Revision ID: 20260715_0001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FUND_NAV_ACTION_CANDIDATE_TERMINAL_TRIGGER = (
    "trg_fund_nav_action_candidate_terminal_status"
)
POSTGRES_FUND_NAV_ACTION_CANDIDATE_TERMINAL_FUNCTION = (
    "enforce_fund_nav_action_candidate_terminal_status"
)


def _fund_nav_decimal_type() -> sa.types.TypeEngine:
    return sa.Numeric(precision=38, scale=18).with_variant(
        sa.String(length=64),
        "sqlite",
    )


def _drop_fund_nav_action_candidate_terminal_guard(
    connection: sa.Connection,
) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS "
                f"{FUND_NAV_ACTION_CANDIDATE_TERMINAL_TRIGGER} "
                "ON fund_nav_action_candidate"
            )
        )
        connection.execute(
            sa.text(
                f"DROP FUNCTION IF EXISTS "
                f"{POSTGRES_FUND_NAV_ACTION_CANDIDATE_TERMINAL_FUNCTION}()"
            )
        )
    elif connection.dialect.name == "sqlite":
        connection.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS "
                f"{FUND_NAV_ACTION_CANDIDATE_TERMINAL_TRIGGER}"
            )
        )


def _create_fund_nav_action_candidate_terminal_guard(
    connection: sa.Connection,
) -> None:
    _drop_fund_nav_action_candidate_terminal_guard(connection)
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                f"""
                CREATE FUNCTION
                    {POSTGRES_FUND_NAV_ACTION_CANDIDATE_TERMINAL_FUNCTION}()
                RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
                BEGIN
                    IF OLD.status IN ('resolved', 'rejected') THEN
                        RAISE EXCEPTION
                            'resolved or rejected fund NAV action candidate is immutable'
                            USING ERRCODE = '23514';
                    END IF;
                    IF OLD.status = 'confirming' AND (
                        NEW.status <> 'resolved'
                        OR NEW.resolved_fund_nav_event_id IS DISTINCT FROM
                            OLD.resolved_fund_nav_event_id
                        OR NEW.decision_by IS DISTINCT FROM OLD.decision_by
                        OR NEW.confirmation_client_mutation_id IS DISTINCT FROM
                            OLD.confirmation_client_mutation_id
                        OR NEW.confirmation_request_fingerprint IS DISTINCT FROM
                            OLD.confirmation_request_fingerprint
                        OR CAST(NEW.confirmation_request_json AS TEXT)
                            IS DISTINCT FROM
                            CAST(OLD.confirmation_request_json AS TEXT)
                    ) THEN
                        RAISE EXCEPTION
                            'confirming fund NAV action candidate may only complete its reserved decision'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END; $$
                """
            )
        )
        connection.execute(
            sa.text(
                f"""
                CREATE TRIGGER {FUND_NAV_ACTION_CANDIDATE_TERMINAL_TRIGGER}
                BEFORE UPDATE ON fund_nav_action_candidate
                FOR EACH ROW EXECUTE FUNCTION
                    {POSTGRES_FUND_NAV_ACTION_CANDIDATE_TERMINAL_FUNCTION}()
                """
            )
        )
    elif connection.dialect.name == "sqlite":
        connection.execute(
            sa.text(
                f"""
                CREATE TRIGGER {FUND_NAV_ACTION_CANDIDATE_TERMINAL_TRIGGER}
                BEFORE UPDATE ON fund_nav_action_candidate
                FOR EACH ROW
                WHEN OLD.status IN ('resolved', 'rejected') OR (
                    OLD.status = 'confirming' AND (
                        NEW.status <> 'resolved'
                        OR NEW.resolved_fund_nav_event_id IS NOT
                            OLD.resolved_fund_nav_event_id
                        OR NEW.decision_by IS NOT OLD.decision_by
                        OR NEW.confirmation_client_mutation_id IS NOT
                            OLD.confirmation_client_mutation_id
                        OR NEW.confirmation_request_fingerprint IS NOT
                            OLD.confirmation_request_fingerprint
                        OR CAST(NEW.confirmation_request_json AS TEXT) IS NOT
                            CAST(OLD.confirmation_request_json AS TEXT)
                    )
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'fund NAV action candidate decision is immutable'
                    );
                END
                """
            )
        )


def upgrade() -> None:
    op.create_table(
        "platform_metadata",
        sa.Column("metadata_key", sa.String(length=128), nullable=False),
        sa.Column(
            "value_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("metadata_key", name="pk_platform_metadata"),
    )
    op.create_table(
        "fund_nav_raw_observation",
        sa.Column(
            "fund_nav_raw_observation_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("unit_nav_value", sa.Text(), nullable=True),
        sa.Column("cash_cumulative_nav_value", sa.Text(), nullable=True),
        sa.Column("observed_total_return_nav_value", sa.Text(), nullable=True),
        sa.Column("unit_nav_status", sa.String(length=32), nullable=True),
        sa.Column("cash_cumulative_nav_status", sa.String(length=32), nullable=True),
        sa.Column("observed_total_return_nav_status", sa.String(length=32), nullable=True),
        sa.Column(
            "total_return_semantics",
            sa.String(length=64),
            server_default=sa.text("'absent'"),
            nullable=False,
        ),
        sa.Column("unit_nav_source_provider", sa.String(), nullable=True),
        sa.Column("cash_cumulative_source_provider", sa.String(), nullable=True),
        sa.Column("total_return_source_provider", sa.String(), nullable=True),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=512), nullable=False),
        sa.Column(
            "evidence_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "total_return_semantics IN "
            "('absent', 'provider_explicit', 'legacy_unverified')",
            name="ck_fund_nav_raw_observation_total_return_semantics",
        ),
        sa.CheckConstraint(
            "unit_nav_value IS NOT NULL OR cash_cumulative_nav_value IS NOT NULL "
            "OR observed_total_return_nav_value IS NOT NULL",
            name="ck_fund_nav_raw_observation_has_value",
        ),
        sa.CheckConstraint(
            "(unit_nav_value IS NULL AND unit_nav_status IS NULL) OR "
            "(unit_nav_value IS NOT NULL AND unit_nav_status IN "
            "('complete', 'partial', 'unavailable'))",
            name="ck_fund_nav_raw_observation_unit_nav_status",
        ),
        sa.CheckConstraint(
            "(cash_cumulative_nav_value IS NULL AND cash_cumulative_nav_status IS NULL) OR "
            "(cash_cumulative_nav_value IS NOT NULL AND cash_cumulative_nav_status IN "
            "('complete', 'partial', 'unavailable'))",
            name="ck_fund_nav_raw_observation_cash_cumulative_nav_status",
        ),
        sa.CheckConstraint(
            "(observed_total_return_nav_value IS NULL "
            "AND observed_total_return_nav_status IS NULL) OR "
            "(observed_total_return_nav_value IS NOT NULL "
            "AND observed_total_return_nav_status IN "
            "('complete', 'partial', 'unavailable'))",
            name="ck_fund_nav_raw_observation_total_return_nav_status",
        ),
        sa.PrimaryKeyConstraint(
            "fund_nav_raw_observation_id",
            name="pk_fund_nav_raw_observation",
        ),
        sa.UniqueConstraint(
            "instrument_id",
            "as_of_date",
            "currency",
            "source_kind",
            "source_ref",
            name="uq_fund_nav_raw_observation_identity",
        ),
    )
    op.create_index(
        "ix_fund_nav_raw_observation_instrument_date",
        "fund_nav_raw_observation",
        ["instrument_id", "as_of_date"],
        unique=False,
    )
    op.create_table(
        "fund_nav_action_candidate",
        sa.Column(
            "fund_nav_action_candidate_id",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("candidate_type", sa.String(length=64), nullable=False),
        sa.Column("interval_start_date", sa.Date(), nullable=False),
        sa.Column("interval_end_date", sa.Date(), nullable=False),
        sa.Column(
            "observed_cash_balance_before",
            _fund_nav_decimal_type(),
            nullable=False,
        ),
        sa.Column(
            "observed_cash_balance_after",
            _fund_nav_decimal_type(),
            nullable=False,
        ),
        sa.Column(
            "observed_cash_delta",
            _fund_nav_decimal_type(),
            nullable=False,
        ),
        sa.Column(
            "expected_cash_balance",
            _fund_nav_decimal_type(),
            nullable=True,
        ),
        sa.Column(
            "measurement_uncertainty",
            _fund_nav_decimal_type(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("source_provider", sa.String(length=1024), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=False),
        sa.Column("source_evidence_json", sa.JSON(), nullable=False),
        sa.Column("resolved_fund_nav_event_id", sa.String(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("decision_by", sa.String(length=512), nullable=True),
        sa.Column(
            "confirmation_client_mutation_id",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "confirmation_request_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "confirmation_request_json",
            sa.JSON(none_as_null=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "candidate_type IN "
            "('cash_distribution_signal', 'cash_balance_discontinuity')",
            name="ck_fund_nav_action_candidate_candidate_type_contract",
        ),
        sa.CheckConstraint(
            "interval_start_date < interval_end_date",
            name="ck_fund_nav_action_candidate_ordered_interval",
        ),
        sa.CheckConstraint(
            "(candidate_type = 'cash_distribution_signal' "
            "AND CAST(observed_cash_delta AS NUMERIC) > 0) OR "
            "(candidate_type = 'cash_balance_discontinuity' "
            "AND CAST(observed_cash_delta AS NUMERIC) <= 0)",
            name="ck_fund_nav_action_candidate_candidate_delta_contract",
        ),
        sa.CheckConstraint(
            "CAST(measurement_uncertainty AS NUMERIC) > 0",
            name=(
                "ck_fund_nav_action_candidate_positive_measurement_uncertainty"
            ),
        ),
        sa.CheckConstraint(
            "status IN "
            "('open', 'confirming', 'superseded', 'resolved', 'rejected')",
            name="ck_fund_nav_action_candidate_status_contract",
        ),
        sa.CheckConstraint(
            "(status IN ('confirming', 'resolved') "
            "AND resolved_fund_nav_event_id IS NOT NULL "
            "AND length(trim(resolved_fund_nav_event_id)) > 0) OR "
            "(status NOT IN ('confirming', 'resolved') "
            "AND resolved_fund_nav_event_id IS NULL)",
            name="ck_fund_nav_action_candidate_resolution_contract",
        ),
        sa.CheckConstraint(
            "(status = 'rejected' AND rejection_reason IS NOT NULL "
            "AND length(trim(rejection_reason)) > 0) OR "
            "(status <> 'rejected' AND rejection_reason IS NULL)",
            name="ck_fund_nav_action_candidate_rejection_contract",
        ),
        sa.CheckConstraint(
            "(status IN ('confirming', 'resolved', 'rejected') "
            "AND decision_by IS NOT NULL "
            "AND length(trim(decision_by)) > 0) OR "
            "(status NOT IN ('confirming', 'resolved', 'rejected') "
            "AND decision_by IS NULL)",
            name="ck_fund_nav_action_candidate_decision_audit_contract",
        ),
        sa.CheckConstraint(
            "(status IN ('confirming', 'resolved') "
            "AND confirmation_client_mutation_id IS NOT NULL "
            "AND length(trim(confirmation_client_mutation_id)) > 0 "
            "AND confirmation_request_fingerprint IS NOT NULL "
            "AND length(confirmation_request_fingerprint) = 64 "
            "AND confirmation_request_json IS NOT NULL "
            "AND length(trim(CAST(confirmation_request_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(confirmation_request_json AS TEXT))) "
            "NOT IN ('{}', 'null')) OR "
            "(status NOT IN ('confirming', 'resolved') "
            "AND confirmation_client_mutation_id IS NULL "
            "AND confirmation_request_fingerprint IS NULL "
            "AND confirmation_request_json IS NULL)",
            name=(
                "ck_fund_nav_action_candidate_confirmation_intent_contract"
            ),
        ),
        sa.CheckConstraint(
            "length(trim(fund_nav_action_candidate_id)) > 0 "
            "AND length(trim(instrument_id)) > 0 "
            "AND length(trim(source_provider)) > 0 "
            "AND length(source_revision) = 64",
            name="ck_fund_nav_action_candidate_identity_contract",
        ),
        sa.CheckConstraint(
            "length(trim(CAST(source_evidence_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(source_evidence_json AS TEXT))) "
            "NOT IN ('{}', 'null')",
            name="ck_fund_nav_action_candidate_evidence_contract",
        ),
        sa.PrimaryKeyConstraint(
            "fund_nav_action_candidate_id",
            name="pk_fund_nav_action_candidate",
        ),
        sa.UniqueConstraint(
            "instrument_id",
            "candidate_type",
            "interval_start_date",
            "interval_end_date",
            "source_revision",
            name="uq_fund_nav_action_candidate_source_revision",
        ),
    )
    op.create_index(
        "ix_fund_nav_action_candidate_instrument_status_end",
        "fund_nav_action_candidate",
        ["instrument_id", "status", "interval_end_date"],
        unique=False,
    )
    op.create_index(
        "uq_fund_nav_action_candidate_open_projection",
        "fund_nav_action_candidate",
        [
            "instrument_id",
            "candidate_type",
            "interval_start_date",
            "interval_end_date",
        ],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
        sqlite_where=sa.text("status = 'open'"),
    )
    _create_fund_nav_action_candidate_terminal_guard(op.get_bind())
    op.create_table(
        "email_folder_cursor",
        sa.Column("email_folder_cursor_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mailbox_key", sa.String(length=64), nullable=False),
        sa.Column("folder_name", sa.String(length=512), nullable=False),
        sa.Column("uid_validity", sa.BigInteger(), nullable=True),
        sa.Column(
            "last_committed_uid",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_observed_uid_next", sa.BigInteger(), nullable=True),
        sa.Column("last_scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "uid_validity IS NULL OR uid_validity > 0",
            name="uid_validity_positive",
        ),
        sa.CheckConstraint(
            "last_committed_uid >= 0",
            name="last_uid_nonnegative",
        ),
        sa.CheckConstraint(
            "last_observed_uid_next IS NULL OR last_observed_uid_next > 0",
            name="uid_next_positive",
        ),
        sa.PrimaryKeyConstraint(
            "email_folder_cursor_id",
            name="pk_email_folder_cursor",
        ),
        sa.UniqueConstraint(
            "mailbox_key",
            "folder_name",
            name="uq_email_folder_cursor_mailbox_folder",
        ),
    )

    op.create_table(
        "email_message_occurrence",
        sa.Column(
            "email_message_occurrence_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("email_folder_cursor_id", sa.Integer(), nullable=False),
        sa.Column("uid_validity", sa.BigInteger(), nullable=False),
        sa.Column("message_uid", sa.BigInteger(), nullable=False),
        sa.Column("rfc_message_id", sa.String(length=998), nullable=True),
        sa.Column("message_sha256", sa.String(length=64), nullable=True),
        sa.Column("sender_address", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("internal_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rfc822_size", sa.BigInteger(), nullable=True),
        sa.Column(
            "acquisition_status",
            sa.String(length=32),
            server_default=sa.text("'discovered'"),
            nullable=False,
        ),
        sa.Column(
            "acquisition_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "uid_validity > 0 AND message_uid > 0",
            name="uid_positive",
        ),
        sa.CheckConstraint(
            "rfc822_size IS NULL OR rfc822_size >= 0",
            name="size_nonnegative",
        ),
        sa.CheckConstraint(
            "acquisition_attempt_count >= 0",
            name="attempt_nonnegative",
        ),
        sa.CheckConstraint(
            "acquisition_status IN "
            "('discovered', 'materialized', 'ignored', 'retryable', 'dead_letter')",
            name="status_contract",
        ),
        sa.ForeignKeyConstraint(
            ["email_folder_cursor_id"],
            ["email_folder_cursor.email_folder_cursor_id"],
            name="fk_email_message_occurrence_folder",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "email_message_occurrence_id",
            name="pk_email_message_occurrence",
        ),
        sa.UniqueConstraint(
            "email_folder_cursor_id",
            "uid_validity",
            "message_uid",
            name="uq_email_message_occurrence_folder_generation_uid",
        ),
    )
    op.create_index(
        "ix_email_message_occurrence_rfc_message_id",
        "email_message_occurrence",
        ["rfc_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_message_occurrence_message_sha256",
        "email_message_occurrence",
        ["message_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_email_message_occurrence_retry",
        "email_message_occurrence",
        ["acquisition_status", "next_retry_at"],
        unique=False,
    )

    op.create_table(
        "email_attachment_artifact",
        sa.Column(
            "email_attachment_artifact_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="sha256_length",
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name="size_positive",
        ),
        sa.PrimaryKeyConstraint(
            "email_attachment_artifact_id",
            name="pk_email_attachment_artifact",
        ),
        sa.UniqueConstraint(
            "content_sha256",
            name="uq_email_attachment_artifact_sha256",
        ),
    )

    op.create_table(
        "email_message_attachment",
        sa.Column(
            "email_message_attachment_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("email_message_occurrence_id", sa.Integer(), nullable=False),
        sa.Column("email_attachment_artifact_id", sa.Integer(), nullable=False),
        sa.Column("part_index", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("content_id", sa.String(length=998), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["email_attachment_artifact_id"],
            ["email_attachment_artifact.email_attachment_artifact_id"],
            name="fk_email_message_attachment_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["email_message_occurrence_id"],
            ["email_message_occurrence.email_message_occurrence_id"],
            name="fk_email_message_attachment_occurrence",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "email_message_attachment_id",
            name="pk_email_message_attachment",
        ),
        sa.UniqueConstraint(
            "email_message_occurrence_id",
            "part_index",
            name="uq_email_message_attachment_occurrence_part",
        ),
    )
    op.create_index(
        "ix_email_message_attachment_artifact",
        "email_message_attachment",
        ["email_attachment_artifact_id"],
        unique=False,
    )

    op.create_table(
        "email_attachment_parse",
        sa.Column(
            "email_attachment_parse_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("email_attachment_artifact_id", sa.Integer(), nullable=False),
        sa.Column("parser_profile", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("source_format", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "parser_metadata_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="attempt_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN "
            "('pending', 'processing', 'succeeded', 'retryable', "
            "'dead_letter', 'unsupported')",
            name="status_contract",
        ),
        sa.ForeignKeyConstraint(
            ["email_attachment_artifact_id"],
            ["email_attachment_artifact.email_attachment_artifact_id"],
            name="fk_email_attachment_parse_artifact",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "email_attachment_parse_id",
            name="pk_email_attachment_parse",
        ),
        sa.UniqueConstraint(
            "email_attachment_artifact_id",
            "parser_profile",
            "parser_version",
            "source_format",
            name="uq_email_attachment_parse_artifact_profile_version_format",
        ),
    )
    op.create_index(
        "ix_email_attachment_parse_retry",
        "email_attachment_parse",
        ["status", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_attachment_parse_lease",
        "email_attachment_parse",
        ["lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "email_attachment_route_context",
        sa.Column(
            "email_attachment_route_context_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("email_message_attachment_id", sa.Integer(), nullable=False),
        sa.Column("email_attachment_parse_id", sa.Integer(), nullable=False),
        sa.Column(
            "routing_context_json",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["email_message_attachment_id"],
            ["email_message_attachment.email_message_attachment_id"],
            name="fk_email_attachment_route_context_message_attachment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["email_attachment_parse_id"],
            ["email_attachment_parse.email_attachment_parse_id"],
            name="fk_email_attachment_route_context_parse",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "email_attachment_route_context_id",
            name="pk_email_attachment_route_context",
        ),
        sa.UniqueConstraint(
            "email_message_attachment_id",
            "email_attachment_parse_id",
            name="uq_email_attachment_route_context_attachment_parse",
        ),
    )
    op.create_index(
        "ix_email_attachment_route_context_parse",
        "email_attachment_route_context",
        ["email_attachment_parse_id"],
        unique=False,
    )

    op.create_table(
        "email_nav_candidate",
        sa.Column(
            "email_nav_candidate_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("email_attachment_parse_id", sa.Integer(), nullable=False),
        sa.Column("row_ordinal", sa.Integer(), nullable=False),
        sa.Column("instrument_code", sa.String(length=256), nullable=True),
        sa.Column("instrument_name", sa.Text(), nullable=True),
        sa.Column("normalized_instrument_code", sa.String(length=256), nullable=True),
        sa.Column("normalized_instrument_name", sa.Text(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("unit_nav_value", sa.Text(), nullable=True),
        sa.Column("reported_cash_cumulative_nav_value", sa.Text(), nullable=True),
        sa.Column("reported_reinvested_nav_value", sa.Text(), nullable=True),
        sa.Column("reported_frequency", sa.String(length=32), nullable=True),
        sa.Column(
            "raw_row_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "row_ordinal >= 0",
            name="row_ordinal_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["email_attachment_parse_id"],
            ["email_attachment_parse.email_attachment_parse_id"],
            name="fk_email_nav_candidate_parse",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "email_nav_candidate_id",
            name="pk_email_nav_candidate",
        ),
        sa.UniqueConstraint(
            "email_attachment_parse_id",
            "row_ordinal",
            name="uq_email_nav_candidate_parse_row",
        ),
    )
    op.create_index(
        "ix_email_nav_candidate_normalized_code",
        "email_nav_candidate",
        ["normalized_instrument_code"],
        unique=False,
    )

    op.create_table(
        "email_nav_candidate_route",
        sa.Column(
            "email_nav_candidate_route_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("email_nav_candidate_id", sa.Integer(), nullable=False),
        sa.Column("email_attachment_route_context_id", sa.Integer(), nullable=False),
        sa.Column(
            "routing_status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "validation_status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("instrument_id", sa.String(), nullable=True),
        sa.Column("routing_rule_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "routing_status IN ('pending', 'matched', 'unmatched', 'ambiguous')",
            name="routing_status_contract",
        ),
        sa.CheckConstraint(
            "validation_status IN ('pending', 'valid', 'rejected', 'conflict')",
            name="validation_status_contract",
        ),
        sa.ForeignKeyConstraint(
            ["email_nav_candidate_id"],
            ["email_nav_candidate.email_nav_candidate_id"],
            name="fk_email_nav_candidate_route_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["email_attachment_route_context_id"],
            ["email_attachment_route_context.email_attachment_route_context_id"],
            name="fk_email_nav_candidate_route_context",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "email_nav_candidate_route_id",
            name="pk_email_nav_candidate_route",
        ),
        sa.UniqueConstraint(
            "email_nav_candidate_id",
            "email_attachment_route_context_id",
            name="uq_email_nav_candidate_route_candidate_context",
        ),
    )
    op.create_index(
        "ix_email_nav_candidate_route_discovery",
        "email_nav_candidate_route",
        ["routing_status", "validation_status"],
        unique=False,
    )
    op.create_index(
        "ix_email_nav_candidate_route_instrument",
        "email_nav_candidate_route",
        ["instrument_id"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_email_nav_candidate_route_instrument_id_instrument",
            "email_nav_candidate_route",
            "instrument",
            ["instrument_id"],
            ["instrument_id"],
            source_schema="platform",
            referent_schema="instrument_registry",
            ondelete="SET NULL",
        )

    _snapshot_legacy_nav_observations()


def _snapshot_legacy_nav_observations() -> None:
    """Preserve every pre-contract NAV value before Registry migration 0012."""
    connection = op.get_bind()
    source_schema = "instrument_registry" if connection.dialect.name == "postgresql" else None
    target_schema = "platform" if connection.dialect.name == "postgresql" else None
    inspector = sa.inspect(connection)
    if not inspector.has_table("instrument_market_data", schema=source_schema):
        source_row_count = 0
        observation_count = 0
    else:
        metadata = sa.MetaData()
        source = sa.Table(
            "instrument_market_data",
            metadata,
            schema=source_schema,
            autoload_with=connection,
        )
        target = sa.Table(
            "fund_nav_raw_observation",
            metadata,
            schema=target_schema,
            autoload_with=connection,
        )
        nav_filter = source.c.metric_family == "nav"
        source_row_count = int(
            connection.scalar(
                sa.select(sa.func.count()).select_from(source).where(nav_filter)
            )
            or 0
        )
        unit_condition = source.c.quote_basis == "official_nav"
        cash_condition = source.c.quote_basis.in_(
            ("cumulative_nav", "accumulated_nav", "cum_nav")
        )
        total_condition = source.c.quote_basis.in_(
            ("total_return_nav", "dividend_adjusted_nav", "reinvested_nav")
        )
        snapshot = (
            sa.select(
                source.c.instrument_id,
                source.c.as_of_date,
                source.c.currency,
                sa.func.max(sa.case((unit_condition, source.c.value))).label(
                    "unit_nav_value"
                ),
                sa.func.max(sa.case((cash_condition, source.c.value))).label(
                    "cash_cumulative_nav_value"
                ),
                sa.func.max(sa.case((total_condition, source.c.value))).label(
                    "observed_total_return_nav_value"
                ),
                sa.func.max(sa.case((unit_condition, source.c.status))).label(
                    "unit_nav_status"
                ),
                sa.func.max(sa.case((cash_condition, source.c.status))).label(
                    "cash_cumulative_nav_status"
                ),
                sa.func.max(sa.case((total_condition, source.c.status))).label(
                    "observed_total_return_nav_status"
                ),
                sa.case(
                    (sa.func.max(sa.case((total_condition, 1), else_=0)) > 0, "legacy_unverified"),
                    else_="absent",
                ).label("total_return_semantics"),
                sa.func.max(sa.case((unit_condition, source.c.provider))).label(
                    "unit_nav_source_provider"
                ),
                sa.func.max(sa.case((cash_condition, source.c.provider))).label(
                    "cash_cumulative_source_provider"
                ),
                sa.func.max(sa.case((total_condition, source.c.provider))).label(
                    "total_return_source_provider"
                ),
                sa.literal("legacy_registry_snapshot").label("source_kind"),
                sa.literal("instrument_registry@20260715_0011").label("source_ref"),
            )
            .where(nav_filter)
            .group_by(source.c.instrument_id, source.c.as_of_date, source.c.currency)
        )
        connection.execute(
            sa.insert(target).from_select(
                [
                    "instrument_id",
                    "as_of_date",
                    "currency",
                    "unit_nav_value",
                    "cash_cumulative_nav_value",
                    "observed_total_return_nav_value",
                    "unit_nav_status",
                    "cash_cumulative_nav_status",
                    "observed_total_return_nav_status",
                    "total_return_semantics",
                    "unit_nav_source_provider",
                    "cash_cumulative_source_provider",
                    "total_return_source_provider",
                    "source_kind",
                    "source_ref",
                ],
                snapshot,
            )
        )
        observation_count = int(
            connection.scalar(sa.select(sa.func.count()).select_from(target)) or 0
        )

    platform_metadata = sa.table(
        "platform_metadata",
        sa.column("metadata_key", sa.String()),
        sa.column("value_json", sa.JSON()),
    )
    connection.execute(
        sa.insert(platform_metadata).values(
            metadata_key="legacy_nav_snapshot_v1",
            value_json={
                "status": "complete",
                "source_registry_revision": "20260715_0011",
                "source_row_count": source_row_count,
                "observation_count": observation_count,
                "captured_at": datetime.now(UTC).isoformat(),
            },
        )
    )


def downgrade() -> None:
    op.drop_table("email_nav_candidate_route")
    op.drop_table("email_nav_candidate")
    op.drop_index(
        "ix_email_attachment_route_context_parse",
        table_name="email_attachment_route_context",
    )
    op.drop_table("email_attachment_route_context")
    op.drop_table("email_attachment_parse")
    op.drop_table("email_message_attachment")
    op.drop_table("email_attachment_artifact")
    op.drop_table("email_message_occurrence")
    op.drop_table("email_folder_cursor")
    _drop_fund_nav_action_candidate_terminal_guard(op.get_bind())
    op.drop_index(
        "uq_fund_nav_action_candidate_open_projection",
        table_name="fund_nav_action_candidate",
    )
    op.drop_index(
        "ix_fund_nav_action_candidate_instrument_status_end",
        table_name="fund_nav_action_candidate",
    )
    op.drop_table("fund_nav_action_candidate")
    op.drop_index(
        "ix_fund_nav_raw_observation_instrument_date",
        table_name="fund_nav_raw_observation",
    )
    op.drop_table("fund_nav_raw_observation")
    op.drop_table("platform_metadata")
