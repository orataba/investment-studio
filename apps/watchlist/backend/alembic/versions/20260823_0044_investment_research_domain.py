"""Promote investment research from a profile JSON blob to first-class records.

Revision ID: 20260823_0044
Revises: 20260822_0043
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import json

from alembic import op
import sqlalchemy as sa


revision = "20260823_0044"
down_revision = "20260822_0043"
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


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise RuntimeError("Invalid legacy research JSON payload.") from error
    return dict(value) if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _date(value: object, *, field: str, instrument_id: str) -> date | None:
    if value in {None, ""}:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as error:
        raise RuntimeError(
            f"Invalid {field} for legacy research record {instrument_id}: {value!r}"
        ) from error


def _rating(value: object, *, instrument_id: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise RuntimeError(
            f"Invalid manual research rating for {instrument_id}: {value!r}"
        )
    return value


def upgrade() -> None:
    op.create_table(
        "instrument_research_profile",
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("thesis", sa.Text(), nullable=False),
        sa.Column("current_view", sa.Text(), nullable=False),
        sa.Column("why_now", sa.Text(), nullable=False),
        sa.Column("edge_assessment", sa.Text(), nullable=False),
        sa.Column("valuation_framework", sa.Text(), nullable=False),
        sa.Column("catalysts", sa.Text(), nullable=False),
        sa.Column("key_risks", sa.Text(), nullable=False),
        sa.Column("disconfirming_evidence", sa.Text(), nullable=False),
        sa.Column("open_questions", sa.Text(), nullable=False),
        sa.Column("monitoring_plan", sa.Text(), nullable=False),
        sa.Column("people_assessment", sa.Text(), nullable=False),
        sa.Column("portfolio_role", sa.Text(), nullable=False),
        sa.Column("time_horizon", sa.Text(), nullable=False),
        sa.Column("decision_rationale", sa.Text(), nullable=False),
        sa.Column("primary_analyst", sa.Text(), nullable=False),
        sa.Column("next_review_date", sa.Date(), nullable=True),
        sa.Column("dd_status", sa.Text(), nullable=False),
        sa.Column("odd_status", sa.Text(), nullable=False),
        sa.Column("ic_status", sa.Text(), nullable=False),
        sa.Column("manual_rating", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
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
            "manual_rating IS NULL OR manual_rating BETWEEN 1 AND 5",
            name=op.f("ck_instrument_research_profile_manual_rating_range"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument_detail.instrument_id"],
            name=op.f("fk_instrument_research_profile_instrument_id_instrument_detail"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instrument_id",
            name=op.f("pk_instrument_research_profile"),
        ),
    )
    op.create_table(
        "instrument_research_note",
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("note_id", sa.String(), nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.CheckConstraint(
            "note_type IN ('research_update', 'thesis_update', 'evidence', "
            "'meeting', 'event', 'risk', 'decision', 'review')",
            name=op.f("ck_instrument_research_note_note_type"),
        ),
        sa.CheckConstraint(
            "importance IN ('low', 'medium', 'high')",
            name=op.f("ck_instrument_research_note_importance"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument_detail.instrument_id"],
            name=op.f("fk_instrument_research_note_instrument_id_instrument_detail"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instrument_id",
            "note_id",
            name=op.f("pk_instrument_research_note"),
        ),
    )
    op.create_index(
        "idx_instrument_research_note_instrument_date",
        "instrument_research_note",
        ["instrument_id", "note_date"],
        unique=False,
    )

    bind = op.get_bind()
    manual_profile = sa.table(
        "instrument_manual_profile",
        sa.column("instrument_id", sa.String()),
        sa.column("research_payload_json", sa.JSON()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("updated_by", sa.String()),
    )
    research_profile = sa.table(
        "instrument_research_profile",
        sa.column("instrument_id", sa.String()),
        *(sa.column(field, sa.Text()) for field in PROFILE_TEXT_FIELDS),
        sa.column("next_review_date", sa.Date()),
        sa.column("manual_rating", sa.Integer()),
        sa.column("updated_by", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    research_note = sa.table(
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
    )

    now = datetime.now(UTC).replace(microsecond=0)
    rows = bind.execute(
        sa.select(
            manual_profile.c.instrument_id,
            manual_profile.c.research_payload_json,
            manual_profile.c.updated_at,
            manual_profile.c.updated_by,
        )
    ).mappings()
    for row in rows:
        instrument_id = str(row["instrument_id"])
        source = _json_object(row["research_payload_json"])
        overview = _json_object(source.get("overview"))
        profile_values = {
            field: _text(overview.get(field))
            for field in PROFILE_TEXT_FIELDS
        }
        profile_values["thesis"] = (
            profile_values["thesis"]
            or _text(source.get("thesis"))
            or _text(overview.get("research_view"))
        )
        profile_values["decision_rationale"] = (
            profile_values["decision_rationale"] or _text(overview.get("decision"))
        )
        next_review_date = _date(
            overview.get("next_review_date"),
            field="next_review_date",
            instrument_id=instrument_id,
        )
        manual_rating = _rating(source.get("manual_rating"), instrument_id=instrument_id)
        timeline_notes = source.get("timeline_notes")
        notes = timeline_notes if isinstance(timeline_notes, list) else []
        changed_at = row["updated_at"] or now

        if any(profile_values.values()) or next_review_date or manual_rating is not None:
            bind.execute(
                sa.insert(research_profile).values(
                    instrument_id=instrument_id,
                    **profile_values,
                    next_review_date=next_review_date,
                    manual_rating=manual_rating,
                    updated_by=row["updated_by"],
                    created_at=changed_at,
                    updated_at=changed_at,
                )
            )

        for index, raw_note in enumerate(notes):
            if not isinstance(raw_note, dict):
                raise RuntimeError(
                    f"Invalid legacy research note for {instrument_id} at position {index}."
                )
            note_date = _date(
                raw_note.get("note_date"),
                field="note_date",
                instrument_id=instrument_id,
            )
            if note_date is None:
                raise RuntimeError(
                    f"Legacy research note has no date for {instrument_id} at position {index}."
                )
            summary = _text(raw_note.get("summary"))
            body = _text(raw_note.get("body"))
            title = _text(raw_note.get("title")) or summary or body
            if not title:
                raise RuntimeError(
                    f"Legacy research note has no content for {instrument_id} at position {index}."
                )
            importance = _text(raw_note.get("importance")) or "medium"
            if importance not in {"low", "medium", "high"}:
                raise RuntimeError(
                    f"Invalid legacy research note importance for {instrument_id}: {importance!r}"
                )
            tags = raw_note.get("tags")
            bind.execute(
                sa.insert(research_note).values(
                    instrument_id=instrument_id,
                    note_id=_text(raw_note.get("note_id")) or f"legacy-{index + 1}",
                    note_date=note_date,
                    note_type="research_update",
                    title=title,
                    summary=summary,
                    body=body,
                    importance=importance,
                    tags_json=[_text(tag) for tag in tags if _text(tag)] if isinstance(tags, list) else [],
                    source_refs="",
                    people="",
                    author="",
                    follow_up_date=None,
                    created_at=changed_at,
                    updated_at=changed_at,
                    updated_by=row["updated_by"],
                )
            )

    with op.batch_alter_table("instrument_manual_profile") as batch_op:
        batch_op.drop_column("research_payload_json")


def downgrade() -> None:
    raise RuntimeError(
        "Investment research records are now first-class data. Restore a pre-migration "
        "database backup instead of recreating the retired JSON blob."
    )
