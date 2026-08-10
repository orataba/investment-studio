from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


InstrumentType = Literal[
    "fund",
    "etf",
    "index",
    "equity",
    "cash",
    "fx",
    "other",
]
INSTRUMENT_TYPES = frozenset(
    {
        "fund",
        "etf",
        "index",
        "equity",
        "cash",
        "fx",
        "other",
    }
)
ExpectedFrequency = Literal["daily", "weekly", "monthly", "event_driven"]
SourceMode = Literal["manual", "email", "api"]
ReturnSemantics = Literal["unknown", "price_return", "total_return"]
IdentifierType = Literal[
    "ticker",
    "exchange_ticker",
    "ts_code",
    "isin",
    "cusip",
    "sedol",
    "internal",
    "fund_name",
    "cash_currency",
    "other",
]
MetricFamily = Literal["price", "nav", "fx"]
PriceUnit = Literal["per_unit", "rate"]
NavLineageKind = Literal["provider_explicit", "derived_dividend_reinvestment"]
FundNavProjectionKind = Literal[
    "provider_explicit",
    "event_derived",
    "hybrid_reanchored",
]
FundNavProjectionStatus = Literal["complete", "partial", "unavailable"]
QuoteBasis = Literal[
    "last",
    "close",
    "adjusted_close",
    "official_nav",
    "total_return_nav",
    "spot",
    "par",
]
QuoteRole = Literal["trading", "valuation", "total_return", "chart", "reference"]
DataStatus = Literal["complete", "partial", "unavailable"]
CorporateActionType = Literal["share_split"]
CorporateActionStatus = Literal["detected", "confirmed", "cancelled"]
QuantityRounding = Literal["exact", "truncate", "round_half_up", "cash_in_lieu"]
BrokerIdentifierType = Literal["symbol", "product_code"]
FundNavEventType = Literal["cash_distribution", "unit_split"]
FundNavEventRevisionKind = Literal["original", "correction", "cancellation"]
FundNavEventEvidenceKind = Literal[
    "provider_notice",
    "manual_verified",
]
FundNavAdjustmentFactorKind = Literal["provider_implied", "event_derived"]
FundNavFactorEvidenceKind = Literal[
    "provider_total_return",
    "provider_cash_cumulative",
    "fund_nav_event",
    "zero_cash_anchor",
    "window_normalized_anchor",
]
VALUATION_PROHIBITED_TOTAL_RETURN_BASES = frozenset(
    {
        "adjusted_close",
        "total_return_nav",
    }
)
QUOTE_BASIS_METRIC_FAMILY: dict[str, MetricFamily] = {
    "last": "price",
    "close": "price",
    "adjusted_close": "price",
    "official_nav": "nav",
    "total_return_nav": "nav",
    "spot": "fx",
    "par": "price",
}
PRICE_UNIT_SCALES: dict[PriceUnit, Decimal] = {
    "per_unit": Decimal("1"),
    "rate": Decimal("1"),
}
QUOTE_SELECTION_PROHIBITED_BASES: frozenset[str] = frozenset()
NAV_HISTORY_INSTRUMENT_TYPES = frozenset({"fund"})
FUND_TOTAL_RETURN_QUOTE_BASES = ("total_return_nav",)
FUND_NAV_FACTOR_QUANTUM = Decimal("0.000000000000000001")


