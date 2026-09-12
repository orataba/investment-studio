from copy import deepcopy
import asyncio
import json

import pytest

from watchlist_app import research_mcp as mcp


def _bytes(value):
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _large_evidence(*, changed):
    rows = []
    for company in range(95):
        for period in range(60):
            rows.append({"symbol": f"C{company:03}", "name": "公司名称", "weight_percent": 1,
                "frequency": "annual" if period % 2 else "quarter", "target_period_end": f"20{27 + period // 4}-12-31",
                "metric": "revenue_avg" if period % 3 else "eps_avg", "unit": "currency", "currency": "USD",
                "previous_currency": "USD", "current_value": 110 + period, "previous_value": 100 + period,
                "current_collected_at": "2026-09-08T05:00:00+00:00", "previous_collected_at": "2026-09-07T05:00:00+00:00",
                "current_currency_status": "inferred_from_reporting_currency", "previous_currency_status": "inferred_from_reporting_currency",
                "current_currency_source": {"statement_date": "2026-06-30", "source_dataset": "fmp_income_statement", "raw_sha256": "a" * 64},
                "previous_currency_source": {"statement_date": "2026-03-31", "source_dataset": "fmp_income_statement", "raw_sha256": "b" * 64},
                "current_num_analysts": 20, "previous_num_analysts": 19,
                "raw_sha256": f"{company}:{period}".ljust(64, "x"), "previous_raw_sha256": f"{company}:{period}".ljust(64, "y"),
                **({"delta": 10, "delta_pct": 10 / (100 + period) * 100, "percent_change_status": "available", "analyst_count_changed": True}
                   if changed else {"reason": "new_period_coverage"})})
    estimate = {"instrument_id": "xlk", "source_id": "estimates:current:xlk", "supported": True,
        "source_ids": [f"estimate:retained:{row['symbol']}:{index}:{row['raw_sha256']}" for index, row in enumerate(rows)],
        "current_snapshot": {"observation_id": "current"}, "previous_snapshot": {"observation_id": "previous"},
        "coverage": {"current_company_count": 95}, "semantics": ["采集时点不等于发布日期。"],
        "changes": rows if changed else [], "observations": [], "unmatched": [] if changed else rows,
        "gaps": [], "company_symbols": [f"C{number:03}" for number in range(95)]}
    asset = {"instrument_id": "xlk", "name": "Technology ETF", "instrument_type": "etf",
        "analyst_estimate_history": estimate, "research_dossier": {"instrument_id": "xlk", "body": "完整档案" * 10000}}
    return {"source_id": "instruments:retained", "tool": "instruments", "result": {"assets": [asset]}}


@pytest.mark.parametrize("changed", [False, True])
def test_large_overview_and_company_drilldown_preserve_all_rows_below_harness_limit(monkeypatch, changed):
    evidence = _large_evidence(changed=changed)
    original = deepcopy(evidence)
    original_estimate = original["result"]["assets"][0]["analyst_estimate_history"]
    assert _bytes(original_estimate["source_ids"]) > 50_000
    monkeypatch.setattr(mcp, "request", lambda *args: evidence)
    overview = mcp.read_instrument_research(["xlk"])
    estimate = overview["result"]["assets"][0]["analyst_estimate_history"]
    assert "source_ids" not in estimate and estimate["source_id"] == original_estimate["source_id"]
    assert estimate["company_symbols"] == [f"C{number:03}" for number in range(95)]
    assert sum(estimate["row_counts"].values()) == 5700
    assert estimate["coverage"] == {"current_company_count": 95}
    assert "changes" not in estimate and "unmatched" not in estimate
    assert "read_instrument_research" in estimate["detail_read"]
    assert "research_dossier" not in overview["result"]["assets"][0]
    assert _bytes(overview) < 50_000
    detail = mcp.read_instrument_research(["xlk"], estimate_symbol="c094")
    compact = detail["result"]["assets"][0]["analyst_estimate_history"]
    assert "source_ids" not in compact and compact["source_id"] == original_estimate["source_id"]
    table = "changes" if changed else "unmatched"
    data = compact["tables"][table]
    decoded = []
    for values in data["rows"]:
        row = {**data["common"], **dict(zip(data["columns"], values))}
        for field in ("current_currency_source", "previous_currency_source"):
            row[field] = compact["currency_source_receipts"][row.pop(field + "_index")]
        decoded.append(row)
    assert decoded == [row for row in original["result"]["assets"][0]["analyst_estimate_history"][table] if row["symbol"] == "C094"]
    assert len(decoded) == 60 and _bytes(detail) < 50_000
    assert evidence == original
    # Exercise the SDK conversion, whose default indented text would consume
    # considerably more of the harness allowance than the underlying JSON.
    for arguments, expected in [({"instrument_ids": ["xlk"]}, overview),
                                ({"instrument_ids": ["xlk"], "estimate_symbol": "c094"}, detail)]:
        wire = asyncio.run(mcp.mcp._tool_manager.get_tool("read_instrument_research").run(arguments, None))
        assert json.loads(wire.content[0].text) == expected
        assert wire.structured_content == expected
        assert len(wire.content[0].text.encode("utf-8")) < 50_000
    with pytest.raises(ValueError, match="一个股票或基金"):
        mcp.read_instrument_research(["xlk", "xlf"], estimate_symbol="C094")


