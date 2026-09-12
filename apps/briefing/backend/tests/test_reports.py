from copy import deepcopy
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from briefing_app.contracts import ReportDraft
from briefing_app.db import Base, Report, get_session
from briefing_app.evidence import collect_text, current_source_index, report_window
from briefing_app.main import create_app
from briefing_app.reports import begin_report, validate_draft
from briefing_app.settings import Settings
from studio_identity import Principal


@pytest.fixture(autouse=True)
def report_test_actor(monkeypatch):
    actor = Principal("pm", "研究员", "default", team_role="admin", credential="test")
    monkeypatch.setattr("briefing_app.main.resolve_request", lambda *a, **k: Principal("pm", "研究员", "default", team_role="admin", credential="test"))
    monkeypatch.setattr("briefing_app.runner.resolve_token", lambda token, audience: Principal("pm", "研究员", "default", team_role="admin", credential=token, resource_scope={"kind": "report", "id": token}))
    monkeypatch.setattr("briefing_app.runner.revoke_delegation", lambda *a, **k: None)
    monkeypatch.setattr("briefing_app.runner.issue_delegation", lambda principal, audience, scope: scope["id"])
    return actor


@pytest.fixture(autouse=True)
def original_store(monkeypatch):
    from studio_market import config, text
    monkeypatch.setattr(config.MarketSettings, "from_environment", lambda **kwargs: None)
    class Store:
        def __init__(self, settings):
            self.closed = False
        def read(self, source_id, *, version_id, as_of):
            assert source_id == "text:v1" and version_id == "v1"
            assert as_of == datetime(2026, 9, 7, 14, 45, tzinfo=UTC)
            return {"source_id": source_id, "version_id": version_id, "occurred_at": "2026-09-06",
                    "content_text": "央行维持政策利率5%，并表示关注就业。", "raw_path": "/private/raw"}
        def close(self):
            self.closed = True
    monkeypatch.setattr(text, "TextStore", Store)


@pytest.fixture
def factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def snapshot():
    return {**report_window("daily", datetime(2026, 9, 7, 14, 45, tzinfo=UTC), "Asia/Shanghai"),
            "source_count": 1, "sources": [
                {"source_id": "text:v1", "version_id": "v1", "source_type": "public_document", "title": "央行声明", "published_at": "2026-09-07T10:00:00+00:00", "occurred_at": "2026-09-06", "observed_at": "2026-09-07T11:00:00+00:00"},
                {"source_id": "market-row:0", "source_type": "market_row", "symbol": "SPY", "return_pct": 2.5}],
            "market_rows": [{"symbol": "SPY", "label": "标普500 ETF", "return_pct": 2.5, "start_date": "2026-09-03", "end_date": "2026-09-04", "start_close": 100, "end_close": 102.5, "source_ids": []}],
            "macro_rows": [], "numeric_coverage": ["美股数据截至上一交易日"], "text_coverage": {"bundle_count": 1}}


def draft():
    return {"report_type": "daily", "sections": [{"kind": "takeaway_section", "title": "重点信息", "groups": [
        {"title": "宏观", "items": [{"title": "央行继续观察就业", "tags": ["利率", "就业"], "summary": "央行维持政策利率5%。", "analysis": "下一步观察就业是否走弱。", "source_ids": ["text:v1"], "number_citations": [{"source_id": "text:v1", "value": "5", "quote": "央行维持政策利率5%"}], "related_market_symbols": []}]},
        {"title": "微观", "items": []}]}]}


def test_daily_and_weekly_windows_keep_exact_clocks():
    cutoff = datetime.fromisoformat("2026-09-11T22:45:00+08:00")
    daily = report_window("daily", cutoff, "Asia/Shanghai")
    weekly = report_window("weekly", cutoff, "Asia/Shanghai")
    assert daily["period_start"] == "2026-09-10T22:45:00+08:00"
    assert weekly["period_start"] == "2026-09-07T00:00:00+08:00"
    dst = report_window("daily", datetime.fromisoformat("2026-11-01T08:00:00-05:00"), "America/New_York")
    assert datetime.fromisoformat(dst["cutoff"]) - datetime.fromisoformat(dst["period_start"]) == timedelta(hours=24)


