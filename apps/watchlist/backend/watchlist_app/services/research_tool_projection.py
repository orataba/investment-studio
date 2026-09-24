"""Server-side, lossless projections of bound research inputs.

The MCP transport forwards selectors only. HTTP responses are bounded before
crossing the process boundary; originals and immutable versions remain available.
"""
import json
from typing import Literal, get_args
from urllib.parse import quote
from watchlist_app.services.research_read_projection import (
    checked_overview, coverage_detail, coverage_summary, dossier_overview, dossier_sections, read_page, shape,
)
from watchlist_app.services.research_estimate_tools import estimate_overview
from watchlist_app.services.risk_read_projection import _risk_overview_pages, required_detail_reads
PortfolioRiskSection = Literal["portfolio_metrics", "targets", "targets_by_taxonomy", "comparisons", "derivatives", "concentration", "tail_risk"]
ResearchReferenceSection = str
REFERENCE_TABLES = {"holdings", "financials", "key_metrics", "ratios", "dividends", "splits"}

def read_research_context(request, section: str = "overview", offset: int = 0, limit: int = 20,
                          path: list[str | int] | None = None) -> dict:
    """Start with the bound question, scope and section counts. Read catalogue to discover related IDs; history/evidence/page_context and selected references hold the actual conversation inputs. Read nonempty selected references before answering; read market_coverage for channel details. Every section is paginated: follow next_offset, then every deferred.path with the SAME section until complete. Text offsets are characters, array offsets rows, object offsets fields. Empty/unread sections never prove no new information. Automatic tracking covers instrument_ids; a conversation follows the question and selected page, not every catalogue member."""
    context = request("context")
    if not context.get("risk_run"):
        sections = {key: context[key] for key in (
            "question", "page_context", "referenced_research_update", "referenced_research_versions", "referenced_risk_case", "history", "conversation", "evidence",
            "watchlists", "limitations", "data_gaps", "incremental_trigger", "analyst_focus", "user_records", "team_publication_instructions") if key in context}
        sections.update({"market_coverage": coverage_detail(context.get("market_coverage")),
            "catalogue": [{key: item[key] for key in ("instrument_id", "name", "instrument_type", "currency", "watchlist_ids") if key in item}
                          for item in context.get("catalogue", [])],
            "tool_evidence": [{key: item.get(key) for key in ("source_id", "tool", "request", "retrieved_at")}
                              for item in context.get("tool_evidence", [])]})
        if section != "overview":
            if section not in sections:
                raise ValueError("该分区不在本轮上下文目录中。")
            return read_page(sections[section], {"run_id": context.get("run_id"), "cutoff": context.get("cutoff"), "section": section},
                             offset=offset, limit=limit, path=path)
        if offset or path:
            raise ValueError("overview不使用offset/path，请读取目录内的section。")
        return checked_overview({**{key: context[key] for key in (
            "sector_run", "research_run", "run_id", "topic_id", "question", "cutoff", "input_snapshot_cutoff", "as_of_date",
            "requested_at", "instrument_ids", "selected_instrument_ids", "watchlist_id", "portfolio_id", "team_id", "visibility") if key in context},
            "market_coverage": coverage_summary(context.get("market_coverage")),
            "sections": {key: shape(value) for key, value in sections.items()},
            "next_read": "自动研究逐一读取instrument_ids；对话按问题读取page_context、非空referenced_research_update/referenced_research_versions/referenced_risk_case及相关history/evidence。图表/底稿追问须读取referenced_research_versions绑定的精确版本及其source_ids，不用当前版本替换。referenced_risk_case是当前风险快照；不是历史版本。catalogue按需分页发现相关标的，不要求遍历登记库。read_research_instrument提供当前判断与资料目录，read_research_dossier按section读取任务、复核议程和底稿，source_id/version_id读取原文/原版本。所有选读分区跟随next_offset及deferred.path读完；未读资料不代表缺失。个人对话仅在用户明确要求后先authorize_team_research，再submit_research_review；组合对话不能发布团队研究。"}, pageable_fields=[(["question"], {"tool": "read_research_context", "section": "question"})] if "question" in context else [])
    if section != "overview" or offset or path:
        raise ValueError("风控使用read_risk_instrument/read_portfolio_risk分区；范围索引不使用section/offset/path。")
    snapshot = context["risk_inputs"]
    # Full portfolio snapshots exceed the harness's 50 KB tool-result limit.
    # Send the scope index here; read each retained instrument separately below.
    portfolio = snapshot.get("portfolio")
    portfolio_context = (portfolio or {}).get("risk_context") or {}
    return {"risk_run": True, "risk_scope": context["risk_scope"], "cutoff": context["cutoff"],
        "risk_inputs": {**{key: snapshot.get(key) for key in (
            "scope", "scope_available", "instrument_ids", "input_as_of", "limitations")},
            "portfolio": {key: value for key, value in portfolio.items() if key != "risk_context"} if portfolio else None},
        "portfolio_risk_sections": [key for key in get_args(PortfolioRiskSection) if key in portfolio_context],
        "portfolio_taxonomies": [{key: row.get(key) for key in ("taxonomy_id", "name", "status")}
                                 for row in portfolio_context.get("targets_by_taxonomy", [])],
        "concentration_scopes": [{key: row.get(key) for key in ("scope", "taxonomy_id", "name", "status")}
                                 for row in portfolio_context.get("concentration", {}).get("scopes", [])],
        "concentration_row_count": sum(len(scope.get("rows", [])) for scope in portfolio_context.get("concentration", {}).get("scopes", [])),
        "derivative_holdings": [{key: row.get(key) for key in ("holding_id", "name", "contract_type", "source_id")}
                                for row in portfolio_context.get("derivatives", {}).get("positions", [])],
        "instruments": [{key: item.get(key) for key in ("instrument_id", "name", "as_of_date", "instrument_type")}
                        for item in snapshot["instruments"]],
        "instrument_overview_pages": _risk_overview_pages(context),
        "instrument_detail_reads": required_detail_reads(context),
        "reports": [{key: case.get(key) for key in ("case_id", "instrument_id", "title", "signal", "severity")}
                    for category in ("research", "quantitative", "coverage") for case in snapshot[category]],
        "prior_input_as_of": (context.get("prior_inputs") or {}).get("input_as_of"),
        "next_read": "执行instrument_overview_pages全部条目：按tool调用批量页offset或单项instrument_id，条目相互独立，可并行读取；核对全部页均已取得，不必逐项重读overview。按instrument_detail_reads明确列出的工具参数读取全部必读页，包含仅历史存在的记录；提交前服务会检查概览与这些资料是否全部交付，缺页须补读后重交完整结果；某分区current与previous计数均为0可直接跳过。comparisons按业绩研判需要读取，选读时沿next_offset读完；未读比较不能据以判断相对表现。PM档案、观点原文和研究判断是待验证输入，不是独立事实。组合另以 read_portfolio_risk 读取每个组合模块；targets_by_taxonomy 与 concentration 包含本轮全部分类，不随浏览器的分组选项裁剪。concentration从offset=0开始，按next_offset逐页读到null；每页source_continuations还须用其offset/source_offset继续读取来源直到清空。tail_risk 保留样本量与未建模敞口，未覆盖不代表零风险。derivatives 按 derivative_holdings 内的 holding_id 逐份读取。这里只是范围索引。"}



