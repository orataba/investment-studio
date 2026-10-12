"""Persist pending information independently from paid model attempts.

The caller holds the existing instrument publication lock. A scan failure rolls
back the transaction; an attempted/failed job never consumes pending changes.
"""
from copy import deepcopy
from datetime import UTC, datetime
import json

from watchlist_app.db.models.research_monitor import ResearchMonitorState


def change_items(trigger):
    items = {}
    for reason in (trigger or {}).get("reasons", []):
        kind = reason["kind"]
        field = next((key for key in ("sources", "cases", "items", "themes", "materials") if key in reason), None)
        for row in reason[field] if field else [reason]:
            identity = next((row[key] for key in ("source_id", "event_version_id", "theme_id", "case_id", "key", "entry_id") if row.get(key)), kind)
            # Identity + actual version/due time, not a scan clock, defines work.
            version = row.get("version_id") or row.get("baseline_requested_at") or row.get("due_at") or row.get("updated_at")
            key = json.dumps([kind, identity, version], ensure_ascii=False, separators=(",", ":"))
            items[key] = {"kind": kind, "reason": reason.get("reason"), "field": field, "value": deepcopy(row)}
    return items


def pending_trigger(state, instrument_id, *, now):
    reasons = {}
    for item in state.pending_changes_json.values():
        kind, field = item["kind"], item["field"]
        reason = reasons.setdefault(kind, {"kind": kind, "reason": item.get("reason")})
        if field:
            reason.setdefault(field, []).append(deepcopy(item["value"]))
    return {"instrument_id": instrument_id, "reasons": list(reasons.values()),
            "coverage_cursor": {**{key: deepcopy(value) for key, value in state.scan_cursor_json.items()
                if key != "applied_due_changes"}, "attempted_at": now.isoformat()},
            "monitor_snapshot": {"revision": state.revision, "scanned_at": state.scanned_at.isoformat(),
                                 "changes": deepcopy(state.pending_changes_json)}}


def scan(session, instrument_id, prior_context, *, team_id, now):
    from watchlist_app.services.research_triggers import _previous_trigger, research_trigger
    state = session.get(ResearchMonitorState, (team_id, instrument_id), with_for_update=True)
    inherit_failed_attempt = state is None and prior_context.get("previous_run_status") in {"failed", "paused"}
    if state is None:
        # A deployment may first encounter a failed pre-monitor run. Its frozen
        # delta was attempted, not successfully applied; retain it without
        # creating an automatic fresh attempt for the same information.
        old_trigger = _previous_trigger(prior_context, instrument_id)
        old_pending = change_items(old_trigger) if prior_context.get("previous_run_status") in {"failed", "paused"} else {}
        state = ResearchMonitorState(team_id=team_id, instrument_id=instrument_id,
            scan_cursor_json={}, pending_changes_json=old_pending,
            revision=int(bool(old_pending)), attempted_revision=int(bool(old_pending)))
        session.add(state)
    context = deepcopy(prior_context)
    if state.scanned_at:
        context["cutoff"] = state.scanned_at.replace(tzinfo=state.scanned_at.tzinfo or UTC).isoformat()
        previous = _previous_trigger(context, instrument_id)
        context["incremental_trigger"] = {instrument_id: {**previous, "coverage_cursor": {
            **state.scan_cursor_json, "attempted_at": state.scanned_at.replace(tzinfo=state.scanned_at.tzinfo or UTC).isoformat()}}}
    # Nothing is mutated until every channel has completed successfully.
    trigger = research_trigger(session, instrument_id, context, now=now, include_quiet=True)
    changes = change_items(trigger)
    if inherit_failed_attempt:
        # A manually requested baseline can fail before it binds any sources or
        # has an incremental trigger. Its exact attempted request is still
        # pending, but is not newly payable work on the first monitor scan.
        attempted = prior_context.get("attempted_theme_baselines") or {}
        inherited = {key: value for key, value in changes.items()
            if value["kind"] == "theme_baseline" and attempted.get(value["value"].get("theme_id"))
            == value["value"].get("baseline_requested_at") and value["value"].get("baseline_requested_at")}
        if inherited:
            state.pending_changes_json = {**state.pending_changes_json, **inherited}
            state.revision = max(state.revision, 1)
            state.attempted_revision = state.revision
    # Closed or superseded due versions no longer need suppression. They must
    # not grow the persistent cursor or future model input with old history.
    current_due = {key for key, value in changes.items() if value["kind"] in {"research_check_due", "theme_baseline"}}
    pending = {key: deepcopy(value) for key, value in state.pending_changes_json.items()
               if value["kind"] not in {"research_check_due", "theme_baseline"} or key in current_due}
    applied_due = set(state.scan_cursor_json.get("applied_due_changes", [])) & current_due
    for key, value in changes.items():
        if value["kind"] == "new_research_sources" and value["value"].get("document_id"):
            document_id = value["value"]["document_id"]
            dependencies = set(value["value"].get("previous_source_ids", []))
            dependencies.update((pending.get(key, {}).get("value") or {}).get("previous_source_ids", []))
            if value["value"].get("previous_source_id"):
                dependencies.add(value["value"]["previous_source_id"])
            for old_key, old in list(pending.items()):
                if old_key != key and old["kind"] == "new_research_sources" and old["value"].get("document_id") == document_id:
                    # The trigger supplies the current immutable document
                    # revision. Keep its predecessor links, not stale pending
                    # bodies, so corrections still reach every current dependent.
                    dependencies.update(old["value"].get("previous_source_ids", []))
                    dependencies.update(old["value"].get(name) for name in ("previous_source_id", "source_id") if old["value"].get(name))
                    pending.pop(old_key)
            if dependencies:
                value = {**value, "value": {**value["value"], "previous_source_ids": sorted(dependencies)}}
        if key not in applied_due:
            pending[key] = value
    if pending != state.pending_changes_json:
        added_or_changed = any(state.pending_changes_json.get(key) != value for key, value in pending.items())
        state.pending_changes_json = pending
        state.revision += int(added_or_changed)
    state.scanned_at = now
    state.scan_cursor_json = {**trigger["coverage_cursor"], "applied_due_changes": sorted(applied_due)}
    if not pending or state.revision <= state.attempted_revision:
        return state, None
    return state, pending_trigger(state, instrument_id, now=now)


def mark_attempt(state, run_id):
    state.attempted_revision = state.revision
    state.attempt_run_id = run_id


def mark_applied(session, run):
    context = run.context_json or {}
    team_id = (context.get("research_actor") or {}).get("team_id") or run.team_id
    for instrument_id in context.get("instrument_ids", []):
        trigger = (context.get("incremental_trigger") or {}).get(instrument_id, {})
        snapshot = trigger.get("monitor_snapshot")
        if not snapshot:
            continue
        state = session.get(ResearchMonitorState, (team_id, instrument_id), with_for_update=True)
        if state is None:
            raise ValueError("本轮研究的增量监测状态不存在，不能确认处理水位")
        pending = dict(state.pending_changes_json)
        applied_due = set(state.scan_cursor_json.get("applied_due_changes", []))
        for key, value in snapshot["changes"].items():
            if pending.get(key) == value:
                pending.pop(key)
            if value["kind"] in {"research_check_due", "theme_baseline"}:
                applied_due.add(key)
        state.pending_changes_json = pending
        state.scan_cursor_json = {**state.scan_cursor_json, "applied_due_changes": sorted(applied_due)}
        # This is the applied frozen window, not the time of a newer cheap scan.
        state.applied_at = datetime.fromisoformat(snapshot["scanned_at"])
