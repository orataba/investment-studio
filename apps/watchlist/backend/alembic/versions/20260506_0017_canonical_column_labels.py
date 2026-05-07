"""canonical watchlist column labels

Revision ID: 20260506_0017
Revises: 20260501_0016
Create Date: 2026-05-06 21:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260506_0017"
down_revision = "20260501_0016"
branch_labels = None
depends_on = None


CANONICAL_LABELS = {
    "price_chart_1m": "Spark Chart",
    "return_1w": "1W Return",
    "return_1m": "1M",
    "return_1y": "1Y",
    "annualized_return": "Ann.",
    "return_3y": "3Y",
    "return_5y": "5Y",
    "max_drawdown": "Max DD",
    "attr.current_drawdown": "Current DD",
    "attr.peer_return_percentile": "Return Pctl",
    "attr.peer_risk_percentile": "Risk Pctl",
    "attr.peer_risk_adjusted_percentile": "Risk-Adj Pctl",
    "attr.peer_return_1w_percentile": "1W Return Pctl",
    "attr.peer_return_1m_percentile": "1M Pctl",
    "attr.peer_return_ytd_percentile": "YTD Pctl",
    "attr.peer_return_1y_percentile": "1Y Pctl",
    "attr.peer_return_3y_percentile": "3Y Pctl",
    "attr.peer_return_5y_percentile": "5Y Pctl",
    "attr.peer_annualized_return_percentile": "Ann. Pctl",
    "attr.peer_max_drawdown_percentile": "Max DD Pctl",
}

PREVIOUS_LABELS = {
    "price_chart_1m": "Price Chart",
    "return_1w": "Total Return (1W)",
    "return_1m": "Total Return (1M)",
    "return_1y": "Total Return (1Y)",
    "annualized_return": "Annualized Return",
    "return_3y": "Total Return (3Y)",
    "return_5y": "Total Return (5Y)",
    "max_drawdown": "Max Drawdown",
    "attr.current_drawdown": "当前回撤",
    "attr.peer_return_percentile": "Peer Return Percentile",
    "attr.peer_risk_percentile": "Peer Risk Percentile",
    "attr.peer_risk_adjusted_percentile": "Peer Risk-Adj Percentile",
    "attr.peer_return_1w_percentile": "1W Return Percentile",
    "attr.peer_return_1m_percentile": "1M Return Percentile",
    "attr.peer_return_ytd_percentile": "YTD Return Percentile",
    "attr.peer_return_1y_percentile": "1Y Return Percentile",
    "attr.peer_return_3y_percentile": "3Y Return Percentile",
    "attr.peer_return_5y_percentile": "5Y Return Percentile",
    "attr.peer_annualized_return_percentile": "Ann. Return Percentile",
    "attr.peer_max_drawdown_percentile": "Max Drawdown Percentile",
}


def _apply_labels(labels: dict[str, str]) -> None:
    field_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
    )
    bind = op.get_bind()
    for field_key, label in labels.items():
        bind.execute(
            sa.update(field_table)
            .where(field_table.c.field_key == field_key)
            .values(label=label)
        )


def upgrade() -> None:
    _apply_labels(CANONICAL_LABELS)


def downgrade() -> None:
    _apply_labels(PREVIOUS_LABELS)
