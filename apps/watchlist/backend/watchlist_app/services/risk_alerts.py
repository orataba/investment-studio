"""Small links to unresolved assessed risk and credible urgent unverified facts."""
from urllib.parse import quote

from sqlalchemy import Boolean, JSON, String, and_, case, func, or_, select, true
from sqlalchemy.orm import aliased
from studio_identity import current_principal
from studio_runtime.model_work import model_work_status

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_access import research_context_projection, research_projection_rows
from watchlist_app.services.risk_case_state import risk_state_projection


def _legacy_risk_alerts(session, *, limit=3, offset=0):
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
    # A published generic team case may originate in a private non-portfolio
    # conversation. Only its shared case is returned, never that conversation.
    # Any retained portfolio binding makes the entire topic private to its ACL.
    history = aliased(ResearchEntry)
    private_history = select(1).where(history.topic_id == ResearchTopic.topic_id,
        or_(history.access_context_json["portfolio_id"].as_string().is_not(None),
            history.access_context_json["risk_scope"]["portfolio_id"].as_string().is_not(None))).exists()
    query = query.where(ResearchTopic.portfolio_id.is_(None), ~private_history)
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
            "target": {"workspace": "watchlist", "path": f"/instruments/{quote(row.instrument_id, safe='')}?tab=risk&risk=1"}})
    return serialize_payload({"alerts": alerts, "total": total, "has_more": total > offset + len(alerts),
        "model_work": model_work_status(),
        "selection_note": "显示尚未解除的重大风险及有原文依据、需及时核实的重大限时事项。"
            "新资料待复核时保留上次已评估判断和原日期，不以旧评估确认新内容；重大待核实单独标示。"
            "其他风险和数据缺口不在此摘要中；空列表不表示没有风险。"})



def _selected_notices(session):
    """Read only latest team notice projections; private portfolio work stays private."""
    from watchlist_app.services.risk_workspace_projection import case_summary_rows
    from watchlist_app.services.risk_notices import current_notices
    column = ResearchEntry.read_context_json
    fields = {"risk_notice_items": JSON, "risk_notice_scope": JSON, "risk_automatic_inputs": JSON}
    relation, values = research_context_projection(session, fields, json_column=column)
    newest = select(ResearchEntry.entry_id, func.row_number().over(
        partition_by=ResearchEntry.topic_id, order_by=(ResearchEntry.completed_at.desc(), ResearchEntry.created_at.desc()))
        .label("position")).where(ResearchEntry.status == "completed", ResearchEntry.topic_id.like("risk-officer:%")).subquery()
    query = select(ResearchEntry.entry_id, ResearchEntry.completed_at,
        *(value.label(key) for key, value in values.items())).select_from(ResearchEntry).join(
        newest, newest.c.entry_id == ResearchEntry.entry_id).join(ResearchTopic,
        ResearchTopic.topic_id == ResearchEntry.topic_id).where(newest.c.position == 1,
        ResearchTopic.portfolio_id.is_(None), ResearchTopic.visibility == "team", values["risk_notice_scope"]["kind"].as_string().in_(("instrument", "watchlist")))
    if relation is not None:
        query = query.join(relation, true())
    principal = current_principal()
    if not principal.local_unrestricted:
        query = query.where(ResearchEntry.team_id == principal.team_id, ResearchTopic.team_id == principal.team_id)
    rows = research_projection_rows(session, query.order_by(ResearchEntry.updated_at.desc(), ResearchEntry.completed_at.desc()),
        {key: (key,) for key in fields}, json_column=column)
    ids = {iid for row in rows if row.risk_notice_items is not None for iid in row.risk_notice_scope.get("instrument_ids", [])}
    cases = case_summary_rows(session, ids) if ids else []
    current = {row.instrument_id: row.instrument_name for row in session.execute(select(
        InstrumentDetail.instrument_id, InstrumentDetail.instrument_name).where(
        InstrumentDetail.instrument_id.in_(ids), InstrumentDetail.is_active.is_(True)))} if ids else {}
    items, covered, seen_cases, seen_notices = [], set(), set(), set()
    selection_pending = False
    for row in rows:
        if row.risk_notice_items is None:
            continue
        scope = row.risk_notice_scope
        scope_ids = set(scope.get("instrument_ids", [])) & current.keys()
        # Newest explicit selection covers overlapping team scopes. A missing
        # notice is a recorded filtering decision, never a closed RiskCase.
        fresh = scope_ids - covered
        from watchlist_app.services.risk_review_state import automatic_review_inputs
        baseline = row.risk_automatic_inputs
        now_inputs = automatic_review_inputs({"scope": scope, "instrument_ids": sorted(scope_ids),
            "cases": [case for case in cases if case["instrument_id"] in scope_ids]},
            tracked_case_ids={item["case_id"] for item in (baseline or {}).get("cases", [])})
        if fresh and (baseline is None or baseline != now_inputs):
            selection_pending = True
        for notice in current_notices({"notice_items": row.risk_notice_items}, cases):
            notice_ids = set(notice.get("instrument_ids") or [])
            if not notice_ids or not notice_ids.issubset(scope_ids) or not (notice_ids & fresh):
                continue
            case_ids = set(notice.get("case_ids") or [])
            if notice["notice_id"] in seen_notices or (case_ids and case_ids <= seen_cases):
                continue
            seen_notices.add(notice["notice_id"])
            seen_cases.update(case_ids)
            iid = notice["instrument_ids"][0]
            items.append({**notice, "case_id": next(iter(notice.get("case_ids") or []), notice["notice_id"]),
                "instrument_id": iid, "instrument_name": current[iid], "updated_at": row.completed_at,
                "observed_on": notice.get("as_of"), "target": {"workspace": "watchlist",
                    "path": f"/instruments/{quote(iid, safe='')}?tab=risk&risk=1"}})
        covered.update(scope_ids)
    coverage = [case.get("body") or case["title"] for case in cases
                if case["severity"] == "coverage" and case["trigger_active"]]
    return items, covered, selection_pending, coverage


