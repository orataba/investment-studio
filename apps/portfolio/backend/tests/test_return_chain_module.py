from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import return_chain


def _snapshot(
    as_of_date: date | str,
    *,
    nav: float | None,
    daily_twr: float | str | None,
    valuation_coverage_state: str = "complete",
    return_coverage_state: str = "complete",
    coverage_state: str | None = None,
    stale_price_flag: bool = False,
    return_chain_continuous: bool = True,
) -> dict[str, object]:
    aggregate_coverage = coverage_state or (
        "complete"
        if valuation_coverage_state == return_coverage_state == "complete"
        else "partial"
    )
    return {
        "as_of_date": as_of_date,
        "coverage_state": aggregate_coverage,
        "valuation_coverage_state": valuation_coverage_state,
        "return_coverage_state": return_coverage_state,
        "stale_price_flag": stale_price_flag,
        "return_chain_continuous": return_chain_continuous,
        "nav": nav,
        "daily_twr": daily_twr,
        "cumulative_twr": 999.0,
        "drawdown": 999.0,
    }


def _return_projection(series: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "as_of_date": point["as_of_date"],
            "return_chain_continuous": point["return_chain_continuous"],
            "cumulative_twr": point["cumulative_twr"],
        }
        for point in series
    ]


def test_coverage_summary_and_compounding_golden_contract() -> None:
    snapshots = [
        _snapshot(date(2026, 1, 1), nav=100.0, daily_twr=0.10),
        _snapshot(
            date(2026, 1, 2),
            nav=100.0,
            daily_twr=None,
            valuation_coverage_state="complete",
            return_coverage_state="partial",
        ),
        _snapshot(
            date(2026, 1, 3),
            nav=90.0,
            daily_twr=-0.10,
            stale_price_flag=True,
        ),
    ]

    assert return_chain.snapshot_coverage_state(snapshots[1], "return") == "partial"
    assert return_chain.incomplete_coverage_state(has_content=True) == "partial"
    assert return_chain.merge_coverage_states(
        ["complete", "partial", "invalid"]
    ) == "partial"
    assert return_chain.has_complete_valuation(snapshots[0]) is True
    assert return_chain.is_reliable_valuation_snapshot(snapshots[2]) is False
    assert return_chain.aggregate_snapshot_coverage(snapshots, "return") == "partial"

    summary = return_chain.summarize_daily_snapshots(
        snapshots,
        requested_start_date=date(2026, 1, 1),
        requested_end_date=date(2026, 1, 3),
        effective_start_date=date(2026, 1, 1),
        effective_end_date=date(2026, 1, 2),
        as_of_clamp_reason="requested_end_stale_price",
    )
    assert summary == {
        "snapshot_count": 3,
        "complete_count": 2,
        "partial_count": 1,
        "unavailable_count": 0,
        "latest_complete_as_of_date": date(2026, 1, 2),
        "requested_start_date": date(2026, 1, 1),
        "requested_end_date": date(2026, 1, 3),
        "effective_start_date": date(2026, 1, 1),
        "effective_end_date": date(2026, 1, 2),
        "as_of_clamp_reason": "requested_end_stale_price",
    }
    assert return_chain.compound_daily_twr(
        snapshots + [{"daily_twr": float("inf")}]
    ) == pytest.approx(-0.01)
    assert return_chain.compound_daily_twr(snapshots) == pytest.approx(-0.01)


def test_reliable_window_clamp_and_start_boundary_golden_contract() -> None:
    snapshots = [
        _snapshot("invalid", nav=1.0, daily_twr=0.0),
        _snapshot("2025-12-31", nav=100.0, daily_twr=0.0),
        _snapshot("2026-01-01", nav=101.0, daily_twr=0.01),
        _snapshot(
            "2026-01-02",
            nav=102.0,
            daily_twr=0.01,
            stale_price_flag=True,
        ),
    ]
    kwargs = {
        "requested_start_date": date(2026, 1, 1),
        "requested_end_date": date(2026, 1, 2),
    }

    extracted = return_chain.resolve_reliable_snapshot_window(deepcopy(snapshots), **kwargs)

    assert extracted["effective_start_date"] == date(2026, 1, 1)
    assert extracted["effective_end_date"] == date(2026, 1, 1)
    assert extracted["as_of_clamp_reason"] == "requested_end_stale_price"
    assert [point["as_of_date"] for point in extracted["snapshots"]] == [
        date(2026, 1, 1),
    ]


