"""Retain the last assessed risk separately from new unassessed evidence."""
from alembic import op
import sqlalchemy as sa

revision = "20261012_0071"
down_revision = "20261012_0070"
branch_labels = None
depends_on = None

# Frozen projection: original evidence/history/timestamps are never rewritten.
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
    if not recover_history:
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
    # Preserve an explicit no-pending projection on a later upgrade replay.
    pending = deepcopy(evidence["pending_revision"]) if "pending_revision" in evidence else pending_revision(event, last)
    return {"last_assessed_snapshot": last, "pending_revision": pending}


def upgrade():
    connection = op.get_bind()
    cases = sa.table("risk_case", sa.column("case_id", sa.String()), sa.column("signal", sa.String()),
        sa.column("title", sa.String()), sa.column("body", sa.Text()),
        sa.column("evidence_json", sa.JSON()), sa.column("history_json", sa.JSON()))
    last_id = None
    while True:
        query = sa.select(cases).where(cases.c.signal.like("sector:%")).order_by(cases.c.case_id).limit(1)
        if last_id is not None:
            query = query.where(cases.c.case_id > last_id)
        row = connection.execute(query).first()
        if row is None:
            break
        value = dict(row._mapping)
        last_id = value["case_id"]
        projection = risk_state_projection(value, recover_history=True)
        evidence = {**(value["evidence_json"] or {}), **projection}
        connection.execute(cases.update().where(cases.c.case_id == last_id).values(evidence_json=evidence))
        del row, value, evidence, projection


def downgrade():
    # The additive evidence projection is harmless to prior readers. Retain it:
    # a rollback must not discard the risk decision/version preserved here.
    pass