def _mandatory_notices(session):
    from watchlist_app.services.risk_notices import deterministic_notice
    fields = {"mandatory_notice": Boolean, "rule_version": String, "metric_snapshot_id": String, "trigger_version": String}
    relation, values = research_context_projection(session, fields, json_column=RiskCase.evidence_json)
    query = select(RiskCase.case_id, RiskCase.instrument_id, RiskCase.title, RiskCase.body,
        RiskCase.observed_on, RiskCase.updated_at, InstrumentDetail.instrument_name,
        *(value.label(key) for key, value in values.items())).select_from(RiskCase).join(
        InstrumentDetail, InstrumentDetail.instrument_id == RiskCase.instrument_id).where(
        InstrumentDetail.is_active.is_(True), RiskCase.trigger_active.is_(True),
        RiskCase.signal.in_(("drawdown_limit", "period_loss")), values["mandatory_notice"].is_(True))
    if relation is not None:
        query = query.join(relation, true())
    rows = research_projection_rows(session, query, {key: (key,) for key in fields}, json_column=RiskCase.evidence_json)
    return [{**deterministic_notice({**dict(row._mapping), "evidence_json": {key: getattr(row, key) for key in fields}}),
        "case_id": row.case_id, "instrument_id": row.instrument_id, "instrument_name": row.instrument_name,
        "updated_at": row.updated_at, "observed_on": row.observed_on,
        "target": {"workspace": "watchlist", "path": f"/instruments/{quote(row.instrument_id, safe='')}?tab=risk&risk=1"}}
        for row in rows]


def important_risk_alerts(session, *, limit=3, offset=0):
    from watchlist_app.services.risk_notices import LEVEL_ORDER
    selected, covered, selection_pending, coverage_notes = _selected_notices(session)
    # Legacy approved risks keep their evidence/date until an explicit short
    # notice selection supersedes them. Never turn all old priorities into alerts.
    legacy = _legacy_risk_alerts(session, limit=None)
    items = [*selected, *({**item, "level": "high", "origin": "legacy_assessment"}
                         for item in legacy["alerts"] if item["instrument_id"] not in covered)]
    mandatory = _mandatory_notices(session)
    for fact in mandatory:
        matching = next((item for item in items if fact["case_id"] in item.get("case_ids", [item.get("case_id")])), None)
        if matching:
            matching["mandatory"] = True
            prior_facts = [row for row in matching.get("current_rule_facts", []) if row["notice_id"] != fact["notice_id"]]
            matching["current_rule_facts"] = [*prior_facts, fact]
        else:
            items.append(fact)
    items.sort(key=lambda item: (not item.get("mandatory", False), LEVEL_ORDER.get(item.get("level"), 3)))
    from watchlist_app.services.risk_notice_actions import apply_receipts
    page = apply_receipts(session, items if limit is None else items[offset:offset + limit])
    return serialize_payload({"alerts": page, "total": len(items), "has_more": len(items) > offset + len(page),
        "model_work": model_work_status(),
        "notice_status": "unavailable" if coverage_notes else "assessed" if covered and not selection_pending else "assessment_pending",
        "coverage_notes": coverage_notes, "checked_instrument_ids": sorted(covered),
        "selection_note": "显示经筛选的研究与量化短提示；严重有效规则独立保留，等级表示关注优先度。"
            "新资料待复核时保留原判断与日期；数据缺口不评为市场风险。空列表不表示没有风险。"})
