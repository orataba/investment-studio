import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("reset_generated_research", Path(__file__).parents[1] / "scripts/reset_generated_research.py")
reset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reset)


def theme(entry_id, **context):
    return {"id": entry_id, "kind": "note", "context": {"role": "research_theme", "origin": "researcher", **context}}


def test_reset_scope_preserves_manual_materials_and_human_adopted_themes():
    entries = [
        {"id": "run", "kind": "analysis", "context": {}},
        {"id": "material", "kind": "evidence", "context": {}},
        {"id": "mandate", "kind": "note", "context": {"role": "mandate"}},
        theme("generated"), theme("pinned", pinned=True), theme("referenced"),
        theme("human_edited", versions=[{"updated_by_role": "user"}]),
        theme("human_created", origin="user"),
    ]
    plan = reset.build_plan(entries, [], [{"theme_id": "referenced"}], [])
    assert plan["analysis_ids"] == ["run"]
    assert plan["theme_ids"] == ["generated"]
    assert plan["theme_updates"] == {}


def test_reset_generated_assessment_keeps_question_manual_reference_and_is_idempotent():
    context = {"role": "research_theme", "origin": "user", "updated_by_role": "researcher",
               "question": "Will margins recover?", "background": "PM context", "pinned": True,
               "reference": {"event_case_id": "retained-event"}, "synthesis": "Old AI analysis",
               "latest_development": "Old AI development", "source_ids": ["generated"],
               "sources": [{"old": "calculation"}], "publication": {"run_id": "old"},
               "versions": [{"updated_by_role": "researcher", "synthesis": "older AI"},
                            {"updated_by_role": "user", "question": "Original PM question"}]}
    result = reset.reset_theme(context)
    assert result["question"] == context["question"] and result["background"] == context["background"]
    assert result["reference"] == context["reference"] and result["pinned"] is True
    assert result["synthesis"] == result["latest_development"] == ""
    assert result["sources"] == [] and result["baseline_status"] == "pending"
    assert "publication" not in result
    assert result["versions"] == [context["versions"][1]]
    assert reset.reset_theme(result) == result
    assert context["synthesis"] == "Old AI analysis"


def test_manual_current_assessment_is_not_erased():
    context = {"updated_by_role": "user", "synthesis": "PM judgment", "sources": ["source"]}
    assert reset.reset_theme(context) == context


def test_generated_events_preserve_manual_risk_actions_and_references():
    def case(case_id, signal="sector:event", evidence=None, history=None):
        return {"id": case_id, "signal": signal, "evidence": evidence or {}, "history": history or []}
    cases = [case("generated"), case("manual", "manual"), case("threshold", "drawdown_limit"),
             case("pinned", evidence={"follow_up_pinned": True}), case("referenced"),
             case("human_status", history=[{"action": "handled", "detail": "PM follow-up"}]),
             case("auto_resolved", history=[{"action": "resolved", "snapshot": {}}]),
             case("edited", history=[{"snapshot": {"recorded_by_role": "user"}}])]
    plan = reset.build_plan([], cases, [{"reference": {"event_case_id": "referenced"}}], [])
    assert plan["case_ids"] == ["generated", "auto_resolved"]
    assert plan["retired_case_ids"] == ["referenced"]


def test_referenced_old_generated_event_is_history_without_a_market_risk_resolution():
    evidence = {"source_ids": ["original"], "sources": [{"data": "Original\\u0000text"}],
        "published_at": "2026-09-01", "risk_assessment": {"status": "pending"},
        "last_assessed_snapshot": {"status": "active"}, "pending_revision": {"status": "pending"},
        "follow_up": "watch", "follow_up_until": "2026-10-20"}
    case = {"evidence": evidence, "history": [{"action": "new", "snapshot": evidence}]}
    result = reset.withdraw_referenced_case(case, "2026-10-13T00:00:00+00:00")
    assert result["evidence"]["withdrawn"] is True
    assert result["evidence"]["risk_assessment"] == {}
    assert result["evidence"]["last_assessed_snapshot"] is result["evidence"]["pending_revision"] is None
    assert result["evidence"]["follow_up"] == "none"
    assert result["evidence"]["sources"] == evidence["sources"]
    assert result["evidence"]["published_at"] == evidence["published_at"]
    assert result["history"][:-1] == case["history"]
    assert result["history"][-1]["before"]["risk_assessment"] == evidence["risk_assessment"]
    assert evidence["follow_up"] == "watch"
    # A prior withdrawal is not a reset boundary if an obsolete decision or
    # follow-up survives. Only the complete current state is idempotent.
    assert not reset.retirement_complete({**case, "evidence": {**evidence, "withdrawn": True}})
    completed = {**case, "evidence": result["evidence"], "trigger_active": False, "status": "handled", "follow_up_date": None}
    assert reset.retirement_complete(completed)
    assert not reset.retirement_complete({**completed, "trigger_active": True})


