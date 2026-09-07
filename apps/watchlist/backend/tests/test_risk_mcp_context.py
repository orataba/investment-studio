import copy
import json

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


def test_conversation_context_is_unchanged_and_cannot_read_risk_scope(monkeypatch):
    context = {"conversation": [{"body": "PM观点"}]}
    monkeypatch.setattr(mcp, "request", lambda _: context)
    assert mcp.read_research_context() is context
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