def _quantized_fund_nav_factor(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 76
        # PostgreSQL NUMERIC/ROUND uses half-away-from-zero.  Factor levels are
        # positive, so ROUND_HALF_UP gives the same deterministic result in the
        # Python validation path and in the database trigger.
        return value.quantize(FUND_NAV_FACTOR_QUANTUM, rounding=ROUND_HALF_UP)


def deterministic_fund_nav_adjustment_factor_id(
    *,
    fund_nav_projection_run_id: str,
    factor_logical_key: str,
) -> str:
    run_id = fund_nav_projection_run_id.strip()
    logical_key = factor_logical_key.strip()
    if not run_id or not logical_key:
        raise ValueError("projection run id and factor logical key must not be blank")
    digest = hashlib.sha256(f"{run_id}\x1f{logical_key}".encode()).hexdigest()
    return f"fund-nav-factor-{digest}"


def normalize_instrument_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in INSTRUMENT_TYPES:
        raise ValueError(f'Unsupported instrument_type "{normalized}".')
    return normalized


def normalize_market_data_currency(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized or len(normalized) > 8:
        raise ValueError("Market-data currency must be a non-blank ISO currency code.")
    return normalized


def parse_positive_market_data_value(value: object) -> Decimal:
    try:
        normalized = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Market-data value must be a finite positive decimal.") from error
    if not normalized.is_finite() or normalized <= 0:
        raise ValueError("Market-data value must be a finite positive decimal.")
    return normalized


def validate_market_data_identity(
    *,
    metric_family: str,
    quote_basis: str,
) -> None:
    expected_family = QUOTE_BASIS_METRIC_FAMILY.get(quote_basis)
    if expected_family is None:
        raise ValueError(f'Unsupported quote_basis "{quote_basis}".')
    if metric_family != expected_family:
        raise ValueError(
            f'quote_basis "{quote_basis}" requires metric_family '
            f'"{expected_family}", not "{metric_family}".'
        )


def canonical_price_contract(
    *,
    instrument_type: str,
    metric_family: str,
    quote_basis: str,
) -> tuple[PriceUnit, Decimal]:
    validate_market_data_identity(
        metric_family=metric_family,
        quote_basis=quote_basis,
    )
    normalized_instrument_type = normalize_instrument_type(instrument_type)
    is_fx_quote = metric_family == "fx" or quote_basis == "spot"
    if (normalized_instrument_type == "fx") != is_fx_quote:
        raise ValueError(
            "FX market data requires an fx instrument with metric_family \"fx\" "
            "and quote_basis \"spot\"."
        )
    if normalized_instrument_type == "fx" or metric_family == "fx":
        return "rate", PRICE_UNIT_SCALES["rate"]
    return "per_unit", PRICE_UNIT_SCALES["per_unit"]


def validate_nav_history_instrument_type(
    *,
    instrument_type: str,
    instrument_id: str | None = None,
) -> None:
    normalized_instrument_type = normalize_instrument_type(instrument_type)
    if normalized_instrument_type in NAV_HISTORY_INSTRUMENT_TYPES:
        return
    instrument_context = f'"{instrument_id}"' if instrument_id else "instrument"
    raise ValueError(
        "NAV history import is only supported for fund instruments; "
        f"{instrument_context} is {normalized_instrument_type or 'untyped'}."
    )


def parse_persisted_price_contract(
    *,
    price_unit: object,
    price_scale: object,
) -> tuple[PriceUnit, Decimal]:
    normalized_unit = str(price_unit or "").strip().lower()
    if normalized_unit not in PRICE_UNIT_SCALES:
        raise ValueError(f'Unsupported price_unit "{normalized_unit}".')
    try:
        normalized_scale = Decimal(str(price_scale).strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f'Invalid price_scale "{price_scale}".') from error
    if not normalized_scale.is_finite() or normalized_scale <= 0:
        raise ValueError("price_scale must be a finite positive decimal.")

    typed_unit = cast(PriceUnit, normalized_unit)
    expected_scale = PRICE_UNIT_SCALES[typed_unit]
    if normalized_scale != expected_scale:
        raise ValueError(
            f'price_unit "{typed_unit}" requires price_scale "{expected_scale}".'
        )
    return typed_unit, expected_scale


class InstrumentIdentifier(BaseModel):
    identifier_type: IdentifierType
    identifier_value: str = Field(min_length=1)
    is_primary: bool = False

    @field_validator("identifier_value", mode="before")
    @classmethod
    def normalize_identifier_value(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("identifier_value must not be blank.")
            return normalized
        return value


class BrokerIdentifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    broker: str = Field(min_length=1)
    identifier_type: BrokerIdentifierType
    identifier_value: str = Field(min_length=1)
    is_primary: bool = False

    @field_validator("broker", "identifier_value", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("broker identifier text must not be blank")
            return normalized
        return value


class InstrumentCore(BaseModel):
    instrument_id: str = Field(min_length=1)
    instrument_name: str = Field(min_length=1)
    instrument_type: InstrumentType
    currency: str = Field(min_length=1, max_length=8)
    identifiers: list[InstrumentIdentifier] = Field(default_factory=list)
    broker_identifiers: list[BrokerIdentifier] = Field(default_factory=list)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        return normalize_market_data_currency(value)

    @model_validator(mode="after")
    def validate_broker_identifiers(self) -> "InstrumentCore":
        broker_keys: set[tuple[str, str, str]] = set()
        primary_count_by_broker: dict[str, int] = {}
        for identifier in self.broker_identifiers:
            key = (
                identifier.broker.casefold(),
                identifier.identifier_type,
                identifier.identifier_value.casefold(),
            )
            if key in broker_keys:
                raise ValueError("Broker identifiers must be unique")
            broker_keys.add(key)
            if identifier.is_primary:
                primary_count_by_broker[key[0]] = primary_count_by_broker.get(key[0], 0) + 1
        represented_brokers = {identifier.broker.casefold() for identifier in self.broker_identifiers}
        if any(primary_count_by_broker.get(broker, 0) != 1 for broker in represented_brokers):
            raise ValueError("Each represented broker requires exactly one primary identifier")
        return self


class SourceSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_mode: SourceMode = "manual"
    source_email: str = ""
    source_location: str = "Shared data ops"
    source_api_profile: str = ""
    source_email_rules: list[dict[str, object]] = Field(default_factory=list)
    expected_frequency: ExpectedFrequency = "event_driven"
    market_calendar: str | None = Field(default=None, min_length=1)
    release_lag_days: int = Field(default=0, ge=0)
    return_semantics: ReturnSemantics = "unknown"

    @field_validator("market_calendar", mode="before")
    @classmethod
    def normalize_market_calendar(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("market_calendar must be a non-empty string or null.")
            return normalized
        return value

    @field_validator("release_lag_days", mode="before")
    @classmethod
    def reject_boolean_release_lag(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("release_lag_days must be a non-negative integer.")
        return value


class QuoteSelectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trading: list[QuoteBasis] = Field(min_length=1)
    valuation: list[QuoteBasis] = Field(min_length=1)
    total_return: list[QuoteBasis] = Field(min_length=1)
    chart: list[QuoteBasis] = Field(min_length=1)
    reference: list[QuoteBasis] = Field(min_length=1)

    @model_validator(mode="after")
    def prohibit_total_return_valuation_bases(self) -> "QuoteSelectionPolicy":
        for role in ("trading", "valuation", "total_return", "chart", "reference"):
            role_values = getattr(self, role)
            if len(role_values) != len(set(role_values)):
                raise ValueError(f"{role} contains duplicate quote bases")
            invalid_standalone_bases = sorted(
                set(role_values).intersection(QUOTE_SELECTION_PROHIBITED_BASES)
            )
            if invalid_standalone_bases:
                raise ValueError(
                    ", ".join(invalid_standalone_bases)
                    + f" is a component-only quote basis and cannot be selected for {role}"
                )
        invalid = sorted(set(self.valuation).intersection(VALUATION_PROHIBITED_TOTAL_RETURN_BASES))
        if invalid:
            raise ValueError(
                "valuation cannot use total-return quote bases: " + ", ".join(invalid)
            )
        return self


def validate_quote_selection_policy_for_instrument_type(
    *,
    instrument_type: str,
    quote_selection_policy: object,
) -> QuoteSelectionPolicy:
    """Validate generic quote semantics plus the strict private-fund return basis.

    A fund return series is available only when a canonical dividend-reinvested
    NAV exists.  Falling back to unit NAV would silently turn a total-return
    request into a price-return request, so both return-facing roles are exact.
    """

    policy = QuoteSelectionPolicy.model_validate(quote_selection_policy)
    if normalize_instrument_type(instrument_type) != "fund":
        return policy
    for role in ("total_return", "chart"):
        if tuple(getattr(policy, role)) != FUND_TOTAL_RETURN_QUOTE_BASES:
            raise ValueError(
                f"fund {role} must use only total_return_nav; missing total-return "
                "NAV must remain unavailable"
            )
    return policy


class NavLineage(BaseModel):
    """Auditable origin of a canonical NAV observation."""

    model_config = ConfigDict(extra="forbid")

    kind: NavLineageKind
    evidence: dict[str, object] = Field(min_length=1)
    method_version: str | None = Field(default=None, min_length=1)
    anchor_date: date | None = None

    @field_validator("method_version", mode="before")
    @classmethod
    def normalize_method_version(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("method_version must not be blank")
            return normalized
        return value

    @model_validator(mode="after")
    def validate_derivation_evidence(self) -> "NavLineage":
        if self.kind == "provider_explicit":
            if self.method_version is not None or self.anchor_date is not None:
                raise ValueError(
                    "provider_explicit NAV must not declare a derivation method or anchor"
                )
            return self
        if self.method_version is None or self.anchor_date is None:
            raise ValueError(
                "derived_dividend_reinvestment NAV requires method_version and anchor_date"
            )
        factor_record_id = self.evidence.get("factor_record_id")
        factor_logical_key = self.evidence.get("factor_logical_key")
        if not (
            isinstance(factor_record_id, str) and factor_record_id.strip()
        ) and not (
            isinstance(factor_logical_key, str) and factor_logical_key.strip()
        ):
            raise ValueError(
                "derived_dividend_reinvestment NAV requires a factor reference"
            )
        return self


class MarketDataPoint(BaseModel):
    instrument_id: str = Field(min_length=1)
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: date
    value: Decimal
    currency: str = Field(min_length=1, max_length=8)
    price_unit: PriceUnit
    price_scale: Decimal = Field(gt=0)
    provider: str | None = None
    status: DataStatus
    nav_lineage: NavLineage | None = None

    @field_validator("value", mode="before")
    @classmethod
    def validate_positive_value(cls, value: object) -> Decimal:
        return parse_positive_market_data_value(value)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        return normalize_market_data_currency(value)

    @model_validator(mode="after")
    def validate_market_data_point(self) -> "MarketDataPoint":
        validate_market_data_identity(
            metric_family=self.metric_family,
            quote_basis=self.quote_basis,
        )
        parse_persisted_price_contract(
            price_unit=self.price_unit,
            price_scale=self.price_scale,
        )
        if self.metric_family != "nav":
            if self.nav_lineage is not None:
                raise ValueError("nav_lineage is only valid for NAV observations")
            return self
        if self.nav_lineage is None:
            raise ValueError("NAV observations require nav_lineage")
        if (
            self.quote_basis == "official_nav"
            and self.nav_lineage.kind != "provider_explicit"
        ):
            raise ValueError("official_nav must use provider_explicit lineage")
        factor_record_id = self.nav_lineage.evidence.get("factor_record_id")
        has_factor = isinstance(factor_record_id, str) and bool(
            factor_record_id.strip()
        )
        factor_logical_key = self.nav_lineage.evidence.get("factor_logical_key")
        has_logical_factor = isinstance(factor_logical_key, str) and bool(
            factor_logical_key.strip()
        )
        if self.quote_basis == "official_nav" and (has_factor or has_logical_factor):
            raise ValueError("official_nav cannot reference an adjustment factor")
        if self.quote_basis == "total_return_nav":
            if self.status != "complete":
                raise ValueError(
                    "total_return_nav must be complete and factor-backed; "
                    "unavailable total return is represented by row absence"
                )
            if not (has_factor or has_logical_factor):
                raise ValueError(
                    "complete total_return_nav requires a factor_record_id"
                )
        return self


class FundNavEvent(BaseModel):
    """One immutable revision of a stable fund action."""

    model_config = ConfigDict(extra="forbid")

    fund_nav_event_id: str = Field(min_length=1)
    fund_nav_action_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    revision_kind: FundNavEventRevisionKind
    supersedes_fund_nav_event_id: str | None = None
    instrument_id: str = Field(min_length=1)
    event_type: FundNavEventType
    announcement_date: date | None = None
    record_date: date | None = None
    effective_date: date
    payable_date: date | None = None
    sequence_order: int | None = Field(default=None, ge=1)
    cash_per_unit: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=38,
        decimal_places=18,
    )
    unit_ratio: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=38,
        decimal_places=18,
    )
    evidence_kind: FundNavEventEvidenceKind
    source: str = Field(min_length=1)
    external_event_id: str | None = None
    provenance: dict[str, object] = Field(min_length=1)
    recorded_by: str = Field(min_length=1)
    revision_reason: str = Field(min_length=1)
    created_at: str
    updated_at: str

    @field_validator(
        "fund_nav_event_id",
        "fund_nav_action_id",
        "supersedes_fund_nav_event_id",
        "instrument_id",
        "source",
        "external_event_id",
        "recorded_by",
        "revision_reason",
        "created_at",
        "updated_at",
        mode="before",
    )
    @classmethod
    def normalize_audit_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("audit text must not be blank")
            return normalized
        return value

    @model_validator(mode="after")
    def validate_event_contract(self) -> "FundNavEvent":
        if self.revision_kind == "original":
            if self.revision_number != 1 or self.supersedes_fund_nav_event_id is not None:
                raise ValueError(
                    "original fund NAV event revision must be revision 1 and supersede nothing"
                )
        elif self.revision_number < 2 or self.supersedes_fund_nav_event_id is None:
            raise ValueError(
                "correction/cancellation fund NAV event revision must supersede its predecessor"
            )
        if self.supersedes_fund_nav_event_id == self.fund_nav_event_id:
            raise ValueError("fund NAV event revision cannot supersede itself")
        if self.record_date is not None and self.record_date > self.effective_date:
            raise ValueError("record_date cannot be after effective_date")
        if self.event_type == "cash_distribution":
            if self.cash_per_unit is None or self.unit_ratio is not None:
                raise ValueError(
                    "cash_distribution requires cash_per_unit and forbids unit_ratio"
                )
        elif (
            self.unit_ratio is None
            or self.unit_ratio == 1
            or self.cash_per_unit is not None
        ):
            raise ValueError(
                "unit_split requires a non-unit unit_ratio and forbids cash fields"
            )
        return self


class FundNavReinvestmentEvidence(BaseModel):
    """One immutable reinvestment-price evidence revision for an action revision."""

    model_config = ConfigDict(extra="forbid")

    fund_nav_reinvestment_evidence_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    fund_nav_event_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    revision_kind: FundNavEventRevisionKind
    supersedes_fund_nav_reinvestment_evidence_id: str | None = None
    reinvestment_nav: Decimal = Field(
        gt=0,
        max_digits=38,
        decimal_places=18,
    )
    evidence_kind: FundNavEventEvidenceKind
    source: str = Field(min_length=1)
    external_evidence_id: str | None = None
    provenance: dict[str, object] = Field(min_length=1)
    recorded_by: str = Field(min_length=1)
    revision_reason: str = Field(min_length=1)
    created_at: str
    updated_at: str

    @field_validator(
        "fund_nav_reinvestment_evidence_id",
        "instrument_id",
        "fund_nav_event_id",
        "supersedes_fund_nav_reinvestment_evidence_id",
        "source",
        "external_evidence_id",
        "recorded_by",
        "revision_reason",
        "created_at",
        "updated_at",
        mode="before",
    )
    @classmethod
    def normalize_nonblank_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("value must not be blank")
            return normalized
        return value

    @model_validator(mode="after")
    def validate_revision_contract(self) -> "FundNavReinvestmentEvidence":
        predecessor_id = self.supersedes_fund_nav_reinvestment_evidence_id
        if self.revision_kind == "original":
            if self.revision_number != 1 or predecessor_id is not None:
                raise ValueError(
                    "original reinvestment evidence must be revision 1 and supersede nothing"
                )
        elif self.revision_number < 2 or predecessor_id is None:
            raise ValueError(
                "correction/cancellation reinvestment evidence must supersede its predecessor"
            )
        if predecessor_id == self.fund_nav_reinvestment_evidence_id:
            raise ValueError("reinvestment evidence revision cannot supersede itself")
        return self


class FundNavProjectionRun(BaseModel):
    """Immutable snapshot of the exact inputs used for one NAV projection."""

    model_config = ConfigDict(extra="forbid")

    fund_nav_projection_run_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_observation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_kind: FundNavProjectionKind
    projection_status: FundNavProjectionStatus
    method_version: str = Field(min_length=1)
    anchor_date: date | None = None
    source_provider: str = Field(min_length=1)
    evidence: dict[str, object] = Field(min_length=1)
    created_by: str = Field(min_length=1)
    created_at: str

    @field_validator(
        "fund_nav_projection_run_id",
        "instrument_id",
        "input_fingerprint",
        "source_observation_fingerprint",
        "method_version",
        "source_provider",
        "created_by",
        "created_at",
        mode="before",
    )
    @classmethod
    def normalize_projection_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("value must not be blank")
            return normalized
        return value

    @model_validator(mode="after")
    def validate_deterministic_id(self) -> "FundNavProjectionRun":
        expected_id = f"fund-nav-projection-{self.input_fingerprint}"
        if self.fund_nav_projection_run_id != expected_id:
            raise ValueError(
                "fund_nav_projection_run_id must be derived from input_fingerprint"
            )
        if self.projection_status in {"complete", "partial"} and self.anchor_date is None:
            raise ValueError(
                "complete or partial projection run requires anchor_date"
            )
        if self.projection_status == "unavailable":
            unavailable_reason = self.evidence.get("unavailable_reason")
            if self.anchor_date is not None:
                raise ValueError("unavailable projection run must not declare anchor_date")
            if self.projection_kind == "hybrid_reanchored":
                raise ValueError(
                    "unavailable projection run cannot claim hybrid reanchoring"
                )
            if not isinstance(unavailable_reason, str) or not unavailable_reason.strip():
                raise ValueError(
                    "unavailable projection run requires evidence.unavailable_reason"
                )
        return self


class FundNavAdjustmentFactor(BaseModel):
    """Tushare-style cumulative factor level used with a unit-NAV curve."""

    model_config = ConfigDict(extra="forbid")

    fund_nav_adjustment_factor_id: str = Field(min_length=1)
    factor_logical_key: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    fund_nav_projection_run_id: str = Field(min_length=1)
    as_of_date: date
    factor_level: Decimal = Field(
        gt=0,
        max_digits=38,
        decimal_places=18,
    )
    factor_kind: FundNavAdjustmentFactorKind
    fund_nav_event_id: str | None = None
    fund_nav_reinvestment_evidence_id: str | None = None
    previous_fund_nav_adjustment_factor_id: str | None = None
    evidence_kind: FundNavFactorEvidenceKind
    method_version: str = Field(min_length=1)
    anchor_date: date
    source_provider: str = Field(min_length=1)
    evidence: dict[str, object] = Field(min_length=1)
    created_at: str
    updated_at: str

    @field_validator("factor_level", mode="before")
    @classmethod
    def normalize_factor_level(cls, value: object) -> object:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("factor_level must be a valid decimal") from error
        if not parsed.is_finite() or parsed <= 0:
            raise ValueError("factor_level must be finite and positive")
        return _quantized_fund_nav_factor(parsed)

    @field_validator(
        "fund_nav_adjustment_factor_id",
        "factor_logical_key",
        "instrument_id",
        "fund_nav_projection_run_id",
        "fund_nav_event_id",
        "fund_nav_reinvestment_evidence_id",
        "previous_fund_nav_adjustment_factor_id",
        "method_version",
        "source_provider",
        "created_at",
        "updated_at",
        mode="before",
    )
    @classmethod
    def normalize_nonblank_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("value must not be blank")
            return normalized
        return value

    @model_validator(mode="after")
    def validate_factor_contract(self) -> "FundNavAdjustmentFactor":
        expected_id = deterministic_fund_nav_adjustment_factor_id(
            fund_nav_projection_run_id=self.fund_nav_projection_run_id,
            factor_logical_key=self.factor_logical_key,
        )
        if self.fund_nav_adjustment_factor_id != expected_id:
            raise ValueError(
                "fund_nav_adjustment_factor_id must be derived from run id and factor_logical_key"
            )
        if self.anchor_date > self.as_of_date:
            raise ValueError("anchor_date cannot be after as_of_date")
        if self.factor_kind == "provider_implied":
            if (
                self.fund_nav_event_id is not None
                or self.fund_nav_reinvestment_evidence_id is not None
                or self.previous_fund_nav_adjustment_factor_id is not None
                or self.anchor_date != self.as_of_date
            ):
                raise ValueError(
                    "provider_implied factor must be a same-date segment root without "
                    "fund-event or reinvestment-evidence references"
                )
            if self.evidence_kind not in {
                "provider_total_return",
                "provider_cash_cumulative",
            }:
                raise ValueError(
                    "provider_implied factor requires provider total-return or "
                    "cash-cumulative evidence"
                )
        else:
            if self.evidence_kind in {
                "zero_cash_anchor",
                "window_normalized_anchor",
            }:
                if (
                    self.fund_nav_event_id is not None
                    or self.fund_nav_reinvestment_evidence_id is not None
                    or self.previous_fund_nav_adjustment_factor_id is not None
                    or self.factor_level != Decimal(1)
                    or self.anchor_date != self.as_of_date
                ):
                    raise ValueError(
                        "an event-derived anchor requires level 1, no "
                        "event/evidence, and anchor_date == as_of_date"
                    )
            elif self.fund_nav_event_id is None:
                raise ValueError("event_derived factor requires fund_nav_event_id")
            elif self.previous_fund_nav_adjustment_factor_id is None:
                raise ValueError(
                    "event_derived fund-event factor requires a previous factor"
                )
            elif self.evidence_kind != "fund_nav_event":
                raise ValueError(
                    "event_derived factor requires fund-event evidence"
                )
        return self


class CorporateActionEvent(BaseModel):
    corporate_action_event_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    action_type: CorporateActionType = "share_split"
    announcement_date: date | None = None
    record_date: date | None = None
    effective_date: date
    payable_date: date | None = None
    new_units: Decimal = Field(gt=0)
    old_units: Decimal = Field(gt=0)
    quantity_rounding: QuantityRounding = "exact"
    quantity_precision: int = Field(default=0, ge=0, le=12)
    cost_basis_treatment: Literal["carry"] = "carry"
    source: str = Field(min_length=1)
    external_event_id: str | None = None
    status: CorporateActionStatus = "confirmed"
    provenance: dict[str, object] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def validate_event_dates_and_ratio(self) -> "CorporateActionEvent":
        if self.record_date is not None and self.record_date > self.effective_date:
            raise ValueError("record_date cannot be after effective_date.")
        if self.new_units == self.old_units:
            raise ValueError("A share split ratio must change the number of units.")
        return self
