"""Version investment views and preserve research-note audit history.

Revision ID: 20260823_0046
Revises: 20260823_0045
"""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260823_0046"
down_revision = "20260823_0045"
branch_labels = None
depends_on = None


PROFILE_TEXT_FIELDS = (
    "thesis",
    "current_view",
    "why_now",
    "edge_assessment",
    "valuation_framework",
    "catalysts",
    "key_risks",
    "disconfirming_evidence",
    "open_questions",
    "monitoring_plan",
    "people_assessment",
    "portfolio_role",
    "time_horizon",
    "decision_rationale",
    "primary_analyst",
    "dd_status",
    "odd_status",
    "ic_status",
)


def upgrade() -> None:
    op.add_column(
        "instrument_research_profile",
        sa.Column(
            "revision_number",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "instrument_research_note",
        sa.Column(
            "revision_number",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "instrument_research_note",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "instrument_research_note",
        sa.Column("deleted_by", sa.String(), nullable=True),
    )

    op.create_table(
        "instrument_research_profile_revision",
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        *(sa.Column(field, sa.Text(), nullable=False) for field in PROFILE_TEXT_FIELDS),
        sa.Column("next_review_date", sa.Date(), nullable=True),
        sa.Column("manual_rating", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by", sa.String(), nullable=True),
        sa.CheckConstraint(
            "manual_rating IS NULL OR manual_rating BETWEEN 1 AND 5",
            name=op.f("ck_instrument_research_profile_revision_manual_rating_range"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument_detail.instrument_id"],
            name=op.f(
                "fk_instrument_research_profile_revision_instrument_id_instrument_detail"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instrument_id",
            "revision_number",
            name=op.f("pk_instrument_research_profile_revision"),
        ),
    )
    op.create_index(
        "idx_instrument_research_profile_revision_instrument_time",
        "instrument_research_profile_revision",
        ["instrument_id", "recorded_at"],
        unique=False,
    )

    op.create_table(
        "instrument_research_note_revision",
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("note_id", sa.String(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(), nullable=False),
        sa.Column("note_date", sa.Date(), nullable=False),
        sa.Column("note_type", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("importance", sa.String(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.Text(), nullable=False),
        sa.Column("people", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("follow_up_date", sa.Date(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by", sa.String(), nullable=True),
        sa.CheckConstraint(
            "change_type IN ('create', 'update', 'delete')",
            name=op.f("ck_instrument_research_note_revision_change_type"),
        ),
        sa.CheckConstraint(
            "note_type IN ('research_update', 'thesis_update', 'evidence', "
            "'meeting', 'event', 'risk', 'decision', 'review')",
            name=op.f("ck_instrument_research_note_revision_note_type"),
        ),
        sa.CheckConstraint(
            "importance IN ('low', 'medium', 'high')",
            name=op.f("ck_instrument_research_note_revision_importance"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument_detail.instrument_id"],
            name=op.f(
                "fk_instrument_research_note_revision_instrument_id_instrument_detail"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instrument_id",
            "note_id",
            "revision_number",
            name=op.f("pk_instrument_research_note_revision"),
        ),
    )
    op.create_index(
        "idx_instrument_research_note_revision_instrument_time",
        "instrument_research_note_revision",
        ["instrument_id", "recorded_at"],
        unique=False,
    )

    bind = op.get_bind()
    profile = sa.table(
        "instrument_research_profile",
        sa.column("instrument_id", sa.String()),
        *(sa.column(field, sa.Text()) for field in PROFILE_TEXT_FIELDS),
        sa.column("next_review_date", sa.Date()),
        sa.column("manual_rating", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("updated_by", sa.String()),
        sa.column("revision_number", sa.Integer()),
    )
    profile_revision = sa.table(
        "instrument_research_profile_revision",
        sa.column("instrument_id", sa.String()),
        sa.column("revision_number", sa.Integer()),
        *(sa.column(field, sa.Text()) for field in PROFILE_TEXT_FIELDS),
        sa.column("next_review_date", sa.Date()),
        sa.column("manual_rating", sa.Integer()),
        sa.column("recorded_at", sa.DateTime(timezone=True)),
        sa.column("recorded_by", sa.String()),
    )
    note = sa.table(
        "instrument_research_note",
        sa.column("instrument_id", sa.String()),
        sa.column("note_id", sa.String()),
        sa.column("note_date", sa.Date()),
        sa.column("note_type", sa.String()),
        sa.column("title", sa.Text()),
        sa.column("summary", sa.Text()),
        sa.column("body", sa.Text()),
        sa.column("importance", sa.String()),
        sa.column("tags_json", sa.JSON()),
        sa.column("source_refs", sa.Text()),
        sa.column("people", sa.Text()),
        sa.column("author", sa.Text()),
        sa.column("follow_up_date", sa.Date()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("updated_by", sa.String()),
        sa.column("revision_number", sa.Integer()),
    )
    note_revision = sa.table(
        "instrument_research_note_revision",
        sa.column("instrument_id", sa.String()),
        sa.column("note_id", sa.String()),
        sa.column("revision_number", sa.Integer()),
        sa.column("change_type", sa.String()),
        sa.column("note_date", sa.Date()),
        sa.column("note_type", sa.String()),
        sa.column("title", sa.Text()),
        sa.column("summary", sa.Text()),
        sa.column("body", sa.Text()),
        sa.column("importance", sa.String()),
        sa.column("tags_json", sa.JSON()),
        sa.column("source_refs", sa.Text()),
        sa.column("people", sa.Text()),
        sa.column("author", sa.Text()),
        sa.column("follow_up_date", sa.Date()),
        sa.column("recorded_at", sa.DateTime(timezone=True)),
        sa.column("recorded_by", sa.String()),
    )
    now = datetime.now(UTC).replace(microsecond=0)
    for row in bind.execute(sa.select(profile)).mappings():
        bind.execute(
            sa.insert(profile_revision).values(
                instrument_id=row["instrument_id"],
                revision_number=1,
                **{field: row[field] for field in PROFILE_TEXT_FIELDS},
                next_review_date=row["next_review_date"],
                manual_rating=row["manual_rating"],
                recorded_at=row["updated_at"] or row["created_at"] or now,
                recorded_by=row["updated_by"],
            )
        )
    for row in bind.execute(sa.select(note)).mappings():
        bind.execute(
            sa.insert(note_revision).values(
                instrument_id=row["instrument_id"],
                note_id=row["note_id"],
                revision_number=1,
                change_type="create",
                note_date=row["note_date"],
                note_type=row["note_type"],
                title=row["title"],
                summary=row["summary"],
                body=row["body"],
                importance=row["importance"],
                tags_json=row["tags_json"],
                source_refs=row["source_refs"],
                people=row["people"],
                author=row["author"],
                follow_up_date=row["follow_up_date"],
                recorded_at=row["updated_at"] or row["created_at"] or now,
                recorded_by=row["updated_by"],
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "Investment research revision history is audit data. Restore a pre-migration "
        "database backup instead of deleting it."
    )
