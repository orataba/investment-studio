#!/usr/bin/env python3
"""Inspect or reset generated research; financial and human-authored facts are preserved.

Run only during write maintenance. --apply requires a verified project-schema
backup; this command does not manage services or change the host model switch.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


PORTFOLIO_FACTS = (
    "portfolio_record", "account_record", "transaction_record", "transaction_change_log",
    "transaction_id_allocator", "transaction_idempotency_record", "derivative_contract_record",
    "option_delivery_link", "transaction_capture_record", "transaction_capture_batch",
    "transaction_capture_batch_item", "transaction_capture_analysis_revision",
    "portfolio_instrument_event_task", "portfolio_instrument_event_task_link", "portfolio_instrument_event_task_review",
    "portfolio_instrument_universe_record", "taxonomy_record", "taxonomy_node_record",
    "taxonomy_assignment_record", "taxonomy_configuration_revision", "portfolio_taxonomy_state",
    "target_set_record", "target_set_line_record", "concentration_policy_revision", "research_settings_record",
    "portfolio_investment_view", "portfolio_investment_view_revision", "portfolio_investment_stance",
    "portfolio_access_state", "portfolio_membership", "portfolio_access_audit",
)
WATCHLIST_FACTS = (
    "instrument_detail", "instrument_manual_profile", "instrument_research_profile",
    "instrument_research_profile_revision", "instrument_research_note", "instrument_research_note_revision",
    "instrument_investment_stance", "instrument_attribute_definition", "instrument_attribute_value",
    "instrument_taxonomy_node", "instrument_taxonomy_assignment", "instrument_taxonomy_assignment_history",
    "watchlist", "watchlist_item", "risk_review_rule",
)
MUTABLE_TABLES = (
    "watchlist.research_entry", "watchlist.research_topic", "watchlist.research_execution",
    "watchlist.research_run_evidence", "watchlist.research_risk_read_state",
    "watchlist.research_monitor_state", "watchlist.risk_case", "portfolio.research_run_record",
    "portfolio.portfolio_workspace_read_model",
)


def relation(name):
    return sql.Identifier(*name.split("."))


def fact_tables(connection):
    # Account/session/authorization data is never reset. Include every current
    # identity relation rather than maintaining a partial authorization inventory.
    identity = [f"identity.{row[0]}" for row in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='identity' ORDER BY tablename")]
    return [*identity, *(f"portfolio.{name}" for name in PORTFOLIO_FACTS),
            *(f"watchlist.{name}" for name in WATCHLIST_FACTS)]


def fingerprint(connection, tables):
    result = {}
    for name in tables:
        digest, count = hashlib.sha256(), 0
        with connection.cursor(name="research_reset_facts") as cursor:
            cursor.execute(sql.SQL("SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text").format(relation(name)))
            for (row,) in cursor:
                encoded = row.encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
                count += 1
        result[name] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def referenced_ids(value, keys):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str):
                found.add(item)
            found.update(referenced_ids(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.update(referenced_ids(item, keys))
    return found


def human_theme(context):
    return (context.get("origin") != "researcher" or bool(context.get("pinned"))
            or context.get("updated_by_role") == "user"
            or any(item.get("updated_by_role") == "user" for item in context.get("versions", [])))


def reset_theme(context):
    """Keep manual questions and history, remove the current model assessment."""
    if context.get("updated_by_role") != "researcher":
        return context
    clean = deepcopy(context)
    for key in ("synthesis", "latest_development"):
        clean[key] = ""
    for key in ("source_ids", "figure_source_ids", "sources"):
        clean[key] = []
    for key in ("source_run_id", "source_quote", "publication", "last_reviewed_at"):
        clean.pop(key, None)
    clean["versions"] = [item for item in clean.get("versions", []) if item.get("updated_by_role") == "user"]
    clean["baseline_status"] = "pending"
    return clean


def required_analysis_ids(value, analysis_ids):
    """Keep resolvable originals, not provenance labels on self-contained sources."""
    found = set()
    if isinstance(value, dict):
        locator = value.get("retained_reference")
        if isinstance(locator, dict) and "data" not in value:
            found.add(locator.get("run_id"))
        # Earlier PM records named their source run without freezing sources.
        if (value.get("source_ids") and value.get("sources") is None
                and "data" not in value and "snapshot" not in value):
            found.add(value.get("source_run_id"))
        for key in ("notebook_version_id", "investment_view_version_id", "theme_version_id",
                    "research_update_id", "source_notebook_version_id", "source_update_id"):
            identifier = value.get(key)
            if isinstance(identifier, str):
                found.update([identifier, *identifier.split(":")])
        for item in value.values():
            found.update(required_analysis_ids(item, analysis_ids))
    elif isinstance(value, list):
        for item in value:
            found.update(required_analysis_ids(item, analysis_ids))
    return found & analysis_ids


def build_plan(entries, cases, notes, runs):
    protected_themes = set()
    protected_cases = set()
    manual_contexts = [*notes, *(entry["context"] for entry in entries if entry["kind"] == "evidence"
                              or entry["kind"] == "note" and human_theme(entry["context"]))]
    for context in manual_contexts:
        protected_themes.update(referenced_ids(context, {"theme_id"}))
        protected_cases.update(referenced_ids(context, {"event_case_id", "case_id"}))
    analysis, themes, updates = [], [], {}
    for entry in entries:
        context = entry["context"]
        if entry["kind"] == "analysis":
            analysis.append(entry["id"])
        elif entry["kind"] == "note" and context.get("role") == "research_theme":
            if not human_theme(context) and entry["id"] not in protected_themes:
                themes.append(entry["id"])
            else:
                clean = reset_theme(context)
                if clean != context:
                    updates[entry["id"]] = clean
    generated_cases = [case["id"] for case in cases if case["signal"].startswith("sector:")
                       and case["id"] not in protected_cases
                       and not case["evidence"].get("follow_up_pinned")
                       and not any(referenced_ids(item, {"recorded_by_role", "updated_by_role"}) & {"user"}
                                   for item in [case["evidence"], *case["history"]])]
    preserved = [*notes, *(updates.get(entry["id"], entry["context"]) for entry in entries
                           if entry["kind"] != "analysis" and entry["id"] not in themes),
                 *(value for case in cases if case["id"] not in generated_cases
                   for value in (case["evidence"], case["history"]))]
    required = required_analysis_ids(preserved, set(analysis))
    return {"analysis_ids": [run_id for run_id in analysis if run_id not in required],
            "retained_analysis_ids": sorted(required), "theme_ids": themes, "theme_updates": updates,
            "case_ids": generated_cases, "portfolio_runs": runs}


def freeze_case_sources(connection, value, seen=frozenset()):
    """Make preserved event sources independent of deleted analysis storage."""
    if isinstance(value, list):
        return [freeze_case_sources(connection, item, seen) for item in value]
    if not isinstance(value, dict):
        return value
    locator = value.get("retained_reference")
    if isinstance(locator, dict) and "data" not in value:
        path = tuple(locator["path"])
        run_id, source_id = locator["run_id"], value["source_id"]
        identity = (run_id, path, locator.get("version_id"), source_id)
        if identity in seen:
            raise ValueError("Preserved source contains a circular original reference")
        source = None
        if path == ("computed_metrics", "*"):
            row = connection.execute("SELECT source_json FROM watchlist.research_run_evidence WHERE run_id=%s AND source_id=%s",
                                     (run_id, source_id)).fetchone()
            if row:
                source = row[0]
            else:
                row = connection.execute("SELECT context_json->'computed_metrics' FROM watchlist.research_entry WHERE entry_id=%s", (run_id,)).fetchone()
                source = next((item for item in (row[0] or []) if item.get("source_id") == source_id), None) if row else None
        elif len(path) == 5 and path[0] == "reviews" and path[2:] == ("research", "sources", "*"):
            row = connection.execute("SELECT context_json #> %s FROM watchlist.research_entry WHERE entry_id=%s", (list(path[:-1]), run_id)).fetchone()
            source = next((item for item in (row[0] or []) if item.get("source_id") == source_id), None) if row else None
        elif path == ("referenced_research_versions", "*", "sources", "*") and locator.get("version_id"):
            row = connection.execute("SELECT context_json->'referenced_research_versions' FROM watchlist.research_entry WHERE entry_id=%s", (run_id,)).fetchone()
            version = next((item for item in (row[0] or []) if item.get("version_id") == locator["version_id"]), {}) if row else {}
            source = next((item for item in version.get("sources", []) if item.get("source_id") == source_id), None)
        if (not source or source.get("source_type") != "computed_metric"
                or source.get("source_id") != source_id or source.get("instrument_id") != value.get("instrument_id")):
            raise ValueError("Preserved event source original is unavailable or has mismatched identity")
        value = freeze_case_sources(connection, source, seen | {identity})
    # A source with its complete original no longer needs a live run locator.
    return {key: freeze_case_sources(connection, item, seen) for key, item in value.items()
            if key != "retained_reference" or "data" not in value}


def empty_managed_topics(topics, entries, removed_ids):
    occupied = {entry.get("topic_id") for entry in entries if entry["id"] not in removed_ids}
    return sorted(topic_id for topic_id in topics if topic_id not in occupied
                  and (topic_id.startswith(("dossier:", "risk-officer:", "instrument-events:"))
                       or topic_id == "us-sector-daily-review"))


def load_plan(connection):
    entries = [{"id": row[0], "kind": row[1], "context": row[2] or {}, "topic_id": row[3]} for row in connection.execute(
        "SELECT entry_id,kind,CASE WHEN kind='analysis' THEN '{}'::json ELSE context_json END,topic_id FROM watchlist.research_entry")]
    cases = [{"id": row[0], "signal": row[1], "evidence": row[2] or {}, "history": row[3] or []}
             for row in connection.execute("SELECT case_id,signal,evidence_json,history_json FROM watchlist.risk_case")]
    notes = [row[0] or {} for row in connection.execute("""SELECT research_context FROM watchlist.instrument_research_note_revision
        UNION ALL SELECT research_context FROM watchlist.instrument_research_note""")]
    runs = [list(row) for row in connection.execute("SELECT portfolio_id,research_run_id FROM portfolio.research_run_record")]
    plan = build_plan(entries, cases, notes, runs)
    case_updates = {}
    for case in cases:
        if case["id"] not in plan["case_ids"]:
            original = (case["evidence"], case["history"])
            case["evidence"] = freeze_case_sources(connection, case["evidence"])
            case["history"] = freeze_case_sources(connection, case["history"])
            if (case["evidence"], case["history"]) != original:
                case_updates[case["id"]] = {"evidence": case["evidence"], "history": case["history"]}
    plan = build_plan(entries, cases, notes, runs)
    plan["case_updates"] = case_updates
    all_analysis = set(plan["analysis_ids"]) | set(plan["retained_analysis_ids"])
    required, checked = set(plan["retained_analysis_ids"]), set()
    # Only referenced originals can bring further hard dependencies. Never load
    # every old analysis JSON merely to delete it (retained contexts can be huge).
    while required - checked:
        for run_id in sorted(required - checked):
            context = connection.execute("SELECT context_json FROM watchlist.research_entry WHERE entry_id=%s", (run_id,)).fetchone()[0]
            required.update(required_analysis_ids(context, all_analysis))
            checked.add(run_id)
    plan["analysis_ids"] = [run_id for run_id in plan["analysis_ids"] if run_id not in required]
    plan["retained_analysis_ids"] = sorted(required)
    topics = [row[0] for row in connection.execute("SELECT topic_id FROM watchlist.research_topic")]
    plan["empty_topic_ids"] = empty_managed_topics(topics, entries, set(plan["analysis_ids"] + plan["theme_ids"]))
    return plan


def apply_plan(connection, plan):
    from watchlist_app.db.research_access_context import derive_access_context
    from watchlist_app.db.research_read_context import derive_read_context

    # No cascade/truncate: explicit current-schema targets keep unrelated facts
    # outside this operation even if a future FK points at research.
    connection.execute("DELETE FROM watchlist.research_monitor_state")
    delete_ids = plan["analysis_ids"] + plan["theme_ids"]
    for table in ("research_execution", "research_run_evidence", "research_risk_read_state"):
        connection.execute(sql.SQL("DELETE FROM watchlist.{} WHERE run_id=ANY(%s)").format(sql.Identifier(table)), (delete_ids,))
    connection.execute("DELETE FROM watchlist.research_entry WHERE entry_id=ANY(%s)", (delete_ids,))
    connection.execute("""DELETE FROM watchlist.research_topic t WHERE topic_id=ANY(%s)
        AND NOT EXISTS (SELECT 1 FROM watchlist.research_entry e WHERE e.topic_id=t.topic_id)""", (plan.get("empty_topic_ids", []),))
    for entry_id, context in plan["theme_updates"].items():
        connection.execute("""UPDATE watchlist.research_entry
            SET context_json=%s,read_context_json=%s,access_context_json=%s WHERE entry_id=%s""",
            (Jsonb(context), Jsonb(derive_read_context(context)), Jsonb(derive_access_context(context)), entry_id))
    connection.execute("DELETE FROM watchlist.risk_case WHERE case_id=ANY(%s)", (plan["case_ids"],))
    for case_id, values in plan.get("case_updates", {}).items():
        connection.execute("UPDATE watchlist.risk_case SET evidence_json=%s,history_json=%s WHERE case_id=%s",
                           (Jsonb(values["evidence"]), Jsonb(values["history"]), case_id))
    connection.execute("DELETE FROM portfolio.research_run_record")
    connection.execute("DELETE FROM portfolio.portfolio_workspace_read_model WHERE surface='research_analysis'")


def verify_backup(path):
    path = path.resolve(strict=True)
    expected = Path(str(path) + ".sha256").read_text().split()[0]
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("Backup checksum does not match")
    # The existing backup primitive fully decodes the archive before publishing
    # its checksum. Also verify this is a PostgreSQL archive, not an arbitrary file.
    with path.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("Expected a verified PostgreSQL custom-format backup")
    return str(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("INVESTMENT_STUDIO_LOCAL_DATABASE_URL"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path, help="Verified project-schema .pgdump from the same maintenance window")
    parser.add_argument("--fingerprint-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or INVESTMENT_STUDIO_LOCAL_DATABASE_URL is required")
    if args.apply and (not args.backup or args.fingerprint_only):
        parser.error("--apply requires --backup and cannot use --fingerprint-only")
    backup = verify_backup(args.backup) if args.apply else None
    dsn = args.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as connection:
        if not args.apply:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        connection.execute("SET LOCAL lock_timeout='5s'")
        if not args.apply:
            connection.execute("SET TRANSACTION READ ONLY")
        tables = fact_tables(connection)
        if args.apply:
            connection.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                sql.SQL(",").join(relation(name) for name in [*tables, *MUTABLE_TABLES])))
            running = connection.execute("""SELECT count(*) FROM watchlist.research_execution WHERE status='running'
                UNION ALL SELECT count(*) FROM portfolio.research_run_record WHERE status='running'""").fetchall()
            if any(row[0] for row in running):
                raise RuntimeError("Running research remains; stop writers and reconcile interrupted work before reset")
        before = fingerprint(connection, tables)
        if args.fingerprint_only:
            print(json.dumps({"core_facts": before}, sort_keys=True))
            return
        plan = load_plan(connection)
        result = {"applied": args.apply, "backup": backup, "core_facts": before,
                  "delete": {"analysis": len(plan["analysis_ids"]), "generated_themes": len(plan["theme_ids"]),
                             "generated_events": len(plan["case_ids"]), "portfolio_runs": len(plan["portfolio_runs"]),
                             "empty_managed_topics": len(plan["empty_topic_ids"])},
                  "retained_themes_reset": len(plan["theme_updates"]),
                  "retained_analysis_dependencies": plan["retained_analysis_ids"],
                  "retained_cases_sources_frozen": len(plan["case_updates"]),
                  "portfolio_artifact_directories": plan["portfolio_runs"]}
        if args.apply:
            apply_plan(connection, plan)
            if fingerprint(connection, tables) != before:
                raise RuntimeError("Protected facts changed; rolling back research reset")
            result["core_facts_unchanged"] = True
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, psycopg.Error, OSError) as error:
        # Do not expose connection strings, SQL parameters, or raw records.
        print(f"Research reset failed ({type(error).__name__}); no successful reset reported.", file=sys.stderr)
        raise SystemExit(1)
