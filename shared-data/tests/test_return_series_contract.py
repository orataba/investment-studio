from math import log

import pytest

from investment_studio_instrument_core import (
    analytical_return_quote_bases,
    build_return_intervals,
    return_interval_unavailable_reason,
    simple_to_log_return,
)


@pytest.mark.parametrize("kind,bases,settings,expected", [
    ("equity", ["close", "adjusted_close", "last"], {}, ["adjusted_close"]),
    ("etf", ["close", "last"], {"return_semantics": "total_return"}, []),
    ("private_fund", ["official_nav", "total_return_nav"], {}, ["total_return_nav"]),
    ("index", ["close"], {"return_semantics": "price_return"}, ["close"]),
    ("index", ["close"], {}, []),
    ("index", ["close"], {"return_semantics": "total_return"}, ["close"]),
    ("crypto", ["close"], {}, ["close"]),
    ("fx", ["spot"], {}, ["spot"]),
    ("cash", ["par"], {}, []),
])
def test_analytical_sources_respect_economic_asset_contract(kind, bases, settings, expected):
    assert analytical_return_quote_bases(
        instrument_type=kind, quote_bases=bases, source_settings=settings,
    ) == expected


def test_simple_and_log_represent_same_period_and_compound_differently():
    rows = build_return_intervals([
        {"date": "2026-01-01", "value": 100},
        {"date": "2026-01-08", "value": 110},
        {"date": "2026-01-15", "value": 99},
    ])
    assert rows[0]["start_date"] == "2026-01-01"
    assert rows[0]["date"] == "2026-01-08"  # retains weekly periods
    assert rows[0]["value"] == pytest.approx(.1)
    assert rows[1]["value"] == pytest.approx(-.1)
    assert (1 + rows[0]["value"]) * (1 + rows[1]["value"]) - 1 == pytest.approx(-.01)
    assert sum(row["log_return"] for row in rows) == pytest.approx(log(.99))


def test_invalid_observation_is_not_dropped_before_return_calculation():
    points = [{"date": f"2026-01-0{day}", "value": value}
              for day, value in enumerate([100, None, 110, 121], 1)]
    rows = build_return_intervals(points)
    assert [row["value"] for row in rows[:2]] == [None, None]
    assert rows[2]["value"] == pytest.approx(.1)
    assert all(row["reason"] == "invalid_level" for row in rows[:2])


def test_missing_session_masks_spanning_return_without_fabricating_zero():
    rows = build_return_intervals([
        {"date": "2026-01-01", "value": 100},
        {"date": "2026-01-05", "value": 105},
    ], break_dates=["2026-01-02"])
    assert rows[0]["value"] is None
    assert rows[0]["status"] == "unavailable"


def test_rebased_segments_never_generate_false_return_across_action():
    settings = {"total_return_segments": [
        {"start_date": "2026-01-01", "end_date": "2026-01-02"},
        {"start_date": "2026-01-03", "end_date": "2026-01-05"},
    ]}
    rows = build_return_intervals([
        {"date": "2026-01-02", "value": 105},
        {"date": "2026-01-03", "value": 100},
        {"date": "2026-01-05", "value": 101},
    ], source_settings=settings, quote_basis="adjusted_close")
    assert rows[0]["value"] is None
    assert rows[0]["reason"] == "total_return_segment_break"
    assert rows[1]["value"] == pytest.approx(.01)
    assert return_interval_unavailable_reason(
        "2026-01-02", "2026-01-03", source_settings=settings, quote_basis="close",
    ) is None  # raw valuation observations do not inherit TR availability


def test_empty_coverage_is_unavailable_and_interval_reason_is_retained():
    assert return_interval_unavailable_reason("2026-01-01", "2026-01-02",
        quote_basis="adjusted_close", source_settings={"total_return_segments": []},
    ) == "total_return_segment_break"
    settings = {"total_return_unavailable_intervals": [{
        "start_date": "2026-01-01", "end_date": "2026-01-02", "reason": "cash_currency_unknown",
    }]}
    assert return_interval_unavailable_reason("2026-01-01", "2026-01-02",
        quote_basis="adjusted_close", source_settings=settings,
    ) == "cash_currency_unknown"
    assert return_interval_unavailable_reason("2026-01-02", "2026-01-03",
        quote_basis="adjusted_close", source_settings=settings,
    ) is None


def test_duplicate_dates_are_rejected_and_log_loss_boundary_is_explicit():
    with pytest.raises(ValueError, match="unique"):
        build_return_intervals([
            {"date": "2026-01-01", "value": None}, {"date": "2026-01-01", "value": 100},
        ])
    assert simple_to_log_return(-1) is None
    assert simple_to_log_return(-1.1) is None
    with pytest.raises(ValueError, match="finite"):
        simple_to_log_return(float("nan"))
