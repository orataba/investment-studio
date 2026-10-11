"""Project one frozen portfolio risk module before HTTP transport."""
import json
from typing import get_args
from watchlist_app.services.research_tool_projection import PortfolioRiskSection
from watchlist_app.services.research_read_projection import checked_overview, read_page


def _concentration_page(portfolio, data, *, cutoff, offset, source_offset):
    """Page bound rows and, independently, a large row's exposure sources.

    The harness permits 50 KB UTF-8 tool text. Measure complete replies, leaving
    room for transport overhead, rather than guessing from number of rows alone.
    """
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("集中度offset必须是非负整数。")
    if source_offset is not None and (isinstance(source_offset, bool) or not isinstance(source_offset, int) or source_offset < 0):
        raise ValueError("集中度source_offset必须是非负整数。")
    scopes = data.get("scopes", [])
    flattened = [(scope_index, row) for scope_index, scope in enumerate(scopes) for row in scope.get("rows", [])]
    total = len(flattened)
    if offset > total or (source_offset is not None and offset >= total):
        raise ValueError("集中度分页超出本次绑定快照的范围。")
    registered = {source["source_id"]: source for source in portfolio.get("sources", [])}
    module_sources = {source["source_id"] for source in data.get("sources", [])}
    aggregate_id = data.get("source_id")
    aggregate = [aggregate_id] if aggregate_id in module_sources and aggregate_id in registered else []
    base = {key: value for key, value in data.items() if key not in {"scopes", "sources", "fcn_contracts"}}

    def build(row_count, source_count):
        projected_scopes, chosen_ids, continuations = {}, list(sorted(aggregate)), []
        for row_offset in range(offset, min(total, offset + row_count)):
            scope_index, row = flattened[row_offset]
            row_sources = list(dict.fromkeys(source["source_id"] for source in row.get("sources", [])))
            begin = source_offset if source_offset is not None else 0
            if begin > len(row_sources):
                raise ValueError("集中度来源分页超出当前行的范围。")
            end = min(len(row_sources), begin + source_count)
            source_ids = row_sources[begin:end]
            projected_row = {key: value for key, value in row.items() if key != "sources"}
            projected_row.update(row_offset=row_offset, source_ids=source_ids,
                source_count=len(row_sources), source_offset=begin,
                next_source_offset=end if end < len(row_sources) else None)
            missing_ids = [source_id for source_id in source_ids if source_id not in registered or source_id not in module_sources]
            if missing_ids:
                projected_row["unregistered_source_ids"] = missing_ids
            chosen_ids.extend(source_id for source_id in source_ids if source_id not in missing_ids)
            if end < len(row_sources):
                continuations.append({"offset": row_offset, "source_offset": end,
                    "scope": scopes[scope_index].get("scope"), "taxonomy_id": scopes[scope_index].get("taxonomy_id"),
                    "entity_id": row.get("entity_id")})
            scope = projected_scopes.setdefault(scope_index, {
                **{key: value for key, value in scopes[scope_index].items() if key != "rows"}, "rows": []})
            scope["rows"].append(projected_row)
        next_offset = min(total, offset + row_count)
        return {"portfolio_id": portfolio["portfolio_id"], "as_of_date": portfolio["as_of_date"],
            "cutoff": cutoff, "section": "concentration", "current": {**base, "scopes": list(projected_scopes.values())},
            "sources": [registered[source_id] for source_id in dict.fromkeys(chosen_ids)],
            "page_kind": "sources" if source_offset is not None else "rows",
            "offset": offset, "next_offset": next_offset if source_offset is None and next_offset < total else None,
            "total_rows": total, "source_continuations": continuations,
            "pagination_note": "Follow next_offset until null to read all bound scopes/taxonomies. Independently follow every source_continuations offset/source_offset until none remain. row_offset is stable within this snapshot; source_ids are partial when next_source_offset is not null. Read contract terms with the existing derivatives holding_id tool. This page does not redefine portfolio scope."}

    row_count = 1 if source_offset is not None else min(20, total - offset)
    source_count = 20 if source_offset is not None else 8
    while True:
        packet = build(row_count, source_count)
        if len(json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()) <= 48000:
            return packet
        if row_count > 1:
            row_count -= 1
        elif source_count > 1:
            source_count = max(1, source_count // 2)
        else:
            raise ValueError("单条集中度记录或来源超过工具返回上限，无法完整读取；请在组合风险页面核对。没有截断数据或将未读部分视为无风险。")


def project_portfolio_risk(context, section: PortfolioRiskSection, holding_id: str | None = None, offset: int = 0, source_offset: int | None = None, path=None, limit=20) -> dict:
    """Read ONE bound portfolio module. targets_by_taxonomy reads ALL classifications. concentration is PAGINATED: start offset=0, follow next_offset until null for every scope/taxonomy (at most 20 rows/page). Also follow each source_continuations entry using its offset and source_offset until empty; large group sources are separately paged, row source_ids may be partial. Browser grouping never filters monitoring. tail_risk includes historical VaR/ES, actual sample/tail mass and excluded exposures; exclusions are not zero risk. Only derivatives accepts holding_id; without it, read its contract index/resources. offset/source_offset are only for concentration. Dates, coverage and source_ids are authoritative. No live fetch or new assessment."""
    if not context.get("risk_run") or context["risk_inputs"]["scope"]["kind"] != "portfolio":
        raise ValueError("只能读取本次风控范围内的组合。")
    portfolio = (context["risk_inputs"].get("portfolio") or {}).get("risk_context") or {}
    if portfolio.get("portfolio_id") != context["risk_inputs"]["scope"]["id"]:
        raise ValueError("组合模块与本次风控范围不一致。")
    if section not in get_args(PortfolioRiskSection) or section not in portfolio:
        raise ValueError("本次组合快照不包含该风险模块。")
    data = portfolio[section]
    sources = portfolio.get("sources", [])
    if section == "concentration":
        if path is not None:
            raise ValueError("集中度使用offset/source_offset续读，不使用path。")
        if holding_id:
            raise ValueError("holding_id 仅用于读取衍生品合约。")
        return _concentration_page(portfolio, data, cutoff=context["cutoff"], offset=offset, source_offset=source_offset)
    if (offset != 0 and path is None) or source_offset is not None:
        raise ValueError("offset/source_offset仅用于集中度分页。")
    if section == "derivatives":
        positions = data.get("positions", [])
        if holding_id:
            position = next((row for row in positions if row["holding_id"] == holding_id), None)
            if position is None:
                raise ValueError("只能读取本次组合快照内的合约。")
            data = {**{key: value for key, value in data.items() if key not in {"positions", "sources"}}, "position": position}
            sources = [source for source in sources if source.get("holding_id") == holding_id]
        else:
            data = {**{key: value for key, value in data.items() if key not in {"positions", "sources"}},
                    "positions": [{key: row.get(key) for key in ("holding_id", "name", "contract_type", "source_id")} for row in positions]}
            sources = []
    else:
        if holding_id:
            raise ValueError("holding_id 仅用于读取衍生品合约。")
        if section == "tail_risk":
            source_ids = {source["source_id"] for source in data.get("sources", [])}
            sources = [source for source in sources if source.get("source_id") in source_ids]
            # Sources appear once in this tool reply, separately from metrics.
            data = {key: value for key, value in data.items() if key != "sources"}
        elif section == "targets_by_taxonomy":
            source_ids = {row["source_id"] for row in data if row.get("source_id")}
            sources = [source for source in sources if source.get("source_id") in source_ids]
        else:
            names = {"portfolio_metrics": ("metrics", "correlations"), "targets": ("targets",),
                     "comparisons": ("comparison",)}[section]
            source_ids = {f"portfolio-risk:{portfolio['portfolio_id']}:{name}" for name in names}
            sources = [source for source in sources if source.get("source_id") in source_ids
                       and not source.get("holding_id") and not source.get("holding_ids")]
    value = {"current": data, "sources": sources}
    metadata = {"portfolio_id": portfolio["portfolio_id"], "as_of_date": portfolio["as_of_date"],
        "cutoff": context["cutoff"], "section": section}
    if path is not None:
        return read_page(value, metadata, path=path, offset=offset, limit=limit)
    read = {"tool": "read_portfolio_risk", "section": section, **({"holding_id": holding_id} if holding_id else {})}
    return checked_overview({**metadata, **value}, pageable_fields=[
        ([key], {**read, "path": [key]}) for key in ("current", "sources")])
