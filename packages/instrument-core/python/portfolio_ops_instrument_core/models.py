from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


InstrumentType = Literal["fund", "etf", "index", "bond", "equity", "cash", "fx", "other"]
INSTRUMENT_TYPES = frozenset(
    {"fund", "etf", "index", "bond", "equity", "cash", "fx", "other"}
)
ExpectedFrequency = Literal["daily", "weekly", "monthly", "event_driven"]
SourceMode = Literal["manual", "email", "api"]
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
PriceUnit = Literal["per_unit", "percent_of_par", "rate"]
QuoteBasis = Literal[
    "last",
    "close",
    "adjusted_close",
    "official_nav",
    "total_return_nav",
    "cumulative_nav",
    "accumulated_nav",
    "cum_nav",
    "dividend_adjusted_nav",
    "reinvested_nav",
    "spot",
    "clean_price",
    "dirty_price",
    "par",
    "accrued_interest",
]
QuoteRole = Literal["trading", "valuation", "total_return", "chart", "reference"]
DataStatus = Literal["complete", "partial", "unavailable"]
CorporateActionType = Literal["share_split"]
CorporateActionStatus = Literal["detected", "confirmed", "cancelled"]
QuantityRounding = Literal["exact", "truncate", "round_half_up", "cash_in_lieu"]
VALUATION_PROHIBITED_TOTAL_RETURN_BASES = frozenset(
    {
        "adjusted_close",
        "total_return_nav",
        "cumulative_nav",
        "accumulated_nav",
        "cum_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
    }
)
CASH_CUMULATIVE_NAV_BASES = frozenset(
    {"cumulative_nav", "accumulated_nav", "cum_nav"}
)
QUOTE_BASIS_METRIC_FAMILY: dict[str, MetricFamily] = {
    "last": "price",
    "close": "price",
    "adjusted_close": "price",
    "official_nav": "nav",
    "total_return_nav": "nav",
    "cumulative_nav": "nav",
    "accumulated_nav": "nav",
    "cum_nav": "nav",
    "dividend_adjusted_nav": "nav",
    "reinvested_nav": "nav",
    "spot": "fx",
    "clean_price": "price",
    "dirty_price": "price",
    "par": "price",
    "accrued_interest": "price",
}
PRICE_UNIT_SCALES: dict[PriceUnit, Decimal] = {
    "per_unit": Decimal("1"),
    "percent_of_par": Decimal("0.01"),
    "rate": Decimal("1"),
}
QUOTE_SELECTION_PROHIBITED_BASES = frozenset({"accrued_interest"})
NAV_HISTORY_INSTRUMENT_TYPES = frozenset({"fund"})


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
    if quote_basis == "accrued_interest" and normalized_instrument_type != "bond":
        raise ValueError("accrued_interest is only valid for bond price data.")
    if normalized_instrument_type == "bond" and metric_family == "price":
        return "percent_of_par", PRICE_UNIT_SCALES["percent_of_par"]
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


class InstrumentCore(BaseModel):
    instrument_id: str = Field(min_length=1)
    instrument_name: str = Field(min_length=1)
    instrument_type: InstrumentType
    currency: str = Field(min_length=1, max_length=8)
    identifiers: list[InstrumentIdentifier] = Field(default_factory=list)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        return normalize_market_data_currency(value)


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
        for role in ("total_return", "chart"):
            invalid_cumulative = sorted(
                set(getattr(self, role)).intersection(CASH_CUMULATIVE_NAV_BASES)
            )
            if invalid_cumulative:
                raise ValueError(
                    f"{role} cannot use cash-cumulative NAV as total return: "
                    + ", ".join(invalid_cumulative)
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
