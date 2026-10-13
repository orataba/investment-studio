"""Run-bound calculation originals and selective retained-source reads.

The enclosing run/dossier authorizes a stored reference. A locator supplied by a
caller is never an authorization grant; endpoints first resolve IDs in that run.
"""
from datetime import UTC, datetime
from copy import deepcopy
import json

from fastapi import HTTPException
from sqlalchemy import JSON, case, cast, literal, select, true, type_coerce, func

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.models.research_run_evidence import ResearchRunEvidence
from watchlist_app.services.research_read_projection import source_index

REFERENCE_KEY = "retained_reference"
INDEX_KEYS = ("source_id", "source_type", "title", "instrument_id", "scope", "as_of", "run_cutoff",
              "published_at", "retrieved_at", "source_run_id", "role", "time_status", "observation_domain", "analysis_kind", "provider")
SOURCE_PATHS = (
    ("computed_metrics", "*"), ("financial_sources", "*"), ("market_text_sources", "*"),
    ("web_evidence", "*", "sources", "*"),
    ("research_dossiers", "*", "prior_sources", "*"),
    ("research_dossiers", "*", "notebook", "sources", "*"),
    ("research_dossiers", "*", "themes", "*", "sources", "*"),
    ("research_dossiers", "*", "pm_views", "*", "sources", "*"),
    ("research_dossiers", "*", "materials", "*"),
    ("research_dossiers", "*", "historical_cases", "*"), ("sector_estimate_evidence", "*"),
)


def locked_computation_run(session, run_id):
    """Fence the attempt under a row lock without materializing the raw context."""
    session.execute(select(ResearchEntry.entry_id).where(ResearchEntry.entry_id == run_id).with_for_update()).first()
    from watchlist_app.services.research_run_context import load_run_fields
    from watchlist_app.services.research_access import require_entry_access
    run = load_run_fields(session, run_id, {"risk_run", "sector_run", "research_run", "cutoff",
        "input_snapshot_cutoff", "instrument_ids"})
    require_entry_access(session, run, tool_write=True)
    if run.status not in {"queued", "running"}:
        raise HTTPException(409, "本轮研究已经结束")
    if run.context_json.get("risk_run"):
        raise HTTPException(422, "风控研判使用已绑定的风险快照")
    return run


def _array_query(session, run_id, path, *, source_id=None, source_ids=None, metadata_only=False, metadata_keys=INDEX_KEYS, filters=(), filters_at=(), projection=None):
    """Project allowed containers in SQL, including legacy originals, not their enclosing tree."""
    from watchlist_app.services.research_access import _postgres_projection_context
    postgres = session.get_bind().dialect.name == "postgresql"
    affected, value = _postgres_projection_context() if postgres else (None, ResearchEntry.context_json)
    joins = []
    conditions = []
    for index, component in enumerate(path):
        if component == "*":
            array = case((func.json_typeof(value) == "array", value), else_=cast(literal("[]"), JSON)) if postgres else value
            rows = (func.json_array_elements(array) if postgres else func.json_each(array)).table_valued("value")
            rows = rows.lateral(f"evidence_{index}") if postgres else rows.alias(f"evidence_{index}")
            joins.append(rows)
            value = type_coerce(rows.c.value, JSON)
        else:
            value = value[component]
        for depth, key, expected in filters_at:
            if depth == index:
                conditions.append((value[key].as_integer() if type(expected) is int else value[key].as_string()) == expected)
    columns = [value.label("source")]
    if metadata_only:
        columns = [value[key].label(key) for key in metadata_keys]
    elif projection is not None:
        from watchlist_app.services.research_figure_projection import sql_columns
        columns = sql_columns(session, value, projection)
    query = select(*columns).select_from(ResearchEntry)
    for rows in joins:
        query = query.join(rows, true())
    query = query.where(ResearchEntry.entry_id == run_id, *conditions)
    if source_id is not None:
        query = query.where(value["source_id"].as_string() == source_id)
    if source_ids is not None:
        query = query.where(value["source_id"].as_string().in_(source_ids))
    for key, expected in filters:
        column = value[key].as_string()
        query = query.where(column.in_(expected) if isinstance(expected, (list, tuple, set)) else column == expected)
    return query


