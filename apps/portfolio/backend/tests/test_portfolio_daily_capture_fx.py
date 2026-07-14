from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest

from portfolio_app.calculations.numeric import (
    exact_decimal_product,
    exact_decimal_subtract,
)
from portfolio_app.calculations.portfolio_daily import capture_fx
from portfolio_app.calculations.portfolio_daily.capture_common import (
    CommonCaptureResult,
    ManifestCaptureError,
)
from portfolio_app.calculations.portfolio_daily.capture_fx import (
    capture_fx_dependencies,
    fx_path_id,
)
from portfolio_ops_instrument_core.canonical_fx import (
    compose_effective_fx_rate,
    effective_fx_leg_rate,
)


pytestmark = pytest.mark.no_database


def _common(
    *,
    base_currency: str,
    currencies: tuple[str, ...],
    acquisition_dates: tuple[date, ...] = (),
) -> CommonCaptureResult:
    return CommonCaptureResult(
        rows_by_table={},
        portfolio_id="portfolio-a",
        base_currency=base_currency,
        valuation_timezone="Asia/Shanghai",
        valuation_cutoff_policy="end_of_calendar_day",
        knowledge_cutoff_at=datetime(2026, 7, 14, 12, tzinfo=UTC),
        range_start=date(2026, 7, 14),
        range_end=date(2026, 7, 14),
        instrument_ids_for_valuation=(),
        instrument_freshness={},
        currencies=currencies,
        acquisition_dates=acquisition_dates,
        fx_max_age_days=5,
    )


def test_exact_fx_residual_arithmetic_ignores_global_decimal_precision() -> None:
    left = Decimal("0.14285714285714285714285714285714285714285714285714")
    right = Decimal("7.000000000000000001")
    with localcontext() as context:
        context.prec = 6
        product = exact_decimal_product(left, right)
        residual = exact_decimal_subtract(product, Decimal("1"))

    assert product == Decimal(
        "1.00000000000000000014285714285714285714285714285712285714285714285714"
    )
    assert residual == Decimal(
        "0.00000000000000000014285714285714285714285714285712285714285714285714"
    )


def test_cross_fx_capture_preserves_long_rate_with_zero_path_residual() -> None:
    inverse_quote = Decimal("7.000000000000000001")
    # Each source quote remains within the canonical 18-decimal source domain;
    # the long derived value comes from exact Decimal50 inverse composition.
    direct_quote = Decimal("1.234567890123456789")
    inverse_rate = effective_fx_leg_rate(inverse_quote, inverted=True)
    resolved_rate = compose_effective_fx_rate(
        ((inverse_quote, True), (direct_quote, False))
    )
    resolution = SimpleNamespace(
        resolution_status="resolved",
        reason_codes=(),
        rate=resolved_rate,
        requested_as_of_date=date(2026, 7, 14),
        base_currency="HKD",
        quote_currency="CNY",
        path_kind="cross",
    )

    with localcontext() as context:
        context.prec = 6
        row = capture_fx._path_row(
            common=_common(base_currency="CNY", currencies=("HKD",)),
            path_id=uuid4(),
            resolution=resolution,
            leg_rows=[
                {"effective_rate": inverse_rate},
                {"effective_rate": direct_quote},
            ],
        )

    assert row["resolved_rate"] == resolved_rate
    assert len(resolved_rate.as_tuple().digits) > 50
    assert row["rate_derivation_residual_exact"] == Decimal("0")
    assert not row["rate_derivation_residual_exact"].is_signed()

    mismatched = SimpleNamespace(**vars(resolution))
    mismatched.rate = Decimal("1")
    with pytest.raises(ManifestCaptureError, match="exact product"):
        capture_fx._path_row(
            common=_common(base_currency="CNY", currencies=("HKD",)),
            path_id=uuid4(),
            resolution=mismatched,
            leg_rows=[
                {"effective_rate": inverse_rate},
                {"effective_rate": direct_quote},
            ],
        )


