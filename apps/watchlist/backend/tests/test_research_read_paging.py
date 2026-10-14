import asyncio
from copy import deepcopy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from watchlist_app import research_mcp as mcp
from watchlist_app.services.research_notebook import dossier_outline
from watchlist_app.services.research_read_projection import coverage_detail, dossier_sections
from watchlist_app.services import research_tool_projection as projection


from .research_projection_fixture import server_projection


def wire(tool, arguments):
    result = asyncio.run(mcp.mcp.call_tool(tool, arguments))
    assert len(result.content[0].text.encode()) <= 48000
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content


def complete(tool, arguments, path=None):
    """Reconstruct the original JSON/text using only the tool's returned cursors."""
    offset, output = 0, None
    while offset is not None:
        page = wire(tool, {**arguments, "path": path or [], "offset": offset})
        kind = page["data_type"]
        if output is None:
            output = {} if kind == "object" else [None] * page["total"] if kind == "array" else "" if kind == "text" else page["data"]
        if kind == "object":
            output.update(page["data"])
        elif kind == "array":
            output[offset:offset + len(page["data"])] = page["data"]
        elif kind == "text":
            output += page["data"]
        for child in page["deferred"]:
            output[child["path"][-1]] = complete(tool, arguments, child["path"])
        offset = page["next_offset"]
    return output


def test_market_search_directory_and_originals_have_lossless_bound_wire_pages(monkeypatch):
    cutoff = "2026-09-03T00:00:00+00:00"
    directory = [{"document_id": f"doc-{i}", "version_id": f"old-{i}", "source_id": f"text:old-{i}",
                  "title": ("完整标题🙂" * 14000 if i in {0, 17} else f"目录标题{i}"),
                  "body_available": True, "entities": [{"name": "完整实体" * 100, "id": j} for j in range(3)]}
                 for i in range(41)]
    coverage = {"sources": [{"channel": i, "details": "完整覆盖状态🙂" * 2000} for i in range(25)]}
    article = {"source_id": "text:old-17", "document_id": "doc-17", "version_id": "old-17",
               "text": "完整版本正文及条件🙂" * 25000, "published_at": "2026-09-01", "body_available": True}
    calls = []

    def request(suffix, payload=None):
        calls.append((suffix, deepcopy(payload)))
        if suffix == "market-search":
            assert payload["query"] == "baseline" and payload["instrument_id"] == "xlk"
            assert payload["entities"] == ["entity"]
            assert payload["channel_ids"] == ["fmp:earnings_transcripts"]
            assert payload["as_of"] in {None, cutoff}
            begin = payload["offset"]
            return {"rows": deepcopy(directory[begin:begin + payload["limit"]]), "total": len(directory),
                    "as_of": cutoff, "coverage": deepcopy(coverage)}
        params = parse_qs(urlsplit(suffix).query)
        assert params == {"document_id": ["doc-17"], "version_id": ["old-17"]}
        return deepcopy(article)

    monkeypatch.setattr(mcp, "request", server_projection(request))
    args = {"query": "baseline", "instrument_id": "xlk", "entities": ["entity"],
            "channel_ids": ["fmp:earnings_transcripts"]}
    first = wire("search_market_information", args)
    assert first["as_of"] == cutoff and first["search_total"] == 41
    assert first["deferred"] == [{"path": [0], "type": "object", "count": len(directory[0])}]
    assert "read_market_source" in first["original_read"]
    assert complete("search_market_information", {**args, "as_of": cutoff}) == directory
    assert complete("search_market_information", {**args, "as_of": cutoff, "section": "coverage"}) == coverage
    assert all(suffix == "market-search" for suffix, _ in calls)
    assert complete("read_market_source", {"document_id": "doc-17", "version_id": "old-17"}) == article
    assert not any("content_text" in row or "text" in row for row in directory)