def _rows(session, run_id, path, *, source_id=None, source_ids=None, metadata_only=False, metadata_keys=INDEX_KEYS, filters_at=(), filters=(), projection=None):
    query = _array_query(session, run_id, path, source_id=source_id, source_ids=source_ids, metadata_only=metadata_only, metadata_keys=metadata_keys, filters_at=filters_at, filters=filters, projection=projection)
    postgres = session.get_bind().dialect.name == "postgresql"
    if postgres:
        from watchlist_app.services.research_access import _postgres_projection_context
        affected, _ = _postgres_projection_context()
        query = query.add_columns(affected.label("_has_json_nul"))
    rows = session.execute(query).all()
    # SQL extracts healthy selected values directly. NUL in a selected old
    # original needs the unchanged legacy JSON, never the sanitized SQL copy.
    if postgres and any(row._mapping["_has_json_nul"] and "\ufffd" in json.dumps(
            {key: value for key, value in row._mapping.items() if key != "_has_json_nul"}, ensure_ascii=False) for row in rows):
        original = session.scalar(select(ResearchEntry.context_json).where(ResearchEntry.entry_id == run_id))
        values = [original]
        for index, component in enumerate(path):
            values = [item for value in values for item in (value if component == "*" and isinstance(value, list)
                else [value.get(component)] if isinstance(value, dict) else []) if item is not None]
            for depth, key, expected in filters_at:
                if depth == index:
                    values = [value for value in values if isinstance(value, dict) and value.get(key) == expected]
        values = [value for value in values if isinstance(value, dict) and (source_id is None or value.get("source_id") == source_id)]
        if source_ids is not None:
            values = [value for value in values if value.get("source_id") in source_ids]
        values = [value for value in values if all(value.get(key) in expected if isinstance(expected, (list, tuple, set))
                  else value.get(key) == expected for key, expected in filters)]
        if projection is not None:
            from watchlist_app.services.research_figure_projection import project_source
            return [project_source(value, projection) for value in values]
        return [{key: value[key] for key in metadata_keys if key in value} for value in values] if metadata_only else values
    if projection is not None:
        from watchlist_app.services.research_figure_projection import from_sql_row
        return [from_sql_row(row._mapping, projection) for row in rows]
    if metadata_only:
        return [{key: value for key, value in row._mapping.items() if key != "_has_json_nul" and value is not None} for row in rows]
    return [row.source for row in rows if isinstance(row.source, dict)]


def computed_reference(source, *, run_id=None, path=None, version_id=None):
    """A derived directory entry; its original is immutable at the named locator."""
    if source.get("source_type") != "computed_metric":
        return source
    locator = source.get(REFERENCE_KEY)
    if locator is None and run_id is not None:
        locator = {"run_id": run_id, "path": list(path or ["computed_metrics", "*"])}
        if version_id is not None:
            locator["version_id"] = version_id
    if locator is None:
        return source  # A source without a verified storage location is not relabelled.
    summary = {key: value for key, value in (source.get("data") or {}).items()
               if value is None or isinstance(value, (str, bool, int, float))}
    from watchlist_app.services.research_metrics import observation_domain
    domain = observation_domain(source)
    return {**source_index(source), **{key: source[key] for key in ("scope", "observation_domain") if key in source},
            **({"observation_domain": domain} if domain != "unclassified" else {}),
            **({"data_summary": summary} if summary else {}),
            REFERENCE_KEY: deepcopy(locator)}


