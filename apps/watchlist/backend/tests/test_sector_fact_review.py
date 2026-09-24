from copy import deepcopy
from io import StringIO
import json

import pytest

from watchlist_app.services import sector_fact_review as review


def event(key="oil-rsi", **changes):
    return {"event_key": key, "action": "new", "direction": "risk",
            "title": "XLE短期过热", "body": "XLE RSI超过70。", "next_watch": "继续观察价格。",
            "confidence": "confirmed", "information_type": "fact", "recording_type": "backfill",
            "published_at": None, "occurred_at": None, "source_ids": ["source-1"], **changes}


def draft(events=None):
    return {"reviews": [
        {"instrument_id": "xle", "summary": "XLE出现过热风险。", "change_kind": "none", "coverage": ["XLE RSI>70，需继续关注。"],
         "events": [event()] if events is None else events, "research": None},
        {"instrument_id": "xlk", "summary": "", "change_kind": "none",
         "coverage": ["社媒覆盖有限"], "events": [], "research": None},
    ]}


def checked(decisions=None):
    return {"reviews": [{"instrument_id": "xle",
        "summary": "未发现经核实的重大新增风险或机会；汇编内容不能证明XLE出现新的过热风险。",
        "coverage": ["未能核实该汇编所述底层信息构成本轮新增事件。"],
        "decisions": decisions if decisions is not None else [
            {"event_key": "oil-rsi", "decision": "remove", "reason": "原文指向USO且是AI汇编旧内容。", "event": None},
        ]}]}


@pytest.fixture
def retained_run(monkeypatch):
    context = {"sector_run": True, "instrument_ids": ["xle", "xlk"],
        "cutoff": "2026-09-06T00:00:00+00:00",
        "sector_inputs": [{"instrument_id": "xle", "holdings": [{"holding_symbol": "XOM"}]},
                          {"instrument_id": "xlk", "holdings": [{"holding_symbol": "MSFT"}]}],
        "instrument_inputs": [{"instrument_id": "xle", "name": "XLE", "analyst_focus": "Use actual disclosed exposure.",
                               "holdings": {"report_period": "2026-06-30"}}, {"instrument_id": "xlk", "name": "XLK"}],
        "prior_events": [],
        "web_evidence": [
            {"operation": "search", "sources": [{"source_id": "search-only", "excerpt": "Not original text"}]},
            {"operation": "fetch", "sources": [{"source_id": "source-1", "url": "https://example.org/digest",
                "title": "AI generated market digest", "text": "USO RSI is above 70. This is an automated digest.",
                "published_at": "2026-08-01T12:00:00+00:00", "time_status": "verified"}]},
        ]}
    calls = []

    def api(run_id, suffix, payload=None):
        assert run_id == "test-run"
        calls.append((suffix, deepcopy(payload)))
        if suffix == "context?originals=true":
            return deepcopy(context)
        if suffix == "sector-company/xle/XOM":
            from watchlist_app.services.research_notebook import company_source
            return company_source(run_id, "xle", "XOM", {"symbol": "XOM", "annual_estimates": []})
        assert suffix == "sector-evidence" and payload["operation"] == "review"
        return payload

    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "test-run")
    monkeypatch.setattr(review, "_api_request", api)
    return calls


def test_removes_or_corrects_each_event_and_preserves_unreviewed_sectors(monkeypatch, retained_run):
    original = draft([event(), event("oil-report", source_ids=["source-1", "fmp:test-run:xle:XOM"])])
    corrected = event("oil-report", direction="uncertain", confidence="reported",
                      title="已核对的报道仍需跟踪", body="只保留原文支持且归属正确的新增事实。")
    result = checked([
        {"event_key": "oil-rsi", "decision": "remove", "reason": "USO不等于XLE，AI汇编不能证明新增。", "event": None},
        {"event_key": "oil-report", "decision": "keep", "reason": "改为实际报道强度，删除不支持的判断。", "event": corrected},
    ])
    result["reviews"][0]["summary"] = "仅保留已核对的报道；不再把USO指标写成XLE风险。"
    packets = []
    monkeypatch.setattr(review, "_call_reviewer", lambda packet: packets.append(packet) or result)
    output = review.review_output(json.dumps(original))
    assert output["reviews"][0]["events"] == [corrected]
    assert output["reviews"][0]["summary"] == result["reviews"][0]["summary"]
    assert output["reviews"][0]["coverage"] == result["reviews"][0]["coverage"]
    assert output["reviews"][1] == original["reviews"][1]
    assert len(packets) == 1
    assert "window_start" not in packets[0]
    assert packets[0]["draft_reviews"][0]["events"][0]["recording_type"] == "backfill"
    assert [row["instrument_id"] for row in packets[0]["sector_inputs"]] == ["xle"]
    instrument_index = packets[0]["instrument_inputs"][0]
    assert instrument_index["source_id"] == "instrument:test-run:xle"
    assert instrument_index["analyst_focus"] == "Use actual disclosed exposure."
    assert "snapshot" not in instrument_index and "holdings" not in instrument_index
    assert {s["source_id"] for s in packets[0]["sources"]} == {
        "source-1", "fmp:test-run:xle:XOM", "instrument:test-run:xle", "sector:test-run:xle"}
    receipts = [payload for suffix, payload in retained_run if suffix == "sector-evidence"]
    assert receipts == [
        {"operation": "review", "review": {"draft": original}},
        {"operation": "review", "review": {"result": result}},
        {"operation": "review", "review": {"checkpoint": {"draft": original, "cutoff": "2026-09-06T00:00:00+00:00", "result": output}}},
    ]


def test_deleting_all_candidates_replaces_the_original_summary(monkeypatch, retained_run):
    monkeypatch.setattr(review, "_call_reviewer", lambda _: checked())
    output = review.review_output(json.dumps(draft()))
    assert output["reviews"][0]["events"] == []
    assert output["reviews"][0]["summary"] == checked()["reviews"][0]["summary"]
    assert output["reviews"][0]["coverage"] == checked()["reviews"][0]["coverage"]
    assert "XLE RSI>70" not in json.dumps(output, ensure_ascii=False)


def test_missing_draft_fields_are_not_misreported_as_a_reviewer_service_failure():
    with pytest.raises(review.ValidationError) as error:
        review.ReviewResult.model_validate({"reviews": [{"events": []}]})
    failure = review._safe_failure(error.value)
    assert "主研究草稿结构不完整" in failure["summary"]
    assert "reviews.0.instrument_id" in failure["diagnostic"]


def test_archived_original_is_loaded_before_event_eligibility_check(monkeypatch):
    candidate = event(source_ids=["archived-original"])
    document = draft([candidate])
    context = {"sector_run": True, "instrument_ids": [row["instrument_id"] for row in document["reviews"]],
        "cutoff": "2026-09-06T00:00:00+00:00", "web_evidence": [],
        "research_dossiers": [{"instrument_id": "xle", "prior_sources": [{"source_id": "archived-original", "body_available": True}]}]}
    original = {"source_id": "archived-original", "instrument_id": "xle", "source_type": "public_source",
        "text": "The retained original describes the material event.", "published_at": None, "time_status": "unknown"}
    calls, packets = [], []
    def api(run_id, suffix, payload=None):
        calls.append((suffix, payload))
        if suffix == "context?originals=true": return context
        if suffix.startswith("dossier/xle?"): return original
        return payload
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "archive-run")
    monkeypatch.setattr(review, "_api_request", api)
    monkeypatch.setattr(review, "_call_reviewer", lambda packet: packets.append(packet) or checked())
    review.review_output(json.dumps(document))
    assert packets[0]["draft_reviews"][0]["events"] == [candidate]
    assert any(source.get("text") == original["text"] for source in packets[0]["sources"])
    assert not any("evidence_exclusions" in str(payload) for _, payload in calls)