def _source_overview(source):
    return {**{key: value for key, value in source.items() if key not in {"section_sources", "source_ids"}},
        "section_source_counts": {key: len(value) for key, value in source.get("section_sources", {}).items()},
        **({"source_count": len(source["source_ids"])} if "source_ids" in source else {})}



def _reference_metadata(reference):
    return {**{key: value for key, value in reference.items() if key not in {"sections", "source"}},
        "source": _source_overview(reference.get("source") or {})}



def _instrument_overview(asset, *, estimate_detail_tool):
    overview = {key: value for key, value in asset.items()
                if key not in {"research_dossier", "risk_cases", "reference_data", "research_tracking"}}
    reference = asset.get("reference_data") or {}
    sections = reference.get("sections") or {}
    overview["reference_data"] = {**_reference_metadata(reference),
        "sections": {key: value for key, value in sections.items() if key not in REFERENCE_TABLES}}
    overview["reference_sections"] = [key for key in sections if key in REFERENCE_TABLES]
    if asset.get("instrument_type") == "equity" and reference.get("provider") == "fmp":
        if "financials" not in overview["reference_sections"]:
            overview["reference_sections"].append("financials")
        overview["financials_read"] = "financials默认列可用财报目录（含三表、季度、年度、信息时间和匹配科目数）；按问题选择period_end/statement_type/fiscal_period，再用financial_view=facts读取该表原始科目。逐页跟随company.next_offset，引用每页source_id；目录不代表读过科目。"
    if any((reference.get("source") or {}).get(key) for key in ("section_sources", "source_ids")):
        overview["reference_sections"].append("source_lineage")
    overview["reference_read"] = "用read_research_instrument的section逐页读取原始财务或持仓表；source_lineage读取完整来源索引。每次跟随next_offset直到null，未读页不代表资料缺失。"
    if "analyst_estimate_history" in overview:
        overview["analyst_estimate_history"] = estimate_overview(overview["analyst_estimate_history"], detail_tool=estimate_detail_tool)
    return overview