def retain_computed_source(session, run, source):
    """Append one original; never update the unrelated run context or earlier sources."""
    from watchlist_app.services.research_access import require_entry_access, run_attempt
    require_entry_access(session, run, tool_write=True)
    if run.status not in {"queued", "running"}:
        raise HTTPException(409, "本轮研究已经结束")
    if source.get("source_type") != "computed_metric" or not source.get("source_id"):
        raise ValueError("计算原件缺少合法来源标识")
    value = {**source, "source_run_id": run.entry_id}
    value.pop(REFERENCE_KEY, None)
    # The source_id identifies one immutable output, not an upsert key.
    if session.get(ResearchRunEvidence, (run.entry_id, value["source_id"])) is not None:
        raise ValueError("计算来源标识已存在，不能覆盖原件")
    index = {key: value[key] for key in INDEX_KEYS if key in value}
    from watchlist_app.services.research_metrics import observation_domain
    domain = observation_domain(value)
    if domain != "unclassified":
        index["observation_domain"] = domain
    index["data"] = {"analysis_kind": (value.get("data") or {}).get("analysis_kind")}
    if "input_series" in value:
        index.update(input_series=[], request=value.get("request"), retrieved_at=value.get("retrieved_at"))
    session.add(ResearchRunEvidence(run_id=run.entry_id, source_id=value["source_id"],
        attempt=run_attempt(run), source_json=value, index_json=index, recorded_at=datetime.now(UTC)))
    session.flush()
    return {**value, REFERENCE_KEY: {"run_id": run.entry_id, "path": ["computed_metrics", "*"]}}


def retained_computed_sources(session, run_id, *, metadata_only=False, source_ids=None, projection=None):
    """New originals only; old arrays remain at their original, separately addressable location."""
    column = ResearchRunEvidence.index_json if metadata_only else ResearchRunEvidence.source_json
    columns = [column]
    affected = None
    if projection is not None:
        from watchlist_app.services.research_figure_projection import sql_columns
        value = ResearchRunEvidence.source_json
        if session.get_bind().dialect.name == "postgresql":
            from watchlist_app.services.research_access import _postgres_projection_context
            affected, value = _postgres_projection_context(value)
        columns = sql_columns(session, value, projection)
        if affected is not None:
            columns.append(affected.label("_has_json_nul"))
    query = select(*columns).where(ResearchRunEvidence.run_id == run_id).order_by(ResearchRunEvidence.recorded_at, ResearchRunEvidence.source_id)
    if source_ids is not None:
        query = query.where(ResearchRunEvidence.source_id.in_(source_ids))
    if projection is not None:
        from watchlist_app.services.research_figure_projection import from_sql_row, project_source
        sources = []
        for row in session.execute(query):
            source = from_sql_row(row._mapping, projection)
            if affected is not None and row._mapping["_has_json_nul"] and "\ufffd" in json.dumps(source, ensure_ascii=False):
                # As with legacy JSON, selected NUL-bearing values need the exact
                # untouched original. NUL elsewhere never hydrates its input tree.
                original = session.scalar(select(ResearchRunEvidence.source_json).where(
                    ResearchRunEvidence.run_id == run_id, ResearchRunEvidence.source_id == source["source_id"]))
                source = project_source(original, projection)
            sources.append(source)
    else:
        sources = session.scalars(query)
    return [{**source, REFERENCE_KEY: {"run_id": run_id, "path": ["computed_metrics", "*"]}} for source in sources]


def read_computed_source(session, run_id, source_id, *, projection=None):
    values = retained_computed_sources(session, run_id, source_ids=[source_id], projection=projection)
    if not values:
        values = _rows(session, run_id, ("computed_metrics", "*"), source_id=source_id, projection=projection)
    if not values:
        return None
    source = values[0]
    return {**source, REFERENCE_KEY: source.get(REFERENCE_KEY) or {"run_id": run_id, "path": ["computed_metrics", "*"]}}