def test_ineligible_sources_remove_candidates_before_reviewing_the_remaining_summary(monkeypatch, retained_run):
    api = review._api_request

    def with_compilation(run_id, suffix, payload=None):
        value = api(run_id, suffix, payload)
        if suffix == "context?originals=true":
            value["cutoff"] = "2026-09-06T01:00:00+00:00"
            sources = value["web_evidence"][1]["sources"]
            sources[0].update(published_at="2026-09-06T00:29:19+00:00", text=(
                "Geopolitics\nIraq fuel shortages as oil prices rise\n"
                "Sun, September 6, 2026 at 12:29 AM GMT+0·Geopolitics·Compiled by Adalytica Engine v1.12\n"
                "The USO oil ETF has its RSI above 70."))
        return value

    monkeypatch.setattr(review, "_api_request", with_compilation)
    def accept_sanitized(packet):
        assert all(not row["events"] for row in packet["draft_reviews"])
        return {"reviews": [{**{key: value for key, value in row.items() if key != "events"}, "decisions": []}
                            for row in packet["draft_reviews"]]}
    monkeypatch.setattr(review, "_call_reviewer", accept_sanitized)
    original = draft([event(source_ids=["source-1", "fmp:test-run:xle:XOM"])])
    output = review.review_output(json.dumps(original))
    assert output["reviews"][0] == {"instrument_id": "xle", "events": [], "change_kind": "none",
        "summary": "本轮未能核实候选所述的重大风险或机会。", "coverage": [review._EXCLUSION_NOTE], "research": None, "themes": []}
    assert output["reviews"][1] == original["reviews"][1]
    assert "XLE RSI" not in json.dumps(output, ensure_ascii=False)
    assert retained_run[:3] == [
        ("context?originals=true", None),
        ("sector-evidence", {"operation": "review", "review": {"draft": original}}),
        ("sector-evidence", {"operation": "review", "review": {"evidence_exclusions": [{
            "instrument_id": "xle", "event_key": "oil-rsi", "source_ids": original["reviews"][0]["events"][0]["source_ids"],
            "reason": review._EXCLUSION_NOTE}]}}),
    ]


@pytest.mark.parametrize("invalid", [
    {"reviews": []},
    {"reviews": [{**checked()["reviews"][0], "instrument_id": "unknown"}]},
    {"reviews": [{key: value for key, value in checked()["reviews"][0].items() if key != "coverage"}]},
    {"reviews": [{**checked()["reviews"][0], "decisions": []}]},
    checked([{"event_key": "unknown", "decision": "remove", "reason": "No", "event": None}]),
    checked(checked()["reviews"][0]["decisions"] * 2),
    checked([{"event_key": "oil-rsi", "decision": "keep", "reason": "Yes", "event": None}]),
    checked([{"event_key": "oil-rsi", "decision": "keep", "reason": "Yes", "event": event("unknown")}]),
    checked([{"event_key": "oil-rsi", "decision": "keep", "reason": "Yes", "event": event(instrument_id="unknown")}]),
    checked([{"event_key": "oil-rsi", "decision": "keep", "reason": "Yes", "event": event(source_ids=["invented-source"])}]),
    checked([{"event_key": "oil-rsi", "decision": "remove", "reason": "No", "event": event()}]),
])
def test_incomplete_or_invented_review_results_never_fall_back_to_draft(monkeypatch, retained_run, invalid):
    monkeypatch.setattr(review, "_call_reviewer", lambda _: invalid)
    with pytest.raises(ValueError):
        review.review_output(json.dumps(draft()))


def test_zero_events_skips_both_api_and_model_and_rejects_trailing_junk(monkeypatch):
    monkeypatch.setattr(review, "_api_request", lambda *a, **k: pytest.fail("No API call expected"))
    monkeypatch.setattr(review, "_call_reviewer", lambda *a, **k: pytest.fail("No model call expected"))
    original = draft([])
    for row in original["reviews"]:
        row["summary"] = ""
    assert review.review_output(json.dumps(original)) == original
    with pytest.raises(ValueError):
        review.review_output(json.dumps(original) + "\ntrailing commentary")


@pytest.mark.parametrize("raw", ["Invalid model final containing private-sentinel", json.dumps(draft())])
def test_cli_missing_structured_draft_retains_raw_output_without_entering_review(monkeypatch, retained_run, capsys, raw):
    monkeypatch.setattr(review.sys, "stdin", StringIO(raw))
    monkeypatch.setattr(review, "review_output", lambda *args: pytest.fail("A missing submission must not enter fact review"))
    with pytest.raises(SystemExit) as error:
        review.main()
    assert error.value.code == 1
    assert retained_run == [("context?originals=true", None), ("sector-evidence", {"operation": "review", "review": {"raw_draft": raw}})]
    captured = capsys.readouterr()
    assert captured.out == "" and "private-sentinel" not in captured.err
    details = json.loads(captured.err.removeprefix("SECTOR_REVIEW_ERROR "))
    assert json.loads(details.pop("diagnostic"))["location"].startswith("sector_fact_review.py:main:")
    assert details == {"type": "MissingResearchDraft",
                       "summary": "研究员未提交结构化草稿，本轮未进入事实核证或发布研究；已有研究记录保持不变。"}


def test_cli_uses_accepted_structured_draft_instead_of_model_prose(monkeypatch, capsys):
    submitted = draft()
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "submitted-run")
    monkeypatch.setattr(review.sys, "stdin", StringIO('已提交；不应解析这段文字中的 { 未转义引号。'))
    monkeypatch.setattr(review, "_api_request", lambda rid, suffix, payload=None: {"sector_run": True, "submitted_draft": submitted} if suffix == "context?originals=true" else payload)
    received = []
    monkeypatch.setattr(review, "review_output", lambda raw, **kwargs: received.append(json.loads(raw)) or submitted)
    review.main()
    assert received == [submitted]
    assert json.loads(capsys.readouterr().out) == submitted


def test_model_failure_is_propagated_after_preserving_original_draft(monkeypatch, retained_run):
    def fail(_):
        raise ValueError("No structured review result")
    monkeypatch.setattr(review, "_call_reviewer", fail)
    with pytest.raises(ValueError, match="No structured"):
        review.review_output(json.dumps(draft()))
    assert retained_run[-1][1] == {"operation": "review", "review": {"draft": draft()}}


def test_safe_failure_keeps_wrapped_timeout_cause_without_exception_text():
    error = review.SectorWebError("Private request details")
    error.__cause__ = TimeoutError("Private connection details")
    failure = review._safe_failure(error)
    assert failure["type"] == "TimeoutError" and "超时" in failure["summary"]
    assert "Private" not in json.dumps(failure)


