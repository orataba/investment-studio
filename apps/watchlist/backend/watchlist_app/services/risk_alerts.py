"""Small links to unresolved assessed risk and credible urgent unverified facts."""
from urllib.parse import quote

from sqlalchemy import Boolean, JSON, String, and_, case, func, or_, select, true
from studio_identity import current_principal
from studio_runtime.model_work import model_work_status

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_access import research_context_projection, research_projection_rows
from watchlist_app.services.risk_case_state import risk_state_projection


def important_risk_alerts(session, *, limit=3, offset=0):
    fields = {name: String for name in (
        "run_id", "event_version_id", "impact_level", "urgency", "confidence", "checked_at")}
    fields.update(withdrawn=Boolean, risk_assessment=JSON, last_assessed_snapshot=JSON, pending_revision=JSON)
    relation, values = research_context_projection(session, fields, json_column=RiskCase.evidence_json)
    assessment, last, pending = (values[key] for key in ("risk_assessment", "last_assessed_snapshot", "pending_revision"))
    # Exact-version legacy decisions remain readable; historical pending cases
    # are backfilled once by migration, never by scanning history on Home reads.
    legacy = and_(last["status"].as_string().is_(None), assessment["status"].as_string() == "active",
        values["event_version_id"].is_not(None), assessment["event_version_id"].as_string() == values["event_version_id"])
    has_last = last["status"].as_string() == "active"
    magnitude = case((has_last, last["impact_level"].as_string()), else_=values["impact_level"])
    urgency = case((has_last, last["urgency"].as_string()), else_=values["urgency"])
    assessed = and_(RiskCase.trigger_active.is_(True), RiskCase.status.in_(("open", "investigating")),
        or_(has_last, legacy), or_(magnitude == "major", urgency == "immediate"))
    unverified = and_(RiskCase.status != "handled",
        pending["urgent_review_required"].as_boolean().is_(True),
        assessment["status"].as_string() == "pending",
        pending["event_version_id"].as_string() == assessment["event_version_id"].as_string())
    query = select(RiskCase.case_id, RiskCase.title, RiskCase.instrument_id,
        InstrumentDetail.instrument_name, RiskCase.observed_on, RiskCase.updated_at,
        RiskCase.trigger_active, RiskCase.status,
        *(value.label(key) for key, value in values.items())).select_from(RiskCase)
    if relation is not None:
        query = query.join(relation, true())
    query = query.join(InstrumentDetail, InstrumentDetail.instrument_id == RiskCase.instrument_id).join(
        ResearchEntry, ResearchEntry.entry_id == values["run_id"]).join(
        ResearchTopic, ResearchTopic.topic_id == ResearchEntry.topic_id).where(
            InstrumentDetail.is_active.is_(True), ResearchEntry.kind == "analysis",
            RiskCase.signal.like("sector:%"), RiskCase.severity == "attention",
            values["withdrawn"].is_not(True), or_(assessed, unverified))
    principal = current_principal()
    if not principal.local_unrestricted:
        query = query.where(ResearchEntry.team_id == principal.team_id, ResearchTopic.team_id == principal.team_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    query = query.order_by(case((or_(urgency == "immediate", unverified), 0), else_=1),
        case((magnitude == "major", 0), else_=1), RiskCase.updated_at.desc(), RiskCase.case_id).limit(limit).offset(offset)
    rows = research_projection_rows(session, query, {key: (key,) for key in values}, json_column=RiskCase.evidence_json)
    alerts = []
    for row in rows:
        evidence = {key: getattr(row, key) for key in fields}
        continuity = risk_state_projection({"title": row.title, "evidence_json": evidence})
        last, pending = continuity["last_assessed_snapshot"], continuity["pending_revision"]
        # The persisted pending projection also carries qualified source metadata
        # which this lightweight query intentionally does not hydrate.
        pending = row.pending_revision or pending
        active = row.trigger_active and row.status in {"open", "investigating"}
        confirmed = active and last and last.get("status") == "active" and (
            last.get("impact_level") == "major" or last.get("urgency") == "immediate")
        display = last if confirmed else pending
        alert_state = "assessment_pending" if confirmed and pending else "assessed" if confirmed else "unverified_urgent"
        # Keep the exact decision's title/date; never lend its approval to the
        # latest unassessed text. Full bodies and sources live on the risk page.
        summary = lambda value: {key: item for key, item in value.items() if key not in {
            "body", "source_ids", "sources", "impact_analysis", "action_condition"}} if value else None
        alerts.append({"case_id": row.case_id, "instrument_id": row.instrument_id,
            "instrument_name": row.instrument_name, "title": display.get("title") or row.title,
            "updated_at": row.updated_at, "observed_on": row.observed_on,
            **{key: display.get(key) for key in ("impact_level", "urgency", "confidence", "reviewed_at")},
            "as_of": display.get("checked_at"), "event_version_id": display.get("event_version_id"),
            "alert_state": alert_state, "last_assessed_snapshot": summary(last), "pending_revision": summary(pending),
            "target": {"workspace": "watchlist", "path": f"/instruments/{quote(row.instrument_id, safe='')}?tab=risk"}})
    return serialize_payload({"alerts": alerts, "total": total, "has_more": total > offset + len(alerts),
        "model_work": model_work_status(),
        "selection_note": "显示尚未解除的重大风险及有原文依据、需及时核实的重大限时事项。"
            "新资料待复核时保留上次已评估判断和原日期，不以旧评估确认新内容；重大待核实单独标示。"
            "其他风险和数据缺口不在此摘要中；空列表不表示没有风险。"})
