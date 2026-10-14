from copy import deepcopy
import json

from jsonschema import Draft202012Validator, ValidationError
from pydantic import ValidationError as ModelValidationError
import pytest

from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service
from watchlist_app.services import sector_fact_review as reviewer
from watchlist_app.services.research_dossier import read_dossier
from watchlist_app import review_mcp
from .test_research_activity import activity_client, event, publish
from .test_research_review_agent import _bound, _complete_read
from .test_sector_review_protocol import accepted


def _check_run(session):
    publish(session, events=[event(body="原事实、影响机制和下一观察条件必须保留。")])
    case = session.query(RiskCase).filter_by(instrument_id="xlk", signal="sector:financing").one()
    original = service.event_record(case)
    run, _ = service.begin_run(session, ["xlk"])
    run.context_json = {**run.context_json, "prior_events": [original],
        "research_dossiers": [read_dossier(session, "xlk")],
        "research_working_sets": {"xlk": {"affected_event_ids": [case.case_id], "affected_theme_ids": []}}}
    from .research_sections_fixture import completed_sections
    completed_sections(run)
    row = {"instrument_id": "xlk", "events": [{"event_key": original["event_key"], "action": "checked",
        "event_version_id": original["event_version_id"]}]}
    return run, case, row


def test_unchanged_receipt_preserves_event_content_sources_clocks_and_risk(activity_client):
    with get_session_factory()() as session:
        run, case, row = _check_run(session)
        before, history = deepcopy(case.evidence_json), deepcopy(case.history_json)
        body, title, observed, status, trigger = case.body, case.title, case.observed_on, case.status, case.trigger_active
        service.apply_result(session, run, json.dumps({"reviews": [row]}))
        session.flush()
        assert case.evidence_json == {**before, "checked_at": run.context_json["cutoff"]}
        assert case.history_json == history
        assert (case.body, case.title, case.observed_on, case.status, case.trigger_active) == (body, title, observed, status, trigger)
        assert run.context_json["reviews"]["xlk"]["change_kind"] == "none"


@pytest.mark.parametrize("kind", ["wrong_version", "concurrent_update", "unbound"])
def test_unchanged_check_cannot_silently_confirm_another_version(activity_client, kind):
    with get_session_factory()() as session:
        run, case, row = _check_run(session)
        if kind == "wrong_version":
            row["events"][0]["event_version_id"] = "unread:2"
        elif kind == "unbound":
            run.context_json = {**run.context_json, "prior_events": []}
        else:
            case.evidence_json = {**case.evidence_json, "event_version_id": f"{case.case_id}:99"}
        before, history = deepcopy(case.evidence_json), deepcopy(case.history_json)
        with pytest.raises((ValueError, service.ResearchVersionConflict)):
            service.apply_result(session, run, json.dumps({"reviews": [row]}))
        assert case.evidence_json == before and case.history_json == history


def test_checked_schema_cannot_carry_replacement_body_or_sources():
    row = {"instrument_id": "xlk", "events": [{"event_key": "event", "action": "checked", "event_version_id": "case:2"}]}
    assert service.ReviewResult.model_validate({"reviews": [row]}).reviews[0].events[0].action == "checked"
    for extra in ({"body": "无变化"}, {"source_ids": ["new"]}, {"occurred_at": "2026-10-14"}):
        with pytest.raises(ModelValidationError):
            service.ReviewResult.model_validate({"reviews": [{**row, "events": [{**row["events"][0], **extra}]}]})


def test_review_roundtrip_checks_retained_version_without_rewriting(monkeypatch, tmp_path):
    rows = [{"instrument_id": "xlk", "summary": "", "change_kind": "none", "coverage": [],
        "events": [{"event_key": "event", "action": "checked", "event_version_id": "case:2"}], "themes": []}]
    prior = {"instrument_id": "xlk", "event_key": "event", "event_version_id": "case:2",
        "body": "完整既有事实与判断。", "sources": [{"source_id": "old-original"}], "checked_at": "2026-09-25"}
    state = _bound(monkeypatch, tmp_path, {"draft_reviews": rows, "sources": [], "prior_events": [prior],
        "acquisition": {"coverage": "本次检查范围内没有新增；未覆盖范围仍未知。"}})
    receipts = accepted(rows)
    _complete_read(review_mcp.read_review_context, section="draft_reviews")
    _complete_read(review_mcp.read_review_context, section="acquisition")
    with pytest.raises(ValueError, match="prior_events"):
        review_mcp.submit_review_receipts(receipts)
    _complete_read(review_mcp.read_review_context, section="prior_events")
    assert review_mcp.submit_review_receipts(receipts)["accepted"]
    saved = json.loads((tmp_path / "result.json").read_text())["result"]
    result = reviewer._apply_checks({"reviews": rows}, saved, [])
    assert result["reviews"][0]["events"] == rows[0]["events"]
    attempted = deepcopy(receipts)
    attempted["reviews"][0]["decisions"] = [{"event_key": "event", "decision": "correct", "reason": "改写",
        "patch": {"event_version_id": "case:3"}}]
    with pytest.raises(ValidationError):
        Draft202012Validator(state["response_schema"]).validate(attempted)
    saved["reviews"][0]["decisions"][0]["event"]["event_version_id"] = "case:3"
    with pytest.raises(ValueError, match="bound event version"):
        reviewer._apply_checks({"reviews": rows}, saved, [])


