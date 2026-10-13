"""Display fields selected before decoding retained calculation originals.

This is a browser projection, not an evidence original or a model input. The
same paths govern inline and SQL-backed sources; input trees stay at their
existing original-source endpoint.
"""
from copy import deepcopy

from sqlalchemy import JSON, case, func, select, true, type_coerce

from watchlist_app.services.research_read_projection import SOURCE_INDEX_FIELDS

TOP_FIELDS = tuple(sorted(SOURCE_INDEX_FIELDS | {"scope", "observation_domain", "retained_reference"}))
FIGURE_TOP_FIELDS = ("current_snapshot", "previous_snapshot", "changes", "coverage", "gaps", "gap_codes", "semantics", "comparison_basis")
DATA_FIELDS = ("analysis_kind", "dataset", "status", "summary", "metrics", "tables", "charts", "history",
    "current", "previous", "five_sessions_ago", "change_pp", "change_pct", "five_session_change_pp",
    "historical_reference", "rows", "comparisons", "available", "sample_return_pct", "sample_start", "sample_end",
    "currency", "frequency", "return_kind", "quote_basis", "method", "limitations", "coverage", "gaps",
    "period", "window", "requested_start", "effective_end", "common_observation_count", "aligned_changes",
    "method_version", "observation_key", "as_of_date", "information_cutoff_at", "benchmark_id")
METHOD_FIELDS = ("metric", "field", "analysis_kind", "description", "method_version", "version", "operation",
    "half_life_sessions", "minimum_returns", "maximum_returns", "annualization", "return_basis", "variance",
    "definition", "quote_basis", "return_kind", "return_measure", "calendar", "relative_return", "breadth",
    "correlation", "holdings", "weight_precision", "input_version_id", "input_information_cutoff", "executed_at",
    "result_status", "alignment", "change", "relative_change")
SERIES_FIELDS = ("series_id", "dataset", "field", "unit", "currency", "frequency", "status", "observations",
    "missing_values", "comparable_units", "first", "latest", "previous", "change", "change_unit",
    "relative_change_pct", "minimum", "maximum")
SNAPSHOT_FIELDS = ("top_holdings", "holdings_as_of", "holdings_observed_on")


def _paths(projection):
    paths = [(key,) for key in TOP_FIELDS]
    paths += [("data", key) for key in (DATA_FIELDS if projection == "figure" else ("analysis_kind", "dataset"))]
    paths += [("methodology", key) for key in (METHOD_FIELDS if projection == "figure" else ("metric", "field"))]
    if projection == "figure":
        paths += [(key,) for key in FIGURE_TOP_FIELDS]
        paths += [("snapshot", key) for key in SNAPSHOT_FIELDS]
        paths += [("data", "observations", "volume", key, "date") for key in ("current", "previous")]
    return paths


