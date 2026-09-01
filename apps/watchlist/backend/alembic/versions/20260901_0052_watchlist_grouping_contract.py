"""replace legacy Watchlist grouping with the universal grouping contract

Revision ID: 20260901_0052
Revises: 20260901_0050
Create Date: 2026-09-01 16:10:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260901_0052"
down_revision = "20260901_0050"
branch_labels = None
depends_on = None


GROUPABLE_FIELD_KEYS = (
    "currency",
    "attr.coverage_status",
    "attr.manual_rating",
)
SAVED_GROUP_BY_CODES = (
    "none",
    "taxonomy",
    *GROUPABLE_FIELD_KEYS,
)
CURRENCY_FIELD = {
    "field_key": "currency",
    "label": "Currency",
    "description": "Canonical instrument currency from the shared Instrument Registry.",
    "category_code": "general",
    "data_type": "string",
    "formatter_code": "text",
    "sort_mode": "alpha",
    "filter_mode": "multi_select",
    "group_mode": "discrete",
    "instrument_scope_json": [],
    "product_scope_json": [],
    "availability_rule_json": {"requires": ["instrument_registry"]},
    "source_domain": "instrument_registry",
    "source_metric_code": "instrument.currency",
    "default_width": 100,
    "default_visible": False,
}


def _field_registry_table() -> sa.TableClause:
    return sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("category_code", sa.String()),
        sa.column("data_type", sa.String()),
        sa.column("formatter_code", sa.String()),
        sa.column("sort_mode", sa.String()),
        sa.column("filter_mode", sa.String()),
        sa.column("group_mode", sa.String()),
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
        sa.column("availability_rule_json", sa.JSON()),
        sa.column("source_domain", sa.String()),
        sa.column("source_metric_code", sa.String()),
        sa.column("default_width", sa.Integer()),
        sa.column("default_visible", sa.Boolean()),
    )


def upgrade() -> None:
    bind = op.get_bind()
    field = _field_registry_table()
    bind.execute(sa.update(field).values(group_mode="none"))

    currency_exists = bind.execute(
        sa.select(field.c.field_key).where(field.c.field_key == "currency")
    ).first()
    if currency_exists is None:
        bind.execute(sa.insert(field).values(**CURRENCY_FIELD))
    else:
        bind.execute(
            sa.update(field)
            .where(field.c.field_key == "currency")
            .values(**CURRENCY_FIELD)
        )

    bind.execute(
        sa.update(field)
        .where(field.c.field_key == "attr.coverage_status")
        .values(label="Investment Status", group_mode="discrete")
    )
    bind.execute(
        sa.update(field)
        .where(field.c.field_key == "attr.manual_rating")
        .values(label="Research Rating", group_mode="discrete")
    )

    definition = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
        sa.column("is_groupable", sa.Boolean()),
    )
    bind.execute(sa.update(definition).values(is_groupable=False))
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key == "coverage_status")
        .values(is_groupable=True)
    )

    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_group_by", sa.String()),
        sa.column("default_filters_json", sa.JSON()),
    )
    bind.execute(
        sa.update(view)
        .where(
            sa.or_(
                view.c.default_group_by.is_(None),
                view.c.default_group_by.not_in(SAVED_GROUP_BY_CODES),
            )
        )
        .values(default_group_by="none")
    )
    for row in bind.execute(
        sa.select(view.c.watchlist_view_id, view.c.default_filters_json)
    ).mappings():
        filters = row["default_filters_json"]
        if not isinstance(filters, dict):
            continue
        taxonomy_keys = [
            key
            for key in filters
            if str(key).startswith("attr.instrument_taxonomy_level_")
        ]
        instrument_types = filters.get("instrument_type")
        if not taxonomy_keys or (
            isinstance(instrument_types, list) and len(instrument_types) == 1
        ):
            continue
        cleaned_filters = {
            key: value for key, value in filters.items() if key not in taxonomy_keys
        }
        bind.execute(
            sa.update(view)
            .where(view.c.watchlist_view_id == row["watchlist_view_id"])
            .values(default_filters_json=cleaned_filters)
        )


def downgrade() -> None:
    raise RuntimeError(
        "The Watchlist grouping contract is a forward-only cleanup; "
        "restore the pre-migration database backup to roll it back."
    )
