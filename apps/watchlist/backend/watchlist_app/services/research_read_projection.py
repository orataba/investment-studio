"""Bound, lossless pages for model-facing research reads; no storage or live reads."""
import json


def json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def checked_overview(value, *, pageable_fields=()):
    # Existing contracts permit a long user question or a large current view.
    # Keep the directory available and explicitly route that complete value to
    # its section, instead of clipping it or making every overview call fail.
    for path, read in pageable_fields:
        if json_bytes(value) <= 48000:
            break
        owner = value
        for key in path[:-1]:
            owner = owner[key]
        item = owner[path[-1]]
        owner[path[-1]] = {"inline": False, **shape(item), "read": read,
                          "note": "完整内容在此分区；概览未展示正文，必须按需读完，不能视为缺失。"}
    if json_bytes(value) > 48000:
        raise ValueError("概览超过工具返回上限；请按section读取当前判断或所需资料。没有截断内容，未读资料不能视为不存在。")
    return value


def shape(value):
    kind = "object" if isinstance(value, dict) else "array" if isinstance(value, list) else "text" if isinstance(value, str) else "value"
    return {"type": kind, "count": len(value) if isinstance(value, (dict, list, str)) else 0 if value is None else 1}


def read_page(value, metadata, *, offset=0, limit=20, path=None):
    """Page arrays/object fields/text; oversized children have explicit read paths.

    Every returned child path is relative to the same selected section/source/version.
    No source is rebound and no JSON value or text suffix is silently discarded.
    """
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("分页offset必须是非负整数。")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("分页limit必须是正整数。")
    path = list(path or [])
    for key in path:
        if isinstance(value, dict) and isinstance(key, str) and key in value:
            value = value[key]
        elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
            value = value[key]
        else:
            raise ValueError("读取路径不在本轮绑定资料中；请使用返回的deferred.path。")
    description = shape(value)
    total = description["count"]
    if offset > total or (description["type"] == "value" and offset):
        raise ValueError("分页超出本轮绑定资料范围。")
    count = min(12000 if isinstance(value, str) else min(limit, 100), total - offset)
    keys = list(value) if isinstance(value, dict) else []

    def packet(data, end, deferred=None):
        return {**metadata, "path": path, "data_type": description["type"], "data": data,
            "offset": offset, "next_offset": end if end < total else None, "total": total,
            "deferred": deferred or [],
            "read_note": "按next_offset读完当前路径；deferred中的每个path还需用同一section/source_id/version_id读取，直到无deferred。数组按offset、对象按字段、文本按字符顺序还原；未读分支不是缺失资料。"}

    while True:
        end = offset + count
        data = ({key: value[key] for key in keys[offset:end]} if isinstance(value, dict)
                else value[offset:end] if isinstance(value, (list, str)) else value)
        result = packet(data, end)
        if json_bytes(result) <= 48000:
            return result
        if count > 1:
            count = max(1, count // 2)
            continue
        if count == 1 and isinstance(value, (dict, list)):
            key = keys[offset] if isinstance(value, dict) else offset
            result = packet({} if isinstance(value, dict) else [], end,
                [{"path": [*path, key], **shape(value[key])}])
            return checked_overview(result)
        raise ValueError("读取元数据超过工具上限，无法完整返回；没有截断资料。")


def source_index(source):
    fields = {"source_id", "source_type", "title", "url", "instrument_id", "instrument_ids", "document_id",
        "version_id", "source_run_id", "run_cutoff", "information_cutoff", "published_at", "occurred_at",
        "observed_at", "received_at", "retrieved_at", "recorded_at", "collected_at", "as_of", "as_of_date",
        "body_sha256", "content_hash", "body_available", "status", "time_status", "provider", "currency", "unit"}
    return {key: item for key, item in source.items() if key in fields}


def run_source_index(context):
    """Only originals actually fetched/read in this run, with immutable versions."""
    originals = [source for capture in context.get("web_evidence") or []
                 if capture.get("operation") == "fetch" for source in capture.get("sources") or []]
    originals.extend(context.get("market_text_sources") or [])
    sources, seen = [], set()
    for source in originals:
        if not (str(source.get("text") or "").strip() or
                (source.get("document_id") and source.get("version_id") and source.get("body_available") is not False)):
            continue
        identity = (source.get("source_id"), source.get("document_id"), source.get("version_id"))
        if any(identity) and identity in seen:
            continue
        seen.add(identity)
        sources.append({**source_index(source), **{key: source[key] for key in
            ("discovered_at", "time_status", "published_at_raw") if key in source}, "body_available": True})
    return sources


def current_view(dossier):
    view = (dossier.get("notebook") or {}).get("investment_view")
    return {key: value for key, value in view.items() if key != "versions"} if view is not None else None


def dossier_sections(dossier):
    notebook = dossier.get("notebook") or {}
    lists = ("modules", "facts", "questions", "catalysts", "forecasts", "forecast_reviews", "lessons")
    pm_views = []
    for note in dossier.get("pm_views", []):
        context = note.get("research_context") or {}
        pm_views.append({**note, "version_id": f"pm:{note['note_id']}:{note['revision_number']}",
            "sources": [source_index(source) for source in note.get("sources", [])],
            "research_context": {**context, **({"sources": [source_index(source) for source in context["sources"]]}
                                               if "sources" in context else {})},
            "source_read": "本观点依据用此PM version_id读取返回的sources；不要用后来档案内同名source_id替换原版本。来源索引不表示原文已读或事实已核实。"})
    return {"investment_view": notebook.get("investment_view"),
        "mandate": dossier.get("mandate"), "frameworks": dossier.get("frameworks", []),
        "research_plan": dossier.get("research_plan"),
        "available_modules": dossier.get("available_modules", []),
        "research_state": {key: value for key, value in notebook.items()
                           if key not in {*lists, "investment_view", "sources"}},
        **{key: notebook.get(key, []) for key in lists},
        "sources": [source_index(source) for source in notebook.get("sources", [])],
        "prior_sources": [source_index(source) for source in dossier.get("prior_sources", [])],
        "materials": dossier.get("materials", []), "historical_cases": dossier.get("historical_cases", []),
        "historical_case_limitations": dossier.get("historical_case_limitations", []),
        "themes": dossier.get("themes", []), "pm_views": pm_views,
        "review_agenda": dossier.get("review_agenda", {}),
        "versions": [{key: row.get(key) for key in ("version_id", "run_id", "created_at", "updated_at", "checked_at")}
                     for row in dossier.get("notebook_history", [])]}


def dossier_overview(dossier):
    notebook = dossier.get("notebook") or {}
    result = {key: dossier.get(key) for key in ("instrument_id", "name", "instrument_type")} | {
        "notebook_version_id": notebook.get("version_id", notebook.get("run_id")),
        "checked_at": notebook.get("checked_at"), "current_investment_view": current_view(dossier),
        "sections": {key: shape(value) for key, value in dossier_sections(dossier).items()},
        "next_read": "用read_research_dossier的section读取research_plan、mandate、review_agenda、modules及相关底稿清单；按next_offset和deferred.path完整读完。当前判断不是独立事实。原文按source_id、原判断/PM观点按version_id读取当时版本；索引不表示已读。"}
    return checked_overview(result, pageable_fields=[(["current_investment_view"], {
        "tool": "read_research_dossier", "instrument_id": dossier["instrument_id"], "section": "investment_view"})])


def coverage_detail(coverage):
    if not isinstance(coverage, dict):
        return coverage
    exported = coverage.get("export_coverage")
    if isinstance(exported, dict) and "sources" in exported and exported["sources"] == coverage.get("sources"):
        return {**coverage, "export_coverage": {key: value for key, value in exported.items() if key != "sources"},
            "export_sources_same_as_sources": True}
    return coverage


def coverage_summary(coverage):
    if not isinstance(coverage, dict):
        return coverage
    exported = coverage.get("export_coverage")
    return {**{key: value for key, value in coverage.items() if key not in {"sources", "export_coverage"}},
        "source_count": len(coverage.get("sources", [])),
        "export_coverage": {**{key: value for key, value in exported.items() if key != "sources"},
                            "source_count": len(exported.get("sources", []))} if isinstance(exported, dict) else exported,
        "detail_read": "read_research_context(section='market_coverage')；索引/采集状态不表示已覆盖全部事实，缺口不能推定无新增。"}
