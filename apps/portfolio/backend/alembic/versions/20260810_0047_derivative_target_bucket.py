"""Add the fixed derivative weight target bucket.

Revision ID: 20260810_0047
Revises: 20260809_0046
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260810_0047"
down_revision: str | None = "20260809_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_CHECK_NAME = "ck_target_set_line_cash_risk_null"
NEW_CHECK_NAME = "ck_target_set_line_non_risk_member_risk_null"


def upgrade() -> None:
    with op.batch_alter_table("target_set_line_record") as batch_op:
        batch_op.drop_constraint(OLD_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(
            NEW_CHECK_NAME,
            "target_member_type NOT IN ('cash_bucket', 'derivative_bucket') "
            "OR target_risk_share IS NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("target_set_line_record") as batch_op:
        batch_op.drop_constraint(NEW_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(
            OLD_CHECK_NAME,
            "target_member_type != 'cash_bucket' OR target_risk_share IS NULL",
        )
