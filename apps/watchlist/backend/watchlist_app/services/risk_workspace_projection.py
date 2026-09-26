"""Small current-risk reads; retained evidence and history stay in the case store."""
from sqlalchemy import Boolean, Float, JSON, String, func, select, true

from watchlist_app.db.models import InstrumentChartReadModel, InstrumentRiskReadModel
from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_access import research_context_projection, research_projection_rows


CASE_EVIDENCE_FIELDS = {
    "importance": String, "direction": String, "confidence": String, "next_watch": String,
    "withdrawn": Boolean, "withdrawal_reason": String, "withdrawn_at": String,
    "risk_assessment": JSON, "event_version_id": String,
}
RISK_FIELDS = {
    "current_drawdown": Float, "drawdown_summary": JSON,
    "data_quality": JSON, "risk_change_monitor": JSON,
}


def _projected_rows(session, model, fields, columns, *, ids, json_column):
    relation, values = research_context_projection(session, fields, json_column=json_column)
    query = select(*columns, *(value.label(key) for key, value in values.items())).select_from(model)
    if relation is not None:
        query = query.join(relation, true())
    if ids is not None:
        query = query.where(model.instrument_id.in_(ids))
    return research_projection_rows(session, query, {key: (key,) for key in values}, json_column=json_column)


def risk_assets(session, ids):
    return {row.instrument_id: row for row in _projected_rows(
        session, InstrumentRiskReadModel, RISK_FIELDS,
        [InstrumentRiskReadModel.instrument_id, InstrumentRiskReadModel.data_freshness_status],
        ids=ids, json_column=InstrumentRiskReadModel.payload_json)}


def return_series(session, ids):
    # The price-monitor calculations need their complete return sample, not the
    # other charts, volatility series, or repeated source metadata in this row.
    return {row.instrument_id: row.research_returns or {} for row in _projected_rows(
        session, InstrumentChartReadModel, {"research_returns": JSON},
        [InstrumentChartReadModel.instrument_id], ids=ids, json_column=InstrumentChartReadModel.payload_json)}


def case_summary_rows(session, ids):
    length = func.json_array_length(RiskCase.history_json)
    columns = [column for column in RiskCase.__table__.columns if column.name not in {"evidence_json", "history_json"}]
    rows = _projected_rows(session, RiskCase, CASE_EVIDENCE_FIELDS, [*columns, length.label("history_count")],
                           ids=ids, json_column=RiskCase.evidence_json)
    cases = []
    for row in rows:
        value = {column.name: getattr(row, column.name) for column in columns}
        evidence = {key: getattr(row, key) for key in CASE_EVIDENCE_FIELDS if getattr(row, key) is not None}
        if row.signal.startswith("sector:"):
            evidence["event_version_id"] = evidence.get("event_version_id") or f"{row.case_id}:{max(1, row.history_count or 0)}"
        cases.append(serialize_payload({**value, "evidence_json": evidence,
            "history_count": row.history_count or 0, "detail_available": True}))
    return sorted(cases, key=lambda row: (row["trigger_active"], row["updated_at"]), reverse=True)


def case_detail(case):
    value = serialize_payload({column.name: getattr(case, column.name) for column in case.__table__.columns})
    if case.signal.startswith("sector:"):
        value["evidence_json"] = {**value["evidence_json"], "event_version_id":
            value["evidence_json"].get("event_version_id") or f"{case.case_id}:{max(1, len(case.history_json or []))}"}
    return value
