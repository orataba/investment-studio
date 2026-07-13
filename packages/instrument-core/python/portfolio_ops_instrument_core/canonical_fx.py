from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_ops_instrument_core.db_models import Instrument
from portfolio_ops_instrument_core.fx_universe import (
    MAINTAINED_FX_INSTRUMENTS,
    PIVOT_CURRENCY,
    SUPPORTED_FX_CURRENCIES,
)
from portfolio_ops_instrument_core.models import (
    CanonicalQuoteCalculationDependency,
    CanonicalQuoteResolution,
    CanonicalQuoteSeriesCalculationDependency,
    CanonicalQuoteSeriesResolution,
    QuoteFreshnessPolicy,
)
from portfolio_ops_instrument_core.quote_resolver import (
    CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
    QuoteResolverError,
    resolve_quote_series_observation_at,
    resolve_role_quote_series_in_session,
    validate_freshness_policy,
)


CANONICAL_FX_RESOLVER_STRATEGY_VERSION = "canonical_fx_resolver.v1"
CANONICAL_FX_QUOTE_ROLE = "valuation"
CANONICAL_FX_METRIC_FAMILY = "fx"
CANONICAL_FX_QUOTE_BASIS = "spot"

FxPathKind = Literal["identity", "direct", "inverse", "cross", "unavailable"]
FxLegOperation = Literal["multiply", "divide"]
FxResolutionStatus = Literal["resolved", "unavailable"]
FxFreshnessStatus = Literal["current", "late", "missing"]
FxReliabilityStatus = Literal["reliable", "qualified", "unavailable"]


