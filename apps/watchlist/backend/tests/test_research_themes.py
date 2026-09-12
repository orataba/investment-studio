from copy import deepcopy
from datetime import UTC, datetime
import json

import pytest


@pytest.fixture
def research_client(client, monkeypatch):
    import watchlist_app.main as main
    from studio_identity import Principal
    monkeypatch.setattr(main, "resolve_request", lambda request, audience, **kwargs: Principal("pm-one", "投资经理甲", "default"))
    wid = client.post("/api/watchlists", json={"name": "PM themes"}).json()["watchlist_id"]
    response = client.post(f"/api/watchlists/{wid}/items", json={"instrument_ids": ["sxv264", "fund-us-agg"]})
    assert response.status_code == 200, response.text
    return client


def _themes(iid="fund-us-agg"):
    return f"/api/research/instruments/{iid}/themes"


def _notes(iid="fund-us-agg"):
    return f"/api/instruments/{iid}/research/notes"


def _theme(client, iid="fund-us-agg"):
    response = client.post(_themes(iid), json={
        "title": "  长债压力与信用预期  ", "question": "长债收益率抬升的主导因素是否发生变化？",
        "background": "区分实际利率、期限溢价和信用担忧",
    })
    assert response.status_code == 201, response.text
    return response.json()


def _note(client, context=None, iid="fund-us-agg", **overrides):
    payload = {"note_date": "2026-09-08", "title": "信用担忧值得关注", "body": "这是中期判断，短期方向尚不明确", **overrides}
    if context is not None:
        payload["research_context"] = context
    response = client.post(_notes(iid), json={"note": payload, "updated_by": "forged-updater"})
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["notes"] if item["title"] == payload["title"])


def test_theme_lifecycle_retains_versions_without_repeating_unchanged_updates(research_client):
    client = research_client
    theme = _theme(client)
    assert theme["title"] == "长债压力与信用预期"
    assert theme["author_user_id"] == "pm-one" and theme["author"] == "投资经理甲"
    assert theme["status"] == "active" and theme["revision_number"] == 1
    path = f"{_themes()}/{theme['theme_id']}"
    response = client.patch(path, json={"status": "paused"})
    assert response.status_code == 200, response.text
    paused = response.json()
    assert paused["status"] == "paused" and paused["revision_number"] == 2
    assert paused["question"] == theme["question"] and paused["background"] == theme["background"]
    assert len(paused["versions"]) == 1 and paused["versions"][0]["status"] == "active"
    assert client.patch(path, json={"status": "paused"}).json() == paused
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.research_themes import theme_index
    with get_session_factory()() as session:
        assert theme_index(session, "fund-us-agg", active_only=True) == []
    closed = client.patch(path, json={"status": "closed", "background": "暂不继续跟进"}).json()
    assert closed["revision_number"] == 3 and [v["status"] for v in closed["versions"]] == ["active", "paused"]
    resumed = client.patch(path, json={"status": "active"}).json()
    assert resumed["revision_number"] == 4
    assert client.get(_themes()).json()["themes"][0]["theme_id"] == theme["theme_id"]


def test_analyst_theme_stable_key_closure_and_user_takeover(research_client):
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.research_themes import AnalystThemeUpdate, save_analyst_theme, theme_index
    with get_session_factory()() as session:
        theme = save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(
            theme_key="term-premium", title="期限溢价变化", question="期限溢价上升是否持续？", background="区分实际利率与期限溢价"))
        assert theme["origin"] == theme["managed_by"] == "researcher" and theme["author"] == "研究员"
        revised = save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(theme_key="term-premium", background="补充主导机制的假设"))
        assert revised["theme_id"] == theme["theme_id"] and revised["question"] == theme["question"]
        assert revised["revision_number"] == 2 and len(theme_index(session, "fund-us-agg")) == 1
        with pytest.raises(ValueError, match="原因"):
            save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(theme_key="term-premium", status="closed"))
        closed = save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(
            theme_key="term-premium", status="closed", close_reason="原问题已得到足够证据回答，后续进入背景观察"))
        assert closed["status"] == "closed" and closed["close_reason"]
        paused = save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(theme_key="term-premium", status="paused"))
        assert paused["close_reason"] == "" and paused["status"] == "paused"
        session.commit()
    # Even repeating an already-paused status is an explicit human control decision.
    response = research_client.patch(f"{_themes()}/{theme['theme_id']}", json={"status": "paused"})
    assert response.status_code == 200, response.text
    manual = response.json()
    assert manual["origin"] == "researcher" and manual["managed_by"] == "user"
    with get_session_factory()() as session:
        with pytest.raises(ValueError, match="人工维护"):
            save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(theme_key="term-premium", status="active"))
        assert theme_index(session, "fund-us-agg")[0]["status"] == "paused"


