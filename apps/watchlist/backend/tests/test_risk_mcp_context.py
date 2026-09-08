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
    assert index["risk_inputs"]["portfolio"] == snapshot["portfolio"]
    assert len(json.dumps(index, ensure_ascii=False, indent=2).encode()) < 50000  # Harness tool-result contract.
    for instrument in index["instruments"]:
        packet = mcp.read_risk_instrument(instrument["instrument_id"])
        assert packet["current"]["instrument"]["performance_evidence"]["monthly_periods"][0]["return_pct"] == -2
        report = packet["current"]["research"][0]
        assert report["body"] == "完整风险分析及反向证据"
        assert report["case_id"].split("-")[1] == instrument["instrument_id"].split("-")[1]
        assert report["sources"][0]["published_at"] == "2026-09-04" and "text" not in report["sources"][0]
        assert report["evidence_json"]["occurred_at"] == "2026-09-03"
        assert packet["previous"] == {"unchanged": True}
        assert len(json.dumps(packet, ensure_ascii=False, indent=2).encode()) < 50000
    snapshot["research"][-1]["body"] = "新增配售风险"
    updated = mcp.read_risk_instrument("asset-11")
    assert updated["current"]["research"][0]["body"] == "新增配售风险"
    assert updated["previous"]["research"][0]["body"] == "完整风险分析及反向证据"
    with pytest.raises(ValueError, match="本次风控范围"):
        mcp.read_risk_instrument("outside")


def test_conversation_is_preserved_and_cannot_read_risk_scope(monkeypatch):
    context = {"conversation": [{"body": "PM观点"}]}
    monkeypatch.setattr(mcp, "request", lambda _: context)
    assert mcp.read_research_context()["conversation"] == [{"body": "PM观点"}]
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
