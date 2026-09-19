"""Use current taxonomy configuration and restate derived history."""
from datetime import UTC, datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "20260920_0064"
down_revision = "20260908_0063"
branch_labels = None
depends_on = None

# Versions remain an immutable change audit. Dates no longer select a version.
_CONFIGURATIONS = (
    ("analytics_scope_policy_record", "analytics_scope_policy", "analytics_scope_policy_id",
     "policy_version", "superseded_by_policy_id", ("portfolio_id", "taxonomy_id", "taxonomy_node_id")),
    ("analytics_taxonomy_selection_record", "analytics_taxonomy_selection", "analytics_taxonomy_selection_id",
     "selection_version", "superseded_by_selection_id", ("portfolio_id",)),
    ("taxonomy_configuration_revision", "taxonomy_configuration_revision", "taxonomy_configuration_revision_id",
     "configuration_version", "superseded_by_revision_id", ("portfolio_id", "taxonomy_id")),
)


def upgrade():
    connection = op.get_bind()
    latest_versions: dict[str, int] = {}
    for table, prefix, identity, version, superseded, keys in _CONFIGURATIONS:
        # The latest saved edit wins, including an edit formerly scheduled for
        # the future. Keep every row and payload for audit and saved-run lineage.
        rows = connection.execute(sa.text(
            f"SELECT {identity}, {version}, {superseded}, {', '.join(keys)} FROM {table} ORDER BY {version} DESC"
        )).mappings().all()
        winners: dict[tuple, str] = {}
        for row in rows:
            key = tuple(row[name] for name in keys)
            winner = winners.setdefault(key, row[identity])
            latest_versions[row["portfolio_id"]] = max(latest_versions.get(row["portfolio_id"], 0), row[version])
            connection.execute(sa.text(f"UPDATE {table} SET {superseded} = :winner WHERE {identity} = :identity"),
                               {"winner": None if row[identity] == winner else (row[superseded] or winner), "identity": row[identity]})
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{prefix}_resolve")
            batch.drop_constraint(f"ck_{prefix}_effective_range", type_="check")
            batch.drop_column("effective_from")
            batch.drop_column("effective_to")
            batch.create_index(f"ix_{prefix}_resolve", [*keys, superseded])
            batch.create_index(f"uq_{prefix}_current", list(keys), unique=True,
                               postgresql_where=sa.text(f"{superseded} IS NULL"),
                               sqlite_where=sa.text(f"{superseded} IS NULL"))

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    portfolios = connection.execute(sa.text("SELECT portfolio_id, default_planning_taxonomy_id FROM portfolio_record")).all()
    for portfolio_id, old_default in portfolios:
        current_version = connection.execute(sa.text(
            "SELECT current_version FROM portfolio_analytics_policy_state WHERE portfolio_id = :id"
        ), {"id": portfolio_id}).scalar_one_or_none()
        next_version = max(current_version or 0, latest_versions.get(portfolio_id, 0)) + 1
        selection = connection.execute(sa.text("SELECT analytics_taxonomy_selection_id, taxonomy_id "
            "FROM analytics_taxonomy_selection_record WHERE portfolio_id = :id "
            "AND superseded_by_selection_id IS NULL"), {"id": portfolio_id}).first()
        selected_id = selection.taxonomy_id if selection else old_default
        valid_selection = selected_id is None or bool(connection.execute(sa.text(
            "SELECT 1 FROM taxonomy_record WHERE portfolio_id = :id AND taxonomy_id = :taxonomy "
            "AND status = 'active' AND planning_enabled = true"),
            {"id": portfolio_id, "taxonomy": selected_id}).scalar())
        # A former future selection may point to a subsequently deleted or
        # disabled taxonomy. Preserve it in the audit; never resurrect it.
        # A valid legacy default is already an explicit business choice, so
        # give that existing choice its missing selection identity.
        if not valid_selection or (selection is None and old_default is not None):
            selected_id = selected_id if valid_selection else None
            selection_id = f"taxonomy-selection-{uuid4().hex}"
            if selection:
                connection.execute(sa.text("UPDATE analytics_taxonomy_selection_record SET "
                    "superseded_by_selection_id = :new WHERE analytics_taxonomy_selection_id = :old"),
                    {"new": selection_id, "old": selection.analytics_taxonomy_selection_id})
            connection.execute(sa.text("INSERT INTO analytics_taxonomy_selection_record "
                "(analytics_taxonomy_selection_id, portfolio_id, taxonomy_id, selection_version, created_at) "
                "VALUES (:selection, :id, :taxonomy, :version, :now)"),
                {"selection": selection_id, "id": portfolio_id, "taxonomy": selected_id,
                 "version": next_version, "now": now})
        connection.execute(sa.text("UPDATE portfolio_record SET default_planning_taxonomy_id = :taxonomy "
            "WHERE portfolio_id = :id"), {"id": portfolio_id, "taxonomy": selected_id})
        if current_version is None:
            connection.execute(sa.text("INSERT INTO portfolio_analytics_policy_state "
                "(portfolio_id, current_version, updated_at) VALUES (:id, :version, :now)"),
                {"id": portfolio_id, "version": next_version, "now": now})
        else:
            connection.execute(sa.text("UPDATE portfolio_analytics_policy_state SET "
                "current_version = :version, updated_at = :now WHERE portfolio_id = :id"),
                {"id": portfolio_id, "version": next_version, "now": now})
        # Current membership/policy changes historical attribution and risk.
        # Upgrade runs with writers stopped; the release rebuild completes this
        # invalidation before serving the new code. Raw facts and runs are intact.
        values = {"id": portfolio_id, "request": str(uuid4())}
        result = connection.execute(sa.text("UPDATE portfolio_calculation_state SET "
            "daily_snapshot_status = 'stale', dirty_from = NULL, refresh_request_id = :request, "
            "error_message = NULL WHERE portfolio_id = :id"), values)
        if result.rowcount == 0:
            connection.execute(sa.text("INSERT INTO portfolio_calculation_state "
                "(portfolio_id, daily_snapshot_status, dirty_from, refresh_request_id) "
                "VALUES (:id, 'stale', NULL, :request)"), values)


def downgrade():
    raise RuntimeError("Current taxonomy has no effective-date history to reconstruct; restore the pre-migration backup.")
