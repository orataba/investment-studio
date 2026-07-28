"""Clarify per-instrument Watchlist metric endpoints and risk definitions.

Revision ID: 20260728_0030
Revises: 20260727_0029
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260728_0030"
down_revision = "20260727_0029"
branch_labels = None
depends_on = None


CURRENT_FIELDS = {
    "return_ytd": (
        None,
        "Year-to-date return from the selected calculation series, ending at this instrument's own latest observation.",
    ),
    "return_mtd": (
        None,
        "Month-to-date return from the selected calculation series, ending at this instrument's own latest observation.",
    ),
    "return_1w": (
        None,
        "Calendar 1-week return from the selected calculation series, ending at this instrument's own latest observation.",
    ),
    "return_1m": (
        None,
        "Calendar 1-month return from the selected calculation series, ending at this instrument's own latest observation.",
    ),
    "return_3m": (
        None,
        "Calendar 3-month return from the selected calculation series, ending at this instrument's own latest observation.",
    ),
    "return_6m": (
        None,
        "Calendar 6-month return from the selected calculation series, ending at this instrument's own latest observation.",
    ),
    "return_1y": (
        None,
        "Trailing 1-year return from the selected calculation series, ending at this instrument's own latest observation.",
    ),
    "annualized_return": (
        None,
        "Since-inception annualized return through this instrument's own latest observation; withheld before the first calendar anniversary.",
    ),
    "return_3y": (
        None,
        "Trailing 3-year annualized return ending at this instrument's own latest calculation-series observation.",
    ),
    "return_5y": (
        None,
        "Trailing 5-year annualized return ending at this instrument's own latest calculation-series observation.",
    ),
    "max_drawdown": (
        None,
        "Since-inception maximum drawdown through this instrument's own latest observation; withheld when expected observations are missing.",
    ),
    "attr.current_drawdown": (
        None,
        "Current drawdown through this instrument's own latest observation relative to its prior high watermark; withheld when expected observations are missing.",
    ),
    "volatility": (
        None,
        "Since-inception annualized volatility through this instrument's own latest observation; withheld when expected observations are missing.",
    ),
    "sharpe_ratio": (
        None,
        "Since-inception zero-risk-free Sharpe ratio through this instrument's own latest observation; withheld when expected observations are missing.",
    ),
    "last_nav_date": (
        "Metric As Of",
        "Instrument-specific endpoint used by the materialized return and risk fields; Watchlist rows need not share one date.",
    ),
}

PREVIOUS_FIELDS = {
    "return_ytd": (None, "Year-to-date return from the selected calculation series."),
    "return_mtd": (None, "Month-to-date return from the selected calculation series."),
    "return_1w": (None, "1-week return from the selected calculation series."),
    "return_1m": (None, "1-month return from the selected calculation series."),
    "return_3m": (None, "3-month return from the selected calculation series."),
    "return_6m": (None, "6-month return from the selected calculation series."),
    "return_1y": (None, "Trailing 1-year return from the selected calculation series."),
    "annualized_return": (
        None,
        "Since-inception annualized return from the selected calculation series.",
    ),
    "return_3y": (
        None,
        "Trailing 3-year annualized return from the selected calculation series.",
    ),
    "return_5y": (
        None,
        "Trailing 5-year annualized return from the selected calculation series.",
    ),
    "max_drawdown": (None, "Maximum drawdown from the active performance snapshot."),
    "attr.current_drawdown": (
        None,
        "Current drawdown of the selected calculation series relative to its prior high watermark.",
    ),
    "volatility": (None, "Risk snapshot volatility for the selected window."),
    "sharpe_ratio": (None, "Current risk-adjusted return metric."),
    "last_nav_date": (
        "Last Quote Date",
        "Most recent selected quote-series date adopted for the instrument.",
    ),
}


def _apply(values: dict[str, tuple[str | None, str]]) -> None:
    field_registry = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.Text()),
    )
    connection = op.get_bind()
    for field_key, (label, description) in values.items():
        payload: dict[str, object] = {"description": description}
        if label is not None:
            payload["label"] = label
        connection.execute(
            sa.update(field_registry)
            .where(field_registry.c.field_key == field_key)
            .values(**payload)
        )


def upgrade() -> None:
    _apply(CURRENT_FIELDS)


def downgrade() -> None:
    _apply(PREVIOUS_FIELDS)