def test_quiet_day_working_paper_is_reviewed_against_original_material(monkeypatch):
    paper = {"modules": [{"key": "fund-strategy", "summary": "管理人已证明策略有效。", "analysis": "没有可比估值依据。"}], "key_drivers": [],
        "questions": [{"key": "strategy", "question": "策略是否有效？",
        "assessment": "需要后续验证。", "evidence_for": [], "evidence_against": [], "next_check": "核对后续共同期间表现。",
        "status": "open", "source_ids": ["material:m1"]}], "important_changes": [], "next_research": [], "source_ids": ["material:m1"]}
    document = {"reviews": [{"instrument_id": "fund", "summary": "原判断", "coverage": [], "events": [], "research": paper}]}
    context = {"sector_run": True, "instrument_ids": ["fund"], "cutoff": "2026-09-06T00:00:00+00:00",
        "instrument_inputs": [], "research_dossiers": [{"instrument_id": "fund", "materials": [{"source_id": "material:m1", "title": "管理人月报"}],
        "historical_cases": [], "notebook": None}], "web_evidence": [{"operation": "search", "sources": []}]}
    material = {"source_id": "material:m1", "title": "管理人月报", "body": "管理人介绍策略，未给出验证样本。", "metadata": {"published_at": "2026-09-01"}}
    receipts, packets = [], []
    def api(run_id, suffix, payload=None):
        if suffix == "context?originals=true":
            return context
        if suffix.startswith("dossier/fund?"):
            return material
        receipts.append(payload)
        return payload
    corrected = {**paper, "modules": [{**paper["modules"][0], "summary": "管理人说明了策略机制，尚不能据此确认策略有效。"}], "catalysts": [], "facts": [], "mandate_update": None}
    result = {"reviews": [{"instrument_id": "fund", "summary": "继续验证策略。", "coverage": [], "decisions": [], "research": corrected}]}
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "quiet-run")
    monkeypatch.setattr(review, "_api_request", api)
    monkeypatch.setattr(review, "_call_reviewer", lambda packet: packets.append(packet) or result)
    output = review.review_output(json.dumps(document))
    assert len(packets) == 1
    assert any(s.get("body") == material["body"] for s in packets[0]["sources"])
    assert output["reviews"][0]["events"] == []
    assert output["reviews"][0]["research"] == {key: value for key, value in corrected.items() if key in paper}
    result["reviews"][0]["research"] = None
    assert review.review_output(json.dumps(document))["reviews"][0]["research"] is None
    result["reviews"][0]["research"] = {**corrected, "source_ids": ["invented-method-proof"]}
    with pytest.raises(ValueError, match="依据"):
        review.review_output(json.dumps(document))


@pytest.mark.parametrize("submitted", [False, True])
def test_chat_cli_preserves_answer_and_wraps_only_requested_shared_research(monkeypatch, capsys, submitted):
    answer = "当前讨论仍保留条件判断。"
    paper = {"reviews": [{"instrument_id": "stock", "research": {"next_research": ["核实竞争变化"]},
                           "change_kind": "knowledge"}]}
    context = {"research_run": True, **({"submitted_draft": paper} if submitted else {})}
    received, receipts = [], []
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "chat-run")
    monkeypatch.setattr(review.sys, "stdin", StringIO(answer))
    monkeypatch.setattr(review, "_api_request", lambda rid, suffix, payload=None:
        context if suffix == "context?originals=true" else receipts.append(payload) or payload)
    monkeypatch.setattr(review, "review_output", lambda raw, **kwargs: received.append(json.loads(raw)) or paper)
    review.main()
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["answer"] == answer and captured.err == ""
    assert result["research_result"] == (paper if submitted else None)
    assert received == ([paper] if submitted else [])
    if not submitted:
        assert result["research_publication"]["status"] == "not_requested" and receipts == []


def test_chat_review_failure_preserves_answer_without_publishing_or_exposing_exception(monkeypatch, capsys):
    answer = "这是一项仍待验证的看法。"
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "chat-run")
    monkeypatch.setattr(review.sys, "stdin", StringIO(answer))
    monkeypatch.setattr(review, "_api_request", lambda rid, suffix, payload=None:
        {"research_run": True, "submitted_draft": {"reviews": []}} if suffix == "context?originals=true" else payload)
    def fail(raw, **kwargs):
        raise ValueError("provider-private-diagnostic")
    monkeypatch.setattr(review, "review_output", fail)
    review.main()
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["answer"] == answer and result["research_result"] is None
    assert result["research_publication"]["status"] == "failed"
    assert "provider-private-diagnostic" not in captured.out + captured.err


def test_supported_forward_judgment_remains_active_and_sparse_research_is_not_expanded(monkeypatch):
    forecast = {"key": "demand-repair", "claim": "若订单恢复，下一季度收入有望改善", "horizon": "下一季度披露",
                "assumptions": ["订单转化保持正常"], "status": "active", "source_ids": ["issuer"]}
    paper = {"forecasts": [forecast], "investment_view": {"direction": "有条件看好", "risk": "订单转化仍不确定",
                                                         "source_ids": ["issuer"]}}
    document = {"reviews": [{"instrument_id": "stock", "change_kind": "knowledge", "research": paper}]}
    context = {"research_run": True, "instrument_ids": ["stock", "other"], "cutoff": "2026-09-08T00:00:00+00:00",
        "web_evidence": [{"operation": "fetch", "sources": [{"source_id": "issuer", "source_type": "public_source",
            "text": "公司披露在手订单增加，未来收入尚未公布。", "published_at": "2026-09-07"}]}]}
    reviewed = {"reviews": [{"instrument_id": "stock", "change_kind": "knowledge", "coverage": [], "decisions": [], "research": paper}]}
    packets = []
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "chat-run")
    monkeypatch.setattr(review, "_api_request", lambda rid, suffix, payload=None:
        context if suffix == "context?originals=true" else payload)
    monkeypatch.setattr(review, "_call_reviewer", lambda packet: packets.append(packet) or reviewed)
    result = review.review_output(json.dumps(document))["reviews"][0]
    assert result["summary"] == "" and result["events"] == []
    assert result["research"] == paper
    assert result["research"]["forecasts"][0]["status"] == "active"
    assert "fundamental_view" not in result["research"] and "valuation_view" not in result["research"]
    assert {source["source_id"] for source in packets[0]["sources"]} == {"issuer"}
    assert [item["instrument_id"] for item in packets[0]["draft_reviews"]] == ["stock"]


def test_chat_context_read_failure_preserves_already_generated_answer(monkeypatch, capsys):
    monkeypatch.setenv('INVESTMENT_STUDIO_RESEARCH_RUN_ID', 'chat')
    monkeypatch.setattr(review.sys, 'argv', ['sector_fact_review', '--conversation'])
    monkeypatch.setattr(review.sys, 'stdin', StringIO('已生成的研究讨论。'))
    monkeypatch.setattr(review, '_api_request', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('local unavailable')))
    review.main()
    result = json.loads(capsys.readouterr().out)
    assert result['answer'] == '已生成的研究讨论。'
    assert result['research_result'] is None and result['research_publication']['status'] == 'failed'


def test_reviewer_defaults_cannot_clear_unrequested_knowledge_or_expand_a_forecast_update():
    paper = {'investment_view': {'risk': '短期波动升高'}, 'forecasts': [{
        'key': 'demand', 'claim': '需求支持仍可能持续', 'horizon': '未来一季', 'status': 'active'}]}
    original = {'reviews': [{'instrument_id': 'gold', 'events': [], 'change_kind': 'knowledge', 'research': paper}]}
    # A model may fill schema defaults even though only two sparse changes were requested.
    full = review.ResearchNotebook.model_validate(paper).model_dump(mode='json')
    result = {'reviews': [{'instrument_id': 'gold', 'coverage': [], 'decisions': [],
        'change_kind': 'investment', 'summary': '没有新增依据的重复报告', 'research': full}]}
    corrected = review._apply_checks(original, result, [])['reviews'][0]
    assert corrected['research'] == paper
    assert corrected['change_kind'] == 'knowledge'


