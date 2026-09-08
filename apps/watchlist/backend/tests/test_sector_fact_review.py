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
        {"instrument_id": "xlk", "summary": "未发现经核实的重大新增事件。", "change_kind": "none",
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
            return {"source_id": "fmp:test-run:xle:XOM", "company": {"symbol": "XOM", "annual_estimates": []}}
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
    assert packets[0]["instrument_inputs"] == [{"instrument_id": "xle", "name": "XLE", "analyst_focus": "Use actual disclosed exposure.",
                                               "holdings": {"report_period": "2026-06-30"}}]
    assert {s["source_id"] for s in packets[0]["sources"]} == {
        "source-1", "fmp:test-run:xle:XOM", "instrument:test-run:xle", "instrument:test-run:xlk",
        "sector:test-run:xle", "sector:test-run:xlk"}
    receipts = [payload for suffix, payload in retained_run if suffix == "sector-evidence"]
    assert receipts == [
        {"operation": "review", "review": {"draft": original}},
        {"operation": "review", "review": {"result": result}},
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


def test_ineligible_sources_remove_all_candidates_before_model_and_replace_coverage(monkeypatch, retained_run):
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
    monkeypatch.setattr(review, "_call_reviewer", lambda _: pytest.fail("Ineligible candidates must not call the model"))
    original = draft([event(source_ids=["source-1", "fmp:test-run:xle:XOM"])])
    output = review.review_output(json.dumps(original))
    assert output["reviews"][0] == {"instrument_id": "xle", "events": [], "change_kind": "none",
        "summary": "本轮未能核实候选所述的重大风险或机会。", "coverage": [review._EXCLUSION_NOTE], "research": None}
    assert output["reviews"][1] == original["reviews"][1]
    assert "XLE RSI" not in json.dumps(output, ensure_ascii=False)
    assert retained_run == [
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
    assert review.review_output(json.dumps(original)) == original
    with pytest.raises(ValueError):
        review.review_output(json.dumps(original) + "\ntrailing commentary")


def test_cli_retains_raw_draft_before_validation_and_prints_only_safe_error(monkeypatch, retained_run, capsys):
    raw = "Invalid model final containing private-sentinel"
    monkeypatch.setattr(review.sys, "stdin", StringIO(raw))
    with pytest.raises(SystemExit) as error:
        review.main()
    assert error.value.code == 1
    assert retained_run == [("context?originals=true", None), ("sector-evidence", {"operation": "review", "review": {"raw_draft": raw}})]
    captured = capsys.readouterr()
    assert captured.out == "" and "private-sentinel" not in captured.err
    details = json.loads(captured.err.removeprefix("SECTOR_REVIEW_ERROR "))
    assert details["type"] == "ValueError" and details["summary"]


def test_cli_uses_accepted_structured_draft_instead_of_model_prose(monkeypatch, capsys):
    submitted = draft()
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "submitted-run")
    monkeypatch.setattr(review.sys, "stdin", StringIO('已提交；不应解析这段文字中的 { 未转义引号。'))
    monkeypatch.setattr(review, "_api_request", lambda rid, suffix, payload=None: {"sector_run": True, "submitted_draft": submitted} if suffix == "context?originals=true" else payload)
    received = []
    monkeypatch.setattr(review, "review_output", lambda raw: received.append(json.loads(raw)) or submitted)
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


def test_reviewer_uses_native_json_output_without_search_tools(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    requests, receipts = [], []
    monkeypatch.setattr(review, "_api_request", lambda rid, suffix, payload: receipts.append((rid, suffix, payload)))

    def request(url, **kwargs):
        requests.append((url, kwargs))
        return 200, {}, json.dumps({"usage": {"completion_tokens": 800}, "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(checked())}},
        ]}).encode()

    monkeypatch.setattr(review, "_request", request)
    assert review._call_reviewer({"run_id": "test-run", "sources": [], "draft_reviews": []}) == checked()
    payload = json.loads(requests[0][1]["body"])
    assert requests[0][1]["timeout"] == 180
    assert payload["model"] == "deepseek-v4-pro" and payload["max_tokens"] == 16000
    assert payload["thinking"] == {"type": "enabled"} and payload["reasoning_effort"] == "high"
    assert requests[0][0] == "https://api.deepseek.com/chat/completions"
    assert payload["response_format"] == {"type": "json_object"}
    assert "tools" not in payload
    schema = json.loads(payload["messages"][1]["content"])["response_schema"]
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    item = schema["properties"]["reviews"]["items"]
    assert set(item["required"]) == {"instrument_id", "coverage", "decisions"}
    assert item["properties"]["summary"]["default"] == ""
    decision = item["properties"]["decisions"]["items"]
    assert set(decision["required"]) == {"event_key", "decision", "reason", "event"}
    assert decision["properties"]["decision"]["enum"] == ["keep", "remove"]
    assert receipts[0][2]["review"]["response_metadata"]["finish_reason"] == "stop"
    assert receipts[0][2]["review"]["response_metadata"]["usage"] == {"completion_tokens": 800}
    assert "USO RSI" in payload["messages"][0]["content"] and "AI-generated rewriting" in payload["messages"][0]["content"]


@pytest.mark.parametrize("choices", [
    [],
    [{"finish_reason": "length", "message": {"content": '{"reviews":['}}],
    [{"finish_reason": "stop", "message": {"content": '{}'}}],
    [{"finish_reason": "stop", "message": {"content": json.dumps(checked())}}] * 2,
])
def test_provider_must_return_one_complete_review_json(monkeypatch, choices):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    monkeypatch.setattr(review, "_request", lambda *a, **k: (200, {}, json.dumps({"choices": choices}).encode()))
    with pytest.raises(review._ReviewProtocolError):
        review._call_reviewer({"sources": [], "draft_reviews": []})


def test_unstructured_provider_text_is_retained_without_accepting_or_printing_it(monkeypatch, retained_run, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    raw_text = "Here is the review:\n```json\n" + json.dumps(checked()) + "\n```"
    response = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": raw_text}}]})
    monkeypatch.setattr(review, "_request", lambda *a, **k: (200, {}, response.encode()))
    with pytest.raises(review._ReviewProtocolError) as error:
        review.review_output(json.dumps(draft()))
    assert raw_text not in str(error.value)
    assert retained_run[-1][1] == {"operation": "review", "review": {"raw_output": response}}
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_http_protocol_error_retains_response_for_diagnosis(monkeypatch, retained_run):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    response = '{"error":{"message":"Unsupported tool choice"}}'
    monkeypatch.setattr(review, "_request", lambda *a, **k: (400, {}, response.encode()))
    with pytest.raises(review._ReviewProtocolError, match="HTTP 400"):
        review.review_output(json.dumps(draft()))
    assert retained_run[-1][1] == {"operation": "review", "review": {"raw_output": response}}


def test_safe_failure_keeps_wrapped_timeout_cause_without_exception_text():
    error = review.SectorWebError("Private request details")
    error.__cause__ = TimeoutError("Private connection details")
    failure = review._safe_failure(error)
    assert failure["type"] == "TimeoutError" and "超时" in failure["summary"]
    assert "Private" not in json.dumps(failure)


def test_quiet_day_working_paper_is_reviewed_against_original_material(monkeypatch):
    paper = {"fundamental_view": "管理人已证明策略有效。", "key_drivers": [],
        "valuation_view": "没有可比估值依据。", "questions": [{"key": "strategy", "question": "策略是否有效？",
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
    corrected = {**paper, "fundamental_view": "管理人说明了策略机制，尚不能据此确认策略有效。", "catalysts": [], "facts": [], "mandate_update": None}
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
    monkeypatch.setattr(review, "review_output", lambda raw: received.append(json.loads(raw)) or paper)
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
    def fail(raw):
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
