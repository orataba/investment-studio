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


def refresh_risk_cases(session: Session, instrument_ids: list[str] | None = None):
    session.flush()
    query = select(InstrumentDetail).where(InstrumentDetail.is_active.is_(True))
    if instrument_ids is not None:
        query = query.where(InstrumentDetail.instrument_id.in_(instrument_ids))
    # Notes, manual thresholds and recalculation can refresh the same instrument.
    # Serialize first creation as well as updates; case locks retain PM history.
    for instrument in session.scalars(query.order_by(InstrumentDetail.instrument_id).with_for_update()
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
        limited = not risk or quality.get("status") != "ready" or freshness.get("data_freshness_status", summary.data_freshness_status if summary else "missing") != "fresh"
        chart = session.get(InstrumentChartReadModel, iid)
        series = (chart.payload_json.get("research_returns") or {}) if chart else {}
        rule = session.get(RiskReviewRule, iid)
        if not limited and (rule is None or not rule.period_limits_json):
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
            # A sample low alone has no materiality threshold. Keep it in readings.
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
                (limited or row["limitation"]) and row["limit_pct"] is not None
                for row in readings
                if row["period"] in {p["period"] for p in case.evidence_json.get("periods", [])}
            ):
                continue
            case.trigger_active = False
            case.resolved_at = now()
            event(case, "cleared", "当前复核规则不再触发；跟进记录保留。")