_CITED_RESEARCH_ITEMS = [
    ("investment_view", {"direction": "已披露财务支持的条件性判断"}),
    ("questions", {"key": "earnings", "question": "盈利质量是否改善", "assessment": "披露数据仍需持续验证", "next_check": "下一次财报"}),
    ("forecasts", {"key": "earnings", "claim": "盈利质量可能改善", "horizon": "下一季度"}),
    ("forecast_reviews", {"key": "earnings-review", "outcome": "部分观察已出现，机制尚待验证", "related_research_update_id": "research:original"}),
    ("lessons", {"key": "earnings-lesson", "lesson": "财务比较须保持相同口径"}),
]


@pytest.mark.parametrize("field,item", _CITED_RESEARCH_ITEMS)
@pytest.mark.parametrize("citation", ["valid", "unknown", "wrong_instrument"])
def test_reviewer_may_attach_only_valid_nonempty_evidence_to_a_proposed_research_item(field, item, citation):
    paper = {field: item if field == "investment_view" else [item]}
    corrected_item = {**item, "source_ids": ["source"], "unrequested_field": "not part of this change"}
    corrected_paper = {field: corrected_item if field == "investment_view" else [corrected_item]}
    original = {"reviews": [{"instrument_id": "600036-sh", "events": [], "research": paper}]}
    checked = {"reviews": [{"instrument_id": "600036-sh", "coverage": [], "decisions": [], "research": corrected_paper}]}
    source = {"source_id": "source", "source_type": "public_source", "text": "已取得原始财务披露和日程",
        "instrument_id": "other-stock" if citation == "wrong_instrument" else "600036-sh", "published_at": "2026-09-11"}
    if citation != "valid":
        with pytest.raises(ValueError, match="研究底稿|预定事件"):
            review._apply_checks(original, checked, [] if citation == "unknown" else [source])
        return
    result = review._apply_checks(original, checked, [source])["reviews"][0]["research"]
    assert result == {field: {**item, "source_ids": ["source"]} if field == "investment_view" else [{**item, "source_ids": ["source"]}]}


def test_citation_exception_does_not_add_unsubmitted_research_items():
    paper = {"questions": [{"key": "original", "question": "原问题", "assessment": "等待披露", "next_check": "下一季度"}]}
    corrected = {"questions": [*paper["questions"], {"key": "invented", "question": "不在草稿中的问题",
        "assessment": "新判断", "next_check": "明天", "source_ids": ["unavailable-source"]}],
        "investment_view": {"direction": "草稿未提交的判断", "source_ids": ["unavailable-source"]}}
    result = review._reviewed_delta(paper, review.ResearchNotebook.model_validate(corrected)).model_dump(mode="json", exclude_unset=True)
    assert result == paper


def test_reviewer_can_supply_missing_root_citations_without_erasing_existing_defaults():
    from watchlist_app.services.research_notebook import retain_notebook
    source = {"source_id": "original", "source_type": "public_source", "text": "Dated disclosed fact"}
    paper = {"modules": [{"key": "pricing-compensation", "summary": "已有披露事实仍适用"}]}
    prior = retain_notebook(review.ResearchNotebook(**paper, source_ids=["original"]), None,
        {"original": source}, "old", "2026-09-01T00:00:00+00:00")
    for correction in (review.ResearchNotebook(**paper).model_dump(mode="json"), {**paper, "source_ids": ["original"]}):
        document = {"reviews": [{"instrument_id": "xlk", "events": [], "research": paper}]}
        checked = {"reviews": [{"instrument_id": "xlk", "coverage": [], "decisions": [], "research": correction}]}
        result = review._apply_checks(document, checked, [source])["reviews"][0]["research"]
        if correction["source_ids"]:
            assert result["source_ids"] == ["original"]
        else:
            assert "source_ids" not in result
        saved = retain_notebook(review.ResearchNotebook.model_validate(result), prior,
            {"original": source}, "new", "2026-09-12T00:00:00+00:00")
        assert saved["source_ids"] == ["original"]


def test_uncited_snapshot_facts_remain_citable_but_pricing_hypothesis_is_not_restored(monkeypatch):
    snapshot_id = "instrument:uncited-run:xlk"
    metric = {"source_id": "computed:actual-risk", "source_type": "computed_metric", "instrument_id": "xlk",
        "scope": "public_market", "as_of": "2026-09-12T00:00:00+00:00", "methodology": "Dated price observations",
        "data": {"volatility": 0.18}}
    asset = {"instrument_id": "xlk", "name": "XLK", "performance": {"return_ytd": 0.10},
        "reference_data": {"sections": {"financials": ["UNREQUESTED_FINANCIAL_TABLE"]}}}
    context = {"sector_run": True, "instrument_ids": ["xlk"], "cutoff": metric["as_of"],
        "instrument_inputs": [asset], "computed_metrics": [metric], "web_evidence": [], "market_queries": []}
    question = {"key": "ai-roi", "question": "AI投入能否改善现金流？", "assessment": "探索性假设，仍待经营披露",
        "next_check": "观察下一次经营披露中投入与现金流的关系", "status": "open"}
    paper = {"investment_view": {"direction": "价格上涨证明市场接受AI叙事", "attractiveness": "估值中性偏高"},
        "questions": [question]}
    corrected = {"investment_view": {"direction": "已观察到价格上涨，驱动因素仍待验证", "attractiveness": "缺少估值与预期输入，尚不能判断定价程度",
        "source_ids": [snapshot_id]}, "questions": [question], "source_ids": [metric["source_id"]]}
    def reviewer(packet):
        sources = {row["source_id"]: row for row in packet["sources"]}
        assert set(sources) == {snapshot_id, metric["source_id"]}
        assert sources[snapshot_id]["snapshot"]["performance"] == asset["performance"]
        assert sources[snapshot_id]["snapshot_scope"] == "overview"
        assert "UNREQUESTED_FINANCIAL_TABLE" not in json.dumps(packet)
        assert json.dumps(packet).count('"return_ytd"') == 1
        return {"reviews": [{"instrument_id": "xlk", "coverage": ["估值与当前市场预期仍待核实"], "decisions": [],
            "change_kind": "knowledge", "research": corrected}]}
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "uncited-run")
    monkeypatch.setattr(review, "_api_request", lambda rid, suffix, payload=None: context if suffix == "context?originals=true" else payload)
    monkeypatch.setattr(review, "_call_reviewer", reviewer)
    output = review.review_output(json.dumps({"reviews": [{"instrument_id": "xlk", "change_kind": "knowledge", "research": paper}]}))["reviews"][0]
    assert output["research"] == corrected
    assert output["research"]["questions"] == [question]  # Exploration needs no invented proof or citation.
    assert "市场接受AI叙事" not in json.dumps(output, ensure_ascii=False)
    assert "估值中性偏高" not in json.dumps(output, ensure_ascii=False)