def test_analyst_cannot_rewrite_user_theme_or_use_foreign_instrument_key(research_client):
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.research_themes import AnalystThemeUpdate, save_analyst_theme
    user_theme = _theme(research_client)
    with get_session_factory()() as session:
        with pytest.raises(ValueError, match="人工维护"):
            save_analyst_theme(session, "fund-us-agg", AnalystThemeUpdate(
                theme_key="credit", theme_id=user_theme["theme_id"], question="改为研究员的新问题"))
        with pytest.raises(LookupError, match="主题"):
            save_analyst_theme(session, "sxv264", AnalystThemeUpdate(
                theme_key="credit", theme_id=user_theme["theme_id"], question="错误标的下的新问题"))
    assert research_client.get(_themes()).json()["themes"][0]["question"] == user_theme["question"]


def test_theme_identity_and_instrument_cannot_be_forged(research_client, monkeypatch):
    client = research_client
    theme = _theme(client)
    request = {"title": "伪造归属", "question": "是否可伪造身份？", "author_user_id": "pm-other"}
    assert client.post(_themes(), json=request).status_code == 422
    assert client.patch(f"{_themes('sxv264')}/{theme['theme_id']}", json={"status": "closed"}).status_code == 404
    assert client.post(_notes("sxv264"), json={"note": {
        "note_date": "2026-09-08", "title": "错误标的关联", "research_context": {"theme_id": theme["theme_id"]},
    }}).status_code == 422
    import watchlist_app.main as main
    from studio_identity import Principal
    monkeypatch.setattr(main, "resolve_request", lambda request, audience, **kwargs: Principal("pm-two", "投资经理乙", "default"))
    assert len(client.get(_themes()).json()["themes"]) == 1
    assert client.patch(f"{_themes()}/{theme['theme_id']}", json={"status": "closed"}).status_code == 200
    assert client.post(_notes(), json={"note": {
        "note_date": "2026-09-08", "title": "团队成员的新判断", "research_context": {"theme_id": theme["theme_id"]},
    }}).status_code == 200


def test_note_author_is_server_owned_and_foreign_pm_cannot_edit_or_delete(research_client, monkeypatch):
    client = research_client
    note = _note(client, author="冒名投资经理")
    assert note["author"] == "投资经理甲"
    assert note["author_user_id"] == note["updated_by"] == "pm-one"
    assert note["research_context"]["author_role"] == "user"
    assert note["research_context"]["recorded_via"] == "editor"
    import watchlist_app.main as main
    from studio_identity import Principal
    monkeypatch.setattr(main, "resolve_request", lambda request, audience, **kwargs: Principal("pm-two", "投资经理乙", "default"))
    assert client.put(f"{_notes()}/{note['note_id']}", json={"note": {
        "note_date": "2026-09-08", "title": "被篡改的判断",
    }}).status_code == 422
    assert client.delete(f"{_notes()}/{note['note_id']}?deleted_by=pm-one").status_code == 403
    import watchlist_app.main as main
    from studio_identity import Principal
    monkeypatch.setattr(main, "resolve_request", lambda request, audience, **kwargs: Principal("pm-one", "投资经理甲", "default"))
    assert client.delete(f"{_notes()}/{note['note_id']}?deleted_by=pm-two").status_code == 200
    history = client.get("/api/instruments/fund-us-agg/research/history").json()["note_revisions"]
    assert all(v["author_user_id"] == "pm-one" and v["recorded_by"] == "pm-one" for v in history)


