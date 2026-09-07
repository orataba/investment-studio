from datetime import UTC, datetime

from investment_studio_instrument_core.db_models import Instrument, InstrumentReferenceObservation
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
    return InstrumentReferenceObservation(observation_id=observation_id, instrument_id="xlk", collected_at=collected_at,
        value_json={"fetched_at": collected_at.isoformat(), "sections": {"sector_market_data": {
            "holdings": [{"holding_symbol": symbol, "holding_name": item["name"], "holding_type": "equity",
                "company_profile": {"reporting_currency_source": item.get("reporting_currency_source")},
                "weight_percent": item["weight_percent"], "as_of_date": "2026-09-04", "snapshot_date": collected_at.date().isoformat(),
                "annual_estimates": item["annual_estimates"], "quarterly_estimates": item["quarterly_estimates"]}
                for symbol, item in companies.items()]}}})


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


def read_without_writes(session, iid, **kwargs):
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = read_estimate_evidence(session, iid, **kwargs)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert not session.new and not session.dirty and not session.deleted
    return result


def test_two_collections_are_compared_without_research_and_as_of_excludes_future(client):
    with get_session_factory()() as session:
        session.add(Instrument(instrument_id="xlk", instrument_name="Technology ETF", instrument_type="etf",
                               currency="USD", exchange_code="ARCX", quote_selection_policy_json={}))
        session.add(ResearchTopic(topic_id="us-sector-daily-review", title="行业检查"))
        session.flush()
        session.add(ResearchEntry(entry_id="published-history", topic_id="us-sector-daily-review", kind="analysis",
            title="已发布研究证据", context_json={"sector_company_data": {"xlk": {"AAA": company(estimate())}}}))
        session.commit()
    with get_session_factory()() as session:
        # Published research remains evidence, but is never fabricated into a collection.
        absent = read_without_writes(session, " XLK ")
        assert absent["supported"] and absent["status"] == "no_snapshot"
        unsupported = read_without_writes(session, "512880")
        assert not unsupported["supported"] and unsupported["status"] == "unsupported"
        old = snapshot("old-estimates", {"AAA": company(estimate(clock=OLD_CLOCK))}, day=5)
        session.add(old)
        session.commit()
    with get_session_factory()() as session:
        baseline = read_without_writes(session, "xlk")
        assert baseline["status"] == "baseline" and baseline["changes"] == []
        assert baseline["current_snapshot"]["observation_id"] == "old-estimates" and baseline["previous_snapshot"] is None
        assert baseline["history_available_from"] == "2026-09-05T09:00:00+00:00"
        session.add(snapshot("current-estimates", {"AAA": company(estimate(value=110))}))
        session.add(snapshot("future-estimates", {"AAA": company(estimate(value=900, clock="2026-09-07T08:00:00+00:00"))}, day=7))
        session.commit()
    with get_session_factory()() as session:
        observed = read_without_writes(session, " XLK ", as_of=datetime(2026, 9, 6, 9, tzinfo=UTC))
        assert observed["status"] == "comparable" and len(observed["changes"]) == 1
        assert observed["current_snapshot"]["observation_id"] == "current-estimates"
        assert observed["previous_snapshot"]["observation_id"] == "old-estimates"
        assert observed["changes"][0]["delta"] == 10
        assert observed["source_id"] == "estimates:current-estimates:xlk"
        explicit = read_without_writes(session, "xlk", as_of=datetime(2026, 9, 6, 8, tzinfo=UTC))
        assert explicit["status"] == "baseline" and explicit["current_snapshot"]["observation_id"] == "old-estimates"
        before_history = read_without_writes(session, "xlk", as_of=datetime(2026, 9, 5, 8, tzinfo=UTC))
        assert before_history["status"] == "no_snapshot"
        assert session.get(ResearchEntry, "published-history").context_json["sector_company_data"]["xlk"]["AAA"]["annual_estimates"][0]["revenue_avg"] == 100
        with pytest.raises(ValueError, match="timezone"):
            read_estimate_evidence(session, "xlk", as_of=datetime(2026, 9, 6))
