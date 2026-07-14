"""Separate manually authored research ratings from quantitative evidence.

Revision ID: 20260713_0026
Revises: 20260712_0025
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import json

from alembic import op
import sqlalchemy as sa


revision = "20260713_0026"
down_revision = "20260712_0025"
branch_labels = None
depends_on = None


_DROP = object()


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        resolved = value
    elif isinstance(value, str):
        resolved = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        resolved = datetime.now(UTC)
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=UTC)
    return resolved


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _clean_research_payload(value: object) -> dict[str, object]:
    source = _json_value(value)
    payload = dict(source) if isinstance(source, dict) else {}
    payload.pop("manual_rating", None)
    overview_source = payload.get("overview")
    overview = dict(overview_source) if isinstance(overview_source, dict) else {}
    overview.pop("current_view", None)
    overview.setdefault("research_view", "")
    payload["overview"] = overview
    timeline_notes = payload.get("timeline_notes")
    payload["timeline_notes"] = timeline_notes if isinstance(timeline_notes, list) else []
    return payload


def _validated_legacy_rating(value: object, *, instrument_id: object) -> int | None:
    source = _json_value(value)
    legacy_rating = source.get("manual_rating") if isinstance(source, dict) else None
    if legacy_rating is not None and (
        isinstance(legacy_rating, bool)
        or not isinstance(legacy_rating, int)
        or not 1 <= legacy_rating <= 5
    ):
        raise RuntimeError(
            "Invalid legacy manual_rating for "
            f"{instrument_id!s}; clean the source record before migrating."
        )
    return legacy_rating


def _rewrite_sort(value: object) -> list[dict[str, object]]:
    source = _json_value(value)
    if not isinstance(source, list):
        return []
    rewritten: list[dict[str, object]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if field == "analyst_stance":
            continue
        rewritten.append(
            {**item, "field": "research_rating" if field == "overall_rating" else field}
        )
    return rewritten


def _rewrite_filters(value: object) -> dict[str, object]:
    source = _json_value(value)
    if not isinstance(source, dict):
        return {}
    rewritten: dict[str, object] = {}
    for key, item in source.items():
        field = str(key).strip()
        if not field or field == "analyst_stance":
            continue
        rewritten["research_rating" if field == "overall_rating" else field] = item
    return rewritten


def _rewrite_advanced_filter(value: object) -> object:
    node = _json_value(value)
    if not isinstance(node, dict):
        return node
    if node.get("type") == "rule":
        field = str(node.get("field") or "").strip()
        if field == "analyst_stance":
            return _DROP
        return {
            **node,
            "field": "research_rating" if field == "overall_rating" else field,
        }
    if node.get("type") == "group":
        conditions = node.get("conditions")
        rewritten_conditions: list[object] = []
        if isinstance(conditions, list):
            for condition in conditions:
                rewritten = _rewrite_advanced_filter(condition)
                if rewritten is not _DROP:
                    rewritten_conditions.append(rewritten)
        return {**node, "conditions": rewritten_conditions}
    return node


def _clean_summary_payload(
    value: object,
    *,
    rating_payload: dict[str, object] | None,
) -> dict[str, object]:
    source = _json_value(value)
    payload = dict(source) if isinstance(source, dict) else {}
    payload.pop("overall_rating", None)
    payload.pop("analyst_stance", None)
    payload.pop("rating_as_of", None)
    payload["research_rating"] = rating_payload
    return payload


def upgrade() -> None:
    connection = op.get_bind()
    preflight_manual_profile = sa.table(
        "instrument_manual_profile",
        sa.column("instrument_id", sa.String()),
        sa.column("research_payload_json", sa.JSON()),
    )
    # Fail before any destructive or SQLite-nontransactional DDL if legacy
    # manual data cannot be migrated exactly.
    for row in connection.execute(sa.select(preflight_manual_profile)).mappings():
        _validated_legacy_rating(
            row["research_payload_json"],
            instrument_id=row["instrument_id"],
        )

    op.create_table(
        "instrument_research_rating",
        sa.Column("rating_revision_id", sa.String(), nullable=False),
        sa.Column("previous_rating_revision_id", sa.String(), nullable=True),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("rating_value", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("next_review_date", sa.Date(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("author", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint(
            "rating_value IS NULL OR (rating_value >= 1 AND rating_value <= 5)",
            name=op.f("ck_instrument_research_rating_rating_value_range"),
        ),
        sa.CheckConstraint(
            "next_review_date IS NULL OR next_review_date >= as_of_date",
            name=op.f("ck_instrument_research_rating_next_review_not_before_as_of"),
        ),
        sa.CheckConstraint(
            "confidence IN ('low', 'medium', 'high', 'unassessed')",
            name=op.f("ck_instrument_research_rating_confidence_value"),
        ),
        sa.ForeignKeyConstraint(
            ["previous_rating_revision_id"],
            ["instrument_research_rating.rating_revision_id"],
            name="fk_research_rating_previous_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument_detail.instrument_id"],
            name=op.f("fk_instrument_research_rating_instrument_id_instrument_detail"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "rating_revision_id",
            name=op.f("pk_instrument_research_rating"),
        ),
        sa.UniqueConstraint(
            "instrument_id",
            "revision_number",
            name=op.f("uq_instrument_research_rating_instrument_revision"),
        ),
    )
    op.create_index(
        "idx_research_rating_instrument_history",
        "instrument_research_rating",
        ["instrument_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_research_rating_current_instrument",
        "instrument_research_rating",
        ["instrument_id"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
        postgresql_where=sa.text("is_current IS TRUE"),
    )

    manual_profile = sa.table(
        "instrument_manual_profile",
        sa.column("instrument_id", sa.String()),
        sa.column("research_payload_json", sa.JSON()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("updated_by", sa.String()),
    )
    research_rating = sa.table(
        "instrument_research_rating",
        sa.column("rating_revision_id", sa.String()),
        sa.column("previous_rating_revision_id", sa.String()),
        sa.column("instrument_id", sa.String()),
        sa.column("revision_number", sa.Integer()),
        sa.column("rating_value", sa.Integer()),
        sa.column("confidence", sa.String()),
        sa.column("as_of_date", sa.Date()),
        sa.column("next_review_date", sa.Date()),
        sa.column("rationale", sa.Text()),
        sa.column("author", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("superseded_at", sa.DateTime(timezone=True)),
        sa.column("is_current", sa.Boolean()),
    )
    migrated_ratings: dict[str, dict[str, object]] = {}
    for row in connection.execute(sa.select(manual_profile)).mappings():
        legacy_rating = _validated_legacy_rating(
            row["research_payload_json"],
            instrument_id=row["instrument_id"],
        )

        if legacy_rating is not None:
            instrument_id = str(row["instrument_id"])
            created_at = _coerce_datetime(row["updated_at"])
            author = str(row["updated_by"] or "legacy-migration").strip() or "legacy-migration"
            revision_id = f"rating-migrated-{instrument_id}"
            rationale = (
                "Migrated from the legacy manual rating. "
                "The legacy schema did not capture a rating-change rationale."
            )
            connection.execute(
                sa.insert(research_rating).values(
                    rating_revision_id=revision_id,
                    previous_rating_revision_id=None,
                    instrument_id=instrument_id,
                    revision_number=1,
                    rating_value=legacy_rating,
                    confidence="unassessed",
                    as_of_date=created_at.date(),
                    next_review_date=None,
                    rationale=rationale,
                    author=author,
                    created_at=created_at,
                    superseded_at=None,
                    is_current=True,
                )
            )
            migrated_ratings[instrument_id] = {
                "rating_revision_id": revision_id,
                "previous_rating_revision_id": None,
                "revision_number": 1,
                "rating": legacy_rating,
                "confidence": "unassessed",
                "as_of_date": created_at.date().isoformat(),
                "rationale": rationale,
                "author": author,
                "next_review_date": None,
                "created_at": _serialize_datetime(created_at),
            }

        connection.execute(
            sa.update(manual_profile)
            .where(manual_profile.c.instrument_id == row["instrument_id"])
            .values(research_payload_json=_clean_research_payload(row["research_payload_json"]))
        )

    with op.batch_alter_table("watchlist_row_read_model") as batch_op:
        batch_op.alter_column(
            "overall_rating",
            existing_type=sa.Integer(),
            new_column_name="research_rating",
            existing_nullable=True,
        )
        batch_op.add_column(sa.Column("research_rating_as_of", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column("research_rating_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.drop_column("analyst_stance")

    watchlist_row = sa.table(
        "watchlist_row_read_model",
        sa.column("instrument_id", sa.String()),
        sa.column("research_rating", sa.Integer()),
        sa.column("research_rating_as_of", sa.Date()),
        sa.column("research_rating_updated_at", sa.DateTime(timezone=True)),
    )
    connection.execute(sa.update(watchlist_row).values(research_rating=None))
    for instrument_id, rating in migrated_ratings.items():
        connection.execute(
            sa.update(watchlist_row)
            .where(watchlist_row.c.instrument_id == instrument_id)
            .values(
                research_rating=rating["rating"],
                research_rating_as_of=date.fromisoformat(str(rating["as_of_date"])),
                research_rating_updated_at=_coerce_datetime(rating["created_at"]),
            )
        )

    view_column = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
    )
    existing_research_columns = {
        str(row["watchlist_view_id"])
        for row in connection.execute(
            sa.select(view_column.c.watchlist_view_id).where(
                view_column.c.field_key == "research_rating"
            )
        ).mappings()
    }
    for row in connection.execute(
        sa.select(view_column).where(
            view_column.c.field_key.in_(("overall_rating", "analyst_stance"))
        )
    ).mappings():
        view_id = str(row["watchlist_view_id"])
        field_key = str(row["field_key"])
        predicate = sa.and_(
            view_column.c.watchlist_view_id == view_id,
            view_column.c.field_key == field_key,
        )
        if field_key == "analyst_stance" or view_id in existing_research_columns:
            connection.execute(sa.delete(view_column).where(predicate))
        else:
            connection.execute(
                sa.update(view_column).where(predicate).values(field_key="research_rating")
            )
            existing_research_columns.add(view_id)

    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_group_by", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
    )
    for row in connection.execute(sa.select(view)).mappings():
        group_by = str(row["default_group_by"] or "").strip()
        if group_by == "overall_rating":
            group_by = "research_rating"
        elif group_by == "analyst_stance":
            group_by = "none"
        advanced = _rewrite_advanced_filter(row["default_advanced_filter_json"])
        connection.execute(
            sa.update(view)
            .where(view.c.watchlist_view_id == row["watchlist_view_id"])
            .values(
                default_group_by=group_by or row["default_group_by"],
                default_sort_json=_rewrite_sort(row["default_sort_json"]),
                default_filters_json=_rewrite_filters(row["default_filters_json"]),
                default_advanced_filter_json={} if advanced is _DROP else advanced,
            )
        )

    field_registry = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("availability_rule_json", sa.JSON()),
        sa.column("source_domain", sa.String()),
        sa.column("source_metric_code", sa.String()),
    )
    connection.execute(
        sa.delete(field_registry).where(field_registry.c.field_key == "analyst_stance")
    )
    connection.execute(
        sa.update(field_registry)
        .where(field_registry.c.field_key == "overall_rating")
        .values(
            field_key="research_rating",
            label="Research Rating",
            description="Manually assigned internal research rating from 1 to 5.",
            availability_rule_json={"requires": ["instrument_research_rating"]},
            source_domain="research",
            source_metric_code="instrument_research_rating.rating_value",
        )
    )

    summary = sa.table(
        "instrument_summary_read_model",
        sa.column("instrument_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )
    for row in connection.execute(sa.select(summary)).mappings():
        instrument_id = str(row["instrument_id"])
        connection.execute(
            sa.update(summary)
            .where(summary.c.instrument_id == instrument_id)
            .values(
                payload_json=_clean_summary_payload(
                    row["payload_json"],
                    rating_payload=migrated_ratings.get(instrument_id),
                )
            )
        )

    recalc_job = sa.table(
        "recalc_job",
        sa.column("job_type", sa.String()),
    )
    connection.execute(sa.delete(recalc_job).where(recalc_job.c.job_type == "ratings"))

    op.drop_table("instrument_rating_read_model")
    op.drop_table("instrument_score_snapshot")


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0026 is intentionally irreversible: automatic ratings were invalid "
        "derived data and are not restored."
    )
