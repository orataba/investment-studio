"""Retire peer fields whose observation windows are not comparable."""
from alembic import op
import sqlalchemy as sa

revision = "20261002_0064"
down_revision = "20260923_0063"
branch_labels = None
depends_on = None

FIELDS = (
    "attr.peer_annualized_return_percentile",
    "attr.peer_volatility_percentile",
    "attr.peer_max_drawdown_percentile",
    "attr.peer_sharpe_percentile",
    "attr.peer_calmar_percentile",
)


def upgrade():
    table = sa.table("field_registry", sa.column("field_key", sa.String()))
    op.get_bind().execute(table.delete().where(table.c.field_key.in_(FIELDS)))
    # Saved filters are retained so a retired criterion produces an explicit
    # unknown-field error, never a silently broadened investment screen.


def downgrade():
    from watchlist_migration_snapshots.watchlist_fields import FIELD_REGISTRY

    table = sa.Table("field_registry", sa.MetaData(), autoload_with=op.get_bind())
    rows = [dict(row) for row in FIELD_REGISTRY if row["field_key"] in FIELDS]
    labels = {"attr.peer_annualized_return_percentile": "Ann. Pctl",
              "attr.peer_max_drawdown_percentile": "Max DD Pctl"}
    for row in rows:
        row["label"] = labels.get(row["field_key"], row["label"])
    op.bulk_insert(table, rows)
