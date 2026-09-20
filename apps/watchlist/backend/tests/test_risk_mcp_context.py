import copy
import json
import asyncio

import pytest

from watchlist_app import research_mcp as mcp


def test_risk_tools_keep_all_instruments_and_shared_reports_without_spilling(monkeypatch):
    instruments = [{"instrument_id": f"asset-{i}", "name": f"标的{i}",
        "risk": {"current_drawdown": -6}, "performance_evidence": {"monthly_periods": [{"return_pct": -2}]}}
        for i in range(12)]
    cases = [{"case_id": f"report-{i}", "instrument_id": f"asset-{i}", "title": "研究员风险上报",
        "body": "完整风险分析及反向证据", "signal": "sector:financing", "severity": "attention",
        "history_json": [{"body": "旧版" * 20000}], "evidence_json": {"direction": "risk",
            "published_at": "2026-09-04", "occurred_at": "2026-09-03", "source_ids": [f"source-{i}"],
            "sources": [{"source_id": f"source-{i}", "url": "https://example.com/disclosure",
                         "published_at": "2026-09-04", "text": "原始披露" * 30000}]}}
        for i in range(12)]
    snapshot = {"scope": {"kind": "portfolio", "id": "p"}, "scope_available": True,
        "instrument_ids": [i["instrument_id"] for i in instruments], "input_as_of": "2026-09-04",
        "instruments": instruments, "research": cases, "quantitative": [], "coverage": [],
        "portfolio": {"nav": 10000, "positions": [{"instrument_id": "asset-11", "market_value_nav_pct": 10}]},
        "limitations": []}
    context = {"risk_run": True, "risk_scope": {"portfolio_id": "p"}, "cutoff": "2026-09-07T00:00:00+00:00",
               "risk_inputs": snapshot, "prior_inputs": copy.deepcopy(snapshot)}
    monkeypatch.setattr(mcp, "request", lambda _: context)
    index = mcp.read_research_context()
    assert len(index["instruments"]) == len(index["reports"]) == 12
    assert sum(page["instrument_count"] for page in index["instrument_overview_pages"]) == 12
    assert index["instrument_overview_pages"][0]["offset"] == 0
    assert index["risk_inputs"]["portfolio"] == snapshot["portfolio"]
    assert len(json.dumps(index, ensure_ascii=False, indent=2).encode()) < 50000  # Harness tool-result contract.
    for instrument in index["instruments"]:
        packet = mcp.read_risk_instrument(instrument["instrument_id"])
        assert packet["current"]["instrument"]["performance_evidence"]["monthly_periods"][0]["return_pct"] == -2
        assert packet["current_counts"]["research"] == 1
        packet = mcp.read_risk_instrument(instrument["instrument_id"], section="cases")
        report = packet["current"]["research"][0]
        assert report["body"] == "完整风险分析及反向证据"
        assert report["case_id"].split("-")[1] == instrument["instrument_id"].split("-")[1]
        assert report["sources"][0]["published_at"] == "2026-09-04" and "text" not in report["sources"][0]
        assert report["evidence_json"]["occurred_at"] == "2026-09-03"
        assert packet["previous"] == {"unchanged": True}
        assert len(json.dumps(packet, ensure_ascii=False, indent=2).encode()) < 50000
    snapshot["research"][-1]["body"] = "新增配售风险"
    updated = mcp.read_risk_instrument("asset-11", section="cases")
    assert updated["current"]["research"][0]["body"] == "新增配售风险"
    assert updated["previous"]["research"][0]["body"] == "完整风险分析及反向证据"
    with pytest.raises(ValueError, match="本次风控范围"):
        mcp.read_risk_instrument("outside")


