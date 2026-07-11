"""Remove unsupported legacy research windows and enforce valid values.

Revision ID: 20260711_0031
Revises: 20260711_0030

Historical runs produced with an experimental, unsupported window cannot be
faithfully relabelled as a supported methodology.  They are intentionally
removed instead of being presented with false metadata.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260711_0031"
down_revision: str | None = "20260711_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SUPPORTED_LOOKBACKS = "30, 90, 180, 366, 730"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"DELETE FROM research_run_record "
            f"WHERE lookback_days NOT IN ({SUPPORTED_LOOKBACKS})"
        )
    )
    op.execute(
        sa.text(
            f"UPDATE research_settings_record SET lookback_days = 90 "
            f"WHERE lookback_days NOT IN ({SUPPORTED_LOOKBACKS})"
        )
    )
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.create_check_constraint(
            "ck_research_settings_supported_lookback",
            f"lookback_days IN ({SUPPORTED_LOOKBACKS})",
        )
    with op.batch_alter_table("research_run_record") as batch_op:
        batch_op.create_check_constraint(
            "ck_research_run_supported_lookback",
            f"lookback_days IN ({SUPPORTED_LOOKBACKS})",
        )


def downgrade() -> None:
    with op.batch_alter_table("research_run_record") as batch_op:
        batch_op.drop_constraint(
            "ck_research_run_supported_lookback",
            type_="check",
        )
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.drop_constraint(
            "ck_research_settings_supported_lookback",
            type_="check",
        )
