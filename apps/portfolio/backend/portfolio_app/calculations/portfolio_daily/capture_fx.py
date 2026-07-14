"""Freeze exact canonical FX paths and per-leg quote revision evidence."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from portfolio_app.calculations.numeric import (
    exact_decimal_product,
    exact_decimal_subtract,
)
from portfolio_app.calculations.portfolio_daily.capture_common import (
    CommonCaptureResult,
    ManifestCaptureError,
)
from portfolio_app.calculations.portfolio_daily.constants import (
    FX_CONSUMER_POLICY_VERSION,
    FX_RATE_MATH_PRECISION,
    FX_RATE_ROUNDING_MODE,
    FX_RESOLVER_STRATEGY_VERSION,
    QUOTE_FRESHNESS_POLICY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_fx_leg,
    portfolio_daily_fx_path,
)
from portfolio_app.calculations.portfolio_daily.identifiers import (
    portfolio_daily_uuid,
)
from portfolio_ops_instrument_core.canonical_fx import (
    CanonicalFxResolution,
    effective_fx_leg_rate,
    resolve_canonical_fx_window_book_in_session,
)
from portfolio_ops_instrument_core.models import (
    CanonicalQuoteSeriesObservation,
    QuoteFreshnessPolicy,
)


def fx_path_id(
    *,
    portfolio_id: str,
    from_currency: str,
    to_currency: str,
    valuation_date: date,
) -> UUID:
    return portfolio_daily_uuid(
        "fx-path",
        portfolio_id,
        from_currency,
        to_currency,
        valuation_date.isoformat(),
    )


def _calendar_dates(start_date: date, end_date: date) -> tuple[date, ...]:
    if start_date > end_date:
        raise ManifestCaptureError("FX capture date range is inverted")
    return tuple(
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    )


def _uuid(value: object, *, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ManifestCaptureError(
            f"canonical FX {field_name} is not a UUID: {value!r}"
        ) from error


def _raw_observations_by_revision(
    window: object,
) -> dict[str, CanonicalQuoteSeriesObservation]:
    boundary = getattr(window, "start_boundary_observation", None)
    observations = getattr(window, "observations", ())
    return {
        observation.revision_id: observation
        for observation in (boundary, *observations)
        if observation is not None
    }


def _reason_codes(values: object, *, fallback: str | None = None) -> list[str]:
    source = values if isinstance(values, (list, tuple)) else ()
    reasons = sorted({str(value) for value in source if str(value)})
    if not reasons and fallback is not None:
        reasons.append(fallback)
    return reasons


def _leg_currency_edge(leg: object) -> tuple[str, str]:
    market_base = str(getattr(leg, "market_base_currency"))
    market_quote = str(getattr(leg, "market_quote_currency"))
    if bool(getattr(leg, "inverted")):
        return market_quote, market_base
    return market_base, market_quote


def _raw_leg_observation(
    *,
    book: object,
    leg: object,
) -> CanonicalQuoteSeriesObservation | None:
    quote_resolution = getattr(leg, "quote_resolution", None)
    revision_id = getattr(quote_resolution, "revision_id", None)
    if revision_id is None:
        return None
    instrument_id = str(getattr(leg, "instrument_id"))
    windows = getattr(book, "windows")
    window = windows[instrument_id]
    raw = _raw_observations_by_revision(window).get(str(revision_id))
    if raw is None:
        raise ManifestCaptureError(
            "canonical FX leg revision is absent from its locked window: "
            + str(revision_id)
        )
    return raw


def _require_known_before_cutoff(
    observation: CanonicalQuoteSeriesObservation,
    *,
    common: CommonCaptureResult,
) -> None:
    if observation.ingested_at is None:
        raise ManifestCaptureError(
            "canonical FX leg revision has unknown ingestion time: "
            + observation.revision_id
        )
    if observation.ingestion_time_state not in {
        "observed",
        "legacy_series_upper_bound",
        "legacy_instrument_upper_bound",
        "legacy_migration_upper_bound",
    }:
        raise ManifestCaptureError(
            "canonical FX leg revision has unknown ingestion evidence state: "
            + observation.revision_id
        )
    if observation.ingested_at > common.knowledge_cutoff_at:
        raise ManifestCaptureError(
            "canonical FX leg revision is after the manifest knowledge cutoff: "
            + observation.revision_id
        )


def _leg_row(
    *,
    book: object,
    common: CommonCaptureResult,
    path_id: UUID,
    leg: object,
) -> dict[str, object]:
    raw = _raw_leg_observation(book=book, leg=leg)
    resolved = str(getattr(leg, "resolution_status")) == "resolved"
    if resolved and raw is None:
        raise ManifestCaptureError("resolved canonical FX leg has no raw revision")
    if raw is not None:
        _require_known_before_cutoff(raw, common=common)

    status = "resolved" if resolved else "rejected" if raw is not None else "missing"
    reasons = (
        []
        if status == "resolved"
        else _reason_codes(
            getattr(leg, "reason_codes", ()),
            fallback="unavailable_fx_leg",
        )
    )
    quoted_rate = raw.value if raw is not None else None
    effective_rate: Decimal | None = None
    residual: Decimal | None = None
    inverted = bool(getattr(leg, "inverted"))
    if resolved:
        if quoted_rate is None or quoted_rate <= 0:
            raise ManifestCaptureError("resolved canonical FX leg has no positive rate")
        if getattr(leg, "rate") != quoted_rate:
            raise ManifestCaptureError("canonical FX leg rate differs from raw revision")
        effective_rate = effective_fx_leg_rate(quoted_rate, inverted=inverted)
        residual = (
            exact_decimal_subtract(
                exact_decimal_product(effective_rate, quoted_rate),
                Decimal("1"),
            )
            if inverted
            else Decimal("0")
        )

    from_currency, to_currency = _leg_currency_edge(leg)
    quote_resolution = getattr(leg, "quote_resolution", None)
    quote_series_id = (
        getattr(quote_resolution, "quote_series_id", None)
        if quote_resolution is not None
        else None
    )
    if quote_series_id is None:
        instrument_id = str(getattr(leg, "instrument_id"))
        quote_series_id = getattr(
            getattr(book, "windows")[instrument_id],
            "quote_series_id",
            None,
        )
    return {
        "portfolio_id": common.portfolio_id,
        "fx_path_id": path_id,
        "leg_order": int(getattr(leg, "path_position")),
        "from_currency": from_currency,
        "to_currency": to_currency,
        "is_inverted": inverted,
        "leg_resolution_status": status,
        "reason_codes": reasons,
        "quote_series_id": (
            _uuid(quote_series_id, field_name="quote_series_id")
            if quote_series_id is not None
            else None
        ),
        "observation_id": (
            _uuid(raw.observation_id, field_name="observation_id")
            if raw is not None
            else None
        ),
        "revision_id": (
            _uuid(raw.revision_id, field_name="revision_id")
            if raw is not None
            else None
        ),
        "revision_number": raw.revision_number if raw is not None else None,
        "observation_date": raw.observation_date if raw is not None else None,
        "quoted_rate": quoted_rate,
        "effective_rate": effective_rate,
        "rate_derivation_residual_exact": residual,
        "quote_status": raw.status if raw is not None else None,
        "source_published_at": raw.source_published_at if raw is not None else None,
        "ingested_at": raw.ingested_at if raw is not None else None,
        "ingestion_time_state": (
            raw.ingestion_time_state if raw is not None else None
        ),
        "payload_hash": raw.payload_hash if raw is not None else None,
        "consumer_policy_version": FX_CONSUMER_POLICY_VERSION,
        "freshness_policy_version": QUOTE_FRESHNESS_POLICY_VERSION,
        "freshness_mode": "calendar_day_carry_forward",
        "freshness_max_age_days": common.fx_max_age_days,
        "resolver_strategy_version": FX_RESOLVER_STRATEGY_VERSION,
        "rate_math_precision": FX_RATE_MATH_PRECISION,
        "rate_rounding_mode": FX_RATE_ROUNDING_MODE,
    }


def _path_row(
    *,
    common: CommonCaptureResult,
    path_id: UUID,
    resolution: CanonicalFxResolution,
    leg_rows: list[dict[str, object]],
) -> dict[str, object]:
    resolved = resolution.resolution_status == "resolved"
    path_reasons = _reason_codes(resolution.reason_codes)
    coverage_state = (
        "complete"
        if resolved and not path_reasons
        else "partial"
        if resolved
        else "unavailable"
    )
    if not resolved and not path_reasons:
        path_reasons = ["unavailable_fx_path"]

    residual: Decimal | None = None
    if resolved:
        if resolution.rate is None:
            raise ManifestCaptureError("resolved canonical FX path has no rate")
        effective_rates = tuple(
            row["effective_rate"]
            for row in leg_rows
            if isinstance(row["effective_rate"], Decimal)
        )
        if len(effective_rates) != len(leg_rows):
            raise ManifestCaptureError("resolved canonical FX path has unresolved legs")
        exact_product = exact_decimal_product(
            *(effective_rates or (Decimal("1"),))
        )
        residual = exact_decimal_subtract(resolution.rate, exact_product)
        if residual != 0:
            raise ManifestCaptureError(
                "resolved canonical FX path rate must equal the exact product "
                "of its effective legs"
            )
        residual = Decimal("0")

    return {
        "portfolio_id": common.portfolio_id,
        "fx_path_id": path_id,
        "valuation_date": resolution.requested_as_of_date,
        "from_currency": resolution.base_currency,
        "to_currency": resolution.quote_currency,
        "path_kind": resolution.path_kind,
        "resolution_status": resolution.resolution_status,
        "leg_count": len(leg_rows),
        "resolved_rate": resolution.rate,
        "rate_derivation_residual_exact": residual,
        "selection_policy_version": QUOTE_SELECTION_POLICY_VERSION,
        "consumer_policy_version": FX_CONSUMER_POLICY_VERSION,
        "freshness_policy_version": QUOTE_FRESHNESS_POLICY_VERSION,
        "freshness_mode": "calendar_day_carry_forward",
        "freshness_max_age_days": common.fx_max_age_days,
        "resolver_strategy_version": FX_RESOLVER_STRATEGY_VERSION,
        "rate_math_precision": FX_RATE_MATH_PRECISION,
        "rate_rounding_mode": FX_RATE_ROUNDING_MODE,
        "coverage_state": coverage_state,
        "reason_codes": path_reasons,
    }


def capture_fx_dependencies(
    session: Session,
    *,
    common: CommonCaptureResult,
) -> dict[str, list[dict[str, object]]]:
    daily_dates = _calendar_dates(common.range_start, common.range_end)
    required_dates = tuple(sorted(set(daily_dates).union(common.acquisition_dates)))
    if not required_dates:
        raise ManifestCaptureError("FX capture requires at least one date")
    currency_pairs = tuple(
        (currency, common.base_currency) for currency in common.currencies
    )
    freshness_policy = QuoteFreshnessPolicy(
        policy_version=QUOTE_FRESHNESS_POLICY_VERSION,
        mode="calendar_day_carry_forward",
        max_age_days=common.fx_max_age_days,
    )
    book = resolve_canonical_fx_window_book_in_session(
        session,
        currency_pairs=currency_pairs,
        start_date=required_dates[0],
        end_date=common.range_end,
        freshness_policy=freshness_policy,
        consumer_policy_version=FX_CONSUMER_POLICY_VERSION,
    )

    path_rows: list[dict[str, object]] = []
    leg_rows: list[dict[str, object]] = []
    for valuation_date in required_dates:
        for from_currency, to_currency in currency_pairs:
            resolution = book.rate_at(
                from_currency,
                to_currency,
                valuation_date,
            )
            path_id = fx_path_id(
                portfolio_id=common.portfolio_id,
                from_currency=from_currency,
                to_currency=to_currency,
                valuation_date=valuation_date,
            )
            current_leg_rows = [
                _leg_row(
                    book=book,
                    common=common,
                    path_id=path_id,
                    leg=leg,
                )
                for leg in resolution.legs
            ]
            path_rows.append(
                _path_row(
                    common=common,
                    path_id=path_id,
                    resolution=resolution,
                    leg_rows=current_leg_rows,
                )
            )
            leg_rows.extend(current_leg_rows)
    return {
        portfolio_daily_fx_path.name: path_rows,
        portfolio_daily_fx_leg.name: leg_rows,
    }


__all__ = [
    "capture_fx_dependencies",
    "fx_path_id",
]
