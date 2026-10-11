"""Retire current missing-return row deletion; preserve immutable run evidence.

The migration changes configuration only. Saved request/result JSON is unchanged.
Downgrade removes constraints but does not re-enable an unsafe former policy.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0072"
down_revision = "20261008_0071"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    portfolios = sa.table("portfolio_record", sa.column("portfolio_id", sa.String()),
                          sa.column("risk_policy_json", sa.JSON()))
    settings = sa.table("research_settings_record", sa.column("missing_return_policy", sa.String()))
    rows = list(connection.execute(sa.select(portfolios)))
    allowed = {None, "strict", "complete_case_drop"}
    for row in rows:
        policy = row.risk_policy_json
        if policy is not None and (not isinstance(policy, dict) or policy.get("missing_return_policy") not in allowed):
            raise RuntimeError(f"Unrecognized risk policy on portfolio {row.portfolio_id}; repair before migration.")
    if any(value not in {"strict", "complete_case_drop"}
           for value in connection.scalars(sa.select(settings.c.missing_return_policy))):
        raise RuntimeError("Unrecognized research missing-return policy; repair before migration.")
    for row in rows:
        if row.risk_policy_json and row.risk_policy_json.get("missing_return_policy") == "complete_case_drop":
            connection.execute(portfolios.update().where(portfolios.c.portfolio_id == row.portfolio_id)
                               .values(risk_policy_json={**row.risk_policy_json, "missing_return_policy": "strict"}))
    connection.execute(settings.update().where(settings.c.missing_return_policy == "complete_case_drop")
                       .values(missing_return_policy="strict"))
    with op.batch_alter_table("research_settings_record") as batch:
        batch.create_check_constraint("research_strict_missing_returns", "missing_return_policy = 'strict'")
    field = sa.column("risk_policy_json", sa.JSON())["missing_return_policy"].as_string()
    with op.batch_alter_table("portfolio_record") as batch:
        batch.create_check_constraint("portfolio_risk_strict_missing_returns", field.is_(None) | (field == "strict"))


def downgrade():
    with op.batch_alter_table("portfolio_record") as batch:
        batch.drop_constraint("portfolio_risk_strict_missing_returns", type_="check")
    with op.batch_alter_table("research_settings_record") as batch:
        batch.drop_constraint("research_strict_missing_returns", type_="check")
