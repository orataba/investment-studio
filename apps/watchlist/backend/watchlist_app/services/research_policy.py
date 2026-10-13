"""Research admission follows current Proposed/Invested status and active identity.

This is automatic/shared-research admission, not an investment status or an
authorization rule. Existing ownership and evidence permissions still apply.
"""
from fastapi import HTTPException
from sqlalchemy import Boolean, JSON, func, select, true
from investment_studio_instrument_core.db_models import Instrument
from studio_runtime.model_work import ModelWorkPaused, model_work_status, require_model_work
from watchlist_app.core.settings import get_settings
from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail


def run_policy_projection(session, run_id, *, locking=False):
    """Queue checks must not hydrate retained prompts, originals or notebooks."""
    from types import SimpleNamespace
    from watchlist_app.db.models.workbench import ResearchEntry
    from watchlist_app.services.research_access import research_context_projection, research_projection_rows
    fields = {"sector_run": Boolean, "risk_run": Boolean, "scheduled": Boolean,
              "instrument_ids": JSON, "risk_scope": JSON}
    relation, values = research_context_projection(session, fields, json_column=ResearchEntry.read_context_json)
    statement = select(ResearchEntry.status, *(value.label(key) for key, value in values.items())).select_from(ResearchEntry)
    if relation is not None:
        statement = statement.join(relation, true())
    statement = statement.where(ResearchEntry.entry_id == run_id)
    if locking:
        statement = statement.with_for_update(of=ResearchEntry)
    rows = research_projection_rows(session, statement, {key: (key,) for key in fields},
                                   json_column=ResearchEntry.read_context_json)
    row = rows[0] if rows else None
    return SimpleNamespace(status=row.status, context_json={key: getattr(row, key) for key in fields}) if row else None


def research_capability(session, instrument_id=None, *, risk=False):
    from watchlist_app.services.instrument_resolution import LOCAL_DETAIL_INSTRUMENT_TYPES
    settings = get_settings()
    latest = select(InstrumentAttributeValue.instrument_id, InstrumentAttributeValue.value_json,
        func.row_number().over(partition_by=InstrumentAttributeValue.instrument_id,
            order_by=(InstrumentAttributeValue.adopted_at.desc(),
                      InstrumentAttributeValue.instrument_attribute_value_id.desc())).label("position")
        ).where(InstrumentAttributeValue.attribute_key == "coverage_status").subquery()
    rows = session.execute(select(Instrument.instrument_id, Instrument.instrument_type, latest.c.value_json)
        .join(InstrumentDetail, (InstrumentDetail.instrument_id == Instrument.instrument_id)
              & (InstrumentDetail.instrument_type == Instrument.instrument_type))
        .join(latest, latest.c.instrument_id == Instrument.instrument_id)
        .where(Instrument.instrument_type.in_(LOCAL_DETAIL_INSTRUMENT_TYPES), InstrumentDetail.is_active.is_(True),
               func.coalesce(Instrument.lifecycle_state_json["status"].as_string(), "active") != "archived",
               latest.c.position == 1)
        .order_by(Instrument.instrument_id)).all()
    statuses = {"Invested"} if risk else {"Proposed", "Invested"}
    eligible = {iid: kind for iid, kind, status in rows if status in statuses}
    if not risk:
        from watchlist_app.services.research_dossier import _registration
        from watchlist_app.services.research_applicability import investment_research_supported
        eligible = {iid: kind for iid, kind in eligible.items() if investment_research_supported(
            _registration(session, session.get(InstrumentDetail, iid)))}
    rows = []
    for iid in ([instrument_id] if instrument_id else eligible):
        detail = session.get(InstrumentDetail, iid) if iid not in eligible else None
        allowed = iid in eligible
        rows.append({"instrument_id": iid, "instrument_type": eligible.get(iid) or (detail.instrument_type if detail else None),
                     "eligible": allowed, "reason": None if allowed else
                     ("仅当前在投且有效的标的可自动更新风险研判。" if risk else "共享投资研究覆盖拟投或在投且有效的股票及权益产品；其他资产保留风险监测和研究助手。")})
    return {"model_work": model_work_status(), "automatic_enabled": settings.research_automatic_enabled,
            "eligible_instruments": list(eligible), "instruments": rows}


def automatic_research_ids(session):
    policy = research_capability(session)
    if policy["model_work"]["paused"] or not policy["automatic_enabled"]:
        return []
    return [row["instrument_id"] for row in policy["instruments"] if row["eligible"]]


def automatic_risk_ids(session):
    policy = research_capability(session, risk=True)
    if policy["model_work"]["paused"] or not policy["automatic_enabled"]:
        return []
    return policy["eligible_instruments"]


def require_instrument_research(session, ids, *, scheduled=False, risk=False):
    require_model_work()
    policy = research_capability(session, risk=risk)
    if scheduled and not policy["automatic_enabled"]:
        raise HTTPException(409, "此环境未启用自动研究。")
    allowed = {row["instrument_id"] for row in policy["instruments"] if row["eligible"]}
    if not ids or not set(ids).issubset(allowed):
        raise HTTPException(409, "仅当前在投且有效的标的可更新风险研判。" if risk else "共享投资研究覆盖拟投或在投且有效的股票及权益产品；其他资产保留风险监测和研究助手。")


def require_run_policy(session, run):
    require_model_work()
    if run is None:
        raise HTTPException(404, "研究记录不存在")
    context = run.context_json or {}
    try:
        if context.get("sector_run"):
            require_instrument_research(session, context.get("instrument_ids", []), scheduled=bool(context.get("scheduled")))
        elif context.get("risk_run") and context.get("scheduled"):
            scope = context.get("risk_scope") or {}
            if not scope.get("instrument_id") or scope.get("portfolio_id") or scope.get("watchlist_id"):
                raise HTTPException(409, "自动风险研判按在投标的的实质变化排队，不重复遍历全列表和组合。")
            require_instrument_research(session, [scope["instrument_id"]], scheduled=True, risk=True)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        raise ModelWorkPaused(str(error.detail)) from error