def test_empty_market_results_do_not_spill_large_coverage(monkeypatch):
    cutoff = "2026-09-03T00:00:00+00:00"
    coverage = {"sources": [{"id": i, "coverage": "完整采集条件🙂" * 3000} for i in range(20)]}
    monkeypatch.setattr(mcp, "request", lambda suffix, payload: {
        "rows": [], "total": 0, "as_of": cutoff, "coverage": coverage})
    first = wire("search_market_information", {"query": "no-match"})
    assert first["data"] == [] and first["total"] == 0 and first["next_offset"] is None
    assert len(json.dumps(first, ensure_ascii=False).encode()) < 1500
    assert first["coverage_read"]["as_of"] == cutoff
    assert complete("search_market_information", {
        "query": "no-match", "section": "coverage", "as_of": cutoff}) == coverage


@pytest.mark.parametrize("tool,args", [
    ("search_market_information", {"offset": 1}),
    ("search_market_information", {"path": [0]}),
    ("read_market_source", {"document_id": "doc", "offset": 1}),
    ("read_market_source", {"document_id": "doc", "path": ["text"]}),
])
def test_market_continuations_require_the_original_cutoff_or_version(monkeypatch, tool, args):
    from mcp.server.mcpserver.exceptions import ToolError
    monkeypatch.setattr(mcp, "request", lambda *args: pytest.fail("unbound continuation queried the backend"))
    with pytest.raises(ToolError, match="as_of|version_id"):
        asyncio.run(mcp.mcp.call_tool(tool, args))


def large_context():
    article = {"source_id": "shared-original", "document_id": "article-one", "version_id": "old-article",
        "source_type": "public_source", "title": "当时原文", "published_at": "2026-09-01", "text": "完整旧版原文与条件🙂" * 7000}
    view = {"direction": "中期有条件向上", "horizon": "未来两个季度", "attractiveness": "取决于兑现与价格", "risk": "订单未转成现金流",
        "assumptions": ["资本开支回报得到验证"], "source_ids": ["shared-original"], "updated_at": "2026-09-02T00:00:00Z",
        "source_run_id": "prior", "version_id": "view-version", "versions": [
            {"version_id": "previous-view", "direction": "原先方向", "updated_at": "2026-09-01T00:00:00Z"}]}
    notebook = {"investment_view": view, "version_id": "notebook-version", "sources": [article],
        "facts": [{"key": str(i), "fact": "完整事实和不确定条件" * 2000, "source_ids": ["shared-original"]} for i in range(4)],
        "questions": [{"key": str(i), "question": "订单能否兑现", "assessment": "待验证", "evidence_against": ["回款仍弱"],
                       "next_check": "下次季报", "tracking_status": "active"} for i in range(35)],
        "fundamental_view": "经营假设", "key_drivers": ["订单", "现金回收"], "forecasts": [], "forecast_reviews": [], "lessons": []}
    dossier = {"instrument_id": "xlk", "name": "研究标的", "notebook": notebook,
        "mandate": {"title": "当前任务", "user_focus": ["优先研究现金回收"], "background": "完整任务依据" * 11000},
        "frameworks": [{"id": "method", "body": "原始方法" * 6000}],
        "prior_sources": [{**article, "source_id": f"source-{i}", "version_id": f"version-{i}"} for i in range(30)],
        "historical_cases": [{"source_id": f"case-{i}", "case_id": str(i), "case_title": "历史机制", "current_use": "适用条件" * 1000} for i in range(14)],
        "materials": [{"source_id": "material-one", "title": "原材料", "body": "材料正文" * 15000}],
        "themes": [{"theme_id": "theme-one", "question": "机制能否延续"}],
        "pm_views": [{"note_id": f"pm-{i}", "revision_number": 2, "body": "PM原始观点和反证" * 400,
            "author": "原作者", "source_ids": ["shared-original"], "sources": [article],
            "research_context": {"information_cutoff": "2026-09-02T00:00:00Z", "source_ids": ["shared-original"], "sources": [article]},
            "versions": [{"version_id": f"pm:pm-{i}:2"}]} for i in range(4)],
        "review_agenda": {"tracked_questions": [{"update_id": f"update-{i}", "question": "具体问题" * 500} for i in range(35)]}}
    channels = [{"channel_id": f"channel-{i}", "latest_run": {"status": "limited", "details": "真实覆盖条件" * 100}} for i in range(58)]
    context = {"sector_run": True, "run_id": "bound", "cutoff": "2026-09-03T00:00:00Z", "instrument_ids": ["xlk"],
        "catalogue": [{"instrument_id": "xlk" if i == 0 else f"asset-{i}", "name": "目录标的" * 30,
                       "instrument_type": "etf", "attributes": {"large": "不属于身份索引" * 100}} for i in range(249)],
        "market_coverage": {"sources": channels, "export_coverage": {"sources": deepcopy(channels), "as_of": "2026-09-03"}},
        "instrument_inputs": [{"instrument_id": "xlk", "name": "研究标的", "source_id": "instrument:bound:xlk",
            "performance": {"data": {"annual_returns": [{"year": i, "basis": "真实收益口径" * 100} for i in range(28)]}},
            "holdings": {"data": {"snapshot_metadata": {"report_date": "2026-06-30"},
                "rows": [{"name": "披露资产", "quantity": 2, "weight": 0.4, "currency": "CNY", "duration": 3.5, "ytw": 0.03}]}},
            "research": {"profile": {"current_view": "PM当前想法", "disconfirming_evidence": "回款不足"}},
            "risk": {"current_drawdown": -3}}],
        "research_dossiers": [dossier_outline(dossier)],
        "prior_events": [{"instrument_id": "xlk", "case_id": f"event-{i}", "body": "完整事件与反证" * 9000,
                          "withdrawn": i == 1, "source_ids": ["shared-original"]} for i in range(3)]}
    return context, article