def test_checked_review_is_not_filtered_for_lacking_new_event_sources(monkeypatch):
    row = {"instrument_id": "xlk", "summary": "", "change_kind": "none", "coverage": [],
        "events": [{"event_key": "event", "action": "checked", "event_version_id": "case:2"}], "themes": []}
    context = {"sector_run": True, "run_id": "run", "cutoff": "2026-10-14T00:00:00+00:00", "instrument_ids": ["xlk"],
        "prior_events": [{"instrument_id": "xlk", "event_key": "event", "event_version_id": "case:2", "body": "原事实"}]}
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "run")
    monkeypatch.setattr(reviewer, "_api_request", lambda *args, **kwargs: {})
    captured = []
    def review(packet):
        captured.append(packet)
        from watchlist_app.services.sector_review_protocol import expand_review_receipts
        return expand_review_receipts(packet["draft_reviews"], accepted(packet["draft_reviews"]))
    monkeypatch.setattr(reviewer, "_call_reviewer", review)
    result = reviewer.review_output(json.dumps({"reviews": [row]}), context=context)
    assert result["reviews"][0]["events"] == row["events"]
    assert captured[0]["prior_events"][0]["event_version_id"] == "case:2"


def test_specialist_can_report_checked_only_but_not_replace_scope(activity_client):
    from dataclasses import replace
    from studio_identity import current_principal, principal_context
    from watchlist_app.services import research_tasks as tasks
    with get_session_factory()() as session:
        run, case, row = _check_run(session)
        run.status = "running"
        run.context_json = {**run.context_json, "execution": {"attempt": 1, "research_section": "overview"}}
        session.commit()
        with principal_context(replace(current_principal(), resource_scope={"kind": "run", "id": run.entry_id, "attempt": "1"})):
            task = tasks.create_task(session, run, tasks.ResearchTaskInput(role="recent_updates", instrument_id="xlk",
                instruction="复核持续跟踪事项，本轮无新增事实则确认原版本"))
            receipt = tasks.save_task_result(session, run, task["task_id"], tasks.ResearchTaskResult(
                status="completed", summary="已复核持续事项，未发现新事实。", source_ids=[], events=row["events"]))
            assert receipt["status"] == "completed" and receipt["source_ids"] == []
            assert run.context_json["research_tasks"][task["task_id"]]["result"]["events"] == row["events"]


def test_checked_mcp_shape_error_names_only_the_selected_receipt_fields(monkeypatch):
    import asyncio
    from mcp.server.mcpserver.exceptions import ToolError
    from watchlist_app import research_mcp
    sent = []
    monkeypatch.setattr(research_mcp,'request',lambda *args: sent.append(args))
    payload = {'reviews':[{'instrument_id':'xlk','events':[{'event_key':'event','action':'checked',
        'event_version_id':'case:2','body':'PRIVATE_REPLACEMENT'}]}]}
    with pytest.raises(ToolError) as raised:
        asyncio.run(research_mcp.mcp.call_tool('submit_research_review',{'result':payload}))
    message = str(raised.value)
    result = json.loads(message[message.index('{'):])
    assert len(result['issues'])==1
    issue = result['issues'][0]
    assert issue['loc']==['result','reviews',0,'events',0,'body']
    assert set(issue['parent_fields'])=={'event_key','action','event_version_id'}
    assert 'PRIVATE_REPLACEMENT' not in message and not sent


@pytest.mark.parametrize('receipt', ['checked', 'unchanged_full_event'])
def test_older_quiet_run_cannot_move_the_last_check_backwards(activity_client, receipt):
    with get_session_factory()() as session:
        run, case, row = _check_run(session)
        newer_check = '2030-01-02T04:00:00+08:00'
        case.evidence_json = {**case.evidence_json,'checked_at':newer_check}
        before, history, body = deepcopy(case.evidence_json), deepcopy(case.history_json), case.body
        if receipt=='checked':
            run.context_json = {**run.context_json,'cutoff':'2030-01-01T19:00:00+00:00'}
            service.apply_result(session,run,json.dumps({'reviews':[row]}))
        else:
            publish(session,events=[event(action='updated',body=body)])
        assert case.evidence_json==before and case.history_json==history and case.body==body
        # Different UTC offsets must compare as instants, not strings.
        if receipt=='checked':
            from .research_sections_fixture import completed_sections
            later, _ = service.begin_run(session,['xlk'])
            later.context_json = {**later.context_json,'cutoff':'2030-01-01T21:00:00+00:00',
                'prior_events':[service.event_record(case)],'research_dossiers':[read_dossier(session,'xlk')],
                'research_working_sets':{'xlk':{'affected_event_ids':[case.case_id],'affected_theme_ids':[]}}}
            completed_sections(later)
            service.apply_result(session,later,json.dumps({'reviews':[row]}))
            assert case.evidence_json=={**before,'checked_at':later.context_json['cutoff']}
            assert case.history_json==history
