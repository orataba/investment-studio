"""Risk observations are materialized by the existing recalc worker, never by GETs."""
from datetime import UTC, date, datetime
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from watchlist_app.db.models import InstrumentDetail, InstrumentRiskReadModel, InstrumentSummaryReadModel, InstrumentChartReadModel
from watchlist_app.db.models.research import InstrumentResearchNote
from watchlist_app.db.models.workbench import RiskCase, RiskReviewRule
from watchlist_app.services.price_risk import initial_price_limits, period_loss_readings, record_rule_revision, rule_configuration, rule_version


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
                previous = rule_configuration(rule)
                rule.period_limits_json = limits
                rule.calibration_json = {**(rule.calibration_json or {}), **calibration}
                record_rule_revision(rule, previous=previous, actor=None,
                                     reason="按既有方法初始化后固定的跌幅复核线", at=now())
        readings = period_loss_readings(series, (rule.period_limits_json or {}) if rule else {})
        signals: dict[str, dict] = {}
        severities = (rule.calibration_json or {}).get("severities") or {} if rule else {}
        provenance = {"metric_snapshot_id": payload.get("metric_snapshot_id"), "rule_version": rule_version(rule)}
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
                severe = severities.get("drawdown") == "severe"
                signals["drawdown_limit"] = dict(title="回撤达到复核线", body=f"当前回撤 {float(drawdown):.2f}%，达到你设置的 {rule.drawdown_limit:g}% 复核线。", severity="severe" if severe else "attention", evidence={"current_drawdown": drawdown, "review_line": rule.drawdown_limit, "sample": dd_summary, "mandatory_notice": severe, **provenance})
        # Each period validates its own confirmed path; an older chain break
        # may leave full-history drawdown unavailable without suppressing it.
        if not price_stale:
            breached = [{**row, **provenance, "evaluation_status": "triggered"} for row in readings if row["breached"]]
            if breached:
                description = "；".join(f"{row['label']}下跌 {abs(row['return_pct']):.2f}%（复核线 {row['limit_pct']:g}%）" for row in breached)
                severe = any(severities.get(row["period"]) == "severe" for row in breached)
                signals["period_loss"] = dict(title="区间跌幅达到复核线", body=description + "。", severity="severe" if severe else "attention", observed_on=date.fromisoformat(breached[0]["end_date"]), evidence={"periods": breached, "return_kind": (series.get("metadata") or {}).get("return_kind"), "mandatory_notice": severe, **provenance})
        for note in session.scalars(select(InstrumentResearchNote).where(InstrumentResearchNote.instrument_id == iid, InstrumentResearchNote.note_type == "risk", InstrumentResearchNote.deleted_at.is_(None))):
            signals[f"note:{note.note_id}"] = dict(title=note.title, body=note.body or note.summary, severity="observation" if note.importance == "low" else "attention", observed_on=note.note_date, evidence={"note_id": note.note_id, "source": note.source_refs, "importance": note.importance, "recorded_on": note.note_date.isoformat()})
        existing = list(session.scalars(select(RiskCase).where(RiskCase.instrument_id == iid, RiskCase.trigger_active.is_(True),
            RiskCase.signal != "manual", ~RiskCase.signal.like("sector:%"))
            .order_by(RiskCase.case_id).with_for_update().execution_options(populate_existing=True)))
        active = {case.signal: case for case in existing}
        # One unavailable horizon cannot disappear merely because another
        # horizon has a newly evaluable trigger. Preserve each independent line.
        prior_period = active.get("period_loss")
        if prior_period is not None and "period_loss" in signals:
            current_periods = signals["period_loss"]["evidence"]["periods"]
            present = {row["period"] for row in current_periods}
            pending = {row["period"]: row for row in readings
                       if row["limit_pct"] is not None and (price_stale or row["limitation"])}
            retained = [{**row, "evaluation_status": "unavailable", "limitation": pending[row["period"]]["limitation"] or "行情新鲜度不足"}
                        for row in prior_period.evidence_json.get("periods", [])
                        if row["period"] in pending and row["period"] not in present]
            if retained:
                current_periods.extend(retained)
                signals["period_loss"]["body"] += "另有" + "、".join(f"{row['label']}（截至 {row['end_date']}）" for row in retained) + "此前有效触发因数据不足尚不能确认恢复。"
                severe = any(severities.get(row["period"]) == "severe" for row in current_periods)
                signals["period_loss"]["severity"] = "severe" if severe else "attention"
                signals["period_loss"]["evidence"]["mandatory_notice"] = severe
        for signal, reading in signals.items():
            reading_date = reading.get("observed_on", observed_on)
            case = active.pop(signal, None)
            if case is not None and signal in {"drawdown_limit", "period_loss"}:
                reading["evidence"]["trigger_version"] = case.evidence_json.get("trigger_version") or f"{case.case_id}:1"
            if case is None and signal == "data_coverage":
                case = _previous_coverage_case(session, iid, reading_date, reading["evidence"])
                if case is not None:
                    case.trigger_active = True
                    case.resolved_at = None
                    event(case, "reactivated", "同一数据缺口再次触发；原跟进状态和记录保留。")
            if case is None:
                case = RiskCase(case_id=uuid4().hex, instrument_id=iid, signal=signal, title=reading["title"], body=reading["body"], severity=reading["severity"], trigger_active=True, status="open", evidence_json=reading["evidence"], history_json=[], observed_on=reading_date)
                event(case, "triggered", reading["body"])
                if signal in {"drawdown_limit", "period_loss"}:
                    case.evidence_json = {**case.evidence_json, "trigger_version": f"{case.case_id}:1"}
                session.add(case)
            elif case.evidence_json != reading["evidence"] or case.title != reading["title"] or case.body != reading["body"] or case.observed_on != reading_date:
                # Daily readings update the same event, without filling its follow-up history.
                revised = (signal.startswith("note:") or case.severity != reading["severity"]
                           or case.evidence_json.get("rule_version") != reading["evidence"].get("rule_version")
                           or case.evidence_json.get("review_line") != reading["evidence"].get("review_line"))
                if signal == "period_loss":
                    previous_periods = {p["period"]: p["limit_pct"] for p in case.evidence_json.get("periods", [])}
                    current_periods = {p["period"]: p["limit_pct"] for p in reading["evidence"]["periods"]}
                    revised = revised or previous_periods != current_periods
                    if current_periods.keys() - previous_periods.keys():
                        case.status = "open"
                case.title = reading["title"]
                case.body = reading["body"]
                case.severity = reading["severity"]
                case.evidence_json = reading["evidence"]
                case.observed_on = reading_date
                if revised:
                    event(case, "updated", reading["body"])
                    if signal in {"drawdown_limit", "period_loss"}:
                        case.evidence_json = {**case.evidence_json, "trigger_version": f"{case.case_id}:{len(case.history_json)}"}
        for case in active.values():
            # Missing data cannot establish recovery from a market-risk trigger.
            retain = limited and case.signal == "drawdown_limit" and rule and rule.drawdown_limit is not None
            retain = retain or (case.signal == "period_loss" and any(
                (price_stale or row["limitation"]) and row["limit_pct"] is not None
                for row in readings
                if row["period"] in {p["period"] for p in case.evidence_json.get("periods", [])}
            ))
            if retain:
                keys = ["drawdown"] if case.signal == "drawdown_limit" else [
                    row["period"] for row in case.evidence_json.get("periods", [])
                    if (rule.period_limits_json or {}).get(row["period"]) is not None]
                severe = any(severities.get(key) == "severe" for key in keys)
                # A source gap preserves the last risk fact, not superseded
                # notification policy. Explicit severity edits still take effect.
                if case.evidence_json.get("rule_version") != rule_version(rule) or bool(case.evidence_json.get("mandatory_notice")) != severe:
                    prior = case.evidence_json
                    case.evidence_json = {**prior, "mandatory_notice": severe, "rule_version": rule_version(rule),
                                          "observed_rule_version": prior.get("observed_rule_version") or prior.get("rule_version")}
                    case.severity = "severe" if severe else "attention"
                    event(case, "updated", "数据不足，保留此前有效风险；严重性按当前规则版本执行。")
                    case.evidence_json = {**case.evidence_json, "trigger_version": f"{case.case_id}:{len(case.history_json)}"}
                continue
            case.trigger_active = False
            case.resolved_at = now()
            event(case, "cleared", "当前复核规则不再触发；跟进记录保留。")