def test_context_keeps_all_catalogue_identities_and_indexes_retained_evidence(monkeypatch):
    evidence = _large_evidence(changed=False)
    context = {"catalogue": [{"instrument_id": f"i{number}", "name": "标的", "instrument_type": "etf",
        "watchlist_ids": ["watch"], "attributes": {"long": "分类" * 1000}} for number in range(225)],
        "tool_evidence": [evidence], "question": "检查标的",
        "referenced_risk_case": {"case_id": "current-case", "instrument_id": "i0",
                                 "updated_at": "2026-09-12T00:00:00Z", "body": "入口所选风险"}}
    monkeypatch.setattr(mcp, "request", lambda *args: context)
    result = mcp.read_research_context()
    assert result["sections"]["catalogue"]["count"] == 225
    catalogue, offset = [], 0
    while offset is not None:
        page = mcp.read_research_context(section="catalogue", offset=offset)
        catalogue.extend(page["data"])
        offset = page["next_offset"]
    assert [row["instrument_id"] for row in catalogue] == [f"i{number}" for number in range(225)]
    assert catalogue[0]["watchlist_ids"] == ["watch"]
    evidence_page = mcp.read_research_context(section="tool_evidence")
    assert evidence_page["data"][0]["source_id"] == "instruments:retained"
    assert "result" not in evidence_page["data"][0] and _bytes(result) < 50_000
    assert "result" in context["tool_evidence"][0]
    assert mcp.read_research_context(section="referenced_risk_case")["data"] == context["referenced_risk_case"]
    assert "当前风险快照" in result["next_read"]


def test_sector_overview_and_company_use_bound_evidence_and_full_holdings_remain_readable(monkeypatch):
    evidence = _large_evidence(changed=True)
    asset = evidence["result"]["assets"][0]
    asset = {key: value for key, value in asset.items() if key != "research_dossier"}
    holdings = [{"symbol": f"C{number:03}", "name": "完整持仓", "description": "原始字段" * 50} for number in range(95)]
    asset["reference_data"] = {"sections": {"holdings": holdings}}
    context = {"sector_run": True, "instrument_ids": ["xlk"], "run_id": "daily", "cutoff": "2026-09-08T05:00:00Z",
        "catalogue": [{"instrument_id": "xlk"}],
        "instrument_inputs": [asset], "research_dossiers": [{"instrument_id": "xlk", "mandate": {"focus": "真实研究任务"}}],
        "sector_estimate_evidence": [asset["analyst_estimate_history"]],
        "sector_inputs": [{"instrument_id": "xlk", "stock_count": 95, "holdings": holdings, "leading_companies": holdings,
                           "source": {"provider": "FMP", "source_ids": asset["analyst_estimate_history"]["source_ids"]}}]}
    def request(suffix, payload=None):
        if suffix == "context":
            return context
        assert suffix == "sector-company/xlk/C094"
        return {"source_id": "fmp:daily:xlk:C094", "company": {"symbol": "C094", "annual_estimates": [{"revenue_avg": 100}]}}
    monkeypatch.setattr(mcp, "request", request)
    overview = mcp.read_research_instrument("xlk")
    assert _bytes(overview) < 50_000
    assert overview["sector_estimate_evidence"][0]["row_counts"]["changes"] == 5700
    assert overview["instrument_inputs"][0]["analyst_estimate_history"]["row_counts"]["changes"] == 5700
    assert mcp.read_research_dossier("xlk", section="mandate")["data"]["focus"] == "真实研究任务"
    assert overview["sector_inputs"][0]["source"]["source_count"] == 5700
    assert "source_ids" not in overview["sector_inputs"][0]["source"]
    recovered, offset = [], 0
    while offset is not None:
        page = mcp.read_research_instrument("xlk", "holdings", offset=offset)
        recovered.extend(page["reference_data"]["data"])
        offset = page["next_offset"]
    assert recovered == holdings
    lineage, offset = [], 0
    while offset is not None:
        page = mcp.read_research_instrument("xlk", "source_lineage", offset=offset, limit=500)
        assert _bytes(page) < 50_000
        lineage.extend(page["reference_data"]["data"])
        offset = page["next_offset"]
    assert lineage == [{"section": "company_source_ids", "value": sid} for sid in asset["analyst_estimate_history"]["source_ids"]]
    detail = mcp.read_sector_company("xlk", "C094")
    assert detail["company"]["annual_estimates"] == [{"revenue_avg": 100}]
    assert len(detail["analyst_estimate_history"]["tables"]["changes"]["rows"]) == 60
    assert _bytes(detail) < 50_000
    with pytest.raises(ValueError, match="本轮研究范围"):
        mcp.read_research_instrument("outside")


