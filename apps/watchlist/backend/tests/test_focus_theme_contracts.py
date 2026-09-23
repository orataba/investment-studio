from copy import deepcopy
from datetime import UTC, datetime
import json

import pytest

from .test_research_themes import research_client, _themes
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_themes import AnalystThemeUpdate, save_analyst_theme, theme_index


def test_title_only_user_theme_is_not_pinned_and_analyst_builds_its_baseline(research_client):
    created = research_client.post(_themes(), json={"title": "持续观察利率与信用", "kind": "quantitative"})
    assert created.status_code == 201, created.text
    theme = created.json()
    assert theme["question"] == "" and theme["baseline_status"] == "pending" and not theme["pinned"]
    assert theme["research_status"] == "unavailable"
    with get_session_factory()() as session:
        updated = save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(
            theme_key=theme["theme_key"], theme_id=theme["theme_id"], question="利率与信用变化是否改变债券回报？",
            synthesis="尚需取得同口径期限和信用数据。", priority_reason="可能改变持有债券的风险补偿。",
            next_check="读取期限结构与实际信用敞口", latest_development="已建立研究问题"))
        session.commit()
        assert updated["origin"] == "user" and updated["managed_by"] == "researcher"
        assert updated["baseline_status"] == "ready" and updated["last_reviewed_at"]
        assert updated["versions"][0]["question"] == ""


def test_quiet_theme_review_only_advances_receipt_not_judgment_version(research_client):
    theme = research_client.post(_themes(), json={"title": "资金成本", "question": "融资约束如何变化？"}).json()
    with get_session_factory()() as session:
        reviewed = save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(
            theme_key=theme["theme_key"], theme_id=theme["theme_id"]))
        assert reviewed["revision_number"] == theme["revision_number"] and reviewed["versions"] == []
        assert reviewed["updated_at"] == theme["updated_at"]
        assert reviewed["last_reviewed_at"] and reviewed["baseline_status"] == "pending"


def test_only_explicit_pin_protects_core_but_not_research_assessment(research_client):
    theme = research_client.post(_themes(), json={"title": "资金成本", "question": "融资约束如何变化？", "pinned": True}).json()
    with get_session_factory()() as session:
        with pytest.raises(ValueError, match="已固定"):
            save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(theme_key=theme["theme_key"], status="closed", close_reason="优先级降低"))
        updated = save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(theme_key=theme["theme_key"], synthesis="尚未取得充分证据"))
        assert updated["pinned"] and updated["synthesis"] == "尚未取得充分证据"


def test_active_theme_capacity_is_enforced_for_creation_and_resume(research_client):
    themes = [research_client.post(_themes(), json={"title": f"具体问题{i}"}).json() for i in range(10)]
    assert research_client.post(_themes(), json={"title": "第十一项"}).status_code == 422
    paused = research_client.patch(f"{_themes()}/{themes[0]['theme_id']}", json={"status": "paused"})
    assert paused.status_code == 200
    assert research_client.post(_themes(), json={"title": "重要新问题"}).status_code == 201
    assert research_client.patch(f"{_themes()}/{themes[0]['theme_id']}", json={"status": "active"}).status_code == 422
    current = research_client.get(_themes()).json()
    assert current["active_limit"] == 10 and sum(row["status"] == "active" for row in current["themes"]) == 10


def test_new_theme_dispatch_returns_the_saved_run_without_calling_a_live_model(research_client, monkeypatch):
    from watchlist_app.services import research_runner
    calls = []
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    monkeypatch.setattr(research_runner, "authorize_run", lambda actor, run_id: "test-run-token")
    monkeypatch.setattr(research_runner, "run_analysis", lambda *args: calls.append(args[0]))
    result = research_client.post(_themes(), json={"title": "新的研究问题"}).json()
    assert result["research_status"] == "queued" and result["research_run_id"]
    assert calls == [result["research_run_id"]]