def resolve_reference(reference, *, session=None, bound_source=False, projection=None, _seen=frozenset()):
    """Resolve only a reference already retained by an authorized containing record."""
    if projection not in {None, "classification", "figure"}:
        raise ValueError("不支持的来源显示投影")
    locator = reference.get(REFERENCE_KEY)
    if locator is None:
        if projection is not None:
            from watchlist_app.services.research_figure_projection import project_source
            return project_source(reference, projection)
        return reference
    if session is None:
        from watchlist_app.db.session import get_session_factory
        with get_session_factory()() as local:
            return resolve_reference(reference, session=local, bound_source=bound_source, projection=projection, _seen=_seen)
    run_id, path = locator["run_id"], tuple(locator["path"])
    from studio_identity import current_principal
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services.research_access import require_topic_access
    owner = session.execute(select(ResearchEntry.topic_id, ResearchEntry.team_id, ResearchEntry.kind)
                            .where(ResearchEntry.entry_id == run_id)).first()
    principal = current_principal()
    if owner is None or owner.kind != "analysis" or (not principal.local_unrestricted and owner.team_id != principal.team_id):
        raise ValueError("计算原件不在当前可访问的研究范围")
    topic = session.get(ResearchTopic, owner.topic_id)
    if bound_source:
        # The authorized enclosing record has already shared this exact source,
        # as an embedded original did before retention was split. It does not
        # grant access to any other original in the private conversation.
        from watchlist_app.services.research_access import topic_portfolio_ids, require_portfolio
        if topic is None:
            raise ValueError("计算原件的对话不存在")
        if not principal.local_unrestricted:
            for portfolio_id in sorted(topic_portfolio_ids(session, topic)):
                require_portfolio(portfolio_id)
    else:
        require_topic_access(session, topic, principal=principal)
    allowed = path == ("computed_metrics", "*") or (
        len(path) == 5 and path[0] == "reviews" and path[2:] == ("research", "sources", "*"))
    historical = path == ("referenced_research_versions", "*", "sources", "*") and bool(locator.get("version_id"))
    if not allowed and not historical:
        raise ValueError("计算原件引用不是受支持的来源位置")
    identity = (run_id, path, locator.get("version_id"), reference.get("source_id"))
    if identity in _seen:
        raise ValueError("计算原件引用形成循环，不能当作已取得的数据")
    if path == ("computed_metrics", "*"):
        source = read_computed_source(session, run_id, reference["source_id"], projection=projection)
    else:
        filters = ((1, "version_id", locator["version_id"]),) if historical else ()
        values = _rows(session, run_id, path, source_id=reference["source_id"], filters_at=filters, projection=projection)
        source = values[0] if values else None
    if source is None:
        raise ValueError("已留存的计算原件不存在，不能用当前数据替代")
    if source.get("source_id") != reference.get("source_id") or source.get("source_type") != "computed_metric":
        raise ValueError("计算原件身份不一致")
    if reference.get("instrument_id") != source.get("instrument_id"):
        raise ValueError("计算原件的标的归属不一致")
    if "data" not in source and source.get(REFERENCE_KEY):
        return resolve_reference(source, session=session, bound_source=bound_source, projection=projection, _seen=_seen | {identity})
    return {**source, REFERENCE_KEY: deepcopy(locator)}


def computed_evidence_receipt(source):
    """Describe an actual retained tool result without embedding its original."""
    if source.get("source_type") != "computed_metric":
        return None
    tool = "quant" if (source.get("data") or {}).get("analysis_kind") == "python_quant" else (
        "comparison" if "input_series" in source else None)
    if tool is None:
        return None
    return {"source_id": source["source_id"], "tool": tool, "request": source.get("request"),
        "retrieved_at": source.get("retrieved_at"), "source_ids": [source["source_id"]],
        "result_storage": "computed_metrics"}


def attach_computed_sources(session, run_id, context, *, metadata_only=False):
    """A read view only. Callers must never assign this hydrated view to context_json."""
    additions = retained_computed_sources(session, run_id, metadata_only=metadata_only)
    if not additions:
        return context
    recorded = {receipt.get("source_id") for receipt in context.get("tool_evidence", [])}
    receipts = [receipt for source in additions if source["source_id"] not in recorded
        if (receipt := computed_evidence_receipt(source)) is not None]
    return {**context, "computed_metrics": [*context.get("computed_metrics", []), *additions],
            **({"tool_evidence": [*context.get("tool_evidence", []), *receipts]} if receipts else {})}


def bound_input(session, run_id, instrument_id):
    values = _rows(session, run_id, ("instrument_inputs", "*"), filters=(("instrument_id", instrument_id),))
    return next((value for value in values if isinstance(value, dict)), None)


def bound_input_cutoff(session, run_id, instrument_id):
    query = _array_query(session, run_id, ("instrument_inputs", "*"), filters=(("instrument_id", instrument_id),))
    return session.scalar(query.with_only_columns(query.selected_columns.source["snapshot_cutoff"].as_string()))