def test_batch_risk_overviews_preserve_every_individual_packet_and_authorize_each_page(monkeypatch):
    instruments = [{"instrument_id": f"asset-{i}", "name": f"标的{i}",
                    "risk": {"current_drawdown": -i, "quality_note": "完整指标说明" * 250},
                    "performance_evidence": {"monthly_periods": [{"return_pct": -2}]},
                    "research_context": {"records": [{"source_id": f"pm-{i}", "value": {"body": "经理判断"}}]}}
                   for i in range(30)]
    snapshot = {"instrument_ids": [item["instrument_id"] for item in instruments], "instruments": instruments,
                "research": [], "quantitative": [], "coverage": []}
    prior = copy.deepcopy(snapshot)
    prior["instruments"][2]["risk"]["current_drawdown"] = -12
    prior["research"] = [{"case_id": "prior-only", "instrument_id": "asset-3", "body": "前次风险"}]
    context = {"risk_run": True, "cutoff": "2026-09-20T00:00:00Z", "risk_inputs": snapshot, "prior_inputs": prior}
    before = copy.deepcopy(context)
    requests = []
    monkeypatch.setattr(mcp, "request", lambda suffix: requests.append(suffix) or context)
    expected = [mcp.read_risk_instrument(iid) for iid in snapshot["instrument_ids"]]
    requests.clear()
    offset, pages, observed = 0, 0, []
    while True:
        result = asyncio.run(mcp.mcp.call_tool("read_risk_instruments", {"offset": offset}))
        page = result.structured_content
        pages += 1
        assert len(result.content[0].text.encode()) <= 48000
        assert json.loads(result.content[0].text) == page
        assert page["total"] == 30
        assert page["offset"] == offset
        assert page["instruments"]
        observed.extend(page["instruments"])
        if page["next_offset"] is None:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert 1 < pages < len(instruments)
    assert requests == ["context"] * pages
    assert observed == expected
    assert observed[2]["previous"] != {"unchanged": True}
    assert observed[3]["previous_counts"]["research"] == 1
    assert context == before

    plan = mcp._risk_overview_pages(context)
    assert sum(page["instrument_count"] for page in plan) == len(instruments)
    assert len(plan) == pages
    requests.clear()
    independent = [mcp.read_risk_instruments(offset=page["offset"]) for page in reversed(plan)]
    assert [item for page in sorted(independent, key=lambda page: page["offset"])
            for item in page["instruments"]] == expected
    assert requests == ["context"] * pages
    assert context == before

    def revoked(_):
        raise PermissionError("revoked")
    monkeypatch.setattr(mcp, "request", revoked)
    with pytest.raises(PermissionError, match="revoked"):
        mcp.read_risk_instruments(offset=offset)


def test_batch_risk_overviews_reject_invalid_scope_offsets_and_oversized_single_item(monkeypatch):
    context = {"risk_run": True, "cutoff": "2026-09-20T00:00:00Z", "risk_inputs": {
        "instrument_ids": [], "instruments": [], "research": [], "quantitative": [], "coverage": []}}
    monkeypatch.setattr(mcp, "request", lambda _: context)
    page = mcp.read_risk_instruments()
    assert page["instruments"] == [] and page["total"] == 0 and page["next_offset"] is None
    for offset in [-1, True, 1]:
        with pytest.raises(ValueError, match="分页"):
            mcp.read_risk_instruments(offset=offset)
    context["risk_run"] = False
    with pytest.raises(ValueError, match="仅风控"):
        mcp.read_risk_instruments()
    context["risk_run"] = True
    context["risk_inputs"].update(instrument_ids=["oversized"], instruments=[{
        "instrument_id": "oversized", "risk": {"unclipped_evidence": "完整" * 50000}}])
    with pytest.raises(ValueError, match="未截断"):
        mcp.read_risk_instruments()
    assert mcp._risk_overview_pages(context) == [{"tool": "read_risk_instrument", "instrument_id": "oversized",
                                                "section": "overview", "instrument_count": 1}]