def test_large_research_entries_page_every_bound_record_within_actual_wire_limit(monkeypatch):
    context, article = large_context()
    original = deepcopy(context)
    monkeypatch.setattr(mcp, "request", server_projection(lambda suffix: context if suffix == "context" else pytest.fail(suffix)))
    scope = wire("read_research_context", {})
    assert scope["instrument_ids"] == ["xlk"] and scope["sections"]["catalogue"]["count"] == 249
    assert "market_coverage" not in scope  # Each role starts with its own acquisition or numerical context.
    assert scope["section_reads"] == [{"tool": "read_research_section", "instrument_id": "xlk", "section": "overview"}]
    catalogue = complete("read_research_context", {"section": "catalogue"})
    assert [row["instrument_id"] for row in catalogue] == [row["instrument_id"] for row in context["catalogue"]]
    assert all("attributes" not in row for row in catalogue)
    coverage = complete("read_research_context", {"section": "market_coverage"})
    assert coverage == coverage_detail(context["market_coverage"])
    assert coverage["export_sources_same_as_sources"] and coverage["sources"] == context["market_coverage"]["sources"]

    overview = wire("read_research_instrument", {"instrument_id": "xlk"})
    view = context["research_dossiers"][0]["notebook"]["investment_view"]
    assert overview["research_dossier"]["current_investment_view"] == {key: value for key, value in view.items() if key != "versions"}
    assert "performance" not in overview["instrument_inputs"][0] and "prior_events" not in overview
    for section, expected in {"performance": context["instrument_inputs"][0]["performance"],
                              "events": context["prior_events"], "pm_profile": context["instrument_inputs"][0]["research"]["profile"],
                              "disclosed_holdings": context["instrument_inputs"][0]["holdings"]}.items():
        assert complete("read_research_instrument", {"instrument_id": "xlk", "section": section}) == expected

    dossier = wire("read_research_dossier", {"instrument_id": "xlk"})
    assert dossier["current_investment_view"] == overview["research_dossier"]["current_investment_view"]
    sections = dossier_sections(context["research_dossiers"][0])
    for section, expected in sections.items():
        assert complete("read_research_dossier", {"instrument_id": "xlk", "section": section}) == expected
    assert sections["investment_view"]["versions"][0]["version_id"] == "previous-view"
    pm = sections["pm_views"][0]
    assert pm["body"] == context["research_dossiers"][0]["pm_views"][0]["body"]
    assert pm["version_id"] == "pm:pm-0:2" and pm["sources"][0]["version_id"] == "old-article"
    assert "text" not in pm["sources"][0] and "text" not in pm["research_context"]["sources"][0]
    assert context == original


