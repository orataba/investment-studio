"""Risk workspace projection shared by the HTTP page and research snapshots."""
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models import InstrumentRiskReadModel, Watchlist, WatchlistItem
from watchlist_app.db.models.workbench import RiskCase, RiskReviewRule
from watchlist_app.services.research_workbench import catalogue
from watchlist_app.services.price_risk import (
    period_loss_readings, price_rule_summary, series_limitation, recent_price_risk_points,
)


def read_risk_workspace(session: Session, *, instrument_id: str | None = None,
                        instrument_ids: str | None = None, watchlist_id: str | None = None,
                        summary: bool = False):
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
    risks = (risk_assets(session, member_ids) if summary else {row.instrument_id: row for row in session.scalars(select(InstrumentRiskReadModel).where(
        InstrumentRiskReadModel.instrument_id.in_(member_ids)).execution_options(populate_existing=True))}) if member_ids else {}
    rules = {row.instrument_id: row for row in session.scalars(select(RiskReviewRule).where(
        RiskReviewRule.instrument_id.in_(member_ids)).execution_options(populate_existing=True))} if member_ids else {}
    series_by_id = return_series(session, member_ids) if member_ids else {}
    for item in instruments:
        iid = item["instrument_id"]
        risk = risks.get(iid)
        rule = rules.get(iid)
        payload = ({key: getattr(risk, key) for key in RISK_FIELDS} if summary else risk.payload_json) if risk else None
        item["risk"] = payload
        item["freshness"] = risk.data_freshness_status if risk else "missing"
        item["drawdown_limit"] = rule.drawdown_limit if rule else None
        item["drawdown_change_pp"] = None
        item["previous_observation_date"] = None
        series = series_by_id.get(iid, {})
        points = series.get("points") or []
        item["period_limits"] = rule.period_limits_json if rule else {}
        item["price_risk_calibration"] = rule.calibration_json if rule else {}
        item["period_readings"] = period_loss_readings(series, item["period_limits"])
        item["price_rule_summary"] = price_rule_summary(item, rule.updated_at if rule else None)
        minimum_returns = 90 if (series.get("frequency") or {}).get("gap_detection_basis") == "market_calendar:24/7" else 63
        item["price_risk_note"] = series_limitation(series) or (f"不足 {minimum_returns} 个日收益，尚未生成波动初值"
            if not item["period_limits"] and len(recent_price_risk_points(series)) <= minimum_returns else None)
        item["return_kind"] = (series.get("metadata") or {}).get("return_kind")
        current = (payload.get("current_drawdown") if payload else None)
        if current is not None and len(points) >= 3 and (payload.get("data_quality") or {}).get("status") == "ready":
            from watchlist_app.services.canonical_recalc import _current_drawdown
            previous = _current_drawdown([{**p, "as_of_date": date.fromisoformat(p["date"])} for p in points[:-1]])
            if previous is not None:
                item["drawdown_change_pp"] = float(current) - previous
                item["previous_observation_date"] = points[-2]["date"]
    cases = case_summary_rows(session, ids) if summary else [case_detail(case) for case in session.scalars(cases_query)]
    return {"instruments": instruments, "cases": cases}

