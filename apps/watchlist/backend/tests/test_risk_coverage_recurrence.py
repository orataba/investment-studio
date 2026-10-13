"""A repeated known data gap keeps its case identity and human follow-up."""
from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import select

from watchlist_app.db.models import InstrumentDetail, InstrumentRiskReadModel, InstrumentSummaryReadModel
from watchlist_app.db.models.workbench import RiskCase, RiskReviewRule
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.risk_workbench import refresh_risk_cases


IID = "coverage-recurrence"
FRESHNESS = {
    "data_freshness_status": "stale",
    "latest_observation_date": "2026-09-03",
    "expected_latest_date": "2026-09-04",
    "last_fact_update_at": "2026-09-04T10:04:50.119154Z",
    "last_recalculated_at": "2026-09-06T00:00:41Z",
    "last_successful_snapshot_at": "2026-09-06T00:00:41Z",
    "observation_lag_days": 3,
}
QUALITY = {"status": "ready", "gap_count": 0, "gap_detection_basis": "market_calendar:XSHG", "note": None}


def observe(freshness=None, *, quality=None, drawdown=-1):
    freshness = deepcopy(FRESHNESS if freshness is None else freshness)
    with get_session_factory()() as session:
        if session.get(InstrumentDetail, IID) is None:
            session.add(InstrumentDetail(instrument_id=IID, instrument_type="private_fund",
                detail_view_type="private_fund", instrument_name="Coverage fixture", metadata_json={}))
            session.flush()
        for model, payload in (
            (InstrumentSummaryReadModel, {"freshness": freshness}),
            (InstrumentRiskReadModel, {"current_drawdown": drawdown, "data_quality": deepcopy(QUALITY if quality is None else quality)}),
        ):
            row = session.get(model, IID)
            if row is None:
                row = model(instrument_id=IID, payload_json={}, data_freshness_status="fresh")
                session.add(row)
            row.payload_json = payload
            row.data_freshness_status = freshness["data_freshness_status"]
        refresh_risk_cases(session, [IID])
        session.commit()


def cases(signal="data_coverage"):
    with get_session_factory()() as session:
        return list(session.scalars(select(RiskCase).where(RiskCase.instrument_id == IID, RiskCase.signal == signal)
            .order_by(RiskCase.created_at, RiskCase.case_id)))


def clear():
    observe({**FRESHNESS, "data_freshness_status": "fresh"})


@pytest.mark.parametrize("pm_status", ["investigating", "handled"])
def test_same_gap_reactivation_preserves_case_and_pm_follow_up(client, pm_status):
    observe()
    original = cases()[0]
    with get_session_factory()() as session:
        case = session.get(RiskCase, original.case_id)
        case.status = pm_status
        case.follow_up_date = date(2026, 9, 9)
        case.history_json = [*case.history_json, {"at": "2026-09-06T01:00:00Z", "action": pm_status, "detail": "已联系管理人核对"}]
        session.commit()
    clear()
    cleared = cases()[0]
    assert not cleared.trigger_active and cleared.resolved_at is not None
    retained = deepcopy(cleared.history_json)
    later = {**FRESHNESS, "last_recalculated_at": "2026-09-07T16:00:11Z",
        "last_successful_snapshot_at": "2026-09-07T16:00:11Z", "observation_lag_days": 5, "release_lag_trading_days": 1}
    observe(later)
    [reactivated] = cases()
    assert reactivated.case_id == original.case_id
    assert reactivated.trigger_active and reactivated.resolved_at is None
    assert reactivated.status == pm_status and reactivated.follow_up_date == date(2026, 9, 9)
    assert reactivated.history_json[:-1] == retained
    assert reactivated.history_json[-1]["action"] == "reactivated"
    assert reactivated.evidence_json["freshness"] == later
    observe(later)
    assert cases()[0].history_json == reactivated.history_json  # Repeated refresh adds no duplicate transition.


@pytest.mark.parametrize("changed", ["observed", "expected", "revision", "quality", "unidentified"])
def test_different_or_unidentified_gap_creates_separate_case(client, changed):
    observe()
    original = cases()[0]
    clear()
    freshness, quality = deepcopy(FRESHNESS), deepcopy(QUALITY)
    if changed == "observed":
        freshness["latest_observation_date"] = "2026-09-02"
    elif changed == "expected":
        freshness["expected_latest_date"] = "2026-09-07"
    elif changed == "revision":
        freshness["last_fact_update_at"] = "2026-09-05T10:04:50Z"
    elif changed == "quality":
        quality.update(status="partial_missing_observations", gap_count=1)
    else:
        freshness.pop("last_fact_update_at")
    observe(freshness, quality=quality)
    result = cases()
    assert len(result) == 2
    assert next(case for case in result if case.case_id == original.case_id).trigger_active is False
    assert next(case for case in result if case.trigger_active).history_json[0]["action"] == "triggered"


def test_existing_duplicate_history_is_retained_and_latest_case_reused(client):
    observe()
    first = cases()[0]
    clear()
    with get_session_factory()() as session:
        session.add(RiskCase(case_id="later-existing-case", instrument_id=IID, signal="data_coverage",
            title=first.title, body=first.body, severity="coverage", trigger_active=False, status="handled",
            observed_on=first.observed_on, evidence_json=deepcopy(first.evidence_json),
            history_json=[{"at": "2026-09-07T16:00:11Z", "action": "triggered"}, {"at": "2026-09-08T13:26:56Z", "action": "cleared"}],
            created_at=first.created_at.replace(year=first.created_at.year + 1)))
        session.commit()
    observe()
    result = cases()
    assert len(result) == 2
    assert [case.case_id for case in result if case.trigger_active] == ["later-existing-case"]
    assert next(case for case in result if case.case_id == first.case_id).history_json[-1]["action"] == "cleared"


def test_coverage_reuse_does_not_change_manual_or_price_case_lifecycles(client):
    observe()
    with get_session_factory()() as session:
        session.add(RiskCase(case_id="manual-gap", instrument_id=IID, signal="manual", title="人工记录",
            severity="coverage", trigger_active=False, status="handled", observed_on=date(2026, 9, 3),
            evidence_json={"freshness": FRESHNESS, "data_quality": QUALITY}, history_json=[{"action": "handled"}]))
        session.add(RiskReviewRule(instrument_id=IID, drawdown_limit=10))
        session.commit()
    fresh = {**FRESHNESS, "data_freshness_status": "fresh"}
    observe(fresh, drawdown=-12)
    original_price = cases("drawdown_limit")[0]
    observe(fresh, drawdown=-2)
    observe(fresh, drawdown=-12)
    prices = cases("drawdown_limit")
    assert len(prices) == 2
    assert next(case for case in prices if case.trigger_active).case_id != original_price.case_id
    observe()
    [coverage] = cases()
    assert coverage.trigger_active and coverage.history_json[-1]["action"] == "reactivated"
    [manual] = cases("manual")
    assert manual.case_id == "manual-gap" and not manual.trigger_active and manual.status == "handled"
    assert manual.history_json == [{"action": "handled"}]