@pytest.mark.parametrize("field,item", _CITED_RESEARCH_ITEMS)
def test_default_empty_citations_do_not_erase_an_existing_items_sources(field, item):
    from watchlist_app.services.research_notebook import retain_notebook
    source = {"source_id": "existing-original", "source_type": "public_source", "instrument_id": "600036-sh",
        "text": "原始披露", "published_at": "2026-09-01"}
    previous_item = {**item, "source_ids": [source["source_id"]]}
    old_paper = {field: previous_item if field == "investment_view" else [previous_item]}
    sources = {source["source_id"]: source}
    prior = retain_notebook(review.ResearchNotebook.model_validate(old_paper), None, sources, "old-run", "2026-09-01T00:00:00+00:00")
    proposed = {field: item if field == "investment_view" else [item]}
    # The reviewer explicitly echoes its schema's empty defaults. The researcher
    # did not request a source change, so this must remain absent from the patch.
    defaults = review.ResearchNotebook.model_validate(proposed).model_dump(mode="json")
    delta = review._reviewed_delta(proposed, review.ResearchNotebook.model_validate(defaults))
    sparse = delta.model_dump(mode="json", exclude_unset=True)
    assert "source_ids" not in (sparse[field] if field == "investment_view" else sparse[field][0])
    saved = retain_notebook(delta, prior, sources, "new-run", "2026-09-12T00:00:00+00:00")
    assert (saved[field] if field == "investment_view" else saved[field][0])["source_ids"] == [source["source_id"]]


def test_reviewer_can_drop_unsupported_research_without_clearing_the_previous_view():
    original = {'reviews': [{'instrument_id': 'gold', 'events': [], 'change_kind': 'investment',
        'research': {'investment_view': {'direction': '缺少证据的看涨结论'}}}]}
    result = {'reviews': [{'instrument_id': 'gold', 'coverage': [], 'decisions': [],
        'change_kind': 'none', 'research': None}]}
    corrected = review._apply_checks(original, result, [])['reviews'][0]
    assert corrected['research'] is None and corrected['change_kind'] == 'none'
    # Null inside a corrected patch must not accidentally withdraw the saved view either.
    result['reviews'][0]['research'] = {'investment_view': None}
    assert review._apply_checks(original, result, [])['reviews'][0]['research'] == {}
    original['reviews'][0]['research'] = {'investment_view': None}
    assert review._apply_checks(original, result, [])['reviews'][0]['research'] == {'investment_view': None}


@pytest.mark.parametrize("existing_theme", [False, True], ids=["rejected-new-theme", "rejected-existing-theme-update"])
def test_rejected_theme_keeps_supported_event_and_preserves_only_valid_links(monkeypatch, retained_run, existing_theme):
    candidate = event(theme_ids=["demand", "unrelated-theme"])
    document = draft([candidate])
    document["reviews"][0]["themes"] = [{"theme_key": "demand", "title": "需求持续性",
        "question": "新增订单能否转化为持续收入", "source_ids": ["source-1"]}]
    api = review._api_request

    def with_themes(run_id, suffix, payload=None):
        result = api(run_id, suffix, payload)
        if suffix == "context?originals=true":
            result["research_dossiers"] = [{"instrument_id": "xle", "themes": [
                {"theme_id": "unrelated-theme", "theme_key": "capacity"},
                *([{"theme_id": "existing-demand", "theme_key": "demand"}] if existing_theme else []),
            ]}]
        return result

    reviewed = checked([{"event_key": candidate["event_key"], "decision": "keep",
        "reason": "事件有依据，但无需建立或改写主题", "event": candidate}])
    reviewed["reviews"][0]["themes"] = []
    monkeypatch.setattr(review, "_api_request", with_themes)
    monkeypatch.setattr(review, "_call_reviewer", lambda packet: reviewed)
    result = review.review_output(json.dumps(document))["reviews"][0]

    expected_links = ["existing-demand", "unrelated-theme"] if existing_theme else ["unrelated-theme"]
    assert result["themes"] == []
    assert result["events"] == [{**candidate, "theme_ids": expected_links}]


@pytest.mark.parametrize("field,body_field", [("forecast_reviews", "outcome"), ("lessons", "lesson")])
def test_sparse_reflection_reads_inherited_original_before_review_without_expanding_patch(monkeypatch, field, body_field):
    original_update = {"update_id": "opinion:pm-note:1", "instrument_id": "stock", "kind": "opinion",
        "recorded_at": "2026-09-01T00:00:00+00:00", "body": "当时保存的判断，与目前观点不同"}
    patch = {"key": "demand-check", body_field: "新披露仍未能验证原判断"}
    paper = {field: [patch]}
    document = {"reviews": [{"instrument_id": "stock", "research": paper}]}
    context = {"research_run": True, "instrument_ids": ["stock"], "cutoff": "2026-09-09T00:00:00+00:00",
        "research_dossiers": [{"instrument_id": "stock", "notebook": {field: [{
            "key": "demand-check", body_field: "此前仍待验证", "related_research_update_id": original_update["update_id"],
        }]}}]}
    reviewed = {"reviews": [{"instrument_id": "stock", "coverage": [], "decisions": [], "research": paper}]}
    reads, packets = [], []

    def api(run_id, suffix, payload=None):
        assert run_id == "reflection-run"
        if suffix == "context?originals=true":
            return deepcopy(context)
        if suffix.startswith("dossier/stock?"):
            from urllib.parse import parse_qs, urlsplit
            assert parse_qs(urlsplit(suffix).query) == {"update_id": [original_update["update_id"]]}
            reads.append(suffix)
            return {"kind": "research_update", "value": deepcopy(original_update)}
        assert suffix == "sector-evidence"
        return payload

    def check(packet):
        assert len(reads) == 1
        packets.append(packet)
        return reviewed

    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "reflection-run")
    monkeypatch.setattr(review, "_api_request", api)
    monkeypatch.setattr(review, "_call_reviewer", check)
    result = review.review_output(json.dumps(document))["reviews"][0]

    assert packets[0]["prior_research_updates"] == [original_update]
    assert result["research"] == paper
    assert "related_research_update_id" not in result["research"][field][0]


@pytest.mark.parametrize("field,body_field", [("forecast_reviews", "outcome"), ("lessons", "lesson")])
@pytest.mark.parametrize("explicit_reference", [False, True], ids=["inherited-reference", "explicit-reference"])
def test_reviewer_cannot_retarget_reflection_to_another_original(field, body_field, explicit_reference):
    proposed = {"key": "demand-check", body_field: "待核证的复盘判断"}
    if explicit_reference:
        proposed["related_research_update_id"] = "opinion:pm-note:1"
    paper = {field: [proposed]}
    document = {"reviews": [{"instrument_id": "stock", "events": [], "research": paper}]}
    corrected = {**proposed, body_field: "仅保留证据支持的分析", "related_research_update_id": "opinion:other-note:2"}
    checked_result = {"reviews": [{"instrument_id": "stock", "coverage": [], "decisions": [],
        "research": {field: [corrected]}}]}

    result = review._apply_checks(document, checked_result, [])["reviews"][0]["research"][field][0]

    assert result[body_field] == corrected[body_field]
    assert result.get("related_research_update_id") == proposed.get("related_research_update_id")
    assert ("related_research_update_id" in result) is explicit_reference


