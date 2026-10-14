from copy import deepcopy
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from studio_identity import Principal, principal_context

from watchlist_app.db.models.research_guidance import ResearchGuidance
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_guidance as guidance
from watchlist_app.services.research_sections import section_context
from .test_research_themes import research_client


def _payload(value, **changes):
    return {"guidance": value["guidance"], "section_skills": value["section_skills"],
            "expected_revision": value["revision"], **changes}


def test_guidance_read_has_no_write_and_team_cas_preserves_history(research_client, monkeypatch):
    client = research_client
    initial = client.get("/api/research/guidance").json()
    assert initial["revision"] == 0 and set(initial["section_skills"]) == {
        "overview", "recent_updates", "themes", "quantitative", "risk", "review"}
    with get_session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(ResearchGuidance)) == 0
    first = client.put("/api/research/guidance", json=_payload(initial, guidance="通用投资框架"))
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["revision"] == 1 and saved["author"]["user_id"] == "pm-one"
    assert saved["versions"] == [{key: value for key, value in initial.items() if key != "versions"}]
    assert client.put("/api/research/guidance", json=_payload(initial, guidance="旧页覆盖")).status_code == 409
    second = client.put("/api/research/guidance", json=_payload(saved, guidance="新投资框架")).json()
    assert second["versions"][-1]["guidance"] == "通用投资框架"
    assert second["revision"] == 2
    import watchlist_app.main as main
    monkeypatch.setattr(main, "resolve_request", lambda *args, **kwargs: Principal("other", "其他团队", "other-team"))
    other = client.get("/api/research/guidance").json()
    assert other["team_id"] == "other-team" and other["revision"] == 0


@pytest.mark.parametrize("principal", [Principal("reader", "只读成员", "default", team_role="reader"),
    Principal(None, "研究服务", "default", kind="service", service_id="research", scopes=("watchlist:research",))])
def test_only_human_writable_members_can_edit(research_client, monkeypatch, principal):
    import watchlist_app.main as main
    monkeypatch.setattr(main, "resolve_request", lambda *args, **kwargs: principal)
    current = research_client.get("/api/research/guidance")
    assert current.status_code == 200
    assert research_client.put("/api/research/guidance", json=_payload(current.json(), guidance="改写")).status_code == 403
    with principal_context(principal), get_session_factory()() as session:
        with pytest.raises(HTTPException) as raised:
            guidance.save_guidance(session, guidance.GuidanceInput(**_payload(current.json(), guidance="绕API")))
        assert raised.value.status_code == 403


def test_first_writer_race_uses_unique_team_key_without_overwriting(research_client, monkeypatch):
    principal = Principal("writer", "成员", "race-team")
    with principal_context(principal), get_session_factory()() as session:
        initial = guidance.read_guidance(session)
        winner = guidance.save_guidance(session, guidance.GuidanceInput(**_payload(initial, guidance="先提交")))
    # Both writers saw revision 0 before either insert. Reproduce the loser's
    # absent-row SELECT while exercising the actual database unique constraint.
    with principal_context(principal), get_session_factory()() as session:
        monkeypatch.setattr(session, "scalar", lambda query: None)
        with pytest.raises(HTTPException) as raised:
            guidance.save_guidance(session, guidance.GuidanceInput(**_payload(initial, guidance="后提交")))
        assert raised.value.status_code == 409
        assert guidance.read_guidance(session) == winner


def test_frozen_role_guidance_reaches_real_section_and_child_routes(research_client, monkeypatch):
    client = research_client
    initial = client.get("/api/research/guidance").json()
    skills = {key: f"ROLE:{key}" for key in initial["section_skills"]}
    saved = client.put("/api/research/guidance", json=_payload(initial, guidance="团队重点", section_skills=skills)).json()
    principal = Principal("pm-one", "投资经理甲", "default")
    with principal_context(principal), get_session_factory()() as session:
        frozen = guidance.freeze_guidance(session, {"research_actor": {"team_id": "default"}})
        context = {"research_actor": {"team_id": "default", "user_id": "pm-one"},
            "sector_run": True, "instrument_ids": ["fund-us-agg"], "research_guidance": frozen,
            "cutoff": "2026-10-14T00:00:00+00:00", "input_snapshot_cutoff": "2026-10-14T00:00:00+00:00",
            "execution": {"attempt": 1}, "research_dossiers": [{"instrument_id": "fund-us-agg",
                "mandate": {"revision": 7, "version_id": "mandate:v7", "guidance": "标的范围",
                    "section_skills": {"themes": "订单兑现"}}}],
            "research_tasks": {"task": {"task_id": "task", "role": "themes", "instrument_id": "fund-us-agg",
                "input_snapshot_cutoff": "2026-10-14T00:00:00+00:00", "attempt": 1, "status": "running", "theme_ids": []}}}
        session.add(ResearchTopic(topic_id="guidance-test", team_id="default", visibility="team", title="指导冻结", instrument_ids=["fund-us-agg"]))
        session.add(ResearchEntry(entry_id="guidance-run", topic_id="guidance-test", team_id="default",
            kind="analysis", status="running", title="指导冻结", context_json=context))
        session.commit()
    client.put("/api/research/guidance", json=_payload(saved, guidance="后来的变更", section_skills={key: "NEW" for key in skills}))
    import watchlist_app.main as main
    monkeypatch.setattr(main, "resolve_request", lambda *args, **kwargs: Principal("pm-one", "投资经理甲", "default",
        resource_scope={"kind": "run", "id": "guidance-run", "attempt": "1"}))
    for path, body in [("/section-context", {"instrument_id": "fund-us-agg", "section": "themes"}),
                       ("/tasks/task/context", None)]:
        url = "/api/research/runs/guidance-run" + path
        response = client.post(url, json=body) if body else client.get(url)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["skill"] == "ROLE:themes" and result["team_guidance"]["guidance"] == "团队重点"
        assert result["instrument_supplement"] == {"revision": 7, "version_id": "mandate:v7", "skill": "订单兑现"}
        assert "ROLE:risk" not in str(result) and "ROLE:review" not in str(result) and "NEW" not in str(result)
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "guidance-run").context_json == context