def test_full_week_source_pagination_preserves_late_old_fact_dates():
    cutoff = datetime(2026, 9, 11, 14, 45, tzinfo=UTC)
    window = report_window("weekly", cutoff, "Asia/Shanghai")
    originals = [{"source_id": f"text:{i}", "published_at": "2026-08-01", "observed_at": "2026-09-10T10:00:00+00:00", "content_text": "旧事本周补录"} for i in range(213)]
    calls = []
    class Store:
        def search(self, **kwargs):
            calls.append(kwargs)
            offset = kwargs["offset"]
            return {"rows": originals[offset:offset + kwargs["limit"]], "total": len(originals), "coverage": {"bundle_count": 2}}
        def read(self, source_id, **kwargs):
            assert kwargs["as_of"] == cutoff
            return originals[int(source_id.split(":")[1])]
    result, _ = collect_text(Store(), window)
    assert len(result) == 213
    assert {row["published_at"] for row in result} == {"2026-08-01"}
    assert [call["offset"] for call in calls] == [0, 100, 200] * 3
    assert "content_text" not in result[0]
    assert calls[-1]["received_after"] == datetime.fromisoformat(window["period_start"])
    assert calls[0]["published_after"] == datetime.fromisoformat("2026-09-07T00:00:00+08:00")
    assert result[0]["window_reasons"] == ["published", "observed", "received"]


def test_first_import_retains_historical_material_as_searchable_context():
    class Store:
        def search(self, **kwargs):
            rows = [{"source_id": "text:old", "version_id": "old", "title": "历史研究", "content_text": "原文", "provenance": {"large": "metadata"}}] if "received_after" in kwargs else []
            return {"rows": rows, "total": len(rows), "coverage": {"bundle_count": 1, "export_coverage": {"large": "manifest"}}}
    rows, coverage = collect_text(Store(), report_window("daily", datetime(2026, 9, 7, tzinfo=UTC), "Asia/Shanghai"))
    assert rows[0]["window_scope"] == "late_received"
    assert rows[0]["window_reasons"] == ["received"]
    assert "content_text" not in rows[0] and "provenance" not in rows[0]
    assert "export_coverage" not in coverage


def test_complete_headline_index_keeps_last_channel_without_body_or_pagination():
    sources = [{"source_id": f"text:{index}", "source_type": "public_document", "window_scope": "current",
                "channel_id": f"channel-{index % 3}", "title": "完整保留的市场标题" * 20,
                "published_at": "2026-09-07", "content_text": "不要重复正文", "provenance": {"raw_path": "private"}}
               for index in range(705)]
    sources.append({"source_id": "text:late", "source_type": "public_document", "window_scope": "late_received"})
    index = current_source_index({"sources": sources})
    assert index["count"] == 705
    assert index["columns"] == ["source_id", "title", "published_at"]
    rows = [row for channel in index["channels"] for row in channel["rows"]]
    assert {row[0] for row in rows} == {f"text:{index}" for index in range(705)}
    assert all(len(row) == 3 for row in rows)
    assert "不要重复正文" not in str(index) and "raw_path" not in str(index)


def test_report_checks_numeric_text_quote_and_source_scope(snapshot):
    payload = draft()
    assert validate_draft(ReportDraft.model_validate(payload), snapshot)["report_type"] == "daily"
    item = payload["sections"][0]["groups"][0]["items"][0]
    item["number_citations"][0]["value"] = "5%"
    with pytest.raises(ValueError, match="纯数字"):
        validate_draft(ReportDraft.model_validate(payload), snapshot)
    item["number_citations"][0]["value"] = "5"
    item["number_citations"][0]["quote"] = "央行维持政策利率6%"
    with pytest.raises(ValueError, match="央行继续观察就业.*'5'.*text:v1.*逐字"):
        validate_draft(ReportDraft.model_validate(payload), snapshot)
    item["number_citations"][0]["quote"] = "央行维持政策利率5%"
    item["source_ids"] = ["text:not-this-report"]
    with pytest.raises(ValueError, match="未保留"):
        validate_draft(ReportDraft.model_validate(payload), snapshot)