def _scope_matches(source, instrument_id, cutoff):
    if source.get("role") in {"research_method", "historical_research", "derived_reference"}:
        return False
    kind = source.get("source_type")
    if kind == "computed_metric":
        if source.get("instrument_id") != instrument_id and source.get("scope") != "public_market":
            return False
    elif source.get("instrument_id") not in {None, instrument_id}:
        return False
    if source.get("time_status") == "future":
        return False
    stamp = source.get("as_of") if kind == "computed_metric" else source.get("published_at")
    if cutoff and stamp:
        try:
            observed = datetime.fromisoformat(stamp)
            horizon = datetime.fromisoformat(cutoff)
            if len(stamp) == 10:
                return observed.date() <= horizon.date()
            return observed <= horizon
        except (TypeError, ValueError):
            return kind != "computed_metric"
    return True


def _version_cutoff(session, run_id, instrument_id, version_id):
    # Select only the explicitly bound version; never merge its namespace with now.
    query = _array_query(session, run_id, ("referenced_research_versions", "*"),
        filters=(("version_id", version_id), ("instrument_id", instrument_id)))
    values = session.scalars(query.with_only_columns(query.selected_columns.source["information_cutoff"].as_string())).all()
    if len(values) != 1:
        raise ValueError("该历史版本未在本轮上下文中明确绑定，请从原研究版本发起追问")
    return values[0]


def _historical_sources(session, run_id, instrument_id, version_id, *, source_id=None, metadata_only=False):
    return _rows(session, run_id, ("referenced_research_versions", "*", "sources", "*"),
        source_id=source_id, metadata_only=metadata_only,
        filters_at=((1, "version_id", version_id), (1, "instrument_id", instrument_id)))


def _snapshot_source(session, run, instrument_id, source_id):
    from watchlist_app.services.research_notebook import company_source, research_sources
    for prefix, field in (("instrument", "instrument_inputs"), ("sector", "sector_inputs")):
        if source_id == f"{prefix}:{run.entry_id}:{instrument_id}":
            rows = _rows(session, run.entry_id, (field, "*"), filters=(("instrument_id", instrument_id),))
            if not rows:
                return None
            asset = rows[0]
            if prefix == "instrument":
                asset = {"name": instrument_id, **asset}
            return research_sources({"cutoff": run.context_json["cutoff"], field: [asset]}, run.entry_id).get(source_id)
    prefix = f"fmp:{run.entry_id}:{instrument_id}:"
    if source_id.startswith(prefix):
        symbol = source_id[len(prefix):]
        rows = _rows(session, run.entry_id, ("sector_company_data", instrument_id, symbol))
        return company_source(run.entry_id, instrument_id, symbol, rows[0]) if rows else None
    return None


def _snapshot_catalogue(session, run, instrument_id):
    # Read identity fields only. Company keys enumerate captured company objects,
    # not the multi-company bodies retained with the old run.
    result = []
    for prefix, field in (("instrument", "instrument_inputs"), ("sector", "sector_inputs")):
        query = _array_query(session, run.entry_id, (field, "*"), filters=(("instrument_id", instrument_id),))
        value = query.selected_columns.source
        rows = session.execute(query.with_only_columns(value["name"].as_string(), value["snapshot_cutoff"].as_string())).all()
        if rows:
            result.append({"source_id": f"{prefix}:{run.entry_id}:{instrument_id}", "instrument_id": instrument_id,
                "source_type": f"{prefix}_snapshot", "source_run_id": run.entry_id,
                "title": f"{rows[0][0] or instrument_id} · 已留存快照", "run_cutoff": rows[0][1] or run.context_json["cutoff"]})
    from watchlist_app.services.research_access import _postgres_projection_context
    if session.get_bind().dialect.name == "postgresql":
        _, context = _postgres_projection_context()
        query = select(func.json_object_keys(context["sector_company_data"][instrument_id])).where(ResearchEntry.entry_id == run.entry_id)
    else:
        rows = func.json_each(ResearchEntry.context_json["sector_company_data"][instrument_id]).table_valued("key")
        query = select(rows.c.key).select_from(ResearchEntry).join(rows, true()).where(ResearchEntry.entry_id == run.entry_id)
    for symbol in session.scalars(query):
        if symbol is not None:
            result.append({"source_id": f"fmp:{run.entry_id}:{instrument_id}:{symbol}", "instrument_id": instrument_id,
                "source_type": "company_snapshot", "source_run_id": run.entry_id, "title": f"FMP · {symbol} 公司资料与预期"})
    return result