def test_period_coverage_requires_a_complete_start_boundary() -> None:
    boundary = _snapshot(
        date(2026, 1, 1),
        nav=110.0,
        daily_twr=None,
        return_coverage_state="unavailable",
    )
    visible = [
        boundary,
        _snapshot(date(2026, 1, 2), nav=110.0, daily_twr=0.0),
    ]
    kwargs = {
        "requested_start_date": date(2026, 1, 1),
        "effective_end_date": date(2026, 1, 2),
        "inception_date": date(2025, 12, 31),
        "start_is_close_boundary": True,
    }

    extracted_complete = return_chain.period_return_coverage_state(
        visible, visible, **kwargs
    )
    extracted_missing = return_chain.period_return_coverage_state(
        visible[1:],
        visible[1:],
        **kwargs,
    )

    assert extracted_complete == "complete"
    assert extracted_missing == "partial"


def test_period_coverage_clamps_a_pre_inception_request_to_inception() -> None:
    visible = [
        _snapshot(date(2026, 1, 5), nav=100.0, daily_twr=0.0),
        _snapshot(date(2026, 1, 6), nav=101.0, daily_twr=0.01),
        _snapshot(date(2026, 1, 7), nav=99.99, daily_twr=-0.01),
    ]

    extracted = return_chain.period_return_coverage_state(
        visible,
        visible,
        requested_start_date=date(2026, 1, 1),
        effective_end_date=date(2026, 1, 7),
        inception_date=date(2026, 1, 5),
    )

    assert extracted == "complete"


def test_initial_valuation_anchor_starts_rebased_twr_without_a_synthetic_return() -> None:
    snapshots = [
        _snapshot(
            date(2026, 1, 1),
            nav=100.0,
            daily_twr=None,
            return_coverage_state="unavailable",
        ),
        _snapshot(date(2026, 1, 2), nav=110.0, daily_twr=0.10),
        _snapshot(date(2026, 1, 3), nav=104.5, daily_twr=-0.05),
    ]

    extracted_coverage = return_chain.period_return_coverage_state(
        snapshots,
        snapshots,
        requested_start_date=None,
        effective_end_date=date(2026, 1, 3),
        inception_date=date(2026, 1, 1),
    )
    extracted_series = _return_projection(return_chain.rebased_twr_series(snapshots))

    assert extracted_coverage == "complete"
    assert extracted_series[0]["return_chain_continuous"] is True
    assert extracted_series[0]["cumulative_twr"] == pytest.approx(0.0)
    assert extracted_series[1]["cumulative_twr"] == pytest.approx(0.10)
    assert extracted_series[2]["cumulative_twr"] == pytest.approx(0.045)


def test_gap_reanchor_does_not_reconnect_a_broken_return_chain() -> None:
    snapshots = [
        _snapshot(date(2026, 1, 1), nav=110.0, daily_twr=0.10),
        _snapshot(
            date(2026, 1, 2),
            nav=None,
            daily_twr=None,
            valuation_coverage_state="unavailable",
            return_coverage_state="unavailable",
            return_chain_continuous=False,
        ),
        _snapshot(
            date(2026, 1, 3),
            nav=210.0,
            daily_twr=None,
            return_coverage_state="partial",
            return_chain_continuous=False,
        ),
        _snapshot(
            date(2026, 1, 4),
            nav=220.5,
            daily_twr=0.05,
            return_chain_continuous=False,
        ),
    ]

    extracted = _return_projection(return_chain.rebased_twr_series(snapshots))

    assert extracted[0]["cumulative_twr"] == pytest.approx(0.10)
    assert [point["return_chain_continuous"] for point in extracted] == [
        True,
        False,
        False,
        False,
    ]
    assert [point["cumulative_twr"] for point in extracted[1:]] == [None, None, None]