def test_title_is_bound_evidence_but_quotes_cannot_join_title_and_body(snapshot, monkeypatch):
    monkeypatch.setattr("briefing_app.reports.read_bound_source", lambda *_: {
        "title": "央行维持政策利率5%", "content_text": "并表示关注就业。"})
    payload = draft()
    assert validate_draft(ReportDraft.model_validate(payload), snapshot)["report_type"] == "daily"
    citation = payload["sections"][0]["groups"][0]["items"][0]["number_citations"][0]
    citation["quote"] = "央行维持政策利率5%并表示关注就业。"
    with pytest.raises(ValueError, match="标题或正文之一"):
        validate_draft(ReportDraft.model_validate(payload), snapshot)


def test_validation_reports_errors_across_items_in_one_response(snapshot):
    payload = draft()
    first = payload["sections"][0]["groups"][0]["items"][0]
    first["number_citations"] = []
    second = deepcopy(first)
    second.update(title="股市变化", summary="SPY上涨3%。", source_ids=["market-row:0"],
                  number_citations=[{"source_id": "market-row:0", "value": "3", "field": "return_pct"}])
    payload["sections"][0]["groups"][1]["items"].append(second)
    with pytest.raises(ValueError) as caught:
        validate_draft(ReportDraft.model_validate(payload), snapshot)
    assert "央行继续观察就业：正文数字 5 缺少数字引用" in str(caught.value)
    assert "股市变化：数字与本轮原始或程序计算结果不一致" in str(caught.value)


def test_program_return_cannot_be_overwritten_in_model_prose(snapshot):
    payload = draft()
    item = payload["sections"][0]["groups"][0]["items"][0]
    item.update(summary="SPY上涨2.5%。", source_ids=["market-row:0"], related_market_symbols=["SPY"],
                number_citations=[{"source_id": "market-row:0", "value": "2.5", "field": "return_pct"}])
    validate_draft(ReportDraft.model_validate(payload), snapshot)
    item["number_citations"][0]["value"] = "3"
    with pytest.raises(ValueError, match="不一致"):
        validate_draft(ReportDraft.model_validate(payload), snapshot)
    item["number_citations"] = []
    with pytest.raises(ValueError, match="数字引用"):
        validate_draft(ReportDraft.model_validate(payload), snapshot)


def test_versions_and_active_run_deduplication(factory):
    with factory() as session:
        first, created = begin_report(session, "daily", datetime(2026, 9, 7, tzinfo=UTC), "Asia/Shanghai", edition_role="publisher")
        same, duplicate = begin_report(session, "daily", datetime(2026, 9, 7, 1, tzinfo=UTC), "Asia/Shanghai", edition_role="publisher")
        assert created and not duplicate and first.report_id == same.report_id
        from briefing_app.reports import report_summary
        assert report_summary(first)["edition_role"] == "publisher"
        first.status = "completed"
        session.commit()
        second, created = begin_report(session, "daily", datetime(2026, 9, 7, 2, tzinfo=UTC), "Asia/Shanghai", edition_role="publisher")
        assert created and second.version == 2
        assert session.get(Report, first.report_id).status == "completed"
        with pytest.raises(ValueError, match="截止"):
            begin_report(session, "daily", datetime.now(UTC) + timedelta(days=1), "Asia/Shanghai", edition_role="publisher")