def _at(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _put(result, path, value):
    if value is None:
        return
    for key in path[:-1]:
        result = result.setdefault(key, {})
    result[path[-1]] = value


def _finish(result, projection):
    from watchlist_app.services.research_metrics import observation_domain
    result["observation_domain"] = observation_domain(result)
    if projection == "figure":
        result.pop("input_series", None)  # Classification marker, never an input.
        data = result.get("data")
        if isinstance(data, dict) and isinstance(data.get("observations"), dict):
            volume = data.pop("observations").get("volume", {})
            clock = {f"{key}_date": row["date"] for key, row in volume.items() if isinstance(row, dict) and row.get("date")}
            if clock:
                data["observation_clocks"] = {"volume": clock}
        result["display_projection"] = "figure"
    return result


def project_source(source, projection):
    """Apply the same display contract to an already loaded inline original."""
    result = {}
    for path in _paths(projection):
        _put(result, path, deepcopy(_at(source, path)))
    if "data" in source:
        result.setdefault("data", {})  # Distinguishes an empty original from a locator.
    if "input_series" in source:
        result["input_series"] = []
    if projection == "figure":
        if isinstance(source.get("methodology"), str):
            result["methodology"] = source["methodology"]
        data = source.get("data") or {}
        if isinstance(data.get("observations"), (int, float)):
            result.setdefault("data", {})["observations"] = data["observations"]
        if isinstance(data.get("series"), list):
            result.setdefault("data", {})["series"] = [
                {key: deepcopy(row[key]) for key in SERIES_FIELDS if key in row}
                for row in data["series"] if isinstance(row, dict)]
    return _finish(result, projection)


def sql_projection(session, value, projection):
    """Share PostgreSQL object field extraction across display columns."""
    postgres = session.get_bind().dialect.name == "postgresql"
    kind = func.json_typeof if postgres else func.json_type
    paths = _paths(projection)
    joins, selected = [], {}
    if postgres:
        from watchlist_app.services.research_access import postgres_record_projection
        required = [*paths, ("data",), ("methodology",)]
        if projection == "figure":
            required += [("data", "observations"), ("data", "series")]
        parents = sorted({path[:depth] for path in required for depth in range(len(path))}, key=lambda path: (len(path), path))
        for index, parent in enumerate(parents):
            fields = dict.fromkeys((path[len(parent)] for path in required
                if len(path) > len(parent) and path[:len(parent)] == parent), JSON)
            node = selected[parent] if parent else value
            # Missing/null/scalar containers have no object fields. In particular,
            # a string methodology remains available separately as its original.
            if parent:
                node = case((kind(node) == "object", node))
            relation, columns = postgres_record_projection(node, fields, name=f"figure_object_{index}")
            joins.append(relation)
            selected.update({(*parent, key): column for key, column in columns.items()})

    def at(path):
        if postgres:
            return selected[path]
        item = value
        for key in path:
            item = item[key]
        return item

    columns = []
    for index, path in enumerate(paths):
        columns.append(at(path).label(f"projection_{index}"))
    # json_to_record maps both an absent key and JSON null to SQL NULL. Keep the
    # two key-presence checks so empty originals/classification markers stay exact.
    columns += [(kind(value["data"]) if postgres else kind(value, "$.data")).is_not(None).label("projection_has_data"),
                (kind(value["input_series"]) if postgres else kind(value, "$.input_series")).is_not(None).label("projection_has_series")]
    if projection == "figure":
        method = at(("methodology",))
        columns.append(type_coerce(case((kind(method).in_(("string", "text")), method)), JSON).label("projection_method"))
        observations = at(("data", "observations"))
        columns.append(type_coerce(case((kind(observations).in_(("number", "integer", "real")), observations)), JSON).label("projection_count"))
        # SQL strips numeric-series input points before crossing the DB boundary.
        series = at(("data", "series"))
        array = case((kind(series) == "array", series))
        rows = (func.json_array_elements(array) if postgres else func.json_each(array)).table_valued("value").alias("figure_series")
        row = type_coerce(rows.c.value, JSON)
        if postgres:
            summary_row, values = postgres_record_projection(case((kind(row) == "object", row)),
                dict.fromkeys(SERIES_FIELDS, JSON), name="figure_series_fields")
            relation = rows.join(summary_row, true())
        else:
            relation, values = rows, {key: row[key] for key in SERIES_FIELDS}
        pairs = [part for key in SERIES_FIELDS for part in (key, values[key])]
        summary = func.json_build_object(*pairs) if postgres else func.json_object(*pairs)
        aggregate = func.json_agg(summary) if postgres else func.json_group_array(summary)
        columns.append(type_coerce(select(aggregate).select_from(relation).correlate_except(relation).scalar_subquery(), JSON).label("projection_series"))
    return joins, columns


def from_sql_row(row, projection):
    result = {}
    for index, path in enumerate(_paths(projection)):
        _put(result, path, row[f"projection_{index}"])
    if row["projection_has_data"]:
        result.setdefault("data", {})
    if row["projection_has_series"]:
        result["input_series"] = []
    if projection == "figure":
        if row["projection_method"] is not None:
            result["methodology"] = row["projection_method"]
        if row["projection_count"] is not None:
            result.setdefault("data", {})["observations"] = row["projection_count"]
        if row["projection_series"]:
            result.setdefault("data", {})["series"] = row["projection_series"]
    return _finish(result, projection)