def test_fx_path_identity_is_deterministic_and_date_sensitive() -> None:
    first = fx_path_id(
        portfolio_id="portfolio-a",
        from_currency="CNY",
        to_currency="USD",
        valuation_date=date(2026, 7, 14),
    )
    assert first == fx_path_id(
        portfolio_id="portfolio-a",
        from_currency="CNY",
        to_currency="USD",
        valuation_date=date(2026, 7, 14),
    )
    assert first != fx_path_id(
        portfolio_id="portfolio-a",
        from_currency="CNY",
        to_currency="USD",
        valuation_date=date(2026, 7, 13),
    )


def test_fx_capture_freezes_inverse_leg_and_acquisition_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series_id = str(uuid4())
    observation_id = str(uuid4())
    revision_id = str(uuid4())
    raw = SimpleNamespace(
        quote_series_id=series_id,
        observation_id=observation_id,
        revision_id=revision_id,
        revision_number=3,
        observation_date=date(2026, 7, 1),
        value=Decimal("7.000000000000000001"),
        status="complete",
        source_published_at=datetime(2026, 7, 1, 8, tzinfo=UTC),
        ingested_at=datetime(2026, 7, 1, 9, tzinfo=UTC),
        payload_hash="a" * 64,
    )
    quote_resolution = SimpleNamespace(
        revision_id=revision_id,
        quote_series_id=series_id,
    )
    quoted_rate = raw.value
    effective_rate = effective_fx_leg_rate(quoted_rate, inverted=True)
    leg = SimpleNamespace(
        path_position=1,
        instrument_id="fx-usd-cny",
        market_base_currency="USD",
        market_quote_currency="CNY",
        inverted=True,
        resolution_status="resolved",
        rate=quoted_rate,
        reason_codes=("carried_forward_observation",),
        quote_resolution=quote_resolution,
    )
    window = SimpleNamespace(
        start_boundary_observation=raw,
        observations=(),
        quote_series_id=series_id,
    )

    class FakeBook:
        windows = {"fx-usd-cny": window}

        def rate_at(self, base: str, quote: str, requested: date) -> object:
            assert (base, quote) == ("CNY", "USD")
            return SimpleNamespace(
                resolution_status="resolved",
                reason_codes=("carried_forward_fx_leg",),
                rate=effective_rate,
                requested_as_of_date=requested,
                base_currency=base,
                quote_currency=quote,
                path_kind="inverse",
                legs=(leg,),
            )

    captured_loader_args: dict[str, object] = {}

    def fake_loader(*args: object, **kwargs: object) -> FakeBook:
        captured_loader_args.update(kwargs)
        return FakeBook()

    monkeypatch.setattr(
        capture_fx,
        "resolve_canonical_fx_window_book_in_session",
        fake_loader,
    )
    common = _common(
        base_currency="USD",
        currencies=("CNY",),
        acquisition_dates=(date(2026, 7, 1),),
    )

    rows = capture_fx_dependencies(object(), common=common)

    assert captured_loader_args["start_date"] == date(2026, 7, 1)
    assert len(rows["portfolio_daily_fx_path"]) == 2
    assert len(rows["portfolio_daily_fx_leg"]) == 2
    first_path = rows["portfolio_daily_fx_path"][0]
    first_leg = rows["portfolio_daily_fx_leg"][0]
    assert first_path["coverage_state"] == "partial"
    assert first_path["rate_derivation_residual_exact"] == Decimal("0")
    assert first_leg["from_currency"] == "CNY"
    assert first_leg["to_currency"] == "USD"
    assert first_leg["effective_rate"] == effective_rate
    assert first_leg["rate_derivation_residual_exact"] == exact_decimal_subtract(
        exact_decimal_product(effective_rate, quoted_rate),
        Decimal("1"),
    )
    assert first_leg["reason_codes"] == []