def test_reflection_only_check_is_independently_corrected_against_actual_acquisition(monkeypatch):
    original_reflection = {"status": "reviewed", "summary": "QCOM未列入前75大持仓，因此影响可忽略，今日没有重大新闻。",
        "reviewed_update_ids": ["research:prior-judgment"]}
    document = {"reviews": [{"instrument_id": "xlk", "reflection": original_reflection}]}
    prior = {"update_id": "research:prior-judgment", "kind": "question", "body": "需要核实真实持仓影响"}
    coverage = {"latest_bundle_window_end": "2026-09-08", "latest_received": "2026-09-09"}
    queries = [{"query": "XLK", "total": 0, "cutoff": "2026-09-12T00:00:00+00:00"}]
    tool_evidence = [{"source_id": "market:actual-read", "tool": "market", "retrieved_at": "2026-09-12T00:00:00+00:00",
        "result": {"as_of": "2026-09-08", "limitations": ["仅代表配置的市场"]}}]
    context = {"sector_run": True, "instrument_ids": ["xlk"], "cutoff": "2026-09-12T00:00:00+00:00",
        "market_queries": queries, "market_coverage": coverage, "tool_evidence": tool_evidence,
        "web_evidence": [{"operation": "error", "query": "latest news", "coverage": ["检索未执行"]}]}
    corrected = {"status": "insufficient_evidence", "summary": "最新资讯覆盖停留在9月9日，部分持仓不能确定总敞口，本轮未核实后续影响。",
        "reviewed_update_ids": original_reflection["reviewed_update_ids"]}
    requests, packets = [], []
    def api(run_id, suffix, payload=None):
        requests.append(suffix)
        if suffix == "context?originals=true": return context
        if suffix.startswith("dossier/xlk?"): return {"value": prior}
        return payload
    def reviewer(packet):
        packets.append(packet)
        return {"reviews": [{"instrument_id": "xlk", "coverage": ["资讯覆盖不足"], "decisions": [],
            "reflection": corrected}]}
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "receipt-run")
    monkeypatch.setattr(review, "_api_request", api)
    monkeypatch.setattr(review, "_call_reviewer", reviewer)
    output = review.review_output(json.dumps(document))["reviews"][0]
    assert len(packets) == 1 and packets[0]["prior_research_updates"] == [prior]
    assert packets[0]["acquisition"]["market_coverage"] == coverage
    assert packets[0]["acquisition"]["market_queries"] == queries
    assert packets[0]["acquisition"]["web_operations"][0]["coverage"] == ["检索未执行"]
    assert packets[0]["tool_evidence"] == tool_evidence
    assert output["reflection"] == corrected
    assert original_reflection["summary"] not in json.dumps(output, ensure_ascii=False)
    assert output["summary"] == "" and output["events"] == [] and output["research"] is None
    assert output["change_kind"] == "none"


@pytest.mark.parametrize("corrected", [{}, {"reflection": None}])
def test_reviewer_cannot_leave_reflection_claims_unreviewed(corrected):
    document = {"reviews": [{"instrument_id": "xlk", "events": [], "reflection": {
        "status": "reviewed", "summary": "尚未核实的事实与投资推断", "reviewed_update_ids": []}}]}
    checked_result = {"reviews": [{"instrument_id": "xlk", "coverage": [], "decisions": [], **corrected}]}
    with pytest.raises(ValueError, match="examine the supplied reflection"):
        review._apply_checks(document, checked_result, [])


def test_reviewer_cannot_invent_or_retarget_a_reflection_receipt():
    receipt = {"status": "reviewed", "summary": "已对照原判断", "reviewed_update_ids": ["research:other-version"]}
    corrected = {"reviews": [{"instrument_id": "xlk", "coverage": [], "decisions": [], "reflection": receipt}]}
    invented = {"reviews": [{"instrument_id": "xlk", "events": [], "research": {}}]}
    with pytest.raises(ValueError, match="invent a reflection"):
        review._apply_checks(invented, corrected, [])
    original = {"reviews": [{"instrument_id": "xlk", "events": [], "reflection": {
        **receipt, "reviewed_update_ids": ["research:original-version"]}}]}
    with pytest.raises(ValueError, match="retarget a reflection"):
        review._apply_checks(original, corrected, [])


def test_empty_reflection_receipt_still_requires_its_status_to_be_reviewed():
    receipt = {"status": "reviewed", "summary": "", "reviewed_update_ids": []}
    document = {"reviews": [{"instrument_id": "xlk", "events": [], "reflection": receipt}]}
    corrected = {"reviews": [{"instrument_id": "xlk", "coverage": [], "decisions": [], "reflection": receipt}]}
    assert review._needs_review(document["reviews"][0])
    result = review._apply_checks(document, corrected, [])["reviews"][0]
    assert result["reflection"] == receipt and result["summary"] == "" and result["research"] is None


def test_receipt_keeps_original_reference_order_without_rejecting_an_equivalent_review():
    receipt = {"status": "reviewed", "summary": "已对照原判断", "reviewed_update_ids": ["research:first", "research:second"]}
    document = {"reviews": [{"instrument_id": "xlk", "events": [], "reflection": receipt}]}
    corrected = {"reviews": [{"instrument_id": "xlk", "coverage": [], "decisions": [],
        "reflection": {**receipt, "reviewed_update_ids": list(reversed(receipt["reviewed_update_ids"]))}}]}
    result = review._apply_checks(document, corrected, [])["reviews"][0]
    assert result["reflection"]["reviewed_update_ids"] == receipt["reviewed_update_ids"]


def test_quiet_packet_keeps_complete_dated_holdings_and_only_actually_read_originals():
    holdings = [{"holding_symbol": f"S{index}", "weight_percent": index / 100,
        "as_of_date": "2026-06-30", "snapshot_date": "2026-09-09"} for index in range(84)]
    original = {"source_id": "actually-read", "source_type": "public_source",
        "text": "FULL_CURRENT_ORIGINAL", "published_at": "2026-09-09"}
    history = {"source_ids": ["unrelated-estimate"], "changes": [], "observations": [
        {"symbol": "S83", "reason": "baseline", "raw": "UNRELATED_ESTIMATE_ROW"}], "unmatched": []}
    theme = {"theme_id": "persistent-theme", "theme_key": "demand", "status": "active"}
    saved_review = {"key": "old-review", "outcome": "仍待核实",
        "related_research_update_id": "research:old-original", "versions": [
            {"version_id": "old-version", "outcome": "OLD_VERSION_BODY"}]}
    context = {"cutoff": "2026-09-12T00:00:00+00:00", "sector_inputs": [{
        "instrument_id": "xlk", "holdings": holdings, "holdings_as_of": "2026-06-30",
        "holdings_observed_on": "2026-09-09", "leading_companies": [{"raw": "UNRELATED_COMPANY_ESTIMATES"}]}],
        "instrument_inputs": [{"instrument_id": "xlk", "name": "XLK",
            "holdings": {"report_period": "2026-06-30", "data": holdings},
            "analyst_estimate_history": history, "reference_data": {"source": "retained",
                "sections": {"profile": {"asset_type": "ETF"}, "financials": ["RAW_FINANCIAL_SEQUENCE"],
                    "holdings": holdings}}}],
        "sector_company_data": {"xlk": {"S83": {"raw": "UNRELATED_ALL_COMPANY_DATA"}}},
        "sector_estimate_evidence": [{"source_id": "unrelated-estimate", "current_snapshot": ["UNRELATED_ESTIMATE_ORIGINAL"]}],
        "web_evidence": [{"operation": "search", "sources": [{"source_id": "search-only", "text": "SEARCH_SNIPPET"}]},
            {"operation": "fetch", "sources": [original]}],
        "market_text_sources": [original],
        "research_dossiers": [{"instrument_id": "xlk", "themes": [theme],
            "prior_sources": [{"source_id": "unread-history", "source_type": "public_source",
                "text": "UNREAD_HISTORICAL_ORIGINAL", "instrument_id": "xlk"}],
            "notebook": {"forecast_reviews": [saved_review]},
            "notebook_history": [{"body": "OLD_FULL_NOTEBOOK"}]}]}
    before = deepcopy(context)

    packet = review._evidence_packet(context, [{"instrument_id": "xlk", "events": [],
        "reflection": {"status": "insufficient_evidence", "summary": "覆盖不足", "reviewed_update_ids": []}}], "bounded-run")

    assert context == before
    sources = {source["source_id"]: source for source in packet["sources"]}
    sector = sources[packet["sector_inputs"][0]["source_id"]]["snapshot"]
    instrument = sources[packet["instrument_inputs"][0]["source_id"]]["snapshot"]
    assert sector["holdings"] == holdings
    assert sector["holdings_as_of"] == "2026-06-30"
    assert sector["holdings_observed_on"] == "2026-09-09"
    assert instrument["holdings"] == {"report_period": "2026-06-30"}
    assert instrument["reference_data"]["sections"] == {"profile": {"asset_type": "ETF"}}
    assert sources["instrument:bounded-run:xlk"]["snapshot_scope"] == "overview"
    assert packet["research_dossiers"][0]["themes"] == [theme]
    assert packet["research_dossiers"][0]["notebook"]["forecast_reviews"][0] == {
        **saved_review, "versions": [{"version_id": "old-version"}]}
    assert set(sources) == {"actually-read", "instrument:bounded-run:xlk", "sector:bounded-run:xlk"}
    encoded = json.dumps(packet)
    assert encoded.count("FULL_CURRENT_ORIGINAL") == 1
    assert encoded.count('"holding_symbol": "S83"') == 1
    for excluded in ("UNRELATED_ESTIMATE_ROW", "UNRELATED_COMPANY_ESTIMATES", "UNRELATED_ALL_COMPANY_DATA",
                     "UNRELATED_ESTIMATE_ORIGINAL", "UNREAD_HISTORICAL_ORIGINAL", "RAW_FINANCIAL_SEQUENCE",
                     "OLD_VERSION_BODY", "OLD_FULL_NOTEBOOK", "SEARCH_SNIPPET"):
        assert excluded not in encoded