def test_automatic_round_requires_each_active_theme_receipt(research_client):
    from watchlist_app.services import sector_research
    from watchlist_app.services.research_dossier import read_dossier
    theme = research_client.post(_themes(), json={"title": "期限结构"}).json()
    with get_session_factory()() as session:
        run, _ = sector_research.begin_run(session, ["fund-us-agg"])
        run.context_json = {**run.context_json, "research_dossiers": [read_dossier(session, "fund-us-agg")]}
        with pytest.raises(ValueError, match="逐一复核"):
            sector_research.validate_result(session, run, sector_research.ReviewResult.model_validate({"reviews": [{"instrument_id": "fund-us-agg"}]}))
        result = {"reviews": [{"instrument_id": "fund-us-agg", "themes": [{"theme_key": theme["theme_key"], "theme_id": theme["theme_id"]}]}]}
        sector_research.apply_result(session, run, json.dumps(result))
        assert theme_index(session, "fund-us-agg")[0]["revision_number"] == 1


def test_standalone_continuing_question_is_rejected(research_client):
    from watchlist_app.services import sector_research
    with get_session_factory()() as session:
        run, _ = sector_research.begin_run(session, ["fund-us-agg"])
        result = {"reviews": [{"instrument_id": "fund-us-agg", "research": {"questions": [{"key": "credit", "question": "信用会否恶化？", "assessment": "尚待证据", "next_check": "读取披露"}]}}]}
        with pytest.raises(ValueError, match="重点主题"):
            sector_research.validate_result(session, run, sector_research.ReviewResult.model_validate(result))


from .test_research_activity import activity_client, event, publish


def test_attach_brief_event_to_existing_theme_preserves_original_and_requests_research(activity_client, monkeypatch):
    from watchlist_app.services.research_activity import research_activity, resolve_event_reference
    from watchlist_app.services import research_runner
    theme = activity_client.post("/api/research/instruments/xlk/themes", json={"title": "资金用途"}).json()
    with get_session_factory()() as session:
        publish(session, events=[event(follow_up="none", next_watch="")])
        original = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "event")
    calls = []
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    monkeypatch.setattr(research_runner, "authorize_run", lambda actor, run_id: "test-token")
    monkeypatch.setattr(research_runner, "run_analysis", lambda *args: calls.append(args[0]))
    reference = {key: value for key, value in original["reference"].items() if key != "instrument_id"}
    response = activity_client.patch(f"/api/research/instruments/xlk/themes/{theme['theme_id']}", json={"reference": reference})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["baseline_status"] == "pending" and updated["baseline_requested_at"] > theme["baseline_requested_at"]
    assert calls == [updated["research_run_id"]]
    with get_session_factory()() as session:
        activity = research_activity(session, "xlk", include_recent_events=True)
        assert activity["recent_events"] == []
        events = [row for row in activity["updates"] if row["kind"] == "event"]
        assert len(events) == 2 and events[0]["theme_ids"] == [theme["theme_id"]]
        assert events[1]["update_id"] == original["update_id"] and events[1]["superseded"]
        assert resolve_event_reference(session, "xlk", reference["event_case_id"], reference["event_version_id"])["body"] == original["body"]
        assert updated["sources"][0]["source_id"] == "original" and updated["sources"][0]["text"] == "A financing transaction and its original terms."


def test_data_theme_retains_selected_notebook_source_version(activity_client):
    from watchlist_app.services.research_dossier import _notebooks
    with get_session_factory()() as session:
        publish(session, research={"source_ids": ["original"], "investment_view": {"direction": "等待验证"}})
        original, _ = _notebooks(session, "xlk", False)
    response = activity_client.post("/api/research/instruments/xlk/themes", json={"title": "依据原始披露持续研究", "reference": {
        "notebook_version_id": original["version_id"], "source_ids": ["original"]}})
    assert response.status_code == 201, response.text
    assert response.json()["sources"] == original["sources"]
    invalid = activity_client.post("/api/research/instruments/xlk/themes", json={"title": "无依据引用", "reference": {
        "notebook_version_id": original["version_id"], "source_ids": ["not-in-version"]}})
    assert invalid.status_code == 422


def test_failed_before_binding_does_not_retry_same_theme_every_worker_tick(research_client, monkeypatch):
    from watchlist_app.services import sector_research
    monkeypatch.setattr(sector_research, "_research_market", lambda session, iid: "us")
    theme = research_client.post(_themes(), json={"title": "期限结构"}).json()
    with get_session_factory()() as session:
        run, _ = sector_research.begin_run(session, ["fund-us-agg"])
        assert run.context_json["attempted_theme_baselines"][theme["theme_id"]] == theme["baseline_requested_at"]
        run.status = "failed"
        session.commit()
        retry, created = sector_research.begin_run(session, ["fund-us-agg"], scheduled=True)
        assert not created and retry.entry_id == run.entry_id


