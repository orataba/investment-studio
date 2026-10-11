"""Financial regressions for declared-cash reinvestment and bounded coverage."""
from decimal import Decimal
import json

import pytest

from studio_market.futu.returns import derive_returns


def raw(days, closes, symbol="HK.02822"):
    return [dict(symbol=symbol, date=day, close=close, stock_id=82, source_id=f"raw:{day}", batch_id="prices")
            for day, close in zip(days, closes)]


def snapshot(events=(), *, stock_type="STOCK", **changes):
    return dict(stock_type=stock_type, coverage_status="provider_reported" if events else "covered_no_actions",
                coverage_basis="provider_reported_since_listing", coverage_start="1980-01-01", coverage_end="2026-10-07",
                events_json=json.dumps(events), event_count=len(events), source_id="actions:v1", batch_id="events", **changes)


def cash(day, amount="1", currency="HKD", **changes):
    return dict(ex_div_date=day, action_types=["DIVIDEND"], per_cash_div=float(amount),
                desc_sc=f"1股派股息{amount}{currency}", split_ratio=0, **changes)


def derive(rows, actions):
    return derive_returns(rows, actions, currency="HKD", calendar_name="XHKG")


def test_dividend_reinvestment_uses_ex_date_close_not_subtractive_adjustment():
    rows = raw(["2026-09-07", "2026-09-08", "2026-09-09"], [100, 90, 99])
    result = derive(rows, snapshot([cash("2026-09-08", "10")]))
    assert [float(row["adjusted_close"]) for row in result.rows] == pytest.approx([100, 100, 110])
    assert Decimal(result.rows[1]["adjustment_factor"]) == pytest.approx(Decimal(100) / Decimal(90))
    assert result.metadata["total_return_segments"] == [{"start_date": "2026-09-07", "end_date": "2026-09-09"}]
    assert result.metadata["total_return_unavailable_intervals"] == []
    assert result.rows[1]["previous_raw_source_id"] == "raw:2026-09-07"
    assert result.metadata["total_return_tax_treatment"] == "no_investor_tax_deduction"


def test_aapl_actual_2020_split_uses_contemporaneous_cash_and_share_count():
    rows = raw(["2020-08-27", "2020-08-28", "2020-08-31", "2020-09-01"], [500, 499.23, 129.04, 134.18], "US.AAPL")
    event = dict(ex_div_date="2020-08-31", action_types=["SPLIT"], split_base=1, split_ert=4,
                 split_ratio=.25, per_cash_div=0)
    result = derive_returns(rows, snapshot([event]), currency="USD", calendar_name="XNYS")
    assert Decimal(result.rows[2]["gross_return"]) == pytest.approx(Decimal("129.04") * 4 / Decimal("499.23"))
    assert Decimal(result.rows[2]["adjusted_close"]) == Decimal("516.16")
    assert Decimal(result.rows[3]["adjustment_factor"]) == 4


@pytest.mark.parametrize("mutate,reason", [
    (lambda event: event.update(desc_sc="派息"), "unverified_dividend_currency_or_share_basis"),
    (lambda event: event.update(desc_sc="1股派股息1USD"), "dividend_currency_requires_verified_fx"),
    (lambda event: event.update(per_cash_div=2), "dividend_amount_conflicts_with_statement"),
    (lambda event: event.update(action_types=["DIVIDEND", "SPLIT"]), "unsupported_or_combined_corporate_action"),
    (lambda event: event.update(special_dividend=1), "unclassified_corporate_action_component"),
])
def test_unknown_cash_never_becomes_zero_and_later_segment_can_resume(mutate, reason):
    event = cash("2026-09-08")
    mutate(event)
    result = derive(raw(["2026-09-07", "2026-09-08", "2026-09-09"], [100, 90, 99]), snapshot([event]))
    assert result.metadata["total_return_segments"] == [
        {"start_date": "2026-09-07", "end_date": "2026-09-07"},
        {"start_date": "2026-09-08", "end_date": "2026-09-09"}]
    assert result.metadata["total_return_unavailable_intervals"] == [
        {"start_date": "2026-09-07", "end_date": "2026-09-08", "reason": reason}]
    assert result.rows[1]["gross_return"] is None
    assert Decimal(result.rows[2]["gross_return"]) == Decimal("1.1")


def test_empty_stock_is_reported_no_events_but_empty_etf_is_unknown():
    rows = raw(["2026-09-07", "2026-09-08"], [100, 101])
    assert derive(rows, snapshot()).rows[-1]["gross_return"] == "1.01"
    for evidence in (None, snapshot(stock_type="ETF"), {**snapshot(), "events_json": "null"}):
        result = derive(rows, evidence)
        assert all(row["adjusted_close"] is None for row in result.rows)
        assert result.metadata["total_return_segments"] == []
        assert result.metadata["total_return_unavailable_intervals"]


def test_missing_session_ex_date_prevents_cumulative_or_daily_return_guess():
    result = derive(raw(["2026-09-07", "2026-09-09", "2026-09-10"], [100, 95, 96]),
                    snapshot([cash("2026-09-08")]))
    assert result.rows[1]["gross_return"] is None
    assert len(result.metadata["total_return_segments"]) == 2
    assert result.metadata["total_return_unavailable_intervals"][0]["reason"] == "missing_market_session_close"


def test_action_correction_rebuilds_old_factors_and_retraction_is_explicit():
    rows = raw(["2026-09-07", "2026-09-08", "2026-09-09"], [100, 90, 99])
    old = derive(rows, snapshot([cash("2026-09-08", "10")]))
    corrected = derive(rows, snapshot([cash("2026-09-08", "5")]))
    assert Decimal(old.rows[-1]["adjusted_close"]) == pytest.approx(Decimal(110))
    assert Decimal(corrected.rows[-1]["adjusted_close"]) == Decimal("104.5")
    withdrawn = derive(rows, {**snapshot(), "coverage_status": "unknown_empty_etf", "stock_type": "ETF"})
    assert len(withdrawn.rows) == len(rows)
    assert all(row["adjustment_factor"] is None for row in withdrawn.rows)


def test_stale_action_coverage_does_not_extend_to_new_prices():
    result = derive(raw(["2026-09-07", "2026-09-08", "2026-09-09"], [100, 90, 99]),
                    {**snapshot(), "coverage_end": "2026-09-08"})
    assert result.rows[-1]["adjusted_close"] is None
    assert result.metadata["total_return_segments"][0]["end_date"] == "2026-09-08"


def test_exchange_confirmed_2023_hk_closures_do_not_create_false_missing_sessions():
    dates = ["2023-08-31", "2023-09-04", "2023-09-05", "2023-09-06", "2023-09-07", "2023-09-11"]
    result = derive(raw(dates, [100, 101, 102, 103, 104, 105]), snapshot())
    assert result.metadata["total_return_unavailable_intervals"] == []
    assert result.metadata["total_return_segments"] == [{"start_date": dates[0], "end_date": dates[-1]}]
    assert [Decimal(row["adjusted_close"]) for row in result.rows] == list(map(Decimal, [100, 101, 102, 103, 104, 105]))
