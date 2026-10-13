"""Separate an assessed risk decision from a newer, unassessed event revision.

Pure projections over the existing case and immutable history. Numerical evidence
refreshes remain recorded, but do not by themselves constitute a new risk claim.
"""
from copy import deepcopy


EVENT_FIELDS = ("event_version_id", "title", "body", "impact_level", "urgency", "confidence",
                "checked_at", "source_ids", "action_condition", "impact_analysis")
ASSESSMENT_FIELDS = ("status", "reason", "reviewed_at", "run_id")


def _value(case, name, default=None):
    return case.get(name, default) if isinstance(case, dict) else getattr(case, name, default)


def _event_sources(event):
    # Bound references only: retaining a decision must not duplicate provider
    # rows or source originals into Home/risk summary projections.
    fields = ("source_id", "document_id", "version_id", "url", "title", "source_type", "as_of",
              "published_at", "occurred_at", "observed_at", "received_at", "retrieved_at",
              "discovered_at", "time_status", "run_cutoff", "pm_binding_note")
    return [{key: deepcopy(source[key]) for key in fields if key in source}
            for source in event.get("source_views", event.get("sources") or [])]


def _snapshot(event, assessment):
    if not event.get("event_version_id") or assessment.get("event_version_id") != event["event_version_id"]:
        return None
    return {**{key: deepcopy(event.get(key)) for key in EVENT_FIELDS},
            "source_run_id": event.get("run_id"), "sources": _event_sources(event),
            **{key: assessment.get(key) for key in ASSESSMENT_FIELDS}}


def last_assessed_snapshot(case, *, recover_history=False):
    evidence = _value(case, "evidence_json", {}) or {}
    saved = evidence.get("last_assessed_snapshot")
    if saved and saved.get("status") in {"active", "resolved", "dismissed"}:
        return deepcopy(saved)
    current = {"title": _value(case, "title"), "body": _value(case, "body"), **evidence}
    assessment = evidence.get("risk_assessment") or {}
    if assessment.get("status") in {"active", "resolved", "dismissed"}:
        snapshot = _snapshot(current, assessment)
        if snapshot:
            return snapshot
    # An explicit empty retained decision is authoritative. A reset may keep
    # original event history for human citations without retaining its former
    # risk judgment. Only an absent field permits reconstruction from history;
    # a valid exact-version current assessment above remains independently valid.
    if not recover_history or "last_assessed_snapshot" in evidence:
        return None
    history = _value(case, "history_json", []) or []
    events = {item["snapshot"].get("event_version_id"): item["snapshot"] for item in history
              if isinstance(item.get("snapshot"), dict) and item["snapshot"].get("event_version_id")}
    events[current.get("event_version_id")] = current
    for item in reversed(history):
        # Only a retained officer decision establishes confirmation. A copied
        # pending referral may still carry an old reviewed_at/run_id.
        prior = ((item.get("after") or {}).get("risk_assessment") or item.get("risk_assessment")
                 if item.get("action") == "risk_assessed" else
                 (item.get("snapshot") or {}).get("risk_assessment")) or {}
        if prior.get("status") not in {"active", "resolved", "dismissed"}:
            continue
        event = events.get(prior.get("event_version_id"))
        # A known later terminal decision with missing evidence must not fall
        # back to an older active decision and resurrect a cleared risk.
        return _snapshot(event, prior) if event else None
    return None


def urgent_unverified(event):
    """A concrete, important deadline backed by retained original evidence.

    This establishes a reason to check promptly, never independent fact approval.
    A model's severity adjective or a numeric observation alone is insufficient.
    """
    return bool(not event.get("withdrawn") and event.get("direction") in {"risk", "uncertain"}
        and event.get("information_type") == "fact" and event.get("confidence") in {"confirmed", "reported"}
        and event.get("impact_level") == "major" and event.get("urgency") == "immediate"
        and str(event.get("action_condition") or "").strip()
        and any(source.get("source_type") == "public_source" and source.get("published_at")
                and source.get("document_id") and source.get("version_id")
                and not source.get("withdrawn") and source.get("time_status") != "future"
                for source in event.get("sources", [])))