def test_scope_plan_keeps_single_overview_that_fits_without_batch_envelope(monkeypatch):
    instruments = [{"instrument_id": iid, "name": iid, "risk": {"evidence": ""}}
                   for iid in ["before", "large", "after"]]
    context = {"risk_run": True, "risk_scope": {"watchlist_id": "scope"}, "cutoff": "2026-09-20T00:00:00Z",
               "risk_inputs": {"instrument_ids": [item["instrument_id"] for item in instruments],
                               "instruments": instruments, "research": [], "quantitative": [], "coverage": []}}
    monkeypatch.setattr(mcp, "request", lambda _: context)
    byte_size = lambda payload: len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    empty_size = byte_size(mcp.read_risk_instrument("large"))
    instruments[1]["risk"]["evidence"] = "x" * (47950 - empty_size)
    standalone = mcp.read_risk_instrument("large")
    assert byte_size(standalone) == 47950
    with pytest.raises(ValueError, match="批量页上限"):
        mcp.read_risk_instruments(offset=1)

    index = mcp.read_research_context()
    plan = index["instrument_overview_pages"]
    assert plan == [
        {"tool": "read_risk_instruments", "offset": 0, "instrument_count": 1},
        {"tool": "read_risk_instrument", "instrument_id": "large", "section": "overview", "instrument_count": 1},
        {"tool": "read_risk_instruments", "offset": 2, "instrument_count": 1},
    ]
    actual = []
    for entry in plan:
        if entry["tool"] == "read_risk_instrument":
            actual.append(mcp.read_risk_instrument(entry["instrument_id"]))
        else:
            actual.extend(mcp.read_risk_instruments(offset=entry["offset"])["instruments"])
    assert actual == [mcp.read_risk_instrument(item["instrument_id"]) for item in instruments]


