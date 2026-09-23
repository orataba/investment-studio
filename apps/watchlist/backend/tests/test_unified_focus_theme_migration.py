from copy import deepcopy
from datetime import UTC, datetime
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select

from .test_research_themes import research_client
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env
from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.db.session import get_session_factory


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20260923_0061_unified_focus_themes.py"
    spec = importlib.util.spec_from_file_location("unified_focus_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_migration(session, iid):
    old_time = datetime(2026, 9, 1, tzinfo=UTC)
    topic = ResearchTopic(topic_id=f"instrument-events:{iid}", title="旧研究", instrument_ids=[iid], visibility="team")
    dossier = ResearchTopic(topic_id=f"dossier:{iid}", title="档案", instrument_ids=[iid], visibility="team")
    session.add_all([topic, dossier])
    session.flush()
    old_theme = ResearchEntry(entry_id="old-theme", topic_id=dossier.topic_id, kind="note", title="原有问题", body="原有核心问题",
        status="recorded", created_at=old_time, updated_at=old_time, context_json={"role": "research_theme", "instrument_id": iid,
            "author": "原作者", "origin": "user", "managed_by": "user", "theme_status": "active", "revision_number": 1, "versions": []})
    question = {"key": "funding", "event_key": "funding", "question": "融资用途如何", "assessment": "用途待证",
        "status": "open", "tracking_status": "active", "next_check": "核对披露", "source_ids": [],
        "version_id": "old-run:questions:funding", "updated_at": old_time.isoformat()}
    notebook = {"questions": [question], "forecasts": [{"key": "cash-flow", "claim": "现金流可能改善", "horizon": "下一季", "status": "active"}],
        "catalysts": [{"key": "report", "title": "预定报告", "scheduled_at": "2026-09-30", "status": "scheduled", "relevance": "现金流", "next_check": "报告原文", "source_ids": []}],
        "sources": [], "checked_at": old_time.isoformat(), "updated_at": old_time.isoformat(), "version_id": "old-run"}
    original_context = {"sector_run": True, "instrument_ids": [iid], "cutoff": old_time.isoformat(),
        "reviews": {iid: {"status": "completed", "research": notebook}}}
    old_run = ResearchEntry(entry_id="old-run", topic_id=topic.topic_id, kind="analysis", title="旧研究",
        status="completed", created_at=old_time, completed_at=old_time, context_json=deepcopy(original_context))
    case = RiskCase(case_id="old-event", instrument_id=iid, signal="sector:funding", title="融资事件", body="融资事实",
        status="open", trigger_active=True, created_at=old_time, updated_at=old_time, history_json=[],
        evidence_json={"follow_up": "watch", "next_watch": "核对用途", "direction": "uncertain", "source_ids": [],
            "sources": [], "occurred_at": "2026-08-31", "recorded_at": old_time.isoformat(), "event_version_id": "old-event:1"})
    session.add_all([old_theme, old_run, case])
    session.commit()
    original_context = deepcopy(old_run.context_json)
    migration().migrate_focus_themes(session.connection())
    session.commit()
    session.expire_all()
    assert session.get(ResearchEntry, "old-run").context_json == original_context
    theme = session.get(ResearchEntry, "old-theme")
    assert theme.context_json["revision_number"] == 2
    assert theme.context_json["versions"][0]["managed_by"] == "user"
    assert theme.context_json["versions"][0]["question"] == "原有核心问题"
    assert theme.context_json["last_reviewed_at"] is None and not theme.context_json["pinned"]
    from watchlist_app.services.research_dossier import _notebooks
    current, history = _notebooks(session, iid, True)
    assert current["checked_at"] == old_time.isoformat()
    assert all(item.get("theme_id") for field in ("questions", "forecasts", "catalysts") for item in current[field])
    case = session.get(RiskCase, "old-event")
    assert len(case.history_json) == 2 and case.history_json[0]["snapshot"]["event_version_id"] == "old-event:1"
    assert case.history_json[0]["snapshot"]["recorded_at"] == old_time.isoformat()
    assert case.history_json[1]["snapshot"]["title"] == "融资事件"
    assert case.history_json[1]["snapshot"]["body"] == "融资事实"
    assert case.evidence_json["occurred_at"] == "2026-08-31"
    assert case.evidence_json["theme_ids"] == [current["questions"][0]["theme_id"]]
    assert any(item["version_id"] == "old-run" for item in history)
    from watchlist_app.services.research_activity import research_activity, judgment_changed_at
    updates = research_activity(session, iid)["updates"]
    assert len({item["update_id"] for item in updates}) == len(updates)
    question_updates = [item for item in updates if item["kind"] == "question"]
    assert len(question_updates) == 2
    assert question_updates[0]["change"] == "organized" and question_updates[0]["author_role"] == "system"
    assert question_updates[1]["update_id"] == "research:old-run:questions:funding" and question_updates[1]["superseded"]
    assert judgment_changed_at(question_updates[0]) == old_time.isoformat()
    from watchlist_app.services.research_dossier import read_dossier_version
    assert read_dossier_version(session, iid, "old-run:questions:funding")["value"]["question"] == question["question"]


def test_migration_preserves_originals_versions_and_actual_research_clock(research_client):
    with get_session_factory()() as session:
        assert_migration(session, "fund-us-agg")


@pytest.mark.postgresql_integration
def test_postgres_focus_migration_preserves_originals_and_financial_clocks(postgres_watchlist_env):
    iid = postgres_watchlist_env["instrument_id"]
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund", instrument_name="测试基金", metadata_json={}))
        session.commit()
        assert_migration(session, iid)


def assert_retained_scope_is_not_promoted(session, iid, scope):
    from sqlalchemy import select
    from watchlist_app.services.research_themes import theme_index

    topic = ResearchTopic(topic_id="former-portfolio-conversation", title="历史组合对话", instrument_ids=[iid],
                          portfolio_id=None, visibility="team")
    session.add(topic)
    session.flush()
    stamp = datetime(2026, 9, 1, tzinfo=UTC)
    contexts = {
        "scope-history": {**scope, "original": "保留的私有组合资料\u0000，不得发布为团队主题"},
        "scope-research": {"research_run": True, "instrument_ids": [iid], "cutoff": stamp.isoformat(),
            "reviews": {iid: {"status": "completed", "research": {"questions": [
                {"key": "private-plan", "question": "该组合的私有调仓计划", "tracking_status": "active"}]}}}},
    }
    for identifier, context in contexts.items():
        session.add(ResearchEntry(entry_id=identifier, topic_id=topic.topic_id,
            kind="analysis" if identifier == "scope-research" else "evidence", status="completed",
            title="原始记录", created_at=stamp, updated_at=stamp, completed_at=stamp, context_json=deepcopy(context)))
    session.commit()
    original_records = {row.entry_id: deepcopy(row.context_json) for row in session.scalars(select(ResearchEntry))}
    migration().migrate_focus_themes(session.connection())
    session.commit()
    session.expire_all()
    assert theme_index(session, iid) == []
    assert {row.entry_id: row.context_json for row in session.scalars(select(ResearchEntry))} == original_records


@pytest.mark.parametrize("scope", [{"portfolio_id": "private-portfolio"}, {"risk_scope": {"portfolio_id": "private-portfolio"}}])
def test_migration_does_not_publish_retained_portfolio_history(research_client, scope):
    with get_session_factory()() as session:
        assert_retained_scope_is_not_promoted(session, "fund-us-agg", scope)


@pytest.mark.postgresql_integration
@pytest.mark.parametrize("scope", [{"portfolio_id": "private-portfolio"}, {"risk_scope": {"portfolio_id": "private-portfolio"}}])
def test_postgres_migration_does_not_publish_retained_portfolio_history(postgres_watchlist_env, scope):
    iid = postgres_watchlist_env["instrument_id"]
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund",
                                    instrument_name="历史范围隔离", metadata_json={}))
        session.commit()
        assert_retained_scope_is_not_promoted(session, iid, scope)


