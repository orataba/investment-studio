"""Index retained research scope before reading large evidence snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "20260920_0060"
down_revision = "20260920_0059"
branch_labels = None
depends_on = None

INDEX = "ix_research_entry_instrument_scope"
# Frozen here: later runtime changes must not alter this historical migration.
FUNCTION_DDL = r"""
CREATE OR REPLACE FUNCTION watchlist.research_entry_instrument_scope(payload json)
RETURNS text[] LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $scope$
WITH retained AS MATERIALIZED (
  SELECT (CASE WHEN CASE WHEN strpos(payload::text, '\u0000') > 0
                        THEN payload::text ~ '(?<!\\)((?:\\\\)*)\\u0000' ELSE false END
          THEN regexp_replace(payload::text, '(?<!\\)((?:\\\\)*)\\u0000', '\1�', 'g')::json
          ELSE payload END)->'instrument_ids' AS ids
)
SELECT CASE WHEN json_typeof(ids) = 'array'
            THEN ARRAY(SELECT value FROM json_array_elements_text(ids))
            ELSE ARRAY[]::text[] END
FROM retained
$scope$
"""


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(FUNCTION_DDL)
        op.create_index(INDEX, "research_entry",
                        [sa.text("watchlist.research_entry_instrument_scope(context_json)")], postgresql_using="gin")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index(INDEX, table_name="research_entry")
        op.execute("DROP FUNCTION watchlist.research_entry_instrument_scope(json)")
