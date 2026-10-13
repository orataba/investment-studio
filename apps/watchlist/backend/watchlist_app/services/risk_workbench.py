"""Risk observations are materialized by the existing recalc worker, never by GETs."""
from datetime import UTC, date, datetime
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from watchlist_app.db.models import InstrumentDetail, InstrumentRiskReadModel, InstrumentSummaryReadModel, InstrumentChartReadModel
from watchlist_app.db.models.research import InstrumentResearchNote
from watchlist_app.db.models.workbench import RiskCase, RiskReviewRule
from watchlist_app.services.price_risk import initial_price_limits, period_loss_readings


def now():
    return datetime.now(UTC)


def event(case: RiskCase, action: str, detail: str):
    case.history_json = [*(case.history_json or []), {"at": now().isoformat(), "action": action, "detail": detail}]


def _coverage_fact(evidence: dict, observed_on: date | None):
    """Identify a confirmed gap, excluding clocks that only describe recalculation."""
    freshness = evidence.get("freshness") or {}
    expected = freshness.get("expected_latest_date")
    revision = freshness.get("last_fact_update_at")
    if not observed_on or not expected or not revision:
        return None
    return (observed_on, expected, revision, freshness.get("data_freshness_status"),
            evidence.get("data_quality") or {})


def _previous_coverage_case(session: Session, instrument_id: str, observed_on: date | None, evidence: dict):
    fact = _coverage_fact(evidence, observed_on)
    if fact is None:
        return None
    # Instrument locking in refresh_risk_cases also serializes reactivation.
    # Retain existing case identities: saved research and PM follow-ups refer to them.
    candidates = session.scalars(select(RiskCase).where(
        RiskCase.instrument_id == instrument_id, RiskCase.signal == "data_coverage",
        RiskCase.trigger_active.is_(False), RiskCase.observed_on == observed_on,
    ).order_by(RiskCase.created_at.desc(), RiskCase.case_id).with_for_update()
        .execution_options(populate_existing=True))
    return next((case for case in candidates if _coverage_fact(case.evidence_json, case.observed_on) == fact), None)