def test_teams_version_and_deduplicate_their_own_report_period(factory):
    cutoff = datetime(2026, 9, 7, tzinfo=UTC)
    with factory() as session:
        first, _ = begin_report(session, "daily", cutoff, "Asia/Shanghai", edition_role="publisher",
                                principal=Principal("a", "甲", "team-a"))
        second, created = begin_report(session, "daily", cutoff, "Asia/Shanghai", edition_role="publisher",
                                       principal=Principal("b", "乙", "team-b"))
        assert created and first.report_id != second.report_id
        assert first.version == second.version == 1
        duplicate, created = begin_report(session, "daily", cutoff, "Asia/Shanghai", edition_role="publisher",
                                          principal=Principal("b", "乙", "team-b"))
        assert not created and duplicate.report_id == second.report_id
        second.status = "completed"
        session.commit()
        revision, created = begin_report(session, "daily", cutoff, "Asia/Shanghai", edition_role="publisher",
                                         principal=Principal("b", "乙", "team-b"))
        assert created and revision.version == 2


def test_api_preserves_source_versions_and_rejects_late_draft(factory, snapshot):
    app = create_app(Settings(database_url="sqlite+pysqlite:///:memory:"))
    def session_override():
        with factory() as session:
            yield session
    app.dependency_overrides[get_session] = session_override
    with factory() as session:
        report, _ = begin_report(session, "daily", datetime(2026, 9, 7, tzinfo=UTC), "Asia/Shanghai", edition_role="publisher")
        report.input_json, report.status = deepcopy(snapshot), "running"
        report.input_json["sources"][0]["window_scope"] = "current"
        report.input_json["sources"].append({"source_id": "text:old", "version_id": "old", "source_type": "public_document", "title": "历史研究背景", "window_scope": "late_received"})
        session.commit()
        report_id = report.report_id
    client = TestClient(app, headers={"Authorization": "Bearer test"})
    response = client.post(f"/api/briefing/reports/{report_id}/draft", params={"mode": "write"}, json=draft())
    assert response.status_code == 200
    assert response.json()["mode"] == "write"
    assert client.get(f"/api/briefing/reports/{report_id}/context").json()["draft_json"] == ReportDraft.model_validate(draft()).model_dump(mode="json")
    assert client.get(f"/api/briefing/reports/{report_id}").json()["report"] is None
    source = client.get(f"/api/briefing/reports/{report_id}/sources/text:v1").json()
    assert source["occurred_at"] == "2026-09-06"
    assert "raw_path" not in source
    assert source["content_text"].startswith("央行维持")
    assert client.get(f"/api/briefing/reports/{report_id}/sources/text:another").status_code == 404
    assert client.get(f"/api/briefing/reports/{report_id}/source-index").json()["total"] == 2
    historical = client.get(f"/api/briefing/reports/{report_id}/source-index", params={"scope": "late_received", "query": "历史研究"}).json()
    assert historical["total"] == 1 and historical["rows"][0]["source_id"] == "text:old"
    with factory() as session:
        report = session.get(Report, report_id)
        report.status = "completed"
        report.result_json = report.draft_json["report"]
        session.commit()
    assert client.post(f"/api/briefing/reports/{report_id}/draft", params={"mode": "review"}, json=draft()).status_code == 409
    detail = client.get(f"/api/briefing/reports/{report_id}").json()
    assert detail["market_rows"][0]["end_date"] == "2026-09-04"
    assert detail["report"]["sections"][0]["title"] == "重点信息"
    assert detail["report"]["sections"][0]["groups"][0]["items"][0]["tags"] == ["利率", "就业"]


