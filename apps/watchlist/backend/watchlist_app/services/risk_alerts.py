"""Home links to current, assessed material risk; no new risk calculation/store."""
from urllib.parse import quote

from sqlalchemy import Boolean, JSON, String, case, func, or_, select, true
from studio_identity import current_principal

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_access import research_context_projection, research_projection_rows


def important_risk_alerts(session, *, limit=3, offset=0):
    fields = {name: String for name in (
        "run_id", "event_version_id", "impact_level", "urgency", "confidence", "checked_at")}
    fields.update(withdrawn=Boolean, risk_assessment=JSON)
    relation, values = research_context_projection(session, fields, json_column=RiskCase.evidence_json)
    assessment = values["risk_assessment"]
    query = select(RiskCase.case_id, RiskCase.title, RiskCase.instrument_id,
        InstrumentDetail.instrument_name, RiskCase.observed_on, RiskCase.updated_at,
        values["impact_level"].label("impact_level"), values["urgency"].label("urgency"),
        values["confidence"].label("confidence"), values["checked_at"].label("as_of"),
        assessment["reviewed_at"].as_string().label("reviewed_at")).select_from(RiskCase)
    if relation is not None:
        query = query.join(relation, true())
    query = query.join(InstrumentDetail, InstrumentDetail.instrument_id == RiskCase.instrument_id).join(
        ResearchEntry, ResearchEntry.entry_id == values["run_id"]).join(
        ResearchTopic, ResearchTopic.topic_id == ResearchEntry.topic_id).where(
            InstrumentDetail.is_active.is_(True), ResearchEntry.kind == "analysis",
            RiskCase.signal.like("sector:%"), RiskCase.severity == "attention",
            RiskCase.trigger_active.is_(True), RiskCase.status.in_(("open", "investigating")),
            values["withdrawn"].is_not(True),
            assessment["status"].as_string() == "active",
            values["event_version_id"].is_not(None),
            assessment["event_version_id"].as_string() == values["event_version_id"],
            or_(values["impact_level"] == "major", values["urgency"] == "immediate"))
    principal = current_principal()
    if not principal.local_unrestricted:
        query = query.where(ResearchEntry.team_id == principal.team_id, ResearchTopic.team_id == principal.team_id)
    # An explicitly authorized private conversation may publish shared events.
    # Its private answer is not read here; the published case supplies the text.
    # Research direction cannot clear a separately confirmed current risk.
    # Magnitude and timing are independent existing judgments, not a new score.
    # SQL filters the publication/assessment/ownership before count and limit.
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    query = query.order_by(case((values["urgency"] == "immediate", 0), else_=1),
        case((values["impact_level"] == "major", 0), else_=1),
        RiskCase.updated_at.desc(), RiskCase.case_id).limit(limit).offset(offset)
    projection = {key: (key,) for key in ("impact_level", "urgency", "confidence")}
    projection.update(as_of=("checked_at",), reviewed_at=("risk_assessment", "reviewed_at"))
    rows = research_projection_rows(session, query, projection, json_column=RiskCase.evidence_json)
    alerts = [{**row._mapping, "target": {"workspace": "watchlist",
        "path": f"/instruments/{quote(row.instrument_id, safe='')}?tab=risk"}} for row in rows]
    return serialize_payload({"alerts": alerts, "total": total, "has_more": total > offset + len(alerts),
        "selection_note": "仅显示已发布、当前版本经风控判断仍需关注且未处理的重大或即时事项；"
            "影响幅度与处置时点独立判断。保留原证据可信度和时点，风控确认不等于事实已独立核验。"
            "其他风险、待核实事项和数据缺口不在此摘要中；空列表不表示没有风险。"})
