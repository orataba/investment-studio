from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from portfolio_app.calculations.portfolio_daily import capture_quotes
from portfolio_app.calculations.portfolio_daily.capture_common import (
    CommonCaptureResult,
    ManifestCaptureError,
    _aware_timestamp,
)
from portfolio_app.calculations.portfolio_daily.capture_quotes import (
    capture_quote_dependencies,
    quote_window_id,
)
from portfolio_app.calculations.portfolio_daily.input_policy import (
    InstrumentFreshnessPolicy,
)


pytestmark = pytest.mark.no_database


def test_corporate_action_timestamp_is_normalized_without_losing_instant() -> None:
    assert _aware_timestamp(
        "2026-07-14T12:30:45.123456+08:00",
        field_name="updated_at",
    ) == datetime(2026, 7, 14, 4, 30, 45, 123456, tzinfo=UTC)


@pytest.mark.parametrize("value", ["2026-07-14T12:30:45", "invalid", None])
def test_corporate_action_timestamp_requires_valid_timezone_aware_input(
    value: object,
) -> None:
    with pytest.raises(ManifestCaptureError, match="ISO-8601|UTC offset"):
        _aware_timestamp(value, field_name="updated_at")


def test_quote_window_identity_is_deterministic_and_dimension_sensitive() -> None:
    first = quote_window_id(
        portfolio_id="portfolio-a",
        instrument_id="instrument-a",
        role="valuation",
        valuation_date=date(2026, 7, 14),
    )
    assert first == quote_window_id(
        portfolio_id="portfolio-a",
        instrument_id="instrument-a",
        role="valuation",
        valuation_date=date(2026, 7, 14),
    )
    assert first != quote_window_id(
        portfolio_id="portfolio-a",
        instrument_id="instrument-a",
        role="valuation",
        valuation_date=date(2026, 7, 13),
    )


def _common(*, cutoff: datetime) -> CommonCaptureResult:
    return CommonCaptureResult(
        rows_by_table={
            "portfolio_daily_instrument_input": [
                {"instrument_id": "instrument-a", "currency": "CNY"}
            ]
        },
        portfolio_id="portfolio-a",
        base_currency="CNY",
        valuation_timezone="Asia/Shanghai",
        valuation_cutoff_policy="end_of_calendar_day",
        knowledge_cutoff_at=cutoff,
        range_start=date(2026, 7, 14),
        range_end=date(2026, 7, 14),
        instrument_ids_for_valuation=("instrument-a",),
        instrument_freshness={
            "instrument-a": InstrumentFreshnessPolicy(
                mode="calendar_day_carry_forward",
                max_age_days=5,
            )
        },
        currencies=("CNY",),
        acquisition_dates=(),
        fx_max_age_days=5,
    )


def _install_quote_window(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ingested_at: datetime | None,
) -> None:
    series_id = str(uuid4())
    observation_id = str(uuid4())
    revision_id = str(uuid4())
    observation = SimpleNamespace(
        quote_series_id=series_id,
        observation_id=observation_id,
        revision_id=revision_id,
        revision_number=2,
        observation_date=date(2026, 7, 13),
        value=Decimal("1.234567890123456789"),
        status="complete",
        source_published_at=datetime(2026, 7, 13, 8, tzinfo=UTC),
        ingested_at=ingested_at,
        payload_hash="a" * 64,
    )
    window = SimpleNamespace(
        quote_selection_policy_revision="sha256:" + "b" * 64,
        start_boundary_observation=observation,
        observations=(),
        quote_series_id=series_id,
        currency="CNY",
        reason_codes=(),
    )
    resolution = SimpleNamespace(
        resolution_status="resolved",
        revision_id=revision_id,
        reason_codes=("carried_forward_observation",),
        carry_forward=True,
    )
    monkeypatch.setattr(
        capture_quotes,
        "resolve_role_quote_series_in_session",
        lambda *args, **kwargs: window,
    )
    monkeypatch.setattr(
        capture_quotes,
        "resolve_quote_series_observation_at",
        lambda *args, **kwargs: resolution,
    )


def test_quote_capture_freezes_exact_carried_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2026, 7, 14, 4, tzinfo=UTC)
    _install_quote_window(
        monkeypatch,
        ingested_at=datetime(2026, 7, 14, 3, tzinfo=UTC),
    )

    rows = capture_quote_dependencies(object(), common=_common(cutoff=cutoff))

    window = rows["portfolio_daily_quote_window"][0]
    candidate = rows["portfolio_daily_quote_candidate"][0]
    assert window["selection_status"] == "selected"
    assert window["candidate_count"] == window["adopted_count"] == 1
    assert candidate["quote_value"] == Decimal("1.234567890123456789")
    assert candidate["decision_reason_code"] == "adopted_carry_forward"
    assert candidate["decision"] == "adopted"


@pytest.mark.parametrize(
    "ingested_at",
    [None, datetime(2026, 7, 14, 5, tzinfo=UTC)],
)
def test_quote_capture_rejects_unknown_or_post_cutoff_revision(
    monkeypatch: pytest.MonkeyPatch,
    ingested_at: datetime | None,
) -> None:
    cutoff = datetime(2026, 7, 14, 4, tzinfo=UTC)
    _install_quote_window(monkeypatch, ingested_at=ingested_at)

    with pytest.raises(ManifestCaptureError, match="ingestion|knowledge cutoff"):
        capture_quote_dependencies(object(), common=_common(cutoff=cutoff))
