"""V1 event publication, chronology and risk ownership through persisted paths."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service
from watchlist_app.services.research_activity import event_page
from watchlist_app.services.research_projection import build_risk_watchlist_attribute_overrides
from .test_sector_research import seed_sector, add_sources, result


def publish(session, run, **values):
    service.apply_result(session, run, result(sources=["web-one"], **values))
    session.flush()
    return session.scalar(select(RiskCase).where(RiskCase.signal == "sector:new-policy"))


def test_independent_follow_up_default_term_sparse_merge_and_risk_expiry(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        case = publish(session, run, direction="risk")
        initial = service.event_record(case)
        assert initial["theme_ids"] == [] and initial["follow_up"] == "watch"
        assert initial["follow_up_until"] == (datetime.fromisoformat(run.context_json["cutoff"]).astimezone(ZoneInfo("America/New_York")).date() + timedelta(days=30)).isoformat()
        assert initial["risk_assessment"]["status"] == "pending" and not case.trigger_active
        case.trigger_active, case.status = True, "investigating"
        case.evidence_json = {**case.evidence_json, "follow_up_until": "2000-01-01", "risk_assessment": {"status": "active"}}
        published = json.loads(result(sources=["web-one"], action="updated", direction="risk", follow_up="none", follow_up_reason="收尾检查完成，由既有风险事项继续观察。"))
        published["reviews"][0]["events"][0].pop("importance_score")
        published["reviews"][0]["events"][0].pop("importance_reason")
        service.apply_result(session, run, json.dumps(published))
        session.commit()
        assert case.trigger_active and case.status == "investigating"
        assert case.evidence_json["importance_score"] == 3
        assert case.evidence_json["follow_up"] == "none"
        assert case.evidence_json["material_progress_at"] == initial["material_progress_at"]
        assert not case.history_json[-1]["snapshot"]["material_change"]
        assert build_risk_watchlist_attribute_overrides(session, instrument_ids=["xlk"])["xlk"]["risk_attention"] == "attention"


def test_pm_pin_and_extensions_are_explicit_and_versioned(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        case = publish(session, run)
        saved = service.event_record(case)
        session.commit()
        case_id, run_id = case.case_id, run.entry_id
    response = client.patch(f"/api/sector-research/events/{case_id}/follow-up", json={"follow_up_pinned": True, "event_version_id": saved["event_version_id"]})
    assert response.status_code == 200 and response.json()["follow_up_pinned"]
    assert client.patch(f"/api/sector-research/events/{case_id}/follow-up", json={"follow_up_pinned": False, "event_version_id": saved["event_version_id"]}).status_code == 409
    from watchlist_app.db.models.workbench import ResearchEntry
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        case = session.get(RiskCase, case_id)
        with pytest.raises(ValueError, match="固定"):
            publish(session, run, action="updated", follow_up="none")
        extension = json.loads(result(sources=["web-one"], action="updated", follow_up_until="2099-01-01"))
        extension["reviews"][0]["events"][0].pop("follow_up_reason")
        with pytest.raises(ValueError, match="延期"):
            service.validate_result(session, run, service.ReviewResult.model_validate(extension))
        publish(session, run, action="updated", follow_up_until="2099-01-01", follow_up_reason="等待已公告的下一次正式披露节点。")
        assert case.evidence_json["follow_up_until"] == "2099-01-01"
        assert case.evidence_json["material_progress_at"] == saved["material_progress_at"]


def test_independent_watch_event_is_in_research_review_agenda(client, monkeypatch):
    from watchlist_app.services.research_activity import review_agenda
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        case = publish(session, run)
        assert service.event_record(case)["theme_ids"] == []
        pending = review_agenda(session, "xlk", {}, [])['pending_events']
        assert [row['reference']['event_case_id'] for row in pending] == [case.case_id]


def test_overdue_event_remains_due_after_unavailable_check_and_retries(client, monkeypatch):
    from watchlist_app.services.research_triggers import _due_items
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        case = publish(session, run)
        case.evidence_json = {**case.evidence_json, "follow_up_until": "2000-01-01"}
        session.flush()
        now = datetime.now(UTC)
        # A recent attempt/cursor does not make an older overdue item disappear.
        due = _due_items(session, "xlk", run.context_json, now - timedelta(seconds=1), now)
        assert due[0]["case_id"] == case.case_id
        run.status = "failed"
        assert _due_items(session, "xlk", run.context_json, now, now + timedelta(seconds=1))
        assert case.evidence_json["follow_up"] == "watch"
        page = event_page(session, "xlk", scope="watch", now=now)
        assert page["events"][0]["follow_up_review_status"] == "due"


def test_timeline_uses_local_fact_dates_and_keeps_legacy_unknown_and_backfill(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        now = datetime(2026, 9, 25, 1, tzinfo=UTC)
        for key, fact, score in [("boundary", "2026-09-18T23:30:00Z", 3), ("low", "2026-09-25", 2), ("old", "2020-01-01", 4), ("unknown", None, None)]:
            session.add(RiskCase(case_id=key, instrument_id="xlk", signal=f"sector:{key}", title=key, body="保留事实",
                status="recorded", trigger_active=False, history_json=[], evidence_json={"follow_up": "watch", "next_watch": "下一次披露",
                    "occurred_at": fact, "importance_score": score, "recorded_at": now.isoformat(), "source_views": []}))
        session.flush()
        page = event_page(session, "xlk", display_timezone="Asia/Tokyo", now=now)
        assert [row["event_key"] for row in page["events"]] == ["boundary"]
        assert page["events"][0]["timeline_date"] == "2026-09-19"
        assert {row["event_key"] for row in page["late_arrivals"]} == {"old", "unknown"}
        assert next(row for row in page["late_arrivals"] if row["event_key"] == "unknown")["timeline_date"] is None
        assert len(event_page(session, "xlk", scope="watch", now=now)["events"]) == 4
        session.commit()
    response = client.get("/api/research/instruments/xlk/events?event_key=old")
    assert response.status_code == 200 and response.json()["events"][0]["event_key"] == "old"
    assert client.get("/api/research/instruments/xlk/events?display_timezone=invalid").status_code == 422


def test_market_views_and_reaction_survive_publication_history_and_exact_source_read(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run, published_at="2026-01-01")
        metric = {"source_id": "computed:reaction", "source_type": "computed_metric", "scope": "instrument", "instrument_id": "xlk",
            "as_of": run.context_json["cutoff"], "methodology": "固定样例：事件后完整日频窗口。", "data": {"analysis_kind": "event_market_reaction", "status": "available", "event_date": "2026-01-01", "target_return_pct": 1.2}}
        run.context_json = {**run.context_json, "computed_metrics": [metric]}
        case = publish(session, run, published_at="2026-01-01", market_views=[{"publisher": "独立研究机构", "published_at": "2026-01-01",
            "view": "这项政策可能影响现金流，尚有分歧。", "source_ids": ["web-one"]}],
            market_reaction={"status": "available", "figure_source_ids": [metric["source_id"]], "explanation": "同期表现不能证明事件因果。"})
        version = service.event_record(case)["event_version_id"]
        session.commit()
        assert case.history_json[-1]["snapshot"]["market_views"][0]["publisher"] == "独立研究机构"
    saved = client.get("/api/research/instruments/xlk/dossier", params={"version_id": version, "source_id": metric["source_id"]})
    assert saved.status_code == 200 and saved.json()["data"]["target_return_pct"] == 1.2
    assert client.get("/api/research/instruments/xlf/dossier", params={"version_id": version, "source_id": metric["source_id"]}).status_code in {404, 422}
    with pytest.raises(ValueError):
        service.EventMarketReaction.model_validate({"status": "available", "target_return_pct": 99})


def test_risk_officer_owns_activation_and_stale_or_private_assessment_cannot_publish(client, monkeypatch):
    from watchlist_app.services import risk_officer
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        case = publish(session, run, direction="risk")
        version = service.event_record(case)["event_version_id"]
        snapshot = {"scope": {"kind": "instrument", "id": "xlk"}, "instrument_ids": ["xlk"], "instruments": [], "limitations": [],
            "research": [{"case_id": case.case_id, "instrument_id": "xlk", "signal": case.signal, "evidence_json": dict(case.evidence_json)}], "quantitative": [], "coverage": []}
        officer = SimpleNamespace(entry_id="officer", context_json={"risk_inputs": snapshot})
        payload = {"summary": "复核完毕", "priorities": [], "limitations": [], "case_assessments": [
            {"case_id": case.case_id, "event_version_id": version, "status": "active", "reason": "原文证据明确支持持续风险，需要保持关注。"}]}
        risk_officer.apply_result(session, officer, payload)
        assert case.trigger_active and case.status == "open"
        publish(session, run, action="updated", direction="opportunity", follow_up="none", body="发现同一事项的有利影响，但既有风险待独立复核。")
        assert case.trigger_active and case.evidence_json["risk_assessment"]["status"] == "active"
        with pytest.raises(ValueError, match="已有更新"):
            risk_officer.apply_result(session, officer, payload)
        snapshot["scope"]["kind"] = "portfolio"
        with pytest.raises(ValueError, match="私有"):
            risk_officer.validate_result(officer, risk_officer.RiskReview.model_validate(payload))


def test_duplicate_source_new_id_and_old_message_cannot_create_or_reactivate(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        case = publish(session, run)
        with pytest.raises(ValueError, match="沿用事件标识"):
            publish(session, run, event_key="same-policy-new-title", title="政策影响的同义标题")
        publish(session, run, action="updated", follow_up="none")
        with pytest.raises(ValueError, match="重新激活"):
            publish(session, run, action="updated", follow_up="watch")
        publish(session, run, action="updated", follow_up="watch", body="新的正式披露明确了一个此前未知的重要条件。", follow_up_reason="新披露改变后续检查节点。")
        assert case.evidence_json["follow_up"] == "watch"


def test_new_event_importance_is_required_and_legacy_remains_unassessed(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        with pytest.raises(ValueError, match="评分理由"):
            publish(session, run, importance_score=None, importance_reason="")


def test_editorial_and_republication_updates_retain_material_clock(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        case = publish(session, run, direction="risk")
        clock, submitted = case.evidence_json["material_progress_at"], case.evidence_json["risk_assessment"]["submitted_at"]
        publish(session, run, action="updated", direction="risk", progress_kind="editorial", body="新政策或影响现金流，市场预期仍待进一步核实。")
        assert case.evidence_json["material_progress_at"] == clock
        assert case.evidence_json["risk_assessment"]["submitted_at"] == submitted
        # A syndicated copy with a different URL and fetch ID is retained evidence,
        # never sufficient on its own to create progress or restart tracking.
        add_sources(run, source_id="web-one", url="https://syndicated.example.test/same-original")
        publish(session, run, action="updated", direction="risk", progress_kind="editorial", body="新政策或影响现金流，市场预期仍待进一步核实。")
        assert case.evidence_json["material_progress_at"] == clock
        assert not case.history_json[-1]["snapshot"]["material_change"]
        assert case.evidence_json["sources"][0]["url"].startswith("https://syndicated")


def test_later_syndication_date_cannot_refresh_create_or_restart_old_event(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        today = datetime.now(UTC).date()
        original_date, copy_date = (today - timedelta(days=1)).isoformat(), today.isoformat()
        add_sources(run, published_at=original_date)
        metric = {"source_id": "computed:reaction", "source_type": "computed_metric", "scope": "instrument", "instrument_id": "xlk",
            "as_of": run.context_json["cutoff"], "methodology": "固定样例：事件后完整日频窗口。", "data": {
                "analysis_kind": "event_market_reaction", "status": "available", "event_date": "2020-01-01", "target_return_pct": 1.2}}
        run.context_json = {**run.context_json, "computed_metrics": [metric]}
        reaction = {"status": "available", "figure_source_ids": [metric["source_id"]], "explanation": "同期表现不能证明事件因果。"}
        case = publish(session, run, direction="risk", occurred_at="2020-01-01", published_at=original_date,
            follow_up="none", market_reaction=reaction)
        original = dict(case.evidence_json)
        add_sources(run, url="https://syndicated.example.test/same-message", published_at=copy_date)
        copy = {"action": "updated", "direction": "risk", "recording_type": "update", "occurred_at": "2020-01-01", "published_at": copy_date}
        with pytest.raises(ValueError, match="重新激活"):
            publish(session, run, **copy, follow_up="watch")
        with pytest.raises(ValueError, match="沿用事件标识"):
            publish(session, run, **{**copy, "action": "new"}, event_key="syndicated-policy", follow_up="none", market_reaction=reaction)
        publish(session, run, **copy, follow_up="none")
        session.commit()
        assert case.evidence_json["published_at"] == copy_date
        assert case.evidence_json["sources"][0]["url"].startswith("https://syndicated")
        assert not case.history_json[-1]["snapshot"]["material_change"]
        assert case.evidence_json["material_progress_at"] == original["material_progress_at"]
        assert case.evidence_json["development_at"] == original["development_at"]
        assert case.evidence_json["risk_assessment"] == original["risk_assessment"]
        assert case.evidence_json["market_reaction"] == reaction
        assert event_page(session, "xlk", display_timezone="UTC")["events"] == []
        with pytest.raises(ValueError, match="市场反应窗口与事件日期不一致"):
            publish(session, run, **{**copy, "occurred_at": copy_date}, follow_up="none")


@pytest.mark.parametrize("change", ["publication_correction", "occurrence_correction", "new_market_view"])
def test_actual_date_corrections_and_new_public_views_remain_material(client, monkeypatch, change):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run, published_at="2020-01-02")
        case = publish(session, run, occurred_at="2020-01-01", published_at="2020-01-02", follow_up="none")
        changes = {"occurred_at": "2020-01-01", "published_at": "2020-01-02"}
        if change == "publication_correction":
            # The retained original's own publication date was corrected.
            add_sources(run, published_at="2020-01-03")
            changes["published_at"] = "2020-01-03"
        elif change == "occurrence_correction":
            add_sources(run, published_at="2020-01-02", url="https://example.test/fact-correction")
            changes["occurred_at"] = "2019-12-31"
        else:
            today = datetime.now(UTC).date().isoformat()
            add_sources(run, published_at=today, url="https://example.test/new-market-view")
            changes.update(published_at=today, market_views=[{"publisher": "公开研究机构", "published_at": today,
                "view": "新增公开观点认为政策兑现取决于下个月的实施细则。", "source_ids": ["web-one"]}])
        publish(session, run, action="updated", recording_type="update", follow_up="watch", **changes)
        session.commit()
        assert case.evidence_json["follow_up"] == "watch"
        assert case.history_json[-1]["snapshot"]["material_change"]
        if change == "new_market_view":
            assert event_page(session, "xlk", display_timezone="UTC")["events"][0]["timeline_date"] == today


def test_summary_links_freeze_event_sources_and_refuse_stale_event_version(client, monkeypatch):
    from watchlist_app.services.research_dossier import read_dossier
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        payload = json.loads(result(sources=["web-one"]))
        payload["reviews"][0]["research"] = {"investment_view": {"direction": "政策可能带来机会，但尚待核实。", "opportunities": [{
            "key": "policy", "title": "现金流条件", "explanation": "政策可能改变现金流。", "next_watch": "下一次正式披露", "event_keys": ["new-policy"]}]}}
        service.apply_result(session, run, json.dumps(payload))
        session.commit()
        notebook = read_dossier(session, "xlk")["notebook"]
        assert notebook["investment_view"]["opportunities"][0]["source_ids"] == ["web-one"]
        assert notebook["sources"][0]["source_id"] == "web-one"
        follow, _ = service.begin_run(session, ["xlk"])
        follow.context_json = {**follow.context_json, "research_dossiers": [read_dossier(session, "xlk")],
            "prior_events": service.events_for_instruments(session, ["xlk"])}
        add_sources(follow)
        case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == "xlk"))
        case.evidence_json = {**case.evidence_json, "event_version_id": f"{case.case_id}:next"}
        summary_only = {"reviews": [{"instrument_id": "xlk", "research": payload["reviews"][0]["research"]}]}
        with pytest.raises(service.ResearchVersionConflict, match="事件判断"):
            service.apply_result(session, follow, json.dumps(summary_only))


def test_failed_due_attempt_cursor_does_not_consume_next_calendar_day(client, monkeypatch):
    from watchlist_app.services.research_triggers import _due_items
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run)
        case = publish(session, run)
        case.evidence_json = {**case.evidence_json, "follow_up_until": "2000-01-01"}
        now = datetime.now(UTC)
        context = {**run.context_json, "incremental_trigger": {"instrument_id": "xlk", "coverage_cursor": {
            "attempted_at": now.isoformat(), "due_event_versions": {case.case_id: service.event_record(case)["event_version_id"]}}}}
        # Retry recovery owns repeated attempts within a day; the cursor cannot
        # permanently mark expiry as successfully covered after a failed run.
        assert not _due_items(session, "xlk", context, now, now)
        assert _due_items(session, "xlk", context, now, now + timedelta(days=1))
        assert case.evidence_json["follow_up"] == "watch"


def test_old_event_new_dated_development_enters_recent_without_redating_original(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"])
        add_sources(run, published_at="2020-01-01")
        case = publish(session, run, occurred_at="2019-12-31", published_at="2020-01-01")
        original = dict(case.history_json[0]["snapshot"])
        today = datetime.now(UTC).date().isoformat()
        add_sources(run, published_at=today, url="https://example.test/new-official-development")
        publish(session, run, action="updated", recording_type="update", occurred_at="2019-12-31", published_at=today,
            body="原事项出现新的正式进展，新增条款将在下个月实施。")
        session.commit()
        page = event_page(session, "xlk", display_timezone="UTC")
        assert page["events"][0]["timeline_date"] == today
        assert page["events"][0]["occurred_at"] == "2019-12-31"
        assert not page["late_arrivals"]
        assert case.history_json[0]["snapshot"] == original
        # Later score and follow-up changes do not change the development date.
        publish(session, run, action="updated", recording_type="update", occurred_at="2019-12-31", published_at=today,
            body="原事项出现新的正式进展，新增条款将在下个月实施。", importance_score=4)
        assert case.evidence_json["development_at"] == today
