"""Risk workspace projection shared by the HTTP page and research snapshots."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models import InstrumentRiskReadModel, Watchlist, WatchlistItem
from watchlist_app.db.models.workbench import RiskCase, RiskReviewRule
from watchlist_app.services.research_workbench import catalogue
from watchlist_app.services.price_risk import price_rule_summary, rule_version
from watchlist_app.services.risk_workspace_price_context import build_workspace_price_context, apply_workspace_price_context


def read_risk_workspace(session: Session, *, instrument_id: str | None = None,
                        instrument_ids: str | None = None, watchlist_id: str | None = None,
                        summary: bool = False, workspace_projection: bool = False):
    from watchlist_app.services.risk_workspace_projection import RISK_FIELDS, case_detail, case_summary_rows, return_series, risk_assets
    cases_query = select(RiskCase).order_by(RiskCase.trigger_active.desc(), RiskCase.updated_at.desc()).execution_options(populate_existing=True)
    ids = None
    if watchlist_id:
        if session.get(Watchlist, watchlist_id) is None:
            raise HTTPException(404, "Record not found")
        ids = set(session.scalars(select(WatchlistItem.instrument_id).where(WatchlistItem.watchlist_id == watchlist_id)))
    if instrument_ids is not None:
        requested = set(filter(None, instrument_ids.split(",")))
        ids = requested if ids is None else ids & requested
    if instrument_id:
        ids = {instrument_id} if ids is None else ids & {instrument_id}
    if ids is not None:
        cases_query = cases_query.where(RiskCase.instrument_id.in_(ids))
    instruments = catalogue(session, instrument_ids=ids)
    member_ids = [item["instrument_id"] for item in instruments]
    summary = summary or workspace_projection
    risks = (risk_assets(session, member_ids, workspace=workspace_projection) if summary else {row.instrument_id: row for row in session.scalars(select(InstrumentRiskReadModel).where(
        InstrumentRiskReadModel.instrument_id.in_(member_ids)).execution_options(populate_existing=True))}) if member_ids else {}
    rules = {row.instrument_id: row for row in session.scalars(select(RiskReviewRule).where(
        RiskReviewRule.instrument_id.in_(member_ids)).execution_options(populate_existing=True))} if member_ids else {}
    # Retained/archived rows can predate this projection. Only those rows use the
    # unchanged read-only calculation until normal release materialization runs.
    legacy_ids = [iid for iid in member_ids if not workspace_projection
        or getattr(risks.get(iid), "workspace_price_context_json", None) is None]
    series_by_id = return_series(session, legacy_ids) if legacy_ids else {}
    for item in instruments:
        iid = item["instrument_id"]
        risk = risks.get(iid)
        rule = rules.get(iid)
        # Curves belong to the dedicated monitoring endpoint; research snapshots
        # and workspace consumers share the bounded, versioned metric summary.
        payload = ({key: getattr(risk, key) for key in RISK_FIELDS if not workspace_projection or key != "monitoring_summary"} if summary else {
            key: value for key, value in risk.payload_json.items() if key != "monitoring"
        }) if risk else None
        item["risk"] = payload
        item["freshness"] = risk.data_freshness_status if risk else "missing"
        item["drawdown_limit"] = rule.drawdown_limit if rule else None
        item["period_limits"] = rule.period_limits_json if rule else {}
        item["price_risk_calibration"] = rule.calibration_json if rule else {}
        item["rule_version"] = rule_version(rule)
        context = getattr(risk, "workspace_price_context_json", None) if workspace_projection else None
        if context is None:
            context = build_workspace_price_context(series_by_id.get(iid, {}), payload or {})
        apply_workspace_price_context(item, context)
        item["price_rule_summary"] = price_rule_summary(item, rule.updated_at if rule else None)
    cases = case_summary_rows(session, ids) if summary else [case_detail(case) for case in session.scalars(cases_query)]
    return {"instruments": instruments, "cases": cases}