def test_exact_pm_version_and_large_source_can_be_read_losslessly_without_rebinding(monkeypatch):
    context, old = large_context()
    financial = {"source_id": "financials:old", "source_type": "company_snapshot", "source_run_id": "old-run",
        "run_cutoff": "2026-09-01T00:00:00Z", "retrieved_at": "2026-09-02T00:00:00Z",
        "company": {"statements": [{"id": i, "value": "完整原始科目" * 8000} for i in range(2)]}}
    version = {"kind": "pm_view", "version_id": "pm:pm-0:2", "information_cutoff": "2026-09-02T00:00:00Z",
               "value": {"body": "当时观点"}, "sources": [old, financial]}
    later = {**old, "text": "后来版本不能代替PM当时依据", "version_id": "new-article"}
    update = {"kind": "research_update", "value": {"update_id": "update-one", "body": "当时完整研究变化" * 14000}}
    calls = []
    def request(suffix):
        if suffix == "context":
            return context
        calls.append(suffix)
        query = parse_qs(urlsplit(suffix).query)
        if query == {"version_id": ["pm:pm-0:2"]}:
            return version
        if query == {"source_id": ["shared-original"]}:
            return later
        if query == {"update_id": ["update-one"]}:
            return update
        pytest.fail(suffix)
    monkeypatch.setattr(mcp, "request", server_projection(request))
    assert complete("read_research_dossier", {"instrument_id": "xlk", "version_id": "pm:pm-0:2"}) == version
    assert all("version_id=pm" in call for call in calls)
    assert complete("read_research_dossier", {"instrument_id": "xlk", "source_id": "shared-original"}) == later
    assert complete("read_research_dossier", {"instrument_id": "xlk", "update_id": "update-one"}) == update
    assert version["sources"][0]["text"] == old["text"]


def test_conversation_focus_and_long_evidence_remain_explicit_read_sections(monkeypatch):
    context, _ = large_context()
    context.pop("sector_run")
    context.update(research_run=True, question="核对选中的风险", page_context={"instrument_id": "xlk"},
        referenced_risk_case={"case_id": "selected", "body": "选中风险全文" * 12000},
        history=[{"question": "原问题", "answer": "旧回答" * 16000}],
        evidence=[{"source_id": "attachment", "text": "用户材料" * 14000}])
    monkeypatch.setattr(mcp, "request", server_projection(lambda _: context))
    index = wire("read_research_context", {})
    assert index["question"] == context["question"]
    for section in ("page_context", "referenced_risk_case", "history", "evidence"):
        assert section in index["sections"]
        assert complete("read_research_context", {"section": section}) == context[section]
    with pytest.raises(ValueError, match="不能混用"):
        mcp.read_research_dossier("xlk", source_id="one", version_id="two")
    with pytest.raises(ValueError, match="读取路径"):
        mcp.read_research_dossier("xlk", section="facts", path=[True])
    with pytest.raises(ValueError, match="非负整数"):
        mcp.read_research_context(section="catalogue", offset=-1)
    with pytest.raises(ValueError, match="超出"):
        mcp.read_research_context(section="catalogue", offset=250)


def test_contract_size_question_and_current_judgment_leave_a_readable_overview(monkeypatch):
    context, _ = large_context()
    context["question"] = "请" * 20000  # AnalysisInput's supported maximum length.
    view = context["research_dossiers"][0]["notebook"]["investment_view"]
    view["assumptions"] = ["完整假设及其适用条件" * 1000 for _ in range(5)]
    monkeypatch.setattr(mcp, "request", server_projection(lambda _: context))
    scope = wire("read_research_context", {})
    assert scope["question"]["inline"] is False
    assert scope["question"]["read"] == {"tool": "read_research_context", "section": "question"}
    assert scope["instrument_ids"] == ["xlk"] and "catalogue" in scope["sections"]
    assert complete("read_research_context", {"section": "question"}) == context["question"]
    for tool in ("read_research_instrument", "read_research_dossier"):
        packet = wire(tool, {"instrument_id": "xlk"})
        overview = packet.get("research_dossier", packet)
        assert overview["current_investment_view"]["inline"] is False
        assert overview["current_investment_view"]["read"]["section"] == "investment_view"
    assert complete("read_research_dossier", {"instrument_id": "xlk", "section": "investment_view"}) == view