class CanonicalFxResolverError(ValueError):
    """Fail-closed canonical FX configuration or window-boundary error."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class CanonicalFxLegCalculationDependency(BaseModel):
    path_position: int = Field(ge=1)
    instrument_id: str = Field(min_length=1)
    market_base_currency: str = Field(min_length=1, max_length=8)
    market_quote_currency: str = Field(min_length=1, max_length=8)
    operation: FxLegOperation
    inverted: bool
    quote_calculation_dependency: CanonicalQuoteCalculationDependency | None = None
    quote_window_calculation_dependency: CanonicalQuoteSeriesCalculationDependency
    fingerprint: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_operation(self) -> "CanonicalFxLegCalculationDependency":
        if self.inverted != (self.operation == "divide"):
            raise ValueError("FX leg inversion must agree with its operation")
        expected_fingerprint = _stable_hash(
            _dependency_fingerprint_payload(self, include_fingerprint=False)
        )
        if self.fingerprint != expected_fingerprint:
            raise ValueError("FX leg dependency fingerprint does not match its payload")
        return self


class CanonicalFxLegResolution(BaseModel):
    path_position: int = Field(ge=1)
    instrument_id: str = Field(min_length=1)
    market_base_currency: str = Field(min_length=1, max_length=8)
    market_quote_currency: str = Field(min_length=1, max_length=8)
    operation: FxLegOperation
    inverted: bool
    resolution_status: FxResolutionStatus
    rate: Decimal | None = Field(default=None, gt=0)
    observation_date: date | None = None
    freshness_status: FxFreshnessStatus
    reliability_status: FxReliabilityStatus
    reason_codes: list[str] = Field(default_factory=list)
    quote_resolution: CanonicalQuoteResolution | None = None
    calculation_dependency: CanonicalFxLegCalculationDependency

    @model_validator(mode="after")
    def validate_resolution(self) -> "CanonicalFxLegResolution":
        if self.inverted != (self.operation == "divide"):
            raise ValueError("FX leg inversion must agree with its operation")
        if self.resolution_status == "resolved":
            if (
                self.rate is None
                or self.observation_date is None
                or self.quote_resolution is None
                or self.quote_resolution.resolution_status != "resolved"
            ):
                raise ValueError(
                    "resolved FX leg requires an adopted canonical quote and date"
                )
        elif self.rate is not None:
            raise ValueError("unavailable FX leg must not expose an adopted rate")
        dependency = self.calculation_dependency
        if (
            self.path_position != dependency.path_position
            or self.instrument_id != dependency.instrument_id
            or self.market_base_currency != dependency.market_base_currency
            or self.market_quote_currency != dependency.market_quote_currency
            or self.operation != dependency.operation
            or self.inverted != dependency.inverted
        ):
            raise ValueError("FX leg resolution does not match its dependency path")
        expected_quote_dependency = (
            self.quote_resolution.calculation_dependency
            if self.quote_resolution is not None
            else None
        )
        if dependency.quote_calculation_dependency != expected_quote_dependency:
            raise ValueError("FX leg quote dependency does not match its resolution")
        if self.resolution_status == "resolved" and self.quote_resolution is not None:
            if (
                self.rate != self.quote_resolution.value
                or self.observation_date != self.quote_resolution.observation_date
                or self.freshness_status != self.quote_resolution.freshness_status
                or self.reliability_status
                != self.quote_resolution.reliability_status
            ):
                raise ValueError("FX leg adopted state does not match its quote resolution")
        return self


class CanonicalFxCalculationDependency(BaseModel):
    resolver_strategy_version: Literal["canonical_fx_resolver.v1"]
    consumer_policy_version: str = Field(min_length=1)
    freshness_policy: QuoteFreshnessPolicy
    base_currency: str = Field(min_length=1, max_length=8)
    quote_currency: str = Field(min_length=1, max_length=8)
    requested_as_of_date: date
    path_kind: FxPathKind
    resolution_status: FxResolutionStatus
    reason_codes: list[str] = Field(default_factory=list)
    legs: list[CanonicalFxLegCalculationDependency] = Field(default_factory=list)
    fingerprint: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_path(self) -> "CanonicalFxCalculationDependency":
        expected_leg_count = {
            "identity": 0,
            "direct": 1,
            "inverse": 1,
            "cross": 2,
            "unavailable": 0,
        }[self.path_kind]
        if len(self.legs) != expected_leg_count:
            raise ValueError(
                f"{self.path_kind} FX dependency requires {expected_leg_count} legs"
            )
        if [leg.path_position for leg in self.legs] != list(
            range(1, len(self.legs) + 1)
        ):
            raise ValueError("FX dependency legs must retain canonical path order")
        if self.path_kind == "unavailable" and self.resolution_status != "unavailable":
            raise ValueError("unavailable FX path cannot have a resolved dependency")
        if self.resolution_status == "unavailable" and not self.reason_codes:
            raise ValueError("unavailable FX dependency requires reason codes")
        if self.path_kind == "identity":
            if (
                self.base_currency != self.quote_currency
                or self.resolution_status != "resolved"
            ):
                raise ValueError("identity FX dependency requires one currency and a result")
        elif self.path_kind in {"direct", "inverse", "cross"}:
            if self.base_currency == self.quote_currency:
                raise ValueError("non-identity FX dependency requires distinct currencies")
        if self.path_kind == "direct":
            leg = self.legs[0]
            if (
                leg.market_base_currency != self.base_currency
                or leg.market_quote_currency != self.quote_currency
                or leg.operation != "multiply"
            ):
                raise ValueError("direct FX dependency has an inconsistent leg")
        elif self.path_kind == "inverse":
            leg = self.legs[0]
            if (
                leg.market_base_currency != self.quote_currency
                or leg.market_quote_currency != self.base_currency
                or leg.operation != "divide"
            ):
                raise ValueError("inverse FX dependency has an inconsistent leg")
        elif self.path_kind == "cross":
            base_leg, quote_leg = self.legs
            valid_base_leg = (
                (
                    base_leg.market_base_currency == PIVOT_CURRENCY
                    and base_leg.market_quote_currency == self.base_currency
                    and base_leg.operation == "divide"
                )
                or (
                    base_leg.market_base_currency == self.base_currency
                    and base_leg.market_quote_currency == PIVOT_CURRENCY
                    and base_leg.operation == "multiply"
                )
            )
            valid_quote_leg = (
                (
                    quote_leg.market_base_currency == PIVOT_CURRENCY
                    and quote_leg.market_quote_currency == self.quote_currency
                    and quote_leg.operation == "multiply"
                )
                or (
                    quote_leg.market_base_currency == self.quote_currency
                    and quote_leg.market_quote_currency == PIVOT_CURRENCY
                    and quote_leg.operation == "divide"
                )
            )
            if (
                PIVOT_CURRENCY in {self.base_currency, self.quote_currency}
                or not valid_base_leg
                or not valid_quote_leg
                or base_leg.instrument_id == quote_leg.instrument_id
            ):
                raise ValueError("cross FX dependency has inconsistent pivot legs")
        expected_fingerprint = _stable_hash(
            _fx_dependency_fingerprint_payload(self, include_fingerprint=False)
        )
        if self.fingerprint != expected_fingerprint:
            raise ValueError("FX dependency fingerprint does not match its payload")
        return self


class CanonicalFxResolution(BaseModel):
    resolver_strategy_version: Literal["canonical_fx_resolver.v1"]
    consumer_policy_version: str = Field(min_length=1)
    base_currency: str = Field(min_length=1, max_length=8)
    quote_currency: str = Field(min_length=1, max_length=8)
    requested_as_of_date: date
    freshness_policy: QuoteFreshnessPolicy
    path_kind: FxPathKind
    resolution_status: FxResolutionStatus
    rate: Decimal | None = Field(default=None, gt=0)
    effective_as_of_date: date | None = None
    freshness_status: FxFreshnessStatus
    reliability_status: FxReliabilityStatus
    reason_codes: list[str] = Field(default_factory=list)
    legs: list[CanonicalFxLegResolution] = Field(default_factory=list)
    calculation_dependency: CanonicalFxCalculationDependency

    @model_validator(mode="after")
    def validate_resolution(self) -> "CanonicalFxResolution":
        if self.resolution_status == "resolved":
            if self.rate is None or self.effective_as_of_date is None:
                raise ValueError("resolved FX requires a rate and effective date")
            if any(leg.resolution_status != "resolved" for leg in self.legs):
                raise ValueError("resolved FX cannot contain an unavailable leg")
        else:
            if self.rate is not None or self.effective_as_of_date is not None:
                raise ValueError("unavailable FX must not expose an adopted result")
            if not self.reason_codes:
                raise ValueError("unavailable FX requires at least one reason code")
        dependency = self.calculation_dependency
        if (
            self.resolver_strategy_version != dependency.resolver_strategy_version
            or self.consumer_policy_version != dependency.consumer_policy_version
            or self.base_currency != dependency.base_currency
            or self.quote_currency != dependency.quote_currency
            or self.requested_as_of_date != dependency.requested_as_of_date
            or self.freshness_policy != dependency.freshness_policy
            or self.path_kind != dependency.path_kind
            or self.resolution_status != dependency.resolution_status
            or self.reason_codes != dependency.reason_codes
            or [leg.calculation_dependency for leg in self.legs]
            != dependency.legs
        ):
            raise ValueError("FX resolution does not match its calculation dependency")
        if self.resolution_status == "resolved":
            numerator = Decimal("1")
            denominator = Decimal("1")
            for leg in self.legs:
                if leg.rate is None or leg.observation_date is None:
                    raise ValueError("resolved FX contains an incomplete leg")
                if leg.operation == "multiply":
                    numerator *= leg.rate
                else:
                    denominator *= leg.rate
            expected_rate = numerator / denominator
            expected_effective_date = (
                min(leg.observation_date for leg in self.legs)
                if self.legs
                else self.requested_as_of_date
            )
            expected_reliability: FxReliabilityStatus = (
                "qualified"
                if any(leg.reliability_status == "qualified" for leg in self.legs)
                else "reliable"
            )
            if (
                self.rate != expected_rate
                or self.effective_as_of_date != expected_effective_date
                or self.freshness_status != "current"
                or self.reliability_status != expected_reliability
            ):
                raise ValueError("FX adopted result is inconsistent with its legs")
        return self


@dataclass(frozen=True)
class _FxLegSpec:
    path_position: int
    instrument_id: str
    market_base_currency: str
    market_quote_currency: str
    operation: FxLegOperation

    @property
    def inverted(self) -> bool:
        return self.operation == "divide"


@dataclass(frozen=True)
class _FxPath:
    path_kind: FxPathKind
    legs: tuple[_FxLegSpec, ...]


def _stable_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _normalize_currency(value: object) -> str:
    return str(value or "").strip().upper()


def _normalize_pair(raw_pair: object) -> tuple[str, str]:
    if (
        not isinstance(raw_pair, (tuple, list))
        or len(raw_pair) != 2
    ):
        raise CanonicalFxResolverError(
            "invalid_currency_pair",
            "FX currency pairs must contain exactly base and quote currency.",
        )
    base_currency = _normalize_currency(raw_pair[0])
    quote_currency = _normalize_currency(raw_pair[1])
    if (
        not base_currency
        or not quote_currency
        or len(base_currency) > 8
        or len(quote_currency) > 8
    ):
        raise CanonicalFxResolverError(
            "invalid_currency_pair",
            "FX base and quote currencies must be non-blank canonical codes.",
        )
    return base_currency, quote_currency


def _pivot_leg(
    *,
    currency: str,
    path_position: int,
    pivot_to_currency_operation: FxLegOperation,
) -> _FxLegSpec | None:
    """Express one USD pivot leg using either maintained market direction."""

    direct_instrument = MAINTAINED_FX_INSTRUMENTS.get(
        (PIVOT_CURRENCY, currency)
    )
    if direct_instrument is not None:
        return _FxLegSpec(
            path_position=path_position,
            instrument_id=direct_instrument,
            market_base_currency=PIVOT_CURRENCY,
            market_quote_currency=currency,
            operation=pivot_to_currency_operation,
        )
    inverse_instrument = MAINTAINED_FX_INSTRUMENTS.get(
        (currency, PIVOT_CURRENCY)
    )
    if inverse_instrument is None:
        return None
    return _FxLegSpec(
        path_position=path_position,
        instrument_id=inverse_instrument,
        market_base_currency=currency,
        market_quote_currency=PIVOT_CURRENCY,
        operation=(
            "divide"
            if pivot_to_currency_operation == "multiply"
            else "multiply"
        ),
    )


def _path_for(base_currency: str, quote_currency: str) -> _FxPath | None:
    if (
        base_currency not in SUPPORTED_FX_CURRENCIES
        or quote_currency not in SUPPORTED_FX_CURRENCIES
    ):
        return None
    if base_currency == quote_currency:
        return _FxPath(path_kind="identity", legs=())

    direct_instrument = MAINTAINED_FX_INSTRUMENTS.get(
        (base_currency, quote_currency)
    )
    if direct_instrument is not None:
        return _FxPath(
            path_kind="direct",
            legs=(
                _FxLegSpec(
                    path_position=1,
                    instrument_id=direct_instrument,
                    market_base_currency=base_currency,
                    market_quote_currency=quote_currency,
                    operation="multiply",
                ),
            ),
        )

    inverse_instrument = MAINTAINED_FX_INSTRUMENTS.get(
        (quote_currency, base_currency)
    )
    if inverse_instrument is not None:
        return _FxPath(
            path_kind="inverse",
            legs=(
                _FxLegSpec(
                    path_position=1,
                    instrument_id=inverse_instrument,
                    market_base_currency=quote_currency,
                    market_quote_currency=base_currency,
                    operation="divide",
                ),
            ),
        )

    if base_currency == PIVOT_CURRENCY or quote_currency == PIVOT_CURRENCY:
        return None
    base_leg = _pivot_leg(
        currency=base_currency,
        path_position=1,
        pivot_to_currency_operation="divide",
    )
    quote_leg = _pivot_leg(
        currency=quote_currency,
        path_position=2,
        pivot_to_currency_operation="multiply",
    )
    if base_leg is None or quote_leg is None:
        return None
    return _FxPath(
        path_kind="cross",
        legs=(base_leg, quote_leg),
    )


def _append_reason(reason_codes: list[str], reason_code: str) -> None:
    if reason_code not in reason_codes:
        reason_codes.append(reason_code)


def _dependency_fingerprint_payload(
    dependency: CanonicalFxLegCalculationDependency,
    *,
    include_fingerprint: bool,
) -> dict[str, object]:
    """Return provenance identity without treating ``source_ref`` as series identity."""

    payload = {
        "path_position": dependency.path_position,
        "instrument_id": dependency.instrument_id,
        "market_base_currency": dependency.market_base_currency,
        "market_quote_currency": dependency.market_quote_currency,
        "operation": dependency.operation,
        "inverted": dependency.inverted,
        "quote_calculation_dependency": (
            dependency.quote_calculation_dependency.model_dump(mode="json")
            if dependency.quote_calculation_dependency is not None
            else None
        ),
        "quote_window_calculation_dependency": (
            dependency.quote_window_calculation_dependency.model_dump(mode="json")
        ),
    }
    if include_fingerprint:
        payload["fingerprint"] = dependency.fingerprint
    return payload


def _fx_dependency_fingerprint_payload(
    dependency: CanonicalFxCalculationDependency,
    *,
    include_fingerprint: bool,
) -> dict[str, object]:
    payload = {
        "resolver_strategy_version": dependency.resolver_strategy_version,
        "consumer_policy_version": dependency.consumer_policy_version,
        "freshness_policy": dependency.freshness_policy.model_dump(mode="json"),
        "base_currency": dependency.base_currency,
        "quote_currency": dependency.quote_currency,
        "requested_as_of_date": dependency.requested_as_of_date.isoformat(),
        "path_kind": dependency.path_kind,
        "resolution_status": dependency.resolution_status,
        "reason_codes": dependency.reason_codes,
        "legs": [
            _dependency_fingerprint_payload(
                leg,
                include_fingerprint=True,
            )
            for leg in dependency.legs
        ],
    }
    if include_fingerprint:
        payload["fingerprint"] = dependency.fingerprint
    return payload


def _leg_dependency(
    *,
    spec: _FxLegSpec,
    window: CanonicalQuoteSeriesResolution,
    quote_resolution: CanonicalQuoteResolution | None,
) -> CanonicalFxLegCalculationDependency:
    payload: dict[str, object] = {
        "path_position": spec.path_position,
        "instrument_id": spec.instrument_id,
        "market_base_currency": spec.market_base_currency,
        "market_quote_currency": spec.market_quote_currency,
        "operation": spec.operation,
        "inverted": spec.inverted,
        "quote_calculation_dependency": (
            quote_resolution.calculation_dependency.model_dump(mode="json")
            if quote_resolution is not None
            else None
        ),
        "quote_window_calculation_dependency": (
            window.calculation_dependency.model_dump(mode="json")
        ),
    }
    return CanonicalFxLegCalculationDependency(
        path_position=spec.path_position,
        instrument_id=spec.instrument_id,
        market_base_currency=spec.market_base_currency,
        market_quote_currency=spec.market_quote_currency,
        operation=spec.operation,
        inverted=spec.inverted,
        quote_calculation_dependency=(
            quote_resolution.calculation_dependency
            if quote_resolution is not None
            else None
        ),
        quote_window_calculation_dependency=window.calculation_dependency,
        fingerprint=_stable_hash(payload),
    )


def _leg_resolution(
    *,
    spec: _FxLegSpec,
    window: CanonicalQuoteSeriesResolution,
    identity_reason_codes: tuple[str, ...],
    requested_as_of_date: date,
) -> CanonicalFxLegResolution:
    reason_codes = list(identity_reason_codes)
    quote_resolution: CanonicalQuoteResolution | None = None
    expected_identity = (
        window.instrument_id == spec.instrument_id
        and window.role == CANONICAL_FX_QUOTE_ROLE
        and window.metric_family in {None, CANONICAL_FX_METRIC_FAMILY}
        and window.quote_basis in {None, CANONICAL_FX_QUOTE_BASIS}
        and window.currency == spec.market_quote_currency
    )
    if not expected_identity:
        _append_reason(reason_codes, "invalid_fx_quote_identity")
    elif window.quote_series_id is None:
        for reason_code in window.reason_codes:
            _append_reason(reason_codes, reason_code)
    else:
        try:
            quote_resolution = resolve_quote_series_observation_at(
                window,
                requested_as_of_date=requested_as_of_date,
            )
        except QuoteResolverError as error:
            _append_reason(reason_codes, error.reason_code)
        else:
            for reason_code in quote_resolution.reason_codes:
                _append_reason(reason_codes, reason_code)

    resolved = (
        not identity_reason_codes
        and expected_identity
        and quote_resolution is not None
        and quote_resolution.resolution_status == "resolved"
        and quote_resolution.value is not None
        and quote_resolution.value > 0
        and quote_resolution.observation_date is not None
    )
    if quote_resolution is not None:
        freshness_status: FxFreshnessStatus = quote_resolution.freshness_status
        reliability_status: FxReliabilityStatus = (
            quote_resolution.reliability_status
            if resolved
            else "unavailable"
        )
    elif any(
        reason in {"late_observation", "freshness_limit_exceeded"}
        for reason in reason_codes
    ):
        freshness_status = "late"
        reliability_status = "unavailable"
    else:
        freshness_status = "missing"
        reliability_status = "unavailable"

    dependency = _leg_dependency(
        spec=spec,
        window=window,
        quote_resolution=quote_resolution,
    )
    return CanonicalFxLegResolution(
        path_position=spec.path_position,
        instrument_id=spec.instrument_id,
        market_base_currency=spec.market_base_currency,
        market_quote_currency=spec.market_quote_currency,
        operation=spec.operation,
        inverted=spec.inverted,
        resolution_status="resolved" if resolved else "unavailable",
        rate=quote_resolution.value if resolved and quote_resolution is not None else None,
        observation_date=(
            quote_resolution.observation_date
            if resolved and quote_resolution is not None
            else None
        ),
        freshness_status=freshness_status,
        reliability_status=reliability_status,
        reason_codes=reason_codes,
        quote_resolution=quote_resolution,
        calculation_dependency=dependency,
    )


def _overall_dependency(
    *,
    consumer_policy_version: str,
    freshness_policy: QuoteFreshnessPolicy,
    base_currency: str,
    quote_currency: str,
    requested_as_of_date: date,
    path_kind: FxPathKind,
    legs: list[CanonicalFxLegResolution],
    resolution_status: FxResolutionStatus,
    reason_codes: list[str],
) -> CanonicalFxCalculationDependency:
    leg_dependencies = [leg.calculation_dependency for leg in legs]
    payload = {
        "resolver_strategy_version": CANONICAL_FX_RESOLVER_STRATEGY_VERSION,
        "consumer_policy_version": consumer_policy_version,
        "freshness_policy": freshness_policy.model_dump(mode="json"),
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "requested_as_of_date": requested_as_of_date.isoformat(),
        "path_kind": path_kind,
        "resolution_status": resolution_status,
        "reason_codes": reason_codes,
        "legs": [
            _dependency_fingerprint_payload(
                dependency,
                include_fingerprint=True,
            )
            for dependency in leg_dependencies
        ],
    }
    return CanonicalFxCalculationDependency(
        resolver_strategy_version=CANONICAL_FX_RESOLVER_STRATEGY_VERSION,
        consumer_policy_version=consumer_policy_version,
        freshness_policy=freshness_policy,
        base_currency=base_currency,
        quote_currency=quote_currency,
        requested_as_of_date=requested_as_of_date,
        path_kind=path_kind,
        resolution_status=resolution_status,
        reason_codes=reason_codes,
        legs=leg_dependencies,
        fingerprint=_stable_hash(payload),
    )


@dataclass(frozen=True)
class CanonicalFxWindowBook:
    start_date: date
    end_date: date
    consumer_policy_version: str
    freshness_policy: QuoteFreshnessPolicy
    paths: Mapping[tuple[str, str], _FxPath | None]
    windows: Mapping[str, CanonicalQuoteSeriesResolution]
    instrument_identity_reason_codes: Mapping[str, tuple[str, ...]]

    @property
    def locked_currency_pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self.paths))

    def rate_at(
        self,
        base_currency: object,
        quote_currency: object,
        requested_as_of_date: date,
    ) -> CanonicalFxResolution:
        pair = _normalize_pair((base_currency, quote_currency))
        if (
            not isinstance(requested_as_of_date, date)
            or requested_as_of_date < self.start_date
            or requested_as_of_date > self.end_date
        ):
            raise CanonicalFxResolverError(
                "invalid_as_of_date",
                "FX requested_as_of_date must be inside the locked window.",
            )
        if pair not in self.paths:
            return self._unavailable_resolution(
                pair=pair,
                requested_as_of_date=requested_as_of_date,
                reason_code="currency_pair_not_locked",
            )
        path = self.paths[pair]
        if path is None:
            return self._unavailable_resolution(
                pair=pair,
                requested_as_of_date=requested_as_of_date,
                reason_code="unsupported_currency_pair",
            )
        if path.path_kind == "identity":
            dependency = _overall_dependency(
                consumer_policy_version=self.consumer_policy_version,
                freshness_policy=self.freshness_policy,
                base_currency=pair[0],
                quote_currency=pair[1],
                requested_as_of_date=requested_as_of_date,
                path_kind="identity",
                legs=[],
                resolution_status="resolved",
                reason_codes=[],
            )
            return CanonicalFxResolution(
                resolver_strategy_version=CANONICAL_FX_RESOLVER_STRATEGY_VERSION,
                consumer_policy_version=self.consumer_policy_version,
                base_currency=pair[0],
                quote_currency=pair[1],
                requested_as_of_date=requested_as_of_date,
                freshness_policy=self.freshness_policy,
                path_kind="identity",
                resolution_status="resolved",
                rate=Decimal("1"),
                effective_as_of_date=requested_as_of_date,
                freshness_status="current",
                reliability_status="reliable",
                reason_codes=[],
                legs=[],
                calculation_dependency=dependency,
            )

        legs = [
            _leg_resolution(
                spec=spec,
                window=self.windows[spec.instrument_id],
                identity_reason_codes=self.instrument_identity_reason_codes.get(
                    spec.instrument_id,
                    (),
                ),
                requested_as_of_date=requested_as_of_date,
            )
            for spec in path.legs
        ]
        reason_codes: list[str] = []
        for leg in legs:
            if leg.freshness_status == "late":
                _append_reason(reason_codes, "late_fx_leg")
            if leg.freshness_status == "missing":
                _append_reason(reason_codes, "missing_fx_leg")
            if any(
                reason.startswith("invalid_fx_")
                for reason in leg.reason_codes
            ):
                _append_reason(reason_codes, "invalid_fx_leg_identity")
            if leg.resolution_status == "unavailable":
                _append_reason(reason_codes, "unavailable_fx_leg")
            if (
                leg.resolution_status == "resolved"
                and leg.quote_resolution is not None
                and leg.quote_resolution.carry_forward
            ):
                _append_reason(reason_codes, "carried_forward_fx_leg")
            if "unknown_ingestion_time" in leg.reason_codes:
                _append_reason(reason_codes, "unknown_fx_ingestion_time")

        all_legs_resolved = all(
            leg.resolution_status == "resolved" for leg in legs
        )
        resolution_status: FxResolutionStatus = (
            "resolved" if all_legs_resolved else "unavailable"
        )
        rate: Decimal | None = None
        if all_legs_resolved:
            numerator = Decimal("1")
            denominator = Decimal("1")
            for leg in legs:
                if leg.rate is None:
                    raise CanonicalFxResolverError(
                        "invalid_fx_leg",
                        "Resolved FX leg is missing its adopted Decimal rate.",
                    )
                if leg.operation == "multiply":
                    numerator *= leg.rate
                else:
                    denominator *= leg.rate
            rate = numerator / denominator
        effective_as_of_date = (
            min(
                leg.observation_date
                for leg in legs
                if leg.observation_date is not None
            )
            if all_legs_resolved
            else None
        )
        if any(leg.freshness_status == "missing" for leg in legs):
            freshness_status: FxFreshnessStatus = "missing"
        elif any(leg.freshness_status == "late" for leg in legs):
            freshness_status = "late"
        else:
            freshness_status = "current"
        if not all_legs_resolved:
            reliability_status: FxReliabilityStatus = "unavailable"
        elif any(leg.reliability_status == "qualified" for leg in legs):
            reliability_status = "qualified"
        else:
            reliability_status = "reliable"

        dependency = _overall_dependency(
            consumer_policy_version=self.consumer_policy_version,
            freshness_policy=self.freshness_policy,
            base_currency=pair[0],
            quote_currency=pair[1],
            requested_as_of_date=requested_as_of_date,
            path_kind=path.path_kind,
            legs=legs,
            resolution_status=resolution_status,
            reason_codes=reason_codes,
        )
        return CanonicalFxResolution(
            resolver_strategy_version=CANONICAL_FX_RESOLVER_STRATEGY_VERSION,
            consumer_policy_version=self.consumer_policy_version,
            base_currency=pair[0],
            quote_currency=pair[1],
            requested_as_of_date=requested_as_of_date,
            freshness_policy=self.freshness_policy,
            path_kind=path.path_kind,
            resolution_status=resolution_status,
            rate=rate,
            effective_as_of_date=effective_as_of_date,
            freshness_status=freshness_status,
            reliability_status=reliability_status,
            reason_codes=reason_codes,
            legs=legs,
            calculation_dependency=dependency,
        )

    def _unavailable_resolution(
        self,
        *,
        pair: tuple[str, str],
        requested_as_of_date: date,
        reason_code: str,
    ) -> CanonicalFxResolution:
        reason_codes = [reason_code]
        dependency = _overall_dependency(
            consumer_policy_version=self.consumer_policy_version,
            freshness_policy=self.freshness_policy,
            base_currency=pair[0],
            quote_currency=pair[1],
            requested_as_of_date=requested_as_of_date,
            path_kind="unavailable",
            legs=[],
            resolution_status="unavailable",
            reason_codes=reason_codes,
        )
        return CanonicalFxResolution(
            resolver_strategy_version=CANONICAL_FX_RESOLVER_STRATEGY_VERSION,
            consumer_policy_version=self.consumer_policy_version,
            base_currency=pair[0],
            quote_currency=pair[1],
            requested_as_of_date=requested_as_of_date,
            freshness_policy=self.freshness_policy,
            path_kind="unavailable",
            resolution_status="unavailable",
            freshness_status="missing",
            reliability_status="unavailable",
            reason_codes=reason_codes,
            legs=[],
            calculation_dependency=dependency,
        )


def resolve_canonical_fx_window_book_in_session(
    session: Session,
    *,
    currency_pairs: Iterable[tuple[str, str]],
    start_date: date,
    end_date: date,
    freshness_policy: QuoteFreshnessPolicy | Mapping[str, object],
    consumer_policy_version: object,
) -> CanonicalFxWindowBook:
    """Lock all source series required by the requested FX paths in one UoW.

    Each maintained source leg is selected exactly once through the canonical
    quote resolver's ``valuation`` role.  ``rate_at`` is subsequently pure and
    cannot open a session, switch series, fetch a flat instrument detail, or
    silently fall back to an older complete observation after a later
    non-complete observation.
    """

    if (
        not isinstance(start_date, date)
        or not isinstance(end_date, date)
        or start_date > end_date
    ):
        raise CanonicalFxResolverError(
            "invalid_date_window",
            "Canonical FX window requires start_date on or before end_date.",
        )
    normalized_consumer_version = str(consumer_policy_version or "").strip()
    if not normalized_consumer_version:
        raise CanonicalFxResolverError(
            "missing_consumer_policy_version",
            "Canonical FX requires an explicit consumer policy version.",
        )
    try:
        resolved_freshness = validate_freshness_policy(freshness_policy)
    except QuoteResolverError as error:
        raise CanonicalFxResolverError(error.reason_code, str(error)) from error

    normalized_pairs = sorted({_normalize_pair(pair) for pair in currency_pairs})
    if not normalized_pairs:
        raise CanonicalFxResolverError(
            "missing_currency_pairs",
            "Canonical FX window requires at least one explicit currency pair.",
        )
    paths = {pair: _path_for(*pair) for pair in normalized_pairs}
    leg_specs_by_instrument: dict[str, _FxLegSpec] = {}
    for path in paths.values():
        if path is None:
            continue
        for spec in path.legs:
            existing = leg_specs_by_instrument.get(spec.instrument_id)
            if existing is not None and (
                existing.market_base_currency != spec.market_base_currency
                or existing.market_quote_currency != spec.market_quote_currency
            ):
                raise CanonicalFxResolverError(
                    "ambiguous_fx_instrument_mapping",
                    "One maintained FX instrument maps to conflicting currency pairs.",
                )
            leg_specs_by_instrument[spec.instrument_id] = spec

    instrument_ids = sorted(leg_specs_by_instrument)
    instrument_rows = (
        session.scalars(
            select(Instrument).where(Instrument.instrument_id.in_(instrument_ids))
        ).all()
        if instrument_ids
        else []
    )
    instruments = {
        str(instrument.instrument_id): instrument for instrument in instrument_rows
    }
    identity_reason_codes: dict[str, tuple[str, ...]] = {}
    for instrument_id, spec in leg_specs_by_instrument.items():
        instrument = instruments.get(instrument_id)
        reasons: list[str] = []
        if instrument is None:
            reasons.append("fx_instrument_not_found")
        else:
            if str(instrument.instrument_type or "").strip().lower() != "fx":
                reasons.append("invalid_fx_instrument_type")
            if _normalize_currency(instrument.currency) != spec.market_quote_currency:
                reasons.append("invalid_fx_instrument_currency")
        identity_reason_codes[instrument_id] = tuple(reasons)

    windows = {
        instrument_id: resolve_role_quote_series_in_session(
            session,
            resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
            quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
            instrument_id=instrument_id,
            role=CANONICAL_FX_QUOTE_ROLE,
            currency=spec.market_quote_currency,
            range_mode="bounded",
            start_date=start_date,
            end_date=end_date,
            freshness_policy=resolved_freshness,
        )
        for instrument_id, spec in sorted(leg_specs_by_instrument.items())
    }
    return CanonicalFxWindowBook(
        start_date=start_date,
        end_date=end_date,
        consumer_policy_version=normalized_consumer_version,
        freshness_policy=resolved_freshness,
        paths=MappingProxyType(paths),
        windows=MappingProxyType(windows),
        instrument_identity_reason_codes=MappingProxyType(identity_reason_codes),
    )