def test_explicit_original_and_snapshot_evidence_remain_complete_without_body_duplicates(monkeypatch):
    text = "LONG_ORIGINAL_BEGIN" + "完整原始材料" * 12000 + "LONG_ORIGINAL_END"
    original = {"source_id": "archived-full", "source_type": "public_source", "instrument_id": "xlk",
        "text": text, "published_at": "2026-09-08", "time_status": "date_only"}
    rows = [{"date": f"2026-08-{index + 1:02d}", "value": index} for index in range(31)]
    asset = {"instrument_id": "xlk", "name": "XLK", "reference_data": {"sections": {"financials": rows}}}
    metric = {"source_id": "measured-risk", "source_type": "computed_metric", "instrument_id": "xlk",
        "as_of": "2026-09-09", "methodology": "Retained complete measurement", "data": {"observations": rows}}
    estimate = {"source_id": "cited-estimate", "source_type": "analyst_estimate_changes",
        "instrument_id": "xlk", "current_snapshot": rows, "previous_snapshot": rows,
        "changes": [{"metric": "eps", "delta": 0.5}]}
    context = {"cutoff": "2026-09-12T00:00:00+00:00", "instrument_inputs": [asset],
        "computed_metrics": [metric], "sector_estimate_evidence": [estimate, {"source_id": "unrelated-estimate",
            "current_snapshot": ["UNRELATED_ESTIMATE_ORIGINAL"]}],
        "research_dossiers": [{"instrument_id": "xlk", "prior_sources": [original],
            "notebook": {"sources": [original]}}],
        "prior_events": [{"instrument_id": "xlk", "body": "原事件判断", "sources": [original],
            "history": [{"sources": [original]}], "evidence": {"sources": [original]}}]}
    monkeypatch.setattr(review, "_api_request", lambda run_id, suffix, payload=None: deepcopy(original))

    packet = review._evidence_packet(context, [{"instrument_id": "xlk", "events": [], "research": {
        "source_ids": ["archived-full", "instrument:bounded-run:xlk", "measured-risk", "cited-estimate"]}}], "bounded-run")

    sources = {source["source_id"]: source for source in packet["sources"]}
    assert sources["archived-full"] == original
    assert sources["instrument:bounded-run:xlk"]["snapshot"] == asset
    assert sources["measured-risk"] == metric
    assert sources["cited-estimate"] == estimate
    assert "unrelated-estimate" not in sources
    encoded = json.dumps(packet, ensure_ascii=False)
    assert encoded.count(text) == 1
    assert "UNRELATED_ESTIMATE_ORIGINAL" not in encoded


def test_reflection_original_sources_join_selected_evidence_once(monkeypatch):
    source = {"source_id": "original-judgment-source", "source_type": "public_source", "instrument_id": "xlk",
        "text": "EXACT_ORIGINAL_EVIDENCE", "published_at": "2026-09-01"}
    original = {"update_id": "research:prior-version", "body": "当时保存的判断", "sources": [source]}
    context = {"cutoff": "2026-09-12T00:00:00+00:00", "research_dossiers": [{
        "instrument_id": "xlk", "prior_sources": [source], "notebook": {"sources": [source]}}]}
    calls = []
    def api(run_id, suffix, payload=None):
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(suffix).query)
        calls.append(query)
        if "update_id" in query:
            return {"value": original}
        assert query == {"source_id": [source["source_id"]]}
        return source
    monkeypatch.setattr(review, "_api_request", api)

    packet = review._evidence_packet(context, [{"instrument_id": "xlk", "events": [], "reflection": {
        "status": "reviewed", "summary": "核对原判断", "reviewed_update_ids": [original["update_id"]]}}], "bounded-run")

    assert calls == [{"update_id": [original["update_id"]]}, {"source_id": [source["source_id"]]}]
    assert packet["sources"] == [source]
    assert packet["prior_research_updates"][0]["body"] == original["body"]
    assert packet["prior_research_updates"][0]["sources"][0]["source_id"] == source["source_id"]
    assert "text" not in packet["prior_research_updates"][0]["sources"][0]
    assert json.dumps(packet).count(source["text"]) == 1


def test_review_schema_limits_decisions_to_each_instruments_actual_draft_events():
    proposed = [{"instrument_id": "015868-of", "events": [], "reflection": {"status": "reviewed"}},
                {"instrument_id": "600036-sh", "events": [event("funding"), event("earnings")]}]
    schema = review._review_schema(proposed)["properties"]["reviews"]
    assert schema["minItems"] == schema["maxItems"] == 2
    items = {row["properties"]["instrument_id"]["const"]: row for row in schema["items"]["oneOf"]}
    assert "reflection" in items["015868-of"]["required"]
    assert {v["properties"]["decision"]["const"] for v in items["015868-of"]["properties"]["reflection"]["oneOf"]} == {"accept", "correct"}
    assert "reviewed_update_ids" not in json.dumps(items["015868-of"]["properties"]["reflection"])
    candidates = {iid: row["properties"]["decisions"] for iid, row in items.items()}
    assert candidates["015868-of"]["minItems"] == candidates["015868-of"]["maxItems"] == 0
    assert candidates["600036-sh"]["minItems"] == candidates["600036-sh"]["maxItems"] == 2
    assert all(item["properties"]["event_key"]["enum"] == ["funding", "earnings"] for item in candidates["600036-sh"]["items"]["oneOf"])