def test_organization_migration_is_not_a_completed_daily_research_check(research_client, monkeypatch):
    from watchlist_app.services import sector_research
    monkeypatch.setattr(sector_research, "_research_market", lambda session, iid: "us")
    with get_session_factory()() as session:
        organization, _ = sector_research.begin_run(session, ["fund-us-agg"])
        organization.status = "completed"
        organization.context_json = {**organization.context_json, "recordkeeping_only": True}
        session.commit()
        actual, created = sector_research.begin_run(session, ["fund-us-agg"], scheduled=True)
        assert created and actual.entry_id != organization.entry_id


def test_current_authenticated_user_can_own_title_only_theme_without_directory_service(research_client, monkeypatch):
    import studio_identity
    def unavailable(*args, **kwargs):
        raise studio_identity.IdentityError(503, "尚未配置统一账号服务")
    monkeypatch.setattr(studio_identity, "team_directory", unavailable)
    actor = research_client.get(_themes()).json()["identity"]
    response = research_client.post(_themes(), json={"title": "自主研究课题", "responsible_user_id": actor["user_id"]})
    assert response.status_code == 201, response.text
    assert response.json()["research_status"] == "unavailable"


def test_theme_only_numeric_publication_has_its_own_immutable_source_version(research_client):
    theme = research_client.post(_themes(), json={"title": "持续量化研究", "kind": "quantitative"}).json()
    source = {"source_id": "computed:original", "source_type": "computed_metric", "instrument_id": "fund-us-agg",
        "scope": "instrument", "title": "共同样本描述统计", "data": {"analysis_kind": "python_quant", "metrics": {"observations": 12}},
        "methodology": {"code": "result = {'observations': len(inputs)}", "params": {}, "input_sources": []}}
    with get_session_factory()() as session:
        published = save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(theme_key=theme["theme_key"],
            synthesis="这是描述统计，因果机制仍待验证。", figure_source_ids=[source["source_id"]]), sources={source["source_id"]: source})
        session.commit()
        old_version = published["source_version_id"]
        replacement = {**source, "data": {"analysis_kind": "python_quant", "metrics": {"observations": 20}}}
        save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(theme_key=theme["theme_key"],
            synthesis="样本更新，仍不支持因果判断。"), sources={source["source_id"]: replacement})
        session.commit()
    # The original figure remains readable without any published notebook, even
    # after another theme revision retained the same logical source ID.
    response = research_client.get("/api/research/instruments/fund-us-agg/dossier", params={
        "version_id": old_version, "source_id": source["source_id"]})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["metrics"]["observations"] == 12
    current = research_client.get(_themes()).json()["themes"][0]
    assert current["source_version_id"] != old_version
    latest = research_client.get("/api/research/instruments/fund-us-agg/dossier", params={
        "version_id": current["source_version_id"], "source_id": source["source_id"]})
    assert latest.json()["data"]["metrics"]["observations"] == 20


def test_title_only_theme_can_be_edited_without_inventing_a_question(research_client):
    theme = research_client.post(_themes(), json={"title": "待研究主题"}).json()
    response = research_client.patch(f"{_themes()}/{theme['theme_id']}", json={"title": "新的待研究主题", "question": "", "pinned": True})
    assert response.status_code == 200, response.text
    assert response.json()["question"] == "" and response.json()["baseline_status"] == "pending"


def test_theme_reference_uses_exact_theme_version_and_rejects_mislabeled_kind(research_client):
    theme = research_client.post(_themes(), json={"title": "原始研究主题", "background": "原始背景"}).json()
    research_client.patch(f"{_themes()}/{theme['theme_id']}", json={"title": "已修订主题"})
    reference = {"theme_version_id": theme["source_version_id"]}
    response = research_client.post(_themes(), json={"title": "由原始版本建立的研究", "reference": reference})
    assert response.status_code == 201, response.text
    assert "原始研究主题" in response.json()["background"] and "已修订主题" not in response.json()["background"]
    invalid = research_client.post(_themes(), json={"title": "错误的版本类型", "reference": {"notebook_version_id": theme["source_version_id"]}})
    assert invalid.status_code == 422
