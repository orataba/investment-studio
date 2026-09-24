#!/usr/bin/env python3
"""Build/update the current team's Proposed/Invested research through the normal publisher.

The default is a read-only scope preview. Load the target Watchlist/market/identity
environment before running. For a controlled batch, stop the automatic research
worker using research_worker_enabled; the API and its restricted tools stay up.
No report, PM opinion, source clock or run status is written directly by this CLI.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from threading import Lock
import time

from sqlalchemy import select
from sqlalchemy.engine import make_url
from studio_identity import principal_context, service_principal
from watchlist_app.core.settings import get_settings
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research
from watchlist_app.services.research_runner import harness_available, queue_retry, run_analysis
from watchlist_app.services.research_access import instrument_run_scope
from watchlist_app.services.research_run_context import load_run_fields


def instant():
    return datetime.now(UTC).isoformat()


def target_identity():
    url = make_url(get_settings().database_url)
    return {"host": url.host, "port": url.port, "database": url.database, "schema": "watchlist"}


def run_state(run_id, instrument_id):
    """Read only status and published review, never the accumulated tool context."""
    with get_session_factory()() as session:
        row = load_run_fields(session, run_id, ["instrument_ids", "reviews", "runtime_error"])
        if row.context_json.get("instrument_ids") != [instrument_id]:
            raise ValueError("Saved run does not belong to the selected instrument")
        completed_at = session.scalar(select(ResearchEntry.completed_at).where(ResearchEntry.entry_id == run_id))
        state = {"status": row.status, "completed_at": completed_at.isoformat() if completed_at else None,
                 "reviews": row.context_json.get("reviews") or {}, "error": row.context_json.get("runtime_error")}
        if row.status == "completed":
            from watchlist_app.services.research_dossier import read_dossier
            state["current_notebook"] = read_dossier(session, instrument_id).get("notebook")
        return state


def report_ready(notebook):
    """A readable conditional judgment is valid even with honest data limitations."""
    notebook = notebook or {}
    brief = notebook.get("decision_brief") or {}
    return bool(notebook.get("investment_view") and brief.get("recommendation")
        and not brief.get("needs_review") and any(
            (module.get("analysis") or module.get("summary") or "").strip()
            for module in notebook.get("modules") or []))


def result_record(instrument_id, run_id, state):
    review = state["reviews"].get(instrument_id) or {}
    notebook = (state["current_notebook"] if "current_notebook" in state else review.get("research")) or {}
    return {"instrument_id": instrument_id, "run_id": run_id, "status": state["status"],
            "published": state["status"] == "completed" and review.get("status") in {"completed", "limited"},
            "report_ready": report_ready(notebook),
            "coverage_status": review.get("status"), "completed_at": state["completed_at"],
            "change_kind": review.get("change_kind"), "module_count": len(notebook.get("modules") or []),
            "has_investment_view": bool(notebook.get("investment_view")),
            "has_decision_brief": bool(notebook.get("decision_brief")),
            "error": state["error"]}


def refresh_one(instrument_id, resume_run_id=None, on_dispatch=None):
    with principal_context(service_principal("watchlist")):
        with get_session_factory()() as session:
            # Recheck management status immediately before dispatch; list membership
            # or an old manifest never authorizes research after a status change.
            if instrument_id not in sector_research.active_research_ids(session):
                return {"instrument_id": instrument_id, "status": "out_of_scope", "published": False}
            run, created = None, False
            if resume_run_id:
                # Match begin_run's lock order and respect an existing owner.
                session.scalar(select(InstrumentDetail).where(
                    InstrumentDetail.instrument_id == instrument_id).with_for_update())
                active = next((entry for entry in session.scalars(select(ResearchEntry).where(
                    ResearchEntry.kind == "analysis", ResearchEntry.status.in_(["queued", "running"]),
                    instrument_run_scope(session, [instrument_id])))
                    if entry.context_json.get("sector_run")), None)
                if active is not None:
                    run = active
                else:
                    previous = session.get(ResearchEntry, resume_run_id)
                    if previous and previous.context_json.get("instrument_ids") == [instrument_id]:
                        if queue_retry(session, previous, manual=True):
                            run, created = previous, True
                            session.commit()
            if run is None:
                run, created = sector_research.begin_run(session, [instrument_id], scheduled=False,
                    question="完成当前标的的投资经理研究报告：维护完整当前判断、重要变化及其投资含义、行动建议及条件、主要风险、重点主题和适用领域分析。首次缺失的分析建立有依据的基线；已建立的分析按实际新证据更新。核对PM观点与旧假设，保留资料缺口和各自时点；没有新事实不虚构变化。")
            run_id, status = run.entry_id, run.status
        if on_dispatch:
            on_dispatch(instrument_id, run_id, status)
        if created:
            run_analysis(run_id)
        elif status in {"queued", "running"}:
            # Existing owner keeps its run; do not launch a second process or change
            # its state. A bounded wait covers the runner's 30-minute execution.
            deadline = time.monotonic() + 2100
            while status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(10)
                status = run_state(run_id, instrument_id)["status"]
        return result_record(instrument_id, run_id, run_state(run_id, instrument_id))


def write_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Run the selected real research jobs (uses configured model).")
    parser.add_argument("--output", type=Path, help="Private batch manifest outside the source tree; required to execute.")
    parser.add_argument("--resume", action="store_true", help="Recheck completed runs in --output and execute remaining current members.")
    parser.add_argument("--instrument-id", action="append", dest="instrument_ids", help="Limit to named currently Proposed/Invested members.")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    if args.execute and args.output is None:
        parser.error("--execute requires --output")
    if args.resume and (not args.output or not args.output.is_file()):
        parser.error("--resume requires an existing --output manifest")
    if args.output:
        project = Path(__file__).resolve().parents[4]
        if args.output.resolve().is_relative_to(project):
            parser.error("--output must be outside the source tree")
    with principal_context(service_principal("watchlist")):
        with get_session_factory()() as session:
            ids = sector_research.active_research_ids(session)
            labels = {iid: sector_research.instrument_label(session, iid) for iid in ids}
        prior = json.loads(args.output.read_text()) if args.resume else None
        selected = args.instrument_ids if args.instrument_ids is not None else prior["instrument_ids"] if prior else None
        if selected is not None:
            unknown = set(selected) - set(ids)
            if unknown and args.instrument_ids:
                parser.error("Requested instruments are not currently Proposed/Invested: " + ", ".join(sorted(unknown)))
            ids = sorted(set(selected) & set(ids))
        manifest = {"target": target_identity(), "team_id": service_principal("watchlist").team_id,
            "started_at": instant(), "instrument_ids": ids, "labels": {iid: labels[iid] for iid in ids}, "results": {}}
        if args.resume:
            if (prior["target"], prior["team_id"]) != (manifest["target"], manifest["team_id"]):
                parser.error("The saved batch belongs to a different database or team")
            manifest["started_at"] = prior["started_at"]
            for iid, result in prior["results"].items():
                if iid in ids and result.get("run_id"):
                    current = result_record(iid, result["run_id"], run_state(result["run_id"], iid))
                    manifest["results"][iid] = current
        if not args.execute:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 0
        if not harness_available():
            parser.error("The configured Harness is unavailable; no jobs started")
        write_manifest(args.output, manifest)
        pending = [iid for iid in ids if not all(manifest["results"].get(iid, {}).get(key)
                                               for key in ("published", "report_ready"))]
        manifest_lock = Lock()
        def dispatched(iid, run_id, status):
            with manifest_lock:
                manifest["results"][iid] = {"instrument_id": iid, "run_id": run_id, "status": status, "published": False}
                manifest["updated_at"] = instant()
                write_manifest(args.output, manifest)
        print(json.dumps({"selected": len(ids), "pending": len(pending), "output": str(args.output)}, ensure_ascii=False), flush=True)
        resume_ids = {iid: manifest["results"].get(iid, {}).get("run_id") for iid in pending}
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="research-refresh") as pool:
            futures = {pool.submit(refresh_one, iid, resume_ids[iid], dispatched): iid for iid in pending}
            for future in as_completed(futures):
                iid = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    # Exception bodies may contain provider/private data. The normal
                    # runner owns the detailed safe diagnostic record.
                    result = {**manifest["results"].get(iid, {}), "instrument_id": iid, "status": "dispatch_failed", "published": False,
                              "error": {"type": type(error).__name__}}
                with manifest_lock:
                    manifest["results"][iid] = result
                    manifest["updated_at"] = instant()
                    write_manifest(args.output, manifest)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        manifest["finished_at"] = instant()
        manifest["published_count"] = sum(bool(row.get("published")) for row in manifest["results"].values())
        manifest["ready_count"] = sum(bool(row.get("published") and row.get("report_ready")) for row in manifest["results"].values())
        manifest["complete"] = manifest["ready_count"] == len(ids)
        write_manifest(args.output, manifest)
        print(json.dumps({key: manifest[key] for key in ("finished_at", "published_count", "ready_count", "complete")}), flush=True)
        return 0 if manifest["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