def refresh_risk_cases(session: Session, instrument_ids: list[str] | None = None):
    session.flush()
    query = select(InstrumentDetail).where(InstrumentDetail.is_active.is_(True))
    if instrument_ids is not None:
        query = query.where(InstrumentDetail.instrument_id.in_(instrument_ids))
    # Notes, manual thresholds and recalculation can refresh the same instrument.
    # Serialize first creation as well as updates; case locks retain PM history.
    # Identity keys are not changed here. PostgreSQL NO KEY UPDATE keeps risk
    # writers serialized without blocking the KEY SHARE check when a recalc job
    # references this instrument; the market-data enqueue must stay independent.
    for instrument in session.scalars(query.order_by(InstrumentDetail.instrument_id).with_for_update(key_share=True)
            .execution_options(populate_existing=True)):
        iid = instrument.instrument_id
        risk = session.get(InstrumentRiskReadModel, iid)
        summary = session.get(InstrumentSummaryReadModel, iid)
        payload = risk.payload_json if risk else {}
        freshness = (summary.payload_json.get("freshness") or {}) if summary else {}
        drawdown = payload.get("current_drawdown")
        dd_summary = payload.get("drawdown_summary") or {}
        observed = freshness.get("latest_observation_date") or freshness.get("last_nav_date") or freshness.get("latest_quote_date")
        if not observed and summary:
            observed = (summary.payload_json.get("snapshot_metadata") or {}).get("as_of_date")
        observed_on = date.fromisoformat(str(observed)[:10]) if observed else None
        quality = payload.get("data_quality") or {}
        stale = freshness.get("data_freshness_status", summary.data_freshness_status if summary else "missing") != "fresh"
        limited = not risk or quality.get("status") != "ready" or stale
        chart = session.get(InstrumentChartReadModel, iid)
        price_stale = stale or not chart or chart.data_freshness_status != "fresh"
        series = (chart.payload_json.get("research_returns") or {}) if chart else {}
        rule = session.get(RiskReviewRule, iid)
        if not price_stale and (rule is None or not rule.period_limits_json):
            limits, calibration = initial_price_limits(series)
            if limits:
                if rule is None:
                    rule = RiskReviewRule(instrument_id=iid)
                    session.add(rule)
                rule.period_limits_json = limits
                rule.calibration_json = calibration
        readings = period_loss_readings(series, (rule.period_limits_json or {}) if rule else {})
        signals: dict[str, dict] = {}
        if limited:
            reasons = []
            if not observed_on:
                reasons.append("尚无可用净值或行情日期。")
            elif freshness.get("data_freshness_status") != "fresh":
                expected = freshness.get("expected_latest_date")
                reasons.append(f"最新数据停留在 {observed_on.isoformat()}" + (f"，按当前日历应更新至 {expected}。" if expected else "，需要核对更新情况。"))
            if quality.get("gap_count"):
                reasons.append(f"计算区间缺少 {quality['gap_count']} 个应有观察值，回撤等路径风险暂不可计算。")
            elif not risk or quality.get("status") != "ready":
                reasons.append("计算样本尚未就绪，部分风险指标不可用。")
            signals["data_coverage"] = dict(title="风险监测受限", body="".join(reasons), severity="coverage", evidence={"data_quality": quality, "freshness": freshness})
        else:
            if rule and rule.drawdown_limit is not None and drawdown is not None and float(drawdown) <= -rule.drawdown_limit:
                signals["drawdown_limit"] = dict(title="回撤达到复核线", body=f"当前回撤 {float(drawdown):.2f}%，达到你设置的 {rule.drawdown_limit:g}% 复核线。", severity="attention", evidence={"current_drawdown": drawdown, "review_line": rule.drawdown_limit, "sample": dd_summary})
        # Each period validates its own confirmed path; an older chain break
        # may leave full-history drawdown unavailable without suppressing it.
        if not price_stale:
            breached = [row for row in readings if row["breached"]]
            if breached:
                description = "；".join(f"{row['label']}下跌 {abs(row['return_pct']):.2f}%（复核线 {row['limit_pct']:g}%）" for row in breached)
                signals["period_loss"] = dict(title="区间跌幅达到复核线", body=description + "。", severity="attention", observed_on=date.fromisoformat(breached[0]["end_date"]), evidence={"periods": breached, "return_kind": (series.get("metadata") or {}).get("return_kind")})
        for note in session.scalars(select(InstrumentResearchNote).where(InstrumentResearchNote.instrument_id == iid, InstrumentResearchNote.note_type == "risk", InstrumentResearchNote.deleted_at.is_(None))):
            signals[f"note:{note.note_id}"] = dict(title=note.title, body=note.body or note.summary, severity="observation" if note.importance == "low" else "attention", observed_on=note.note_date, evidence={"note_id": note.note_id, "source": note.source_refs, "importance": note.importance, "recorded_on": note.note_date.isoformat()})
        existing = list(session.scalars(select(RiskCase).where(RiskCase.instrument_id == iid, RiskCase.trigger_active.is_(True),
            RiskCase.signal != "manual", ~RiskCase.signal.like("sector:%"))
            .order_by(RiskCase.case_id).with_for_update().execution_options(populate_existing=True)))
        active = {case.signal: case for case in existing}
        for signal, reading in signals.items():
            reading_date = reading.get("observed_on", observed_on)
            case = active.pop(signal, None)
            if case is None and signal == "data_coverage":
                case = _previous_coverage_case(session, iid, reading_date, reading["evidence"])
                if case is not None:
                    case.trigger_active = True
                    case.resolved_at = None
                    event(case, "reactivated", "同一数据缺口再次触发；原跟进状态和记录保留。")
            if case is None:
                case = RiskCase(case_id=uuid4().hex, instrument_id=iid, signal=signal, title=reading["title"], body=reading["body"], severity=reading["severity"], trigger_active=True, status="open", evidence_json=reading["evidence"], history_json=[], observed_on=reading_date)
                event(case, "triggered", reading["body"])
                session.add(case)
            elif case.evidence_json != reading["evidence"] or case.title != reading["title"] or case.body != reading["body"] or case.observed_on != reading_date:
                # Daily readings update the same event, without filling its follow-up history.
                revised = signal.startswith("note:") or case.evidence_json.get("review_line") != reading["evidence"].get("review_line")
                if signal == "period_loss":
                    previous_periods = {p["period"]: p["limit_pct"] for p in case.evidence_json.get("periods", [])}
                    current_periods = {p["period"]: p["limit_pct"] for p in reading["evidence"]["periods"]}
                    revised = previous_periods != current_periods
                    if current_periods.keys() - previous_periods.keys():
                        case.status = "open"
                case.title = reading["title"]
                case.body = reading["body"]
                case.severity = reading["severity"]
                case.evidence_json = reading["evidence"]
                case.observed_on = reading_date
                if revised:
                    event(case, "updated", reading["body"])
        for case in active.values():
            # Missing data cannot establish recovery from a market-risk trigger.
            if limited and case.signal == "drawdown_limit" and rule and rule.drawdown_limit is not None:
                continue
            if case.signal == "period_loss" and any(
                (price_stale or row["limitation"]) and row["limit_pct"] is not None
                for row in readings
                if row["period"] in {p["period"] for p in case.evidence_json.get("periods", [])}
            ):
                continue
            case.trigger_active = False
            case.resolved_at = now()
            event(case, "cleared", "当前复核规则不再触发；跟进记录保留。")