def test_partial_context_edit_preserves_research_and_assistant_provenance(research_client):
    client = research_client
    theme = _theme(client)
    context = {"theme_id": theme["theme_id"], "background": "当时的背景", "horizon": "中期",
               "verification": "观察美元与期限溢价", "invalidation": "实际利率重新成为主导因素"}
    note = _note(client, context)
    from watchlist_app.db.models.research import InstrumentResearchNote
    from watchlist_app.db.session import get_session_factory
    with get_session_factory()() as session:
        record = session.get(InstrumentResearchNote, ("fund-us-agg", note["note_id"]))
        record.research_context = {**record.research_context, "recorded_via": "assistant",
            "source_run_id": "original-dialogue", "source_quote": "记录我的中期判断",
            "evidence_refs": [{"source_id": "original", "published_at": "2026-09-07T00:00:00Z"}]}
        session.commit()
        before = deepcopy(record.research_context)
    response = client.put(f"{_notes()}/{note['note_id']}", json={"note": {
        "note_date": "2026-09-08", "title": note["title"], "author": "冒名人员",
        "research_context": {"horizon": "未来一至三个月"},
    }})
    assert response.status_code == 200, response.text
    updated = response.json()["notes"][0]
    assert updated["research_context"] == {**before, "horizon": "未来一至三个月"}
    assert updated["author"] == "投资经理甲"
    response = client.put(f"{_notes()}/{note['note_id']}", json={"note": {
        "note_date": "2026-09-08", "title": "纯文字修订",
    }})
    assert response.json()["notes"][0]["research_context"] == updated["research_context"]


def test_pm_review_links_original_version_and_is_shared_with_theme_and_dossier(research_client):
    client = research_client
    theme = _theme(client)
    original = _note(client, {"theme_id": theme["theme_id"], "horizon": "中期", "verification": "观察信用担忧是否出现"})
    response = client.put(f"{_notes()}/{original['note_id']}", json={"note": {
        "note_date": "2026-09-08", "title": "后续文字修订", "body": "补充对其他原因的说明",
    }})
    assert response.status_code == 200, response.text
    review = _note(client, {
        "theme_id": theme["theme_id"], "related_note_id": original["note_id"], "related_revision": 1,
        "relationship": "review", "outcome": "方向相符", "mechanism_assessment": "尚不能证实信用机制",
        "alternative_explanations": ["实际利率回落"], "lesson": "分别检查结果与原因", "limitations": "只有一次观察",
    }, title="回看最初判断", note_type="review")
    projected = client.get(_themes()).json()["themes"][0]
    assert {n["note_id"] for n in projected["notes"]} == {original["note_id"], review["note_id"]}
    dossier = client.get("/api/research/instruments/fund-us-agg/dossier").json()
    assert dossier["themes"][0]["theme_id"] == theme["theme_id"]
    views = {n["note_id"]: n for n in dossier["pm_views"]}
    assert len(views[original["note_id"]]["versions"]) == 2
    assert views[review["note_id"]]["research_context"]["related_revision"] == 1
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.research_dossier import read_dossier_version
    with get_session_factory()() as session:
        saved = read_dossier_version(session, "fund-us-agg", f"pm:{original['note_id']}:1")
        assert saved["kind"] == "pm_view" and saved["value"]["title"] == original["title"]
        assert saved["value"]["research_context"]["verification"] == "观察信用担忧是否出现"
        assert saved["information_cutoff"] == saved["value"]["recorded_at"]


@pytest.mark.parametrize("case", ["missing-revision", "unknown-revision", "other-instrument", "other-theme"])
def test_pm_review_rejects_missing_or_mismatched_original_judgment(research_client, case):
    client = research_client
    theme = _theme(client)
    original = _note(client, {"theme_id": theme["theme_id"]})
    context = {"theme_id": theme["theme_id"], "relationship": "review",
               "related_note_id": original["note_id"], "related_revision": 1}
    iid = "fund-us-agg"
    if case == "missing-revision":
        context.pop("related_revision")
    elif case == "unknown-revision":
        context["related_revision"] = 99
    elif case == "other-instrument":
        iid = "sxv264"
        context.pop("theme_id")
    else:
        context["theme_id"] = _theme(client)["theme_id"]
    response = client.post(_notes(iid), json={"note": {
        "note_date": "2026-09-08", "title": "错误复盘关联", "research_context": context,
    }})
    assert response.status_code == 422, response.text