def test_only_live_manual_reference_dependencies_preserve_analyses():
    names = {"unreferenced", "original", "version", "legacy", "provenance", "generated-only"}
    entries = [{"id": name, "kind": "analysis", "context": {}} for name in sorted(names)]
    entries += [theme("adopted", origin="user", updated_by_role="user", sources=[
        {"source_id": "calculation", "retained_reference": {"run_id": "original", "path": ["computed_metrics", "*"]}},
        {"source_id": "snapshot", "source_run_id": "provenance", "data": {"value": 2}}]),
        theme("generated", sources=[{"retained_reference": {"run_id": "generated-only"}}])]
    notes = [{"notebook_version_id": "version:investment-view"}, {"source_ids": ["source"], "source_run_id": "legacy"}]
    plan = reset.build_plan(entries, [], notes, [])
    assert set(plan["retained_analysis_ids"]) == {"original", "version", "legacy"}
    assert set(plan["analysis_ids"]) == {"unreferenced", "provenance", "generated-only"}
    assert plan["theme_ids"] == ["generated"]
    assert reset.required_analysis_ids({"source_run_id": "provenance", "sources": [], "source_ids": []}, names) == set()


def test_reset_sql_write_scope_excludes_every_raw_market_and_financial_table():
    from psycopg import sql
    class Recorder:
        def __init__(self):
            self.statements = []
        def execute(self, statement, _parameters=None):
            self.statements.append(statement.as_string() if isinstance(statement, sql.Composable) else statement)
    connection = Recorder()
    plan = reset.build_plan([{"id": "old", "kind": "analysis", "context": {}},
        theme("retained", origin="user", updated_by_role="researcher", synthesis="Old")], [], [], [])
    reset.apply_plan(connection, plan)
    import re
    targets = {re.search(r'(?:DELETE FROM|UPDATE)\s+([\w.\"]+)', statement).group(1).replace('"', '')
               for statement in connection.statements}
    assert targets == {"watchlist.research_monitor_state", "watchlist.research_execution", "watchlist.research_run_evidence",
        "watchlist.research_risk_read_state", "watchlist.research_entry", "watchlist.research_topic", "watchlist.risk_case",
        "portfolio.research_run_record", "portfolio.portfolio_workspace_read_model"}
    assert all('TRUNCATE' not in statement and 'CASCADE' not in statement for statement in connection.statements)


def test_pinned_event_freezes_exact_source_without_copying_the_run_or_changing_manual_fields():
    original = {"source_id": "calculation", "source_type": "computed_metric", "instrument_id": "asset",
        "source_run_id": "old", "as_of": "2026-10-01", "data": {"weights": [0.1, 0.9], "explanation": "原始文本"}}
    class StoredOriginal:
        def execute(self, statement, parameters):
            assert statement.startswith("SELECT source_json FROM watchlist.research_run_evidence")
            assert parameters == ("old", "calculation")
            return self
        def fetchone(self):
            return (original,)
    reference = {"source_id": "calculation", "instrument_id": "asset",
        "retained_reference": {"run_id": "old", "path": ["computed_metrics", "*"]}}
    event = {"recorded_by_role": "user", "follow_up_pinned": True, "assessment": "PM opinion",
        "sources": [reference], "history": [{"snapshot": {"sources": [reference]}}]}
    frozen = reset.freeze_case_sources(StoredOriginal(), event)
    assert {key: frozen[key] for key in ("recorded_by_role", "follow_up_pinned", "assessment")} == {
        key: event[key] for key in ("recorded_by_role", "follow_up_pinned", "assessment")}
    assert frozen["sources"] == frozen["history"][0]["snapshot"]["sources"] == [original]
    assert "data" not in event["sources"][0]
    assert reset.required_analysis_ids(frozen, {"old"}) == set()
    assert reset.freeze_case_sources(StoredOriginal(), frozen) == frozen


def test_only_empty_managed_topics_are_removed():
    topics = ['custom-empty', 'instrument-events:old', 'risk-officer:retained', 'dossier:material', 'us-sector-daily-review']
    entries = [{'id': 'old-run', 'topic_id': 'instrument-events:old'},
               {'id': 'human-note', 'topic_id': 'risk-officer:retained'},
               {'id': 'material', 'topic_id': 'dossier:material'}]
    assert reset.empty_managed_topics(topics, entries, {'old-run'}) == ['instrument-events:old', 'us-sector-daily-review']


def test_backup_must_match_its_checksum_and_be_postgresql(tmp_path):
    backup = tmp_path / "backup.pgdump"
    backup.write_bytes(b"PGDMPfixture")
    checksum = Path(str(backup) + ".sha256")
    checksum.write_text(hashlib.sha256(backup.read_bytes()).hexdigest() + "  backup.pgdump\n")
    assert reset.verify_backup(backup) == str(backup)
    backup.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        reset.verify_backup(backup)
    checksum.write_text(hashlib.sha256(backup.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="PostgreSQL"):
        reset.verify_backup(backup)


def test_apply_requires_backup_before_connecting(monkeypatch):
    monkeypatch.setattr(reset.psycopg, "connect", lambda *a, **kw: pytest.fail("must not connect"))
    with pytest.raises(SystemExit):
        reset.main(["--database-url", "postgresql://unused/test", "--apply"])