def pending_revision(event, last):
    assessment = event.get("risk_assessment") or {}
    if (assessment.get("status") != "pending"
            and (not last or last.get("event_version_id") == event.get("event_version_id"))):
        return None
    return {**{key: deepcopy(event.get(key)) for key in EVENT_FIELDS},
        "source_run_id": event.get("run_id"), "sources": _event_sources(event), "status": "pending",
        "submitted_at": assessment.get("submitted_at"),
        "urgent_review_required": urgent_unverified(event)}


def risk_state_projection(case, *, recover_history=False):
    evidence = _value(case, "evidence_json", {}) or {}
    last = last_assessed_snapshot(case, recover_history=recover_history)
    event = {"title": _value(case, "title"), "body": _value(case, "body"), **evidence}
    # An explicit None means a publication only refreshed numerical evidence;
    # it must not be reinterpreted as a new pending risk on every read.
    pending = deepcopy(evidence["pending_revision"]) if "pending_revision" in evidence else pending_revision(event, last)
    return {"last_assessed_snapshot": last, "pending_revision": pending}


def _ordinary_numeric_update(previous, item, sources):
    if not previous or not sources or any(not _internal_numeric_source(row) or row.get("withdrawn") for row in sources):
        return False
    fields = ("direction", "impact_level", "urgency", "risk_channels", "impact_analysis", "action_condition",
              "information_type", "confidence", "withdrawal_reason")
    return (bool(previous.get("withdrawn")) == bool(item.get("withdrawn"))
            and all(previous.get(key) == item.get(key) for key in fields))


def _internal_numeric_source(source):
    return (source.get("source_type") == "computed_metric"
            and (source.get("analysis_kind") or (source.get("data") or {}).get("analysis_kind")) != "provider_snapshot")


def should_refer_event(case, item, sources, *, material_change):
    """New numeric observations alone do not re-open an unchanged risk claim.

    No arbitrary return threshold: substantive observations use the existing
    mechanism/condition or a configured quantitative case, separately monitored.
    New/corrected originals and changed risk conditions remain reviewable.
    """
    item = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    if not material_change or not (item.get("direction") == "risk" or
            (item.get("direction") == "uncertain" and (item.get("importance_score") or 0) >= 4
             and item.get("impact_level") in {"material", "major"})):
        return False
    numeric_only = bool(sources) and all(_internal_numeric_source(row) for row in sources)
    if not numeric_only:
        return True
    previous = _value(case, "evidence_json", {}) or {}
    # An initial ordinary numeric observation is a chart, not a material event.
    if not previous:
        return False  # Existing quantitative RiskCases own configured numeric triggers.
    return not _ordinary_numeric_update(previous, item, sources)


def advance_event_risk(case, snapshot, *, material_change, sources):
    """Return fields to merge into a newly published event, before overwriting it."""
    previous = _value(case, "evidence_json", {}) or {}
    last = last_assessed_snapshot(case, recover_history=True) if case is not None else None
    assessment = deepcopy(previous.get("risk_assessment") or {"status": "not_submitted"})
    if should_refer_event(case, snapshot, sources, material_change=material_change):
        assessment = {"status": "pending", "issue_key": f"{_value(case, 'instrument_id', snapshot.get('instrument_id'))}:{snapshot.get('event_key')}",
            "event_version_id": snapshot["event_version_id"], "source_ids": list(snapshot.get("source_ids") or []),
            "impact_explanation": snapshot.get("impact_analysis") or snapshot.get("body"),
            "observation_condition": snapshot.get("next_watch"), "submitted_at": snapshot.get("recorded_at")}
    event = {**snapshot, "risk_assessment": assessment}
    pending = (risk_state_projection(case)["pending_revision"]
               if _ordinary_numeric_update(previous, snapshot, sources) else pending_revision(event, last))
    return {"risk_assessment": assessment, "last_assessed_snapshot": last,
            "pending_revision": pending}


def assessed_risk_fields(case, assessment):
    """An independent pending review preserves the last non-pending decision."""
    evidence = _value(case, "evidence_json", {}) or {}
    event = {"title": _value(case, "title"), "body": _value(case, "body"), **evidence,
             "risk_assessment": assessment, "event_version_id": assessment["event_version_id"]}
    # The caller has checked this version against event_record, including the
    # existing implicit version of legacy events. No new version is generated.
    last = (last_assessed_snapshot(case, recover_history=True) if assessment["status"] == "pending"
            else _snapshot(event, assessment))
    return {"last_assessed_snapshot": last, "pending_revision": pending_revision(event, last)}
