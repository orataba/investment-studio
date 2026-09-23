"""Make taxonomy configuration own classification and planning only."""
from datetime import UTC, datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "20260923_0066"
down_revision = "20260920_0065"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    portfolios = connection.execute(sa.text(
        "SELECT portfolio_id, default_planning_taxonomy_id FROM portfolio_record"
    )).all()
    for portfolio_id, default_taxonomy_id in portfolios:
        # The explicit current choice wins, including an intentional NULL.
        # Only portfolios with no selection row use their existing default.
        selection = connection.execute(sa.text(
            "SELECT taxonomy_id FROM analytics_taxonomy_selection_record "
            "WHERE portfolio_id = :id AND superseded_by_selection_id IS NULL"
        ), {"id": portfolio_id}).first()
        selected_id = selection.taxonomy_id if selection is not None else default_taxonomy_id
        if selected_id is not None and not connection.execute(sa.text(
            "SELECT 1 FROM taxonomy_record WHERE portfolio_id = :id AND taxonomy_id = :taxonomy "
            "AND planning_enabled = true AND status = 'active'"
        ), {"id": portfolio_id, "taxonomy": selected_id}).scalar():
            selected_id = None
        connection.execute(sa.text(
            "UPDATE portfolio_record SET default_planning_taxonomy_id = :taxonomy WHERE portfolio_id = :id"
        ), {"id": portfolio_id, "taxonomy": selected_id})

        # Preserve monotonic configuration identity even for imported rows whose
        # old state counter was absent or behind its recorded revisions.
        versions = [connection.execute(sa.text(
            f"SELECT max({column}) FROM {table} WHERE portfolio_id = :id"
        ), {"id": portfolio_id}).scalar() or 0 for table, column in (
            ("portfolio_analytics_policy_state", "current_version"),
            ("analytics_scope_policy_record", "policy_version"),
            ("analytics_taxonomy_selection_record", "selection_version"),
            ("taxonomy_configuration_revision", "configuration_version"),
        )]
        state_values = {"id": portfolio_id, "version": max(versions) + 1, "now": now}
        updated = connection.execute(sa.text(
            "UPDATE portfolio_analytics_policy_state SET current_version = :version, "
            "updated_at = :now WHERE portfolio_id = :id"
        ), state_values)
        if updated.rowcount == 0:
            connection.execute(sa.text(
                "INSERT INTO portfolio_analytics_policy_state (portfolio_id, current_version, updated_at) "
                "VALUES (:id, :version, :now)"
            ), state_values)

        # Model coverage is now derived from actual data and model support.
        # Rebuild history and make previously saved research stale through its
        # changed source identity; its original request/result JSON stays intact.
        refresh_values = {"id": portfolio_id, "request": str(uuid4())}
        updated = connection.execute(sa.text(
            "UPDATE portfolio_calculation_state SET daily_snapshot_status = 'stale', "
            "dirty_from = NULL, refresh_request_id = :request, error_message = NULL "
            "WHERE portfolio_id = :id"
        ), refresh_values)
        if updated.rowcount == 0:
            connection.execute(sa.text(
                "INSERT INTO portfolio_calculation_state "
                "(portfolio_id, daily_snapshot_status, dirty_from, refresh_request_id) "
                "VALUES (:id, 'stale', NULL, :request)"
            ), refresh_values)

    op.drop_table("analytics_scope_policy_record")
    op.drop_table("analytics_taxonomy_selection_record")
    inspector = sa.inspect(connection)
    old_table = "portfolio_analytics_policy_state"
    primary_key_name = inspector.get_pk_constraint(old_table)["name"]
    foreign_key_name = inspector.get_foreign_keys(old_table)[0]["name"]
    check_name = inspector.get_check_constraints(old_table)[0]["name"]
    op.rename_table(old_table, "portfolio_taxonomy_state")
    with op.batch_alter_table("portfolio_taxonomy_state") as batch:
        batch.drop_constraint(op.f(check_name), type_="check")
        batch.create_check_constraint("ck_taxonomy_state_version", "current_version >= 0")
        batch.drop_constraint(op.f(primary_key_name), type_="primary")
        batch.create_primary_key(op.f("pk_portfolio_taxonomy_state"), ["portfolio_id"])
        batch.drop_constraint(op.f(foreign_key_name), type_="foreignkey")
        batch.create_foreign_key(
            op.f("fk_portfolio_taxonomy_state_portfolio_id_portfolio_record"),
            "portfolio_record", ["portfolio_id"], ["portfolio_id"], ondelete="CASCADE",
        )
    connection.execute(sa.text("DELETE FROM portfolio_workspace_read_model"))


def downgrade():
    raise RuntimeError(
        "Removed manual analytics policies cannot be reconstructed; restore the pre-migration backup."
    )