def test_risk_instrument_pages_real_peer_evidence_cases_and_exact_samples(monkeypatch):
    from datetime import date, timedelta
    from math import sin
    from types import SimpleNamespace
    from watchlist_app.services import risk_performance as performance

    dates, day = [], date(2023, 9, 1)
    while len(dates) < 756:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    series = {"currency": "CNY", "frequency": {"resolved_frequency": "daily"},
        "metadata": {"return_series_status": "ready", "return_kind": "total_return"},
        "points": [{"date": day, "value": 100 * 1.0002 ** index * (1 + .004 * sin(index))}
                   for index, day in enumerate(dates)]}
    class RetainedSession:
        def get(self, model, instrument_id):
            if model is performance.InstrumentChartReadModel:
                return SimpleNamespace(payload_json={"research_returns": series},
                    source_cutoff_at="2026-09-13T00:00:00+00:00", data_freshness_status="fresh")
            if model is performance.InstrumentManualProfile:
                return SimpleNamespace(nav_settings_json={})
            if model is performance.InstrumentDetail:
                return SimpleNamespace(instrument_name=instrument_id)
            return None

    monkeypatch.setattr(performance, "_taxonomy_peers", lambda *_: ([f"peer-{i}" for i in range(15)], {}, None))
    evidence = performance.performance_evidence(RetainedSession(), "target", peer_scope={})
    assert len(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")).encode()) > 50000
    instrument = {"instrument_id": "target", "name": "同类基金", "performance_evidence": evidence}
    judgments = [{"kind": "pm_view", "source_id": f"pm-{i}", "value": {"body": "经理原始判断及其不确定性。" * 500,
        "author": f"经理{i}", "note_date": "2026-09-01"}} for i in range(12)]
    instrument["research_context"] = {"records": judgments, "counts": {"pm_view": 12}}
    cases = [{"case_id": f"case-{i}", "instrument_id": "target", "body": f"风险{i}：" + "实际风险与反证。" * 600,
              "evidence_json": {"source_ids": [f"source-{i}"]}} for i in range(35)]
    snapshot = {"instrument_ids": ["target"], "instruments": [instrument],
                "research": cases, "quantitative": [], "coverage": []}
    previous = copy.deepcopy(snapshot)
    previous["research"].append({"case_id": "previous-only", "instrument_id": "target", "body": "此前仍活跃的事项"})
    previous["research"][0]["body"] = "前次风险判断"
    previous["instruments"][0]["performance_evidence"]["comparisons"][0]["comparison"]["dates"] = dates[:-1]
    # A different interior observation can leave the displayed bounds/count unchanged.
    previous["instruments"][0]["performance_evidence"]["comparisons"][1]["comparison"]["dates"][1] = "2023-09-03"
    context = {"risk_run": True, "cutoff": "2026-09-13T00:00:00+00:00",
               "risk_inputs": snapshot, "prior_inputs": previous}
    original = copy.deepcopy(context)
    monkeypatch.setattr(mcp, "request", lambda _: context)

    overview = asyncio.run(mcp.mcp.call_tool("read_risk_instrument", {"instrument_id": "target"}))
    assert len(overview.content[0].text.encode()) <= 48000
    assert overview.structured_content["current_counts"] == {"research": 35, "quantitative": 0, "coverage": 0, "comparisons": 15}
    assert "comparisons" not in overview.structured_content["current"]["instrument"]["performance_evidence"]

    def pages(section, **kwargs):
        offset = 0
        while True:
            result = asyncio.run(mcp.mcp.call_tool("read_risk_instrument", {
                "instrument_id": "target", "section": section, "offset": offset, **kwargs}))
            assert len(result.content[0].text.encode()) <= 48000
            assert json.loads(result.content[0].text) == result.structured_content
            yield result.structured_content
            following = result.structured_content["next_offset"]
            if following is None:
                break
            assert following > offset
            offset = following

    case_pages = list(pages("cases"))
    assert len(case_pages) > 1
    current_cases = [case for page in case_pages for case in page["current"]["research"]]
    assert current_cases == [mcp._risk_case_brief(case) for case in cases]
    prior_cases = [case for page in case_pages for case in
                   (page["current"] if page["previous"] == {"unchanged": True} else page["previous"])["research"]]
    assert {case["case_id"] for case in prior_cases} == {case["case_id"] for case in previous["research"]}
    assert prior_cases[0]["body"] == "前次风险判断"

    comparison_pages = list(pages("comparisons"))
    assert len(comparison_pages) > 1
    comparisons = [row for page in comparison_pages for row in page["current"]["comparisons"]]
    assert [row["source_id"] for row in comparisons] == [row["source_id"] for row in evidence["comparisons"]]
    assert all("dates" not in row["comparison"] and row["sample_dates_count"] == 756 for row in comparisons)
    assert comparisons[0]["comparison"]["observations"] == 756
    assert comparisons[0]["comparison"]["sample_start"] == dates[0]
    assert comparisons[0]["comparison"]["sample_end"] == dates[-1]
    assert {source for page in comparison_pages for source in page.get("changed_sample_source_ids", [])} == {
        comparisons[0]["source_id"], comparisons[1]["source_id"]}
    sample_pages = list(pages("sample_dates", comparison_source_id=comparisons[0]["source_id"]))
    assert [day for page in sample_pages for day in page["current"]["dates"]] == dates
    assert [day for page in sample_pages for day in page["previous"]["dates"]] == dates[:-1]
    assert context == original
    research_pages = list(pages("research_context"))
    assert len(research_pages) > 1
    assert [row for page in research_pages for row in page["current"]["records"]] == judgments
    with pytest.raises(ValueError, match="来源不在"):
        mcp.read_risk_instrument("target", section="sample_dates", comparison_source_id="outside")


def test_risk_instrument_rejects_unreadable_single_record_without_truncating(monkeypatch):
    context = {"risk_run": True, "cutoff": "2026-09-13", "risk_inputs": {
        "instrument_ids": ["target"], "instruments": [], "research": [
            {"case_id": "large", "instrument_id": "target", "body": "原始风险" * 20000}],
        "quantitative": [], "coverage": []}}
    monkeypatch.setattr(mcp, "request", lambda _: context)
    with pytest.raises(ValueError, match="未截断"):
        mcp.read_risk_instrument("target", section="cases")
    with pytest.raises(ValueError, match="非负整数"):
        mcp.read_risk_instrument("target", section="cases", offset=-1)
    with pytest.raises(ValueError, match="超出"):
        mcp.read_risk_instrument("target", section="cases", offset=2)


def test_conversation_is_preserved_and_cannot_read_risk_scope(monkeypatch):
    context = {"conversation": [{"body": "PM观点"}]}
    monkeypatch.setattr(mcp, "request", lambda _: context)
    assert mcp.read_research_context()["sections"]["conversation"]["count"] == 1
    assert mcp.read_research_context(section="conversation")["data"] == [{"body": "PM观点"}]
    assert context == {"conversation": [{"body": "PM观点"}]}
    with pytest.raises(ValueError, match="本次风控范围"):
        mcp.read_risk_instrument("asset-0")
    with pytest.raises(ValueError, match="本次风控范围"):
        mcp.read_portfolio_risk("portfolio_metrics")


def test_portfolio_tools_partition_modules_and_bound_contracts(monkeypatch):
    derivatives = [{"holding_id": f"contract-{i}", "name": f"合约{i}", "terms": {"text": "实际条款" * 500}, "source_id": f"source-{i}"} for i in range(12)]
    sources = [{"source_id": row["source_id"], "holding_id": row["holding_id"]} for row in derivatives]
    modules = {"portfolio_id": "p", "as_of_date": "2026-09-04", "portfolio_metrics": {"risk_share": .6},
               "targets": {"breach": None}, "comparisons": {"status": "not_comparable"},
               "derivatives": {"positions": derivatives, "resources": {"cash": [{"currency": "USD", "amount": 100}]}},
               "sources": [*sources, {"source_id": "portfolio-risk:p:metrics", "portfolio_id": "p"}]}
    context = {"risk_run": True, "risk_scope": {"portfolio_id": "p"}, "cutoff": "2026-09-07",
               "risk_inputs": {"scope": {"kind": "portfolio", "id": "p"}, "instrument_ids": [], "instruments": [],
                               "research": [], "quantitative": [], "coverage": [], "portfolio": {"risk_context": modules}}}
    monkeypatch.setattr(mcp, "request", lambda _: context)
    index = mcp.read_research_context()
    assert "risk_context" not in index["risk_inputs"]["portfolio"]
    assert len(index["derivative_holdings"]) == 12
    assert len(json.dumps(index, ensure_ascii=False, indent=2).encode()) < 50000
    for section in ("portfolio_metrics", "targets", "comparisons"):
        assert mcp.read_portfolio_risk(section)["current"] == modules[section]
    packet = mcp.read_portfolio_risk("derivatives", "contract-11")
    assert packet["current"]["position"] == derivatives[-1]
    assert packet["sources"] == [sources[-1]]
    assert packet["current"]["resources"]["cash"][0]["amount"] == 100
    assert len(json.dumps(packet, ensure_ascii=False, indent=2).encode()) < 50000
    with pytest.raises(ValueError, match="组合快照内的合约"):
        mcp.read_portfolio_risk("derivatives", "outside")
    with pytest.raises(ValueError, match="仅用于"):
        mcp.read_portfolio_risk("portfolio_metrics", "contract-0")


def test_risk_submission_uses_structured_tool_arguments(monkeypatch):
    result = mcp.RiskReview(summary='需要核对 "策略意图"。', priorities=[], limitations=[])
    calls = []
    monkeypatch.setattr(mcp, "request", lambda suffix, payload: calls.append((suffix, payload)) or {"status": "accepted"})
    assert mcp.submit_risk_review(result) == {"status": "accepted"}
    assert calls == [("risk-draft", result.model_dump(mode="json"))]


def test_risk_submission_retry_requires_complete_result_and_never_saves_partial(monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    calls = []
    monkeypatch.setattr(mcp, "request", lambda suffix, payload: calls.append((suffix, payload)) or {"status": "accepted"})
    drafts = [
        ({"summary": "PRIVATE_SUMMARY_DO_NOT_ECHO", "priorities": []}, {("result", "limitations")}),
        ({"limitations": []}, {("result", "summary"), ("result", "priorities")}),
        ({"summary": "PRIVATE_SUMMARY_DO_NOT_ECHO", "priorities": [{"title": "Issue"}], "limitations": [],
          "unrecognized": "PRIVATE_INPUT_DO_NOT_ECHO"},
         {("result", "priorities", 0, "analysis"), ("result", "priorities", 0, "next_watch"), ("result", "unrecognized")}),
    ]
    for draft, expected in drafts:
        with pytest.raises(ToolError) as error:
            asyncio.run(mcp.mcp.call_tool("submit_risk_review", {"result": draft}))
        diagnostic = json.loads(str(error.value).split(": ", 1)[1])
        assert {tuple(issue["loc"]) for issue in diagnostic["issues"]} == expected
        assert diagnostic["required_result_fields"] == ["summary", "priorities", "limitations"]
        assert diagnostic["required_priority_fields"] == ["title", "analysis", "next_watch"]
        assert diagnostic["resubmit_mode"] == "complete_result"
        assert "PRIVATE_" not in str(error.value)
        assert calls == []

    complete = {"summary": "核对当前风险与证据限制。", "priorities": [], "limitations": []}
    accepted = asyncio.run(mcp.mcp.call_tool("submit_risk_review", {"result": complete}))
    assert json.loads(accepted.content[0].text) == {"status": "accepted"}
    assert calls == [("risk-draft", complete)]


def test_risk_submission_advertises_the_same_strict_result_schema():
    from mcp.server import MCPServer

    original = MCPServer("Original schema")
    @original.tool()
    def original_submit(result: mcp.RiskReview) -> dict:
        return {}

    tool = next(tool for tool in asyncio.run(mcp.mcp.list_tools()) if tool.name == "submit_risk_review")
    original_schema = asyncio.run(original.list_tools())[0].input_schema
    # Pydantic inlines the skipped parameter instead of naming it in $defs;
    # the required fields, bounds and nested Priority reference remain exact.
    assert original_schema["properties"]["result"] == {"$ref": "#/$defs/RiskReview"}
    assert tool.input_schema["properties"]["result"] == original_schema["$defs"]["RiskReview"]
    assert tool.input_schema["$defs"]["Priority"] == original_schema["$defs"]["Priority"]
    assert tool.input_schema["required"] == original_schema["required"]


def new_portfolio_modules():
    concentration_source = {"source_id": "concentration:p:2026-09-08:1", "source_type": "portfolio_concentration", "portfolio_id": "p"}
    tail_source = {"source_id": "tail:p:2026-09-08:0.95:1095", "source_type": "portfolio_tail_risk", "portfolio_id": "p",
                   "confidence": .95, "observation_count": 80, "tail_effective_observations": 4}
    target_sources = [{"source_id": f"portfolio-risk:p:targets:{tid}", "portfolio_id": "p", "taxonomy_id": tid}
                      for tid in ("industry", "country", "custom-risk")]
    modules = {
        "portfolio_id": "p", "as_of_date": "2026-09-08",
        "concentration": {"status": "partial", "settings_revision": 1, "source_id": concentration_source["source_id"], "sources": [concentration_source],
            "scopes": [{"scope": "taxonomy", "taxonomy_id": tid, "name": tid,
                        "rows": [{"entity_id": f"{tid}-node", "status": "breached", "weight": .3, "limit_weight": .2}]}
                       for tid in ("industry", "country", "custom-risk")]},
        "tail_risk": {"status": "available", "confidence": .95, "observation_count": 80,
            "tail_effective_observations": 4, "var_nav_fraction": .01, "expected_shortfall_nav_fraction": .02,
            "coverage_status": "partial", "excluded_gross_nav_fraction": .4,
            "scope_note": "FCN / Option 未建模，不是零风险。", "sources": [tail_source]},
        "targets_by_taxonomy": [{"taxonomy_id": tid, "name": tid, "status": "available", "source_id": f"portfolio-risk:p:targets:{tid}", "rows": [
            {"dimension": "weight", "current": .3, "target": .2, "breach": None}]} for tid in ("industry", "country", "custom-risk")],
        "sources": [concentration_source, tail_source, *target_sources,
                    {"source_id": "contract-source", "holding_id": "contract"},
                    {"source_id": "unrelated-risk-module", "portfolio_id": "p"}],
    }
    context = {"risk_run": True, "risk_scope": {"portfolio_id": "p"}, "cutoff": "2026-09-08",
               "risk_inputs": {"scope": {"kind": "portfolio", "id": "p"}, "instrument_ids": [],
                   "instruments": [], "research": [], "quantitative": [], "coverage": [],
                   "portfolio": {"selected_taxonomy_id": "industry", "risk_context": modules}}}
    return context, modules


def test_new_risk_sections_read_every_bound_taxonomy_and_filter_sources_by_module(monkeypatch):
    context, modules = new_portfolio_modules()
    original = copy.deepcopy(context)
    monkeypatch.setattr(mcp, "request", lambda _: context)
    index = mcp.read_research_context()
    assert set(index["portfolio_risk_sections"]) == {"concentration", "tail_risk", "targets_by_taxonomy"}
    assert {item["taxonomy_id"] for item in index["portfolio_taxonomies"]} == {"industry", "country", "custom-risk"}
    assert len(index["concentration_scopes"]) == 3
    assert "不随浏览器" in index["next_read"]
    concentration = mcp.read_portfolio_risk("concentration")
    assert len(concentration["current"]["scopes"]) == 3
    assert concentration["sources"] == modules["concentration"]["sources"]
    assert "sources" not in concentration["current"]
    tail = mcp.read_portfolio_risk("tail_risk")
    assert tail["sources"] == modules["tail_risk"]["sources"]
    assert tail["current"]["tail_effective_observations"] == 4
    assert tail["current"]["excluded_gross_nav_fraction"] == .4
    targets = mcp.read_portfolio_risk("targets_by_taxonomy")
    assert targets["current"] == modules["targets_by_taxonomy"]
    assert targets["sources"] == modules["sources"][2:5]
    assert context == original


def test_registered_mcp_schema_and_actual_dispatch_accept_new_sections(monkeypatch):
    context, _ = new_portfolio_modules()
    monkeypatch.setattr(mcp, "request", lambda _: context)
    tools = asyncio.run(mcp.mcp.list_tools())
    tool = next(item for item in tools if item.name == "read_portfolio_risk")
    assert {"concentration", "tail_risk", "targets_by_taxonomy"}.issubset(tool.input_schema["properties"]["section"]["enum"])
    for section in ("concentration", "tail_risk", "targets_by_taxonomy"):
        result = asyncio.run(mcp.mcp.call_tool("read_portfolio_risk", {"section": section}))
        assert result.structured_content["section"] == section
        assert len(result.content[0].text.encode()) < 50000


def test_new_module_reads_preserve_portfolio_and_contract_scope_restrictions(monkeypatch):
    context, _ = new_portfolio_modules()
    monkeypatch.setattr(mcp, "request", lambda _: context)
    for section in ("concentration", "tail_risk", "targets_by_taxonomy"):
        with pytest.raises(ValueError, match="仅用于"):
            mcp.read_portfolio_risk(section, "outside-contract")
    with pytest.raises(ValueError, match="不包含"):
        mcp.read_portfolio_risk("sources")
    context["risk_inputs"]["scope"]["id"] = "other"
    with pytest.raises(ValueError, match="不一致"):
        mcp.read_portfolio_risk("concentration")
    context["risk_inputs"]["scope"]["kind"] = "watchlist"
    with pytest.raises(ValueError, match="本次风控范围"):
        mcp.read_portfolio_risk("tail_risk")


def test_concentration_pages_all_taxonomies_and_large_group_sources_within_utf8_budget(monkeypatch):
    context, modules = new_portfolio_modules()
    aggregate = modules["concentration"]["sources"][0]
    exposure_sources = [{"source_id": f"allocated:p:contract:{index}:underlying", "portfolio_id": "p",
        "source_type": "portfolio_concentration", "end_date": "2026-09-08",
        "title": f"本金来源{index} " + "真实挂钩资产与本位币折算依据" * 50,
        "instrument_id": f"stock-{index}", "contract_id": f"contract-{index}",
        "amount_base": 1000, "allocation_weight": .25, "fx_rate_as_of_date": "2026-09-08",
        "detail_path": f"/portfolios/p/holdings/contract-{index}"} for index in range(260)]
    scopes = []
    expected_rows, expected_sources = [], {}
    for scope_number, tid in enumerate(("industry", "country", "custom")):
        rows = []
        for index in range(11):
            row_sources = exposure_sources if scope_number == index == 0 else [
                exposure_sources[(scope_number * 53 + index * 17 + j) % len(exposure_sources)] for j in range(37)]
            row = {"entity_id": f"node-{index}", "name": f"分类成员{index}", "status": "breached",
                   "weight": .3, "limit_weight": .2, "sources": row_sources}
            rows.append(row)
            identity = ("taxonomy", tid, row["entity_id"])
            expected_rows.append(identity)
            expected_sources[identity] = [source["source_id"] for source in row_sources]
        scopes.append({"scope": "taxonomy", "taxonomy_id": tid, "name": tid, "status": "complete", "rows": rows})
    modules["concentration"].update(scopes=scopes, sources=[aggregate, *exposure_sources],
        fcn_contracts=[{"contract_id": "not-needed-on-these-pages", "terms": "条款" * 50000}])
    modules["sources"] = [*modules["sources"], *exposure_sources]
    original = copy.deepcopy(context)
    assert len(json.dumps(modules["concentration"], ensure_ascii=False).encode()) > 50000
    monkeypatch.setattr(mcp, "request", lambda _: context)
    assert mcp.read_research_context()["concentration_row_count"] == 33

    seen_rows, seen_sources, row_page_sizes = [], {}, []
    offset = 0
    source_queue = []

    def read_page(**parameters):
        result = asyncio.run(mcp.mcp.call_tool("read_portfolio_risk", {"section": "concentration", **parameters}))
        assert len(result.content[0].text.encode()) <= 48000
        packet = result.structured_content
        assert "fcn_contracts" not in packet["current"]
        assert "sources" not in packet["current"]
        returned_ids = {source["source_id"] for source in packet["sources"]}
        requested_ids = {aggregate["source_id"]}
        for scope in packet["current"]["scopes"]:
            for row in scope["rows"]:
                identity = (scope["scope"], scope["taxonomy_id"], row["entity_id"])
                assert "sources" not in row
                requested_ids.update(row["source_ids"])
                if packet["page_kind"] == "rows":
                    seen_rows.append(identity)
                    seen_sources[identity] = []
                seen_sources[identity].extend(row["source_ids"])
        assert returned_ids == requested_ids
        assert packet["total_rows"] == 33
        return packet

    while offset is not None:
        packet = read_page(offset=offset)
        row_page_sizes.append(sum(len(scope["rows"]) for scope in packet["current"]["scopes"]))
        source_queue.extend(packet["source_continuations"])
        offset = packet["next_offset"]
    while source_queue:
        continuation = source_queue.pop(0)
        packet = read_page(offset=continuation["offset"], source_offset=continuation["source_offset"])
        assert packet["page_kind"] == "sources" and packet["next_offset"] is None
        source_queue.extend(packet["source_continuations"])
    assert seen_rows == expected_rows
    assert len(set(seen_rows)) == 33
    assert all(1 <= size <= 20 for size in row_page_sizes)
    assert seen_sources == expected_sources
    assert context == original


def test_concentration_pagination_rejects_out_of_scope_or_unsupported_cursors(monkeypatch):
    context, _ = new_portfolio_modules()
    monkeypatch.setattr(mcp, "request", lambda _: context)
    for kwargs in ({"offset": -1}, {"offset": 4}, {"offset": 0, "source_offset": -1}, {"offset": 0, "source_offset": 1}):
        with pytest.raises(ValueError, match="集中度"):
            mcp.read_portfolio_risk("concentration", **kwargs)
    with pytest.raises(ValueError, match="仅用于集中度"):
        mcp.read_portfolio_risk("tail_risk", offset=1)
    assert mcp.read_portfolio_risk("concentration", offset=3)["next_offset"] is None
