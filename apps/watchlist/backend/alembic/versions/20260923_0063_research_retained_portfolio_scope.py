"""Index retained portfolio boundaries without rewriting research originals."""
from alembic import op
import sqlalchemy as sa

revision = "20260923_0063"
down_revision = "20260923_0062"
branch_labels = None
depends_on = None

INDEX = "ix_research_entry_retained_portfolio_topic"
# Frozen here: changing this predicate requires rebuilding its partial index.
FUNCTION_DDL = r"""
CREATE OR REPLACE FUNCTION watchlist.research_entry_has_retained_portfolio_scope(payload json)
RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $scope$
WITH normalized AS MATERIALIZED (
  SELECT CASE WHEN CASE WHEN strpos(payload::text, '\u0000') > 0
                        THEN payload::text ~ '(?<!\\)((?:\\\\)*)\\u0000' ELSE false END
              THEN regexp_replace(payload::text, '(?<!\\)((?:\\\\)*)\\u0000', '\1�', 'g')::json
              ELSE payload END AS value
), fields AS MATERIALIZED (
  SELECT json_typeof(value) AS value_type, value->'portfolio_id' AS portfolio,
         value->'risk_scope' AS risk
  FROM normalized
)
SELECT CASE
  WHEN value_type IS DISTINCT FROM 'object' THEN true
  WHEN portfolio IS NOT NULL AND json_typeof(portfolio) <> 'null'
       AND portfolio::text <> '""' THEN true
  WHEN risk IS NULL OR json_typeof(risk) = 'null' THEN false
  WHEN json_typeof(risk) <> 'object' THEN true
  ELSE coalesce(json_typeof(risk->'portfolio_id') <> 'null'
                AND (risk->'portfolio_id')::text <> '""', false)
END
FROM fields
$scope$
"""


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(FUNCTION_DDL)
        op.create_index(INDEX, "research_entry", ["topic_id"], postgresql_where=sa.text(
            "watchlist.research_entry_has_retained_portfolio_scope(context_json)"))


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index(INDEX, table_name="research_entry")
        op.execute("DROP FUNCTION watchlist.research_entry_has_retained_portfolio_scope(json)")