def test_analyst_uses_bound_theme_and_original_pm_revision_without_rewriting_pm(research_client):
    client = research_client
    theme = _theme(client)
    original = _note(client, {"theme_id": theme["theme_id"], "verification": "信用担忧的独立证据"})
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.research_dossier import read_dossier
    from watchlist_app.services import sector_research

    with get_session_factory()() as session:
        topic = ResearchTopic(topic_id="theme-test-conversation", title="讨论PM判断", instrument_ids=["fund-us-agg"], visibility="team")
        session.add(topic)
        session.flush()
        context = {"research_run": True, "sector_run": True, "instrument_ids": ["fund-us-agg"],
            "cutoff": datetime.now(UTC).isoformat(), "research_actor": {"user_id": "pm-one", "display_name": "投资经理甲", "team_id": "default", "team_role": "member"},
            "research_dossiers": [read_dossier(session, "fund-us-agg")],
            "instrument_inputs": [{"instrument_id": "fund-us-agg", "instrument_type": "etf", "name": "Bond ETF"}]}
        run = ResearchEntry(entry_id="theme-research-1", topic_id=topic.topic_id, kind="analysis", status="running",
                            title="复核PM判断", context_json=context)
        session.add(run)
        session.commit()
        question = {"key": "credit-confidence", "question": theme["question"], "theme_id": theme["theme_id"],
            "pm_note_id": original["note_id"], "pm_note_revision": 1,
            "assessment": "研究员认为仍缺乏独立信用证据，保留与PM不同的判断", "next_check": "查核期限溢价和美元表现"}
        payload = {"reviews": [{"instrument_id": "fund-us-agg", "change_kind": "knowledge", "research": {"questions": [question]}}]}
        sector_research.validate_result(session, run, sector_research.ReviewResult.model_validate(payload))
        for change, message in [({"theme_id": "unread-theme"}, "未读取"),
                                ({"pm_note_id": "another-pm-note"}, "原始版本"),
                                ({"pm_note_revision": 2}, "原始版本")]:
            invalid = deepcopy(payload)
            invalid["reviews"][0]["research"]["questions"][0].update(change)
            with pytest.raises(ValueError, match=message):
                sector_research.validate_result(session, run, sector_research.ReviewResult.model_validate(invalid))
        sector_research.apply_result(session, run, json.dumps(payload, ensure_ascii=False))
        session.commit()
        paused_context = deepcopy(context)
        paused_context["research_dossiers"][0]["themes"][0]["status"] = "paused"
        run.context_json = paused_context
        with pytest.raises(ValueError, match="暂停"):
            sector_research.validate_result(session, run, sector_research.ReviewResult.model_validate(payload))
        pm_only = deepcopy(payload)
        pm_only["reviews"][0]["research"]["questions"][0].pop("theme_id")
        with pytest.raises(ValueError, match="暂停"):
            sector_research.validate_result(session, run, sector_research.ReviewResult.model_validate(pm_only))
        session.rollback()
    projected = client.get(_themes()).json()["themes"][0]
    assert projected["notes"][0]["revision_number"] == 1
    assert projected["notes"][0]["body"] == original["body"]
    assert len(projected["research_progress"]) == 1
    assessment = projected["research_progress"][0]
    assert assessment["author_role"] == "researcher" and assessment["pm_note_revision"] == 1
    assert assessment["assessment"] == question["assessment"]


def test_adopting_edited_assistant_answer_retains_its_original_context(research_client):
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from watchlist_app.db.session import get_session_factory
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="pm-adopt", title="讨论", question="", instrument_ids=["fund-us-agg"], status="active", conclusion=""))
        session.flush()
        session.add(ResearchEntry(entry_id="answer-adopt", topic_id="pm-adopt", kind="analysis", title="请分析长债压力", body="助手判断", status="draft",
            context_json={"research_run": True, "instrument_ids": ["fund-us-agg"],
                "research_actor": {"user_id": "pm-one", "display_name": "投资经理甲", "team_id": "default", "team_role": "member"},
                "cutoff": "2026-09-07T00:00:00+00:00", "research_dossiers": [{"instrument_id": "fund-us-agg",
                    "notebook": {"version_id": "notebook-at-answer"}, "mandate": {"version_id": "mandate-at-answer"}}]}))
        session.commit()
    payload = {"source_entry_id": "answer-adopt", "note": {"note_date": "2026-09-08", "title": "我采纳的判断", "body": "由我核改后的判断"}}
    response = research_client.post(_notes(), json=payload)
    assert response.status_code == 200, response.text
    note = response.json()["notes"][0]
    assert note["body"] == "由我核改后的判断" and note["author"] == "投资经理甲"
    context = note["research_context"]
    assert context["recorded_via"] == "assistant_adopted" and context["source_quote"] == "请分析长债压力"
    assert context["research_snapshot"]["notebook_version_id"] == "notebook-at-answer"
    assert context["information_cutoff"] == "2026-09-07T00:00:00+00:00"
    assert research_client.post(_notes("sxv264"), json=payload).status_code == 422
