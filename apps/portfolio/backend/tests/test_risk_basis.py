from __future__ import annotations

from datetime import date

from portfolio_app.services import risk_basis


def _profile(
    details: dict[str, dict[str, object]],
) -> dict[str, object]:
    return risk_basis.calculation_frequency_profile_for_instruments(
        list(details),
        end_date=date(2026, 7, 28),
        detail_loader=lambda instrument_id: details.get(instrument_id),
    )


def test_registry_expected_frequency_overrides_sparse_history_inference(
    monkeypatch,
) -> None:
    detail = {
        "dates": [
            date(2026, 7, 1),
            date(2026, 7, 8),
            date(2026, 7, 15),
            date(2026, 7, 22),
            date(2026, 7, 28),
        ],
        "source_settings": {
            "expected_frequency": "daily",
            "market_calendar": None,
        },
    }
    monkeypatch.setattr(
        risk_basis,
        "selected_observation_dates_from_detail",
        lambda candidate, *, end_date: candidate["dates"],
    )

    profile = _profile({"fund": detail})

    assert profile["source_frequency_by_instrument"] == {"fund": "daily"}
    assert profile["resolved_frequency"] == "daily"
    assert profile["coverage_state"] == "partial"
    assert profile["gap_instrument_ids"] == ["fund"]


def test_market_calendar_detects_a_missing_session_even_when_all_members_share_it(
    monkeypatch,
) -> None:
    details = {
        instrument_id: {
            "dates": [
                date(2026, 7, 23),
                date(2026, 7, 24),
                date(2026, 7, 28),
            ],
            "source_settings": {
                "expected_frequency": "daily",
                "market_calendar": "XSHG",
            },
        }
        for instrument_id in ("alpha", "beta")
    }
    monkeypatch.setattr(
        risk_basis,
        "selected_observation_dates_from_detail",
        lambda candidate, *, end_date: candidate["dates"],
    )
    monkeypatch.setattr(
        risk_basis,
        "_market_calendar_sessions",
        lambda _calendar, _start, _end: (
            date(2026, 7, 23),
            date(2026, 7, 24),
            date(2026, 7, 27),
            date(2026, 7, 28),
        ),
    )

    profile = _profile(details)

    assert profile["coverage_state"] == "partial"
    assert profile["gap_count"] == 2
    assert profile["gap_instrument_ids"] == ["alpha", "beta"]
    assert all(
        detail["gap_date_sample"] == ["2026-07-27"]
        for detail in profile["gap_details"]
    )


def test_market_calendar_does_not_treat_a_shared_holiday_as_a_gap(
    monkeypatch,
) -> None:
    detail = {
        "dates": [
            date(2026, 7, 23),
            date(2026, 7, 24),
            date(2026, 7, 28),
        ],
        "source_settings": {
            "expected_frequency": "daily",
            "market_calendar": "XSHG",
        },
    }
    monkeypatch.setattr(
        risk_basis,
        "selected_observation_dates_from_detail",
        lambda candidate, *, end_date: candidate["dates"],
    )
    monkeypatch.setattr(
        risk_basis,
        "_market_calendar_sessions",
        lambda _calendar, _start, _end: (
            date(2026, 7, 23),
            date(2026, 7, 24),
            date(2026, 7, 28),
        ),
    )

    profile = _profile({"fund": detail})

    assert profile["coverage_state"] == "complete"
    assert profile["gap_count"] == 0
    assert profile["gap_instrument_ids"] == []