def quant_source_catalogue(session, run, instrument_id, version_id=None):
    if instrument_id not in run.context_json.get("instrument_ids", []):
        raise ValueError("量化标的不在本轮已绑定的研究范围")
    if version_id:
        cutoff = _version_cutoff(session, run.entry_id, instrument_id, version_id)
        rows = _historical_sources(session, run.entry_id, instrument_id, version_id, metadata_only=True)
    else:
        rows = [source for path in SOURCE_PATHS for source in _rows(session, run.entry_id, path, metadata_only=True)]
        rows += retained_computed_sources(session, run.entry_id, metadata_only=True)
        rows += _snapshot_catalogue(session, run, instrument_id)
        cutoff = run.context_json["cutoff"]
    by_id = {source["source_id"]: {**source, "version_id": version_id} for source in rows
             if source.get("source_id") and _scope_matches(source, instrument_id, cutoff)}
    return list(by_id.values())


def selected_quant_sources(session, run, instrument_id, source_ids, version_id=None):
    """Hydrate requested identities only; a guessed ID from another run never qualifies."""
    from watchlist_app.services.market_evidence import hydrate_source
    from watchlist_app.services.research_notebook import ResearchNotebook, _original_source, validate_notebook
    if instrument_id not in run.context_json.get("instrument_ids", []):
        raise ValueError("量化标的不在本轮已绑定的研究范围")
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("量化输入重复")
    cutoff = run.context_json["cutoff"]
    if version_id:
        cutoff = _version_cutoff(session, run.entry_id, instrument_id, version_id)
        originals = _historical_sources(session, run.entry_id, instrument_id, version_id, metadata_only=True)
        if len({source["source_id"] for source in originals}) != len(originals):
            raise ValueError("历史版本包含重复的来源标识")
    selected = {}
    for source_id in source_ids:
        values = _historical_sources(session, run.entry_id, instrument_id, version_id, source_id=source_id) if version_id else []
        source = (values[0] if values else None) if version_id else read_computed_source(session, run.entry_id, source_id)
        if source is None and not version_id:
            for path in SOURCE_PATHS[1:]:
                candidates = _rows(session, run.entry_id, path, source_id=source_id)
                if candidates:
                    source = candidates[-1]
                    break
        if source is None and not version_id:
            source = _snapshot_source(session, run, instrument_id, source_id)
        if source is None:
            raise ValueError("量化输入未在本轮留存或不属于本轮授权标的；请先读取量化输入目录")
        # Selecting an ID directly must obey the same role/scope/time contract
        # as the directory. Hydration cannot turn a method or research summary
        # into an eligible original by resolving its underlying reference.
        if not _scope_matches(source, instrument_id, cutoff):
            raise ValueError("量化输入的来源角色、标的范围或时点不符合本轮要求")
        if version_id and source.get("source_type") == "computed_metric" and REFERENCE_KEY not in source:
            source = {**source, REFERENCE_KEY: {"run_id": run.entry_id,
                "path": ["referenced_research_versions", "*", "sources", "*"], "version_id": version_id}}
        source = resolve_reference(source, session=session, bound_source=True)
        source = hydrate_source(source, cutoff=datetime.fromisoformat(cutoff) if cutoff else None, bound_source=True)
        if source.get("source_type") == "instrument_material":
            source = {**source, "source_type": "research_material", "text": source.get("body", "")}
        if not _original_source(source, instrument_id, datetime.fromisoformat(cutoff) if cutoff else None):
            raise ValueError("量化输入没有可用原件、归属不符或晚于本轮截止")
        selected[source_id] = source
    validate_notebook(ResearchNotebook(source_ids=source_ids), instrument_id, selected)
    return selected, cutoff
