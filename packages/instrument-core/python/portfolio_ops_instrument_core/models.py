from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


InstrumentType = Literal["fund", "etf", "index", "bond", "equity", "cash", "fx", "other"]
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


class QuoteSelectionPolicy(BaseModel):
    trading: list[QuoteBasis] = Field(default_factory=list)
    valuation: list[QuoteBasis] = Field(default_factory=list)
    total_return: list[QuoteBasis] = Field(default_factory=list)
    chart: list[QuoteBasis] = Field(default_factory=list)
    reference: list[QuoteBasis] = Field(default_factory=list)

    @model_validator(mode="after")
    def prohibit_total_return_valuation_bases(self) -> "QuoteSelectionPolicy":
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
    provider: str | None = None
    status: DataStatus = "complete"


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
