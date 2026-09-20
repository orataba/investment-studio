"""The indexed retained-run scope; original research JSON remains authoritative."""
from sqlalchemy import Text, func
from sqlalchemy.dialects.postgresql import ARRAY


JSON_NUL_ESCAPE = r"(?<!\\)((?:\\\\)*)\\u0000"
RESEARCH_SCOPE_INDEX = "ix_research_entry_instrument_scope"

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