def test_risk_and_review_receive_only_their_business_role(monkeypatch, tmp_path):
    from watchlist_app.services.research_tool_projection import _risk_scope_index, read_research_context
    from watchlist_app.services.sector_fact_review import _evidence_packet
    from watchlist_app import review_mcp
    frozen = {"team_id": "default", "revision": 3, "guidance": "通用方法",
        "section_skills": {key: f"ONLY:{key}" for key in guidance.TeamSectionSkills.model_fields},
        "author": {"origin": "user"}, "updated_at": "2026-10-14T00:00:00+00:00"}
    context = {"run_id": "frozen", "cutoff": "2026-10-14T00:00:00+00:00", "research_guidance": frozen,
        "risk_scope": {"instrument_id": "x"}, "risk_inputs": {"instrument_ids": [], "instruments": [], "research": [], "quantitative": [], "coverage": []}}
    risk = _risk_scope_index(context)
    assert risk["business_guidance"]["skill"] == "ONLY:risk"
    assert "ONLY:review" not in str(risk) and "ONLY:overview" not in str(risk)
    packet = _evidence_packet(context, [], "frozen")
    assert packet["business_guidance"]["skill"] == "ONLY:review"
    assert "ONLY:risk" not in str(packet) and "ONLY:overview" not in str(packet)
    input_file, reads_file = tmp_path / "packet.json", tmp_path / "reads.jsonl"
    input_file.write_text(json.dumps({"packet": packet, "response_schema": {}}))
    monkeypatch.setenv("INVESTMENT_STUDIO_REVIEW_PACKET", str(input_file))
    monkeypatch.setenv("INVESTMENT_STUDIO_REVIEW_READS", str(reads_file))
    result = review_mcp.read_review_context(section="business_guidance")
    assert result["data"]["skill"] == "ONLY:review"
    # An already-frozen old run does not acquire today's team settings.
    assert guidance.role_guidance({}, "risk") is None
    private = read_research_context(lambda suffix: context, section="business_guidance")
    assert private["data"]["skill"] == "ONLY:overview"


def test_migration_promotes_exact_defaults_only_and_preserves_frozen_runs(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import sqlalchemy as sa
    path = Path(__file__).parents[1] / "alembic/versions/20261014_0072_team_research_guidance.py"
    spec = importlib.util.spec_from_file_location("guidance_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = sa.MetaData()
    table = sa.Table("research_entry", metadata, sa.Column("entry_id", sa.String, primary_key=True),
        sa.Column("kind", sa.String), sa.Column("context_json", sa.JSON), sa.Column("read_context_json", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True)))
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    old = {"role": "research_mandate", "revision": 2, "mandate": {"guidance": "人工要求",
        "section_skills": {**migration.LEGACY_SECTION_SKILLS, "themes": "人工主题范围", "quantitative": ""}},
        "author": {"origin": "user"}, "versions": [{"version_id": "old:v1"}]}
    frozen = {"research_dossiers": [{"mandate": deepcopy(old["mandate"])}], "submitted_draft": {"unchanged": True}}
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(table.insert(), [dict(entry_id="dossier-mandate:x", kind="note", context_json=old,
            read_context_json={"role": "research_mandate", "author": old["author"]}, created_at=now, updated_at=now),
            dict(entry_id="run", kind="analysis", context_json=frozen, read_context_json={}, created_at=now, updated_at=now)])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        rows = {r.entry_id: r.context_json for r in connection.execute(sa.select(table))}
        assert rows["run"] == frozen
        current = rows["dossier-mandate:x"]
        assert current["mandate"]["section_skills"] == {"overview": "", "recent_updates": "", "themes": "人工主题范围", "quantitative": ""}
        assert current["mandate"]["guidance"] == "人工要求" and current["revision"] == 3
        assert current["versions"][:-1] == old["versions"]
        assert current["versions"][-1]["section_skills"] == old["mandate"]["section_skills"]
        assert current["author"]["origin"] == "migration"
        assert migration.promote_default_skills(current, "dossier-mandate:x", now, now, now) is None