@pytest.mark.postgresql_integration
def test_postgres_concurrent_theme_creation_cannot_exceed_ten(postgres_watchlist_env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from studio_identity import current_principal, principal_context
    from watchlist_app.services.research_themes import ThemeInput, save_theme, theme_index
    iid = postgres_watchlist_env["instrument_id"]
    actor = current_principal()
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund", detail_view_type="public_fund", instrument_name="测试基金", metadata_json={}))
        session.flush()
        for index in range(9):
            save_theme(session, iid, ThemeInput(title=f"研究问题{index}"))
        session.commit()
    ready = Barrier(2)
    def add(index):
        with principal_context(actor), get_session_factory()() as session:
            ready.wait(timeout=10)
            try:
                save_theme(session, iid, ThemeInput(title=f"并发研究问题{index}"))
                session.commit()
                return True
            except ValueError as error:
                assert "10" in str(error)
                session.rollback()
                return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(add, range(2))) == [False, True]
    with get_session_factory()() as session:
        assert len(theme_index(session, iid, active_only=True)) == 10


def test_migration_keeps_overflow_themes_as_paused_history(research_client):
    from watchlist_app.services.research_themes import theme_index
    with get_session_factory()() as session:
        iid = "fund-us-agg"
        topic = ResearchTopic(topic_id=f"dossier:{iid}", title="既有档案", instrument_ids=[iid], visibility="team")
        session.add(topic)
        session.flush()
        for index in range(12):
            session.add(ResearchEntry(entry_id=f"legacy-{index}", topic_id=topic.topic_id, kind="note", title=f"既有主题{index}", body="原有问题",
                status="recorded", context_json={"role": "research_theme", "instrument_id": iid,
                    "author": "原作者", "managed_by": "user", "theme_status": "active", "versions": []}))
        session.commit()
        migration().migrate_focus_themes(session.connection())
        session.commit()
        session.expire_all()
        themes = theme_index(session, iid)
        assert len(themes) == 12 and sum(item["status"] == "active" for item in themes) == 10
        paused = [item for item in themes if item["status"] == "paused"]
        assert all("不表示" in item["close_reason"] and item["versions"][0]["status"] == "active" for item in paused)