def test_runner_publishes_only_structured_success_and_retains_failed_version(factory, snapshot, monkeypatch, caplog, report_test_actor):
    from briefing_app import runner
    monkeypatch.setattr(runner, "get_session_factory", lambda: factory)
    monkeypatch.setattr(runner, "build_input", lambda *args: deepcopy(snapshot))
    with factory() as session:
        report, _ = begin_report(session, "daily", datetime(2026, 9, 7, tzinfo=UTC), "Asia/Shanghai", edition_role="publisher")
        report_id = report.report_id
    modes = []
    class Process:
        returncode = 0
        def __init__(self, args, **kwargs):
            self.mode = args[-1]
            modes.append(self.mode)
        def communicate(self, **kwargs):
            with factory() as session:
                row = session.get(Report, report_id)
                content = draft()
                if self.mode == "review":
                    assert row.draft_json["mode"] == "write" and row.result_json is None
                    content["sections"][0]["groups"][0]["items"][0]["analysis"] = "核对原文后，保留其条件与不确定性。"
                row.draft_json = {"mode": self.mode, "report": content}
                session.commit()
            return "报告已提交", ""
    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    runner.run_report(report_id, report_id, report_test_actor)
    with factory() as session:
        row = session.get(Report, report_id)
        assert row.status == "completed" and modes == ["write", "review"]
        assert row.result_json["sections"][0]["groups"][0]["items"][0]["analysis"] == "核对原文后，保留其条件与不确定性。"
        second, _ = begin_report(session, "daily", datetime(2026, 9, 7, tzinfo=UTC), "Asia/Shanghai", edition_role="publisher")
        second_id = second.report_id
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(returncode=0, communicate=lambda **kw: ("这里只是普通回复", "")))
    runner.run_report(second_id, second_id, report_test_actor)
    with factory() as session:
        rows = list(session.scalars(select(Report).order_by(Report.version)))
        assert [row.status for row in rows] == ["completed", "failed"]
        assert rows[1].result_json is None and rows[1].input_json["sources"]
        third, _ = begin_report(session, "daily", datetime(2026, 9, 7, 3, tzinfo=UTC), "Asia/Shanghai", edition_role="publisher")
        third_id = third.report_id
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(returncode=1, communicate=lambda **kw: ("", "API_KEY=private-value\nerror EEXIST profile bootstrap\n")))
    runner.run_report(third_id, third_id, report_test_actor)
    assert third_id in caplog.text and "exit_code=1" in caplog.text and "EEXIST profile bootstrap" in caplog.text
    assert "private-value" not in caplog.text


@pytest.mark.parametrize("review_exit", [0, 1])
def test_runner_never_publishes_writer_draft_when_editor_does_not_submit(factory, snapshot, monkeypatch, review_exit, report_test_actor):
    from briefing_app import runner
    monkeypatch.setattr(runner, "get_session_factory", lambda: factory)
    monkeypatch.setattr(runner, "build_input", lambda *args: deepcopy(snapshot))
    with factory() as session:
        report, _ = begin_report(session, "daily", datetime(2026, 9, 7, tzinfo=UTC), "Asia/Shanghai", edition_role="publisher")
        report_id = report.report_id
    class Process:
        def __init__(self, args, **kwargs):
            self.mode = args[-1]
            self.returncode = review_exit if self.mode == "review" else 0
        def communicate(self, **kwargs):
            if self.mode == "write":
                with factory() as session:
                    session.get(Report, report_id).draft_json = {"mode": "write", "report": draft()}
                    session.commit()
            return "校稿未提交", ""
    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    runner.run_report(report_id, report_id, report_test_actor)
    with factory() as session:
        report = session.get(Report, report_id)
        assert report.status == "failed" and "校稿" in report.error
        assert report.result_json is None and report.draft_json["mode"] == "write"