def _reference_page(asset, reference, section, cutoff, offset, limit, company_source_ids=()):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("资料分页offset必须是非负整数。")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("资料分页limit必须是正整数。")
    sections = reference.get("sections") or {}
    if section == "source_lineage":
        data = [{"section": key, "value": source} for key, sources in (reference.get("source") or {}).get("section_sources", {}).items()
                for source in sources]
        data.extend({"section": "reference_source_ids", "value": source_id}
                    for source_id in (reference.get("source") or {}).get("source_ids", []))
        data.extend({"section": "company_source_ids", "value": source_id} for source_id in company_source_ids)
        available = bool(data)
    else:
        data, available = sections.get(section), section in sections
    rows = data if isinstance(data, list) else [data] if available else []
    if offset > len(rows) or (not isinstance(data, list) and offset):
        raise ValueError("资料分页超出本轮绑定快照范围。")
    count = min(limit, len(rows) - offset)
    while True:
        end = offset + count
        packet = {"instrument_id": asset["instrument_id"], "source_id": asset.get("source_id"),
            "cutoff": cutoff, "reference_data": {**_reference_metadata(reference), "section": section,
                "data": rows[offset:end] if isinstance(data, list) else data}, "available": available,
            "offset": offset, "next_offset": end if end < len(rows) else None, "total_rows": len(rows),
            "pagination_note": "跟随next_offset直到null读取全部绑定行；source_lineage按section与value还原完整来源索引。数据与资料截止时间保持不变。"}
        if len(json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()) <= 48000:
            return packet
        if count > 1:
            count = max(1, count // 2)
        else:
            raise ValueError("单条资料超过工具返回上限，无法完整读取；请从资料页核对原件，未截断或将未读部分当作缺失。")



def read_research_instrument(request, instrument_id: str, section: ResearchReferenceSection = "overview", offset: int = 0, limit: int = 20,
                             statement_type: Literal["income", "balance_sheet", "cash_flow"] | None = None,
                             fiscal_period: Literal["FY", "Q1", "Q2", "Q3", "Q4"] | None = None,
                             period_end: str | None = None, financial_view: Literal["statements", "facts"] = "statements",
                             path: list[str | int] | None = None) -> dict:
    """Read ONE bound instrument's identity, complete current analyst judgment and section directory. Use read_research_dossier for mandate/agenda/current research lists and exact sources/versions. Read performance, performance_evidence, pm_profile, product_information, disclosed_holdings, registered_materials, events or sector_context here on demand; follow next_offset and ALL deferred.path with the SAME section. Reference tables/source_lineage use next_offset. FMP equity financials first lists statements: select period_end (YYYY-MM-DD), statement_type/fiscal_period then financial_view=facts for original line items; follow company.next_offset and cite each source_id. Directory reads do not mean evidence was read. Statements/facts join by statement_content_sha256. Everything stays on the run snapshot, never live data."""
    context = request("context")
    if context.get("risk_run") or instrument_id not in {row["instrument_id"] for row in context.get("catalogue", [])}:
        raise ValueError("只能读取本轮研究范围目录内的标的。")
    if not any(row["instrument_id"] == instrument_id for row in context.get("instrument_inputs", [])):
        request("tools", {"tool": "instruments", "instrument_ids": [instrument_id]})
        context = request("context")
    asset = next(row for row in context["instrument_inputs"] if row["instrument_id"] == instrument_id)
    reference = asset.get("reference_data") or {}
    if section == "financials" and asset.get("instrument_type") == "equity" and reference.get("provider") == "fmp":
        if path:
            raise ValueError("financials使用财报筛选与offset，不使用path。")
        return request("financials", {"instrument_id": instrument_id, "offset": offset, "limit": limit,
            "statement_type": statement_type, "fiscal_period": fiscal_period, "period_end": period_end, "view": financial_view})
    if statement_type is not None or fiscal_period is not None or period_end is not None or financial_view != "statements":
        raise ValueError("财报类型与财期筛选仅用于已绑定FMP公司的financials部分。")
    company_inputs = [row for row in context.get("sector_inputs", []) if row["instrument_id"] == instrument_id]
    company_source_ids = [sid for row in company_inputs for sid in (row.get("source") or {}).get("source_ids", [])]
    details = {"performance": asset.get("performance"), "performance_evidence": asset.get("performance_evidence"),
        "pm_profile": (asset.get("research") or {}).get("profile"), "product_information": asset.get("product_information"),
        "disclosed_holdings": asset.get("holdings"), "registered_materials": asset.get("materials", []),
        "events": [event for event in context.get("prior_events", []) if event["instrument_id"] == instrument_id],
        "sector_context": {"sector_inputs": company_inputs,
            "sector_estimate_evidence": [row for row in context.get("sector_estimate_evidence", []) if row["instrument_id"] == instrument_id]}}
    if section in details:
        return read_page(details[section], {"instrument_id": instrument_id, "source_id": asset.get("source_id"),
            "run_id": context["run_id"], "cutoff": context["cutoff"], "section": section}, offset=offset, limit=limit, path=path)
    if section != "overview":
        if path:
            raise ValueError("原始资料表使用offset，不使用path。")
        return _reference_page(asset, reference, section, context["cutoff"], offset, limit, company_source_ids)
    if offset != 0 or path:
        raise ValueError("overview不使用offset/path，请读取目录内的section。")
    # Long financial tables and duplicated risk history can exceed the harness's 50 KB reply.
    # Keep the identity/working assignment intact and expose full reference sections on demand.
    overview = _instrument_overview(asset,
        estimate_detail_tool="read_instrument_research(instrument_ids=[instrument_id], estimate_symbol=company_symbols中的一个symbol)")
    overview = {key: value for key, value in overview.items() if key not in {
        "performance", "performance_evidence", "research", "product_information", "materials", "holdings"}}
    if company_source_ids and "source_lineage" not in overview["reference_sections"]:
        overview["reference_sections"].append("source_lineage")
    dossier = next(d for d in context["research_dossiers"] if d["instrument_id"] == instrument_id)
    return checked_overview({"instrument_id": instrument_id, "run_id": context["run_id"], "cutoff": context["cutoff"],
        "instrument_inputs": [overview],
        "reference_sections": overview["reference_sections"],
        "research_sections": {key: shape(value) for key, value in details.items()},
        "next_read": "read_research_dossier按section读取mandate、frameworks、review_agenda及相关当前底稿/PM观点，source_id/version_id读原文和原版本。本工具的performance/performance_evidence、pm_profile、product_information、disclosed_holdings、registered_materials、events、sector_context按需读取；选读分区沿next_offset和deferred.path读全。reference_sections原始表沿next_offset读全；公司预期用detail_read入口。覆盖详情在read_research_context(section='market_coverage')。索引、未读页不能当作已核实或资料缺失。",
        "sector_inputs": [{**{key: value for key, value in row.items() if key not in {"holdings", "leading_companies", "source"}},
                           "source": _source_overview(row.get("source") or {})} for row in company_inputs],
        "sector_estimate_evidence": [estimate_overview(row, detail_tool="read_sector_company(instrument_id, symbol)")
                                     for row in context.get("sector_estimate_evidence", []) if row["instrument_id"] == instrument_id],
        "research_dossier": dossier_overview(dossier), "market_coverage": coverage_summary(context.get("market_coverage")),
        "data_gaps": context.get("data_gaps", [])}, pageable_fields=[(["research_dossier", "current_investment_view"], {
            "tool": "read_research_dossier", "instrument_id": instrument_id, "section": "investment_view"})])



def read_research_dossier(request, instrument_id: str, source_id: str | None = None, version_id: str | None = None, update_id: str | None = None,
                          section: str = "overview", offset: int = 0, limit: int = 20, path: list[str | int] | None = None) -> dict:
    """Read the current judgment and section directory, then select research_plan/mandate/frameworks/available_modules/review_agenda/research_state, modules/facts/questions/catalysts/forecasts/forecast_reviews/lessons, themes/pm_views, materials/prior_sources/sources/historical_cases/versions. Read the assignment and agenda before deciding what to investigate. Select ONE source_id for an exact bound original, version_id for a past notebook/view/module/forecast or pm:<note_id>:<revision>, or update_id for a published update; selectors cannot combine with section. PM source indexes are not the originals: use that PM version's sources, never a later dossier source with the same ID. ALL selected data is paginated: follow next_offset AND every deferred.path, repeating the same section/selector until fully read. Text offsets count characters. No clipping, live substitution or model calls. AI/PM judgments remain hypotheses, not independently verified facts."""
    context = request("context")
    if context.get("risk_run"):
        raise ValueError("风控研判仅使用已绑定的风险快照")
    if not any(d["instrument_id"] == instrument_id for d in context.get("research_dossiers", [])):
        request("tools", {"tool": "instruments", "instrument_ids": [instrument_id]})
        context = request("context")
    dossier = next((d for d in context.get("research_dossiers", []) if d["instrument_id"] == instrument_id), None)
    if dossier is None:
        raise ValueError("本轮尚未绑定该标的研究档案。")
    from urllib.parse import urlencode
    params = {k: v for k, v in {"source_id": source_id, "version_id": version_id, "update_id": update_id}.items() if v}
    if len(params) > 1 or params and section != "overview":
        raise ValueError("请选择一个section或原文/版本/更新标识，不能混用。")
    suffix = f"dossier/{quote(instrument_id, safe='')}"
    metadata = {"instrument_id": instrument_id, "run_id": context.get("run_id"), "cutoff": context.get("cutoff"),
                "section": section, **params}
    if params:
        value = request(suffix + "?" + urlencode(params))
        return read_page(value, metadata, offset=offset, limit=limit, path=path)
    if section == "overview":
        if offset or path:
            raise ValueError("overview不使用offset/path，请读取目录内的section。")
        return checked_overview({**metadata, **dossier_overview(dossier)}, pageable_fields=[(["current_investment_view"], {
            "tool": "read_research_dossier", "instrument_id": instrument_id, "section": "investment_view"})])
    sections = dossier_sections(dossier)
    if section not in sections:
        raise ValueError("该分区不在本轮研究档案目录中。")
    return read_page(sections[section], metadata, offset=offset, limit=limit, path=path)