@pytest.mark.parametrize("instrument_id,instrument_type,section", [
    ("company", "equity", "financials"), ("broad-market", "etf", "holdings"),
    ("bond-fund", "etf", "holdings"), ("gold-fund", "etf", "holdings"),
    ("commodity-fund", "etf", "holdings"), ("public", "public_fund", "holdings"),
    ("private", "private_fund", "holdings"),
])
def test_reference_tables_and_lineage_are_complete_paginated_and_shared_between_tools(monkeypatch, instrument_id, instrument_type, section):
    rows = [{"source_id": f"source:{index}", "symbol": f"ASSET{index}", "currency": "CNY",
             "report_period": "2026-06-30", "observed_at": "2026-09-11T12:00:00Z",
             "revenue" if section == "financials" else "weight_percent": index / 100,
             "provider_detail": "完整披露原始字段" * 180} for index in range(65)]
    lineage = {section: [{"source_id": f"source:{index}", "observed_at": "2026-09-11T12:00:00Z",
                         "raw_sha256": str(index).ljust(64, "a")} for index in range(500)]}
    if section == "financials":
        lineage["financial_facts"] = [{"source_id": f"fact:{index}", "raw_sha256": str(index).ljust(64, "b")}
                                      for index in range(500)]
    asset = {"instrument_id": instrument_id, "instrument_type": instrument_type, "source_id": f"instrument:run:{instrument_id}",
             "analyst_focus": f"{instrument_id}专属研究重点", "reference_data": {"provider": "fixture",
                 "source": {"storage": "studio_market", "section_sources": lineage},
                 "sections": {"profile": {"name": instrument_id}, section: rows}},
             "risk_cases": [{"body": "已有风险正文" * 10000}]}
    context = {"run_id": "run", "cutoff": "2026-09-12T00:00:00Z", "catalogue": [{"instrument_id": instrument_id}],
               "instrument_inputs": [asset], "research_dossiers": [{"instrument_id": instrument_id, "mandate": {"focus": ["自己的研究重点"]}}]}
    original = deepcopy(context)
    evidence = {"source_id": "instruments:run", "result": {"assets": [asset]}}
    monkeypatch.setattr(mcp, "request", lambda suffix, payload=None: context if suffix == "context" else evidence)
    overview = mcp.read_instrument_research([instrument_id])
    single = mcp.read_research_instrument(instrument_id)
    assert overview["result"]["assets"][0] | {"dossier_read": None} == single["instrument_inputs"][0] | {"dossier_read": None}
    assert section in single["reference_sections"] and "source_lineage" in single["reference_sections"]
    assert single["instrument_inputs"][0]["analyst_focus"] == asset["analyst_focus"]
    assert _bytes(overview) < 50_000 and _bytes(single) < 50_000
    assert "section_sources" not in single["instrument_inputs"][0]["reference_data"]["source"]
    for selected, expected in [(section, rows), ("source_lineage", [{"section": key, "value": item} for key, values in lineage.items() for item in values])]:
        recovered, offset = [], 0
        while offset is not None:
            arguments = {"instrument_id": instrument_id, "section": selected, "offset": offset}
            wire = asyncio.run(mcp.mcp._tool_manager.get_tool("read_research_instrument").run(arguments, None))
            page = json.loads(wire.content[0].text)
            assert len(wire.content[0].text.encode()) < 50_000
            assert page["total_rows"] == len(expected) and page["cutoff"] == context["cutoff"]
            assert page["source_id"] == asset["source_id"]
            recovered.extend(page["reference_data"]["data"])
            offset = page["next_offset"]
        assert recovered == expected
    assert context == original
    with pytest.raises(ValueError, match="offset"):
        mcp.read_research_instrument(instrument_id, section, offset=-1)
    with pytest.raises(ValueError, match="范围"):
        mcp.read_research_instrument(instrument_id, section, offset=len(rows) + 1)