@pytest.mark.parametrize("failure", [None, "initial_expired", "issuer_after_prepare", "permission_before_review", "revoked_before_publish"])
def test_each_stage_revalidates_original_issuer_and_revokes_all_grants(factory, snapshot, monkeypatch, report_test_actor, failure):
    from dataclasses import replace
    import json
    from studio_identity import IdentityError
    from briefing_app import runner
    issuer = report_test_actor
    with factory() as session:
        report, _ = begin_report(session, "daily", datetime(2026, 9, 7, tzinfo=UTC), "Asia/Shanghai",
                                  edition_role="publisher", principal=issuer)
        report_id = report.report_id
    elapsed, stages, issued, revoked = [0], [], [], []
    expiries = {"initial-grant": 3600}
    def build(*args):
        elapsed[0] = 3700  # Preparation consumed the original grant; issuer remains valid.
        return deepcopy(snapshot)
    def issue(principal, audience, scope):
        assert principal is issuer and audience == "briefing" and scope == {"kind": "report", "id": report_id}
        if failure == "issuer_after_prepare":
            raise IdentityError(401, "Original issuer expired")
        token = f"stage-grant-{len(issued) + 1}"
        issued.append(token)
        expiries[token] = elapsed[0] + 3600
        return token
    def resolve(token, audience):
        assert audience == "briefing"
        if ((failure == "initial_expired" and token == "initial-grant")
                or elapsed[0] >= expiries[token]
                or (failure == "revoked_before_publish" and len(stages) == 2)):
            raise IdentityError(401, "Report grant no longer valid")
        role = "reader" if failure == "permission_before_review" and len(issued) == 2 else "admin"
        return replace(issuer, credential=token, team_role=role, resource_scope={"kind": "report", "id": report_id})
    def harness(rid, mode, token):
        assert rid == report_id and token == issued[-1]
        stages.append((mode, token))
        elapsed[0] += 1740  # Each allowed model stage finishes within 30 minutes.
        with factory() as session:
            session.get(Report, rid).draft_json = {"mode": mode, "report": draft()}
            session.commit()
        return {"reviewed": mode == "review"}
    monkeypatch.setattr(runner, "get_session_factory", lambda: factory)
    monkeypatch.setattr(runner, "build_input", build)
    monkeypatch.setattr(runner, "issue_delegation", issue)
    monkeypatch.setattr(runner, "resolve_token", resolve)
    monkeypatch.setattr(runner, "_run_harness", harness)
    monkeypatch.setattr(runner, "revoke_delegation", lambda token, principal: revoked.append((token, principal)))
    runner.run_report(report_id, "initial-grant", issuer)
    with factory() as session:
        report = session.get(Report, report_id)
        assert report.status == ("failed" if failure else "completed")
        assert report.result_json == (None if failure else {"reviewed": True})
        assert "stage-grant" not in json.dumps(report.input_json) + json.dumps(report.draft_json) + json.dumps(report.result_json)
    assert {token for token, _ in revoked} == {"initial-grant", *issued}
    assert all(principal is issuer for _, principal in revoked)
    if failure in {"initial_expired", "issuer_after_prepare"}:
        assert stages == []
    elif failure == "permission_before_review":
        assert stages == [("write", "stage-grant-1")]
    else:
        assert stages == [("write", "stage-grant-1"), ("review", "stage-grant-2")]


def test_generate_dispatch_retains_original_issuer(factory, monkeypatch, report_test_actor):
    from briefing_app import main
    app = create_app(Settings(database_url="sqlite+pysqlite:///:memory:"))
    def session_override():
        with factory() as session:
            yield session
    app.dependency_overrides[get_session] = session_override
    calls = []
    monkeypatch.setattr(main, "harness_available", lambda: True)
    monkeypatch.setattr(main, "resolve_request", lambda *args, **kwargs: report_test_actor)
    monkeypatch.setattr(main, "issue_delegation", lambda *args: "initial-grant")
    monkeypatch.setattr(main, "run_report", lambda report_id, token, issuer: calls.append((report_id, token, issuer)))
    response = TestClient(app, headers={"Authorization": "Bearer test"}).post("/api/briefing/reports",
        json={"report_type": "daily", "cutoff": "2026-09-07T00:00:00Z"})
    assert response.status_code == 202
    assert calls == [(response.json()["report_id"], "initial-grant", report_test_actor)]


def test_weekly_schema_is_independent_of_daily_summary(snapshot):
    snapshot["report_type"] = "weekly"
    weekly = {"report_type": "weekly", "sections": [
        {"kind": "topic_recommendations", "title": "本周话题推荐", "groups": []},
        {"kind": "opportunity_leads", "title": "新机会线索", "items": []}]}
    validate_draft(ReportDraft.model_validate(weekly), snapshot)
    with pytest.raises(ValueError, match="类型"):
        validate_draft(ReportDraft.model_validate(draft()), snapshot)