def _risk_scope_context():
    instruments = [{"instrument_id": f"asset-{i}", "name": f"持仓{i}", "instrument_type": "public_fund"}
                   for i in range(11)]
    reports = [{"case_id": f"case-{i}", "instrument_id": f"asset-{i % 10}", "title": f"当时报告{i}",
                "signal": "research", "severity": "attention", "body": "原文证据"} for i in range(39)]
    snapshot = {"scope": {"kind": "portfolio", "id": "p"}, "scope_available": True,
        "instrument_ids": [item["instrument_id"] for item in instruments], "input_as_of": "2026-10-09",
        "instruments": instruments, "research": reports, "quantitative": [], "coverage": [],
        "portfolio": {"portfolio_id": "p", "positions": [{"instrument_id": "asset-0", "note": ""}],
                      "risk_context": {"portfolio_metrics": {"nav": 100}, "derivatives": {"positions": []}}},
        "limitations": []}
    prior = deepcopy(snapshot)
    prior["research"] = [{"case_id": "prior-only", "instrument_id": "asset-10", "title": "前期仍需复核", "body": "历史原文"}]
    context = {"run_id": "scope-run", "risk_run": True, "risk_scope": {"portfolio_id": "p"},
               "cutoff": "2026-10-11T19:00:57.747569+00:00", "risk_inputs": snapshot, "prior_inputs": prior}
    # The real failure had 11 instruments, 39 reports and a 50,451-byte index.
    # Reproduce that envelope with synthetic holdings; no private fixture data.
    index = projection._risk_scope_index(context)
    from watchlist_app.services.research_read_projection import json_bytes
    snapshot["portfolio"]["positions"][0]["note"] = "x" * (50451 - json_bytes(index))
    assert json_bytes(projection._risk_scope_index(context)) == 50451
    return context


@pytest.mark.parametrize("nested_large_fields", [False, True])
def test_risk_scope_index_is_lossless_bounded_and_keeps_prior_required_reads(monkeypatch, nested_large_fields):
    from watchlist_app.services.research_read_projection import checked_overview
    from watchlist_app.services.risk_read_projection import missing_required_reads
    context = _risk_scope_context()
    if nested_large_fields:
        context["risk_inputs"]["research"][0]["title"] = "完整标题🙂" * 15000
        context["risk_inputs"]["portfolio"]["positions"][0]["note"] = "完整持仓限制🙂" * 15000
        context["risk_inputs"]["portfolio"]["risk_context"]["derivatives"]["positions"] = [
            {"holding_id": "contract-1", "name": "完整合约名称🙂" * 15000}]
    before = deepcopy(context)
    raw_index = projection._risk_scope_index(context)
    with pytest.raises(ValueError, match="概览超过工具返回上限"):
        checked_overview(deepcopy(raw_index))
    missing = missing_required_reads(context)
    assert any(read["instrument_id"] == "asset-10" and read["section"] == "cases" for read in missing)
    monkeypatch.setattr(mcp, "request", server_projection(lambda suffix: context))
    overview = wire("read_research_context", {})
    references = {key: value["read"] for key, value in overview.items()
                  if isinstance(value, dict) and value.get("inline") is False}
    assert "risk_inputs" in references
    for key, selector in references.items():
        tool = selector["tool"]
        assert tool == "read_research_context" and selector["section"] == "scope_index"
        assert complete(tool, {"section": selector["section"]}, path=selector["path"]) == raw_index[key]
    assert complete("read_research_context", {"section": "scope_index"}) == raw_index
    assert missing_required_reads(context) == missing  # The directory is not delivered evidence.
    assert context == before
    with pytest.raises(ValueError, match="读取路径"):
        mcp.read_research_context(section="scope_index", path=["unrelated_retained_data"])
    with pytest.raises(ValueError, match="风控范围索引"):
        mcp.read_research_context(section="catalogue")


