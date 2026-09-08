from datetime import UTC, datetime

from investment_studio_instrument_core.db_models import Instrument
import pytest
from sqlalchemy import event

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.sector_estimates import compare_estimate_snapshots, read_estimate_evidence


OLD_CLOCK = "2026-09-05T08:00:00+00:00"
NEW_CLOCK = "2026-09-06T08:00:00+00:00"


def estimate(period="2026-12-31", *, value=100, eps=None, clock=NEW_CLOCK, **overrides):
    return {"target_period_end": period, "revenue_avg": value, "eps_avg": eps, "currency": "USD",
            "collected_at": clock, "num_analysts_revenue": 5, "num_analysts_eps": 4,
            "source_dataset": "fmp_estimates", "raw_sha256": "current-raw" if clock == NEW_CLOCK else "previous-raw",
            **overrides}


def company(*annual, weight=10, quarterly=(), currency_source=None):
    return {"name": "Fixture company", "weight_percent": weight,
            "reporting_currency_source": currency_source,
            "annual_estimates": list(annual), "quarterly_estimates": list(quarterly)}


def snapshot(observation_id, companies, *, day=6):
    collected_at = datetime(2026, 9, day, 9, tzinfo=UTC)
    return {"observation_id": observation_id, "companies": companies,
            "collected_at": collected_at.isoformat(), "holdings_as_of": "2026-09-04",
            "holdings_observed_on": collected_at.date().isoformat()}


def test_same_period_revisions_use_current_exposure_once_and_explain_analyst_changes():
    old = snapshot("old", {
        "AAA": company(estimate(eps=10, clock=OLD_CLOCK, currency_status="inferred_from_reporting_currency"), estimate("2027-12-31", clock=OLD_CLOCK),
                       weight=40, quarterly=[estimate(clock=OLD_CLOCK)], currency_source={"statement_date": "2026-03-31"}),
        "BBB": company(estimate(clock=OLD_CLOCK), weight=50),
    }, day=5)
    current = snapshot("current", {
        "AAA": company(estimate(value=110, eps=12, num_analysts_revenue=6, num_analysts_eps=7, currency_status="inferred_from_reporting_currency"),
                       estimate("2027-12-31", value=150), weight=12,
                       quarterly=[estimate(value=120)], currency_source={"statement_date": "2026-06-30"}),
        "BBB": company(estimate(), weight=3),
    })
    result = compare_estimate_snapshots("xlk", current, old)
    changes = {(row["symbol"], row["frequency"], row["target_period_end"], row["metric"]): row
               for row in result["changes"]}
    assert result["status"] == "comparable" and len(changes) == 4
    annual = changes[("AAA", "annual", "2026-12-31", "revenue_avg")]
    quarter = changes[("AAA", "quarter", "2026-12-31", "revenue_avg")]
    eps = changes[("AAA", "annual", "2026-12-31", "eps_avg")]
    assert (annual["delta"], annual["delta_pct"], annual["weight_percent"]) == pytest.approx((10, 10, 12))
    assert (quarter["delta"], quarter["delta_pct"]) == pytest.approx((20, 20))
    assert (eps["delta"], eps["delta_pct"]) == pytest.approx((2, 20))
    assert annual["current_num_analysts"] == 6 and annual["previous_num_analysts"] == 5
    assert annual["analyst_count_changed"] is True and quarter["analyst_count_changed"] is False
    assert annual["currency"] == "USD" and annual["unit"] == "currency"
    assert annual["current_currency_status"] == annual["previous_currency_status"] == "inferred_from_reporting_currency"
    assert annual["current_currency_source"] == {"statement_date": "2026-06-30"}
    assert annual["previous_currency_source"] == {"statement_date": "2026-03-31"}
    assert eps["unit"] == "currency_per_share"
    assert annual["raw_sha256"] == "current-raw" and annual["previous_raw_sha256"] == "previous-raw"
    coverage = next(row for row in result["coverage"]["metrics"]
                    if row["frequency"] == "annual" and row["metric"] == "revenue_avg")
    assert coverage["current_company_count"] == coverage["comparable_company_count"] == 2
    assert coverage["current_weight_pct"] == coverage["comparable_weight_pct"] == 15
    assert coverage["changed_company_count"] == 1 and coverage["changed_weight_pct"] == 12
    assert result["coverage"]["equity_weight_pct"] == 15
    assert result["current_snapshot"]["holdings_as_of"] == "2026-09-04"


def test_rollover_new_coverage_unverified_currency_and_unchanged_clock_are_not_revisions():
    old = snapshot("old", {
        "ROLL": company(estimate(clock=OLD_CLOCK)),
        "METRIC": company(estimate(value=None, clock=OLD_CLOCK)),
        "UNKNOWN": company(estimate(currency=None, clock=OLD_CLOCK)),
        "FX": company(estimate(clock=OLD_CLOCK)),
        "STALE": company(estimate(clock=OLD_CLOCK)),
        "ANALYSTS": company(estimate(clock=OLD_CLOCK, num_analysts_revenue=0)),
    }, day=5)
    current = snapshot("current", {
        "ROLL": company(estimate("2027-12-31", value=110)),
        "NEW": company(estimate(value=110)),
        "METRIC": company(estimate(value=110)),
        "UNKNOWN": {**company(estimate(value=110, currency=None)), "currency": "USD"},
        "FX": company(estimate(value=110, currency="EUR")),
        "STALE": company(estimate(value=110, clock=OLD_CLOCK)),
        "ANALYSTS": company(estimate(value=110)),
    })
    result = compare_estimate_snapshots("xlk", current, old)
    assert result["status"] == "limited" and result["changes"] == []
    assert {(row["symbol"], row["reason"]) for row in result["unmatched"]} == {
        ("ROLL", "new_period_coverage"), ("ROLL", "missing_current_coverage"),
        ("NEW", "new_company_coverage"), ("METRIC", "new_metric_coverage"),
    }
    assert {(row["symbol"], row["reason"]) for row in result["observations"]} == {
        ("UNKNOWN", "currency_unverified"), ("FX", "currency_changed"),
        ("STALE", "source_observation_not_newer"), ("ANALYSTS", "analyst_coverage_added"),
    }
    assert all("delta" not in row and "delta_pct" not in row for row in result["observations"])
    unknown = next(row for row in result["observations"] if row["symbol"] == "UNKNOWN")
    assert unknown["currency"] is None and unknown["previous_currency"] is None
    metric = next(row for row in result["coverage"]["metrics"]
                  if row["frequency"] == "annual" and row["metric"] == "revenue_avg")
    assert metric["unknown_currency_company_count"] == 1 and metric["unknown_currency_weight_pct"] == 10
    assert metric["comparable_company_count"] == metric["changed_company_count"] == 0


def test_negative_and_zero_eps_bases_keep_absolute_change_without_percentage():
    old = snapshot("old", {"LOSS": company(estimate(value=None, eps=-2, clock=OLD_CLOCK)),
                           "ZERO": company(estimate(value=None, eps=0, clock=OLD_CLOCK))}, day=5)
    current = snapshot("current", {"LOSS": company(estimate(value=None, eps=-1)),
                                   "ZERO": company(estimate(value=None, eps=1))})
    changes = compare_estimate_snapshots("xlk", current, old)["changes"]
    assert len(changes) == 2 and {row["symbol"] for row in changes} == {"LOSS", "ZERO"}
    assert all(row["metric"] == "eps_avg" and row["delta"] == 1 and row["delta_pct"] is None
               and row["percent_change_status"] == "nonpositive_base" for row in changes)
