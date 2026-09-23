"""The indexed retained-run scope; original research JSON remains authoritative."""
from sqlalchemy import Boolean, Text, func
from sqlalchemy.dialects.postgresql import ARRAY


JSON_NUL_ESCAPE = r"(?<!\\)((?:\\\\)*)\\u0000"
RESEARCH_SCOPE_INDEX = "ix_research_entry_instrument_scope"
RESEARCH_PORTFOLIO_SCOPE_INDEX = "ix_research_entry_retained_portfolio_topic"

# Migration 0060 owns this immutable extractor. Changing its semantics requires a
# migration that rebuilds the dependent index, not CREATE OR REPLACE alone.
RESEARCH_SCOPE_FUNCTION_DDL = rf"""
CREATE OR REPLACE FUNCTION watchlist.research_entry_instrument_scope(payload json)
RETURNS text[] LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $scope$
WITH retained AS MATERIALIZED (
  SELECT (CASE WHEN CASE WHEN strpos(payload::text, '\u0000') > 0
                        THEN payload::text ~ '{JSON_NUL_ESCAPE}' ELSE false END
          THEN regexp_replace(payload::text, '{JSON_NUL_ESCAPE}', '\1�', 'g')::json
          ELSE payload END)->'instrument_ids' AS ids
)
SELECT CASE WHEN json_typeof(ids) = 'array'
            THEN ARRAY(SELECT value FROM json_array_elements_text(ids))
            ELSE ARRAY[]::text[] END
FROM retained
$scope$
"""


def research_scope_expression(context_column):
    # A function identity, rather than parameterized JSON/regexp expressions,
    # keeps both custom and generic prepared plans matched to the same GIN index.
    return func.watchlist.research_entry_instrument_scope(context_column, type_=ARRAY(Text()))


# Migration 0063 freezes this predicate. It is a conservative index filter:
# exact permission IDs still come from the untouched context through the reader.
RESEARCH_PORTFOLIO_SCOPE_FUNCTION_DDL = rf"""
CREATE OR REPLACE FUNCTION watchlist.research_entry_has_retained_portfolio_scope(payload json)
RETURNS boolean LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $scope$
WITH normalized AS MATERIALIZED (
  SELECT CASE WHEN CASE WHEN strpos(payload::text, '\u0000') > 0
                        THEN payload::text ~ '{JSON_NUL_ESCAPE}' ELSE false END
              THEN regexp_replace(payload::text, '{JSON_NUL_ESCAPE}', '\1�', 'g')::json
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


def research_portfolio_scope_expression(context_column):
    return func.watchlist.research_entry_has_retained_portfolio_scope(context_column, type_=Boolean())