def test_risk_scope_http_pages_use_read_projection_and_recheck_authority(client, monkeypatch):
    from sqlalchemy import event, select
    from studio_identity import Principal
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, ResearchRiskReadState
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services import research_access, research_run_context
    from watchlist_app.db.research_read_context import derive_read_context
    import watchlist_app.main as main
    context = {**_risk_scope_context(), "research_actor": {"kind": "user", "user_id": "pm-one"},
               "execution": {"attempt": 1}, "unrelated_retained_data": "不得加载的完整历史" * 100000}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="scope-topic", title="风控", visibility="private", created_by_user_id="pm-one"))
        session.flush()
        session.add(ResearchEntry(entry_id="scope-run", topic_id="scope-topic", kind="analysis", title="风控",
                                  status="running", context_json=context))
        session.add(ResearchEntry(entry_id="ordinary-run", topic_id="scope-topic", kind="analysis", title="普通研究",
            status="running", context_json={**context, "risk_run": False, "research_run": True, "run_id": "ordinary-run"}))
        session.commit()
    actor = Principal("pm-one", "PM", "default", resource_scope={"kind": "run", "id": "scope-run", "attempt": "1"})
    monkeypatch.setattr(main, "resolve_request", lambda *args, **kwargs: actor)
    checked_portfolios = []
    monkeypatch.setattr(research_access, "require_portfolio", checked_portfolios.append)
    loaded_fields = []
    original_load = research_run_context.load_run_fields
    def track_load(session, run_id, fields, **kwargs):
        loaded_fields.append((set(fields), kwargs.get("summary_only", False)))
        return original_load(session, run_id, fields, **kwargs)
    monkeypatch.setattr(research_run_context, "load_run_fields", track_load)
    def reject_full_entry_load(*_):
        pytest.fail("A scope index page must not materialize the complete retained research context")
    event.listen(ResearchEntry, "load", reject_full_entry_load)
    try:
        path = "/api/research/runs/scope-run/read"
        overview = client.post(path, json={"resource": "context"})
        assert overview.status_code == 200, overview.text
        assert len(overview.content) <= 48000 and overview.json()["risk_inputs"]["inline"] is False
        loaded_fields.clear()
        response = client.post(path, json={"resource": "context", "section": "scope_index", "path": ["instrument_detail_reads"]})
        assert response.status_code == 200, response.text
        assert response.json()["cutoff"] == context["cutoff"]
        assert response.json()["risk_scope"] == context["risk_scope"]
        expected = projection._risk_scope_index(derive_read_context(context))["instrument_detail_reads"]
        assert response.json()["data"] == expected
        assert any(fields == {"risk_inputs", "prior_inputs"} and summary for fields, summary in loaded_fields)
        assert all(not (fields & {"risk_inputs", "prior_inputs"}) or summary for fields, summary in loaded_fields)
        assert checked_portfolios and set(checked_portfolios) == {"p"}
        actor = Principal("pm-one", "PM", "default", resource_scope={"kind": "run", "id": "scope-run", "attempt": "2"})
        assert client.post(path, json={"resource": "context", "section": "scope_index"}).status_code == 409
        actor = Principal("other-pm", "Other", "default")
        assert client.post(path, json={"resource": "context", "section": "scope_index"}).status_code == 403
        actor = Principal("pm-one", "PM", "default", resource_scope={"kind": "run", "id": "ordinary-run", "attempt": "1"})
        ordinary = client.post("/api/research/runs/ordinary-run/read", json={"resource": "context", "section": "scope_index"})
        assert ordinary.status_code == 422 and "不在本轮上下文目录" in ordinary.text
    finally:
        event.remove(ResearchEntry, "load", reject_full_entry_load)
    with get_session_factory()() as session:
        assert session.scalars(select(ResearchRiskReadState)).all() == []
        assert session.get(ResearchEntry, "scope-run").context_json == context