def test_proposed_themes_and_reflection_are_required_inside_the_instrument_review():
    receipt = {"status": "reviewed", "reviewed_update_ids": ["research:prior-judgment"]}
    schema = review._review_schema([{"instrument_id": "518880-sh", "events": [], "reflection": receipt,
        "themes": [{"theme_key": "real-rates"}]}])
    assert schema["additionalProperties"] is False and set(schema["properties"]) == {"reviews"}
    item = schema["properties"]["reviews"]["items"]
    assert {"reflection", "themes"}.issubset(item["required"])
    assert "reviewed_update_ids" not in json.dumps(item["properties"]["reflection"])
    with pytest.raises(ValueError, match="themes"):
        review._Checks.model_validate({"reviews": [{"instrument_id": "518880-sh", "decisions": [], "coverage": []}], "themes": []})


def test_reflection_schema_only_allows_supplied_original_sources_in_scope_and_cutoff():
    from datetime import datetime
    receipt = {"status": "reviewed", "reviewed_update_ids": []}
    sources = [
        {"source_id": "sector:run:xlc", "source_type": "sector_snapshot", "instrument_id": "xlc", "snapshot": {"holdings": []}},
        {"source_id": "sector:run:xlk", "source_type": "sector_snapshot", "instrument_id": "xlk", "snapshot": {"holdings": []}},
        {"source_id": "public-original", "source_type": "public_source", "published_at": "2026-09-11", "text": "Original source"},
        {"source_id": "future-original", "source_type": "public_source", "published_at": "2026-09-13", "text": "Future original source"},
        {"source_id": "market:actual-read", "tool": "market", "result": {"as_of": "2026-09-11"}},
    ]
    schema = review._review_schema([{"instrument_id": "xlc", "events": [], "reflection": receipt}], sources,
        cutoff=datetime.fromisoformat("2026-09-12T00:00:00+00:00"))
    refs = schema["properties"]["reviews"]["items"]["properties"]["reflection"]["oneOf"][-1]["properties"]["patch"]["properties"]["source_ids"]
    assert refs["items"]["enum"] == ["public-original", "sector:run:xlc"]
    empty = review._review_schema([{"instrument_id": "xlc", "events": [], "reflection": receipt}], [sources[-1]])
    assert empty["properties"]["reviews"]["items"]["properties"]["reflection"]["oneOf"][-1]["properties"]["patch"]["properties"]["source_ids"]["maxItems"] == 0


def test_reflection_only_review_still_rejects_new_decisions_about_prior_events():
    receipt = {"status": "reviewed", "summary": "既有事件仍待披露验证", "reviewed_update_ids": []}
    proposed = {"reviews": [{"instrument_id": "015868-of", "events": [], "reflection": receipt}]}
    result = {"reviews": [{"instrument_id": "015868-of", "coverage": [], "reflection": receipt,
        "decisions": [{"event_key": "prior-event", "decision": "keep", "reason": "既有事件仍需跟踪", "event": event("prior-event")}]}]}
    with pytest.raises(ValueError, match="decide every original event exactly once"):
        review._apply_checks(proposed, result, [])


def test_reflection_financial_snapshot_and_current_metrics_reach_independent_review(monkeypatch):
    iid, run_id = "600036-sh", "receipt-financials"
    snapshot_id = f"instrument:{run_id}:{iid}"
    rows = [{"report_period": f"202{year}-12-31", "published_at": f"202{year + 1}-03-31",
             "net_profit": year * 100, "currency": "CNY", "unit": "million"} for year in range(1, 6)]
    asset = {"instrument_id": iid, "name": "招商银行", "instrument_type": "equity",
        "snapshot_cutoff": "2026-09-12T00:00:00+00:00", "reference_data": {"sections": {"financials": rows}}}
    metric = {"source_id": "actual-current-metric", "source_type": "computed_metric", "scope": "public_market",
        "instrument_id": iid, "as_of": "2026-09-12T00:00:00+00:00", "methodology": "Retained input observations",
        "data": {"return": 0.03}, "input_series": [{"date": "2026-09-11", "close": 38.2}]}
    public_metric = {**metric, "source_id": "actual-market-metric", "instrument_id": None}
    historical = {**metric, "source_id": "historical-metric", "as_of": "2026-08-01T00:00:00+00:00"}
    future = {**metric, "source_id": "future-metric", "as_of": "2026-09-13T00:00:00+00:00"}
    wrong_asset = {**metric, "source_id": "other-asset-metric", "instrument_id": "xlk"}
    context = {"sector_run": True, "instrument_ids": [iid], "cutoff": "2026-09-12T00:00:00+00:00",
        "instrument_inputs": [asset], "computed_metrics": [metric, public_metric, future, wrong_asset],
        "research_dossiers": [{"instrument_id": iid, "prior_sources": [historical]}]}
    receipt = {"status": "reviewed", "summary": "核对已披露财务及本轮价格观察，未来经营结果待验证。",
        "reviewed_update_ids": [], "source_ids": [snapshot_id]}
    corrected = {**receipt, "source_ids": [snapshot_id, metric["source_id"]]}
    packets = []
    def reviewer(packet):
        packets.append(packet)
        return {"reviews": [{"instrument_id": iid, "coverage": [], "decisions": [], "reflection": corrected}]}
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", run_id)
    monkeypatch.setattr(review, "_api_request", lambda rid, suffix, payload=None: context if suffix == "context?originals=true" else payload)
    monkeypatch.setattr(review, "_call_reviewer", reviewer)
    output = review.review_output(json.dumps({"reviews": [{"instrument_id": iid, "reflection": receipt}]}))["reviews"][0]
    assert len(packets) == 1
    sources = {source["source_id"]: source for source in packets[0]["sources"]}
    assert set(sources) == {snapshot_id, metric["source_id"], public_metric["source_id"]}
    assert sources[snapshot_id]["snapshot"] == asset
    assert sources[snapshot_id]["run_cutoff"] == asset["snapshot_cutoff"]
    assert sources[metric["source_id"]] == metric
    assert "reference_data" not in packets[0]["instrument_inputs"][0]
    assert json.dumps(packets[0]).count('"net_profit"') == len(rows)
    assert output["reflection"] == corrected
    assert output["summary"] == "" and output["research"] is None and output["events"] == []


@pytest.mark.parametrize("source", [None,
    {"source_id": "receipt-source", "source_type": "instrument_snapshot", "instrument_id": "other-stock", "snapshot": {"name": "其他股票"}},
    {"source_id": "receipt-source", "source_type": "model_summary", "instrument_id": "600036-sh", "text": "模型推断"}])
def test_reflection_reviewer_cannot_introduce_unavailable_or_inapplicable_evidence(source):
    receipt = {"status": "reviewed", "summary": "复盘判断", "reviewed_update_ids": []}
    proposed = {"reviews": [{"instrument_id": "600036-sh", "events": [], "reflection": receipt}]}
    result = {"reviews": [{"instrument_id": "600036-sh", "coverage": [], "decisions": [],
        "reflection": {**receipt, "source_ids": ["receipt-source"]}}]}
    with pytest.raises(ValueError, match="研究底稿"):
        review._apply_checks(proposed, result, [source] if source else [])


@pytest.mark.parametrize("change_kind", ["none", "knowledge", "investment"])
def test_nonempty_summary_always_requires_review(change_kind):
    assert review._needs_review({"events": [], "summary": "断言市场需求已改善", "change_kind": change_kind})
