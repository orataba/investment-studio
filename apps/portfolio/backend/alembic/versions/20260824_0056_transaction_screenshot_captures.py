"""Add auditable screenshot evidence and agent analysis batches.

Revision ID: 20260824_0056
Revises: 20260822_0055
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_0056"
down_revision: str | None = "20260822_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transaction_capture_record",
        sa.Column("capture_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=50), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_transaction_capture_record_size",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("capture_id"),
        sa.UniqueConstraint(
            "portfolio_id",
            "content_sha256",
            name="uq_transaction_capture_portfolio_content",
        ),
    )
    op.create_index(
        "ix_transaction_capture_portfolio_created",
        "transaction_capture_record",
        ["portfolio_id", "created_at", "capture_id"],
        unique=False,
    )

    op.create_table(
        "transaction_capture_batch",
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="ready"),
        sa.Column("content_key", sa.String(length=64), nullable=False),
        sa.Column("capture_count", sa.Integer(), nullable=False),
        sa.Column(
            "latest_analysis_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "analysis_run_status",
            sa.String(),
            nullable=False,
            server_default="idle",
        ),
        sa.Column(
            "analysis_run_attempt",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("analysis_run_started_at", sa.String(), nullable=True),
        sa.Column("analysis_run_completed_at", sa.String(), nullable=True),
        sa.Column("analysis_run_error", sa.String(length=1_000), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('auto', 'transaction_import', 'portfolio_initialization', "
            "'position_reconciliation')",
            name="ck_transaction_capture_batch_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'review_required')",
            name="ck_transaction_capture_batch_status",
        ),
        sa.CheckConstraint(
            "analysis_run_status IN "
            "('idle', 'queued', 'running', 'succeeded', 'failed')",
            name="ck_transaction_capture_batch_analysis_run_status",
        ),
        sa.CheckConstraint(
            "capture_count > 0 AND latest_analysis_revision >= 0 "
            "AND analysis_run_attempt >= 0",
            name="ck_transaction_capture_batch_count_and_revision",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("batch_id"),
        sa.UniqueConstraint(
            "portfolio_id",
            "content_key",
            name="uq_transaction_capture_batch_portfolio_content",
        ),
    )
    op.create_index(
        "ix_transaction_capture_batch_portfolio_created",
        "transaction_capture_batch",
        ["portfolio_id", "created_at", "batch_id"],
        unique=False,
    )

    op.create_table(
        "transaction_capture_batch_item",
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("capture_id", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 1",
            name="ck_transaction_capture_batch_item_ordinal",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["transaction_capture_batch.batch_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"],
            ["transaction_capture_record.capture_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("batch_id", "capture_id"),
        sa.UniqueConstraint(
            "batch_id",
            "ordinal",
            name="uq_transaction_capture_batch_item_ordinal",
        ),
    )

    op.create_table(
        "transaction_capture_analysis_revision",
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("harness", sa.String(length=80), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("harness_session_id", sa.String(length=255), nullable=True),
        sa.Column("finish_reason", sa.String(length=80), nullable=True),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("analysis_json", sa.JSON(), nullable=False),
        sa.Column(
            "transaction_import_json",
            sa.JSON(none_as_null=True),
            nullable=True,
        ),
        sa.Column("preview_digest", sa.String(length=64), nullable=True),
        sa.Column("preview_error_count", sa.Integer(), nullable=True),
        sa.Column("preview_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_transaction_capture_analysis_revision_revision",
        ),
        sa.CheckConstraint(
            "source IN ('assistant', 'human')",
            name="ck_transaction_capture_analysis_revision_source",
        ),
        sa.CheckConstraint(
            "(preview_digest IS NULL AND preview_error_count IS NULL "
            "AND preview_json IS NULL) OR "
            "(preview_digest IS NOT NULL AND preview_error_count IS NOT NULL "
            "AND preview_json IS NOT NULL)",
            name="ck_transaction_capture_analysis_revision_preview_bundle",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["transaction_capture_batch.batch_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("batch_id", "revision"),
    )


def downgrade() -> None:
    op.drop_table("transaction_capture_analysis_revision")
    op.drop_table("transaction_capture_batch_item")
    op.drop_index(
        "ix_transaction_capture_batch_portfolio_created",
        table_name="transaction_capture_batch",
    )
    op.drop_table("transaction_capture_batch")
    op.drop_index(
        "ix_transaction_capture_portfolio_created",
        table_name="transaction_capture_record",
    )
    op.drop_table("transaction_capture_record")
