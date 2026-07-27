"""Align generic return field descriptions with selected-series semantics.

Revision ID: 20260727_0029
Revises: 20260727_0028
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260727_0029"
down_revision = "20260727_0028"
branch_labels = None
depends_on = None


CURRENT_DESCRIPTIONS = {
    "return_ytd": "Year-to-date return from the selected calculation series.",
    "return_mtd": "Month-to-date return from the selected calculation series.",
    "return_1w": "1-week return from the selected calculation series.",
    "return_1m": "1-month return from the selected calculation series.",
    "return_3m": "3-month return from the selected calculation series.",
    "return_6m": "6-month return from the selected calculation series.",
    "return_1y": "Trailing 1-year return from the selected calculation series.",
    "annualized_return": (
        "Since-inception annualized return from the selected calculation series."
    ),
    "return_3y": (
        "Trailing 3-year annualized return from the selected calculation series."
    ),
    "return_5y": (
        "Trailing 5-year annualized return from the selected calculation series."
    ),
    "attr.current_drawdown": (
        "Current drawdown of the selected calculation series relative to its "
        "prior high watermark."
    ),
}

PREVIOUS_DESCRIPTIONS = {
    "return_ytd": "Derived from canonical NAV facts.",
    "return_mtd": "Month-to-date total return.",
    "return_1w": "1-week total return.",
    "return_1m": "1-month total return.",
    "return_3m": "3-month total return.",
    "return_6m": "6-month total return.",
    "return_1y": "1-year annualized total return.",
    "annualized_return": "Since-inception annualized total return.",
    "return_3y": "3-year annualized total return.",
    "return_5y": "5-year annualized total return.",
    "attr.current_drawdown": (
        "Current drawdown from the latest NAV relative to the prior high watermark."
    ),
}


def _apply_descriptions(descriptions: dict[str, str]) -> None:
    field_registry = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("description", sa.Text()),
    )
    connection = op.get_bind()
    for field_key, description in descriptions.items():
        connection.execute(
            sa.update(field_registry)
            .where(field_registry.c.field_key == field_key)
            .values(description=description)
        )


def upgrade() -> None:
    _apply_descriptions(CURRENT_DESCRIPTIONS)


def downgrade() -> None:
    _apply_descriptions(PREVIOUS_DESCRIPTIONS)
