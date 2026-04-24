from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


AssetType = Literal["fund", "bond", "equity", "cash", "fx", "other"]
IdentifierType = Literal["ticker", "isin", "cusip", "sedol", "internal", "fund_name", "other"]
MetricFamily = Literal["price", "nav", "fx"]
QuoteBasis = Literal[
    "last",
    "close",
    "adjusted_close",
    "official_nav",
    "total_return_nav",
    "spot",
    "clean_price",
    "dirty_price",
    "par",
]
QuoteRole = Literal["trading", "valuation", "total_return", "chart", "reference"]
DataStatus = Literal["complete", "partial", "unavailable"]


class AssetIdentifier(BaseModel):
    identifier_type: IdentifierType
    identifier_value: str = Field(min_length=1)
    is_primary: bool = False


class AssetCore(BaseModel):
    asset_id: str = Field(min_length=1)
    asset_name: str = Field(min_length=1)
    asset_type: AssetType
    currency: str = Field(min_length=1, max_length=8)
    identifiers: list[AssetIdentifier] = Field(default_factory=list)


class QuoteSelectionPolicy(BaseModel):
    trading: list[QuoteBasis] = Field(default_factory=list)
    valuation: list[QuoteBasis] = Field(default_factory=list)
    total_return: list[QuoteBasis] = Field(default_factory=list)
    chart: list[QuoteBasis] = Field(default_factory=list)
    reference: list[QuoteBasis] = Field(default_factory=list)


class MarketDataPoint(BaseModel):
    asset_id: str = Field(min_length=1)
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: date
    value: Decimal
    currency: str = Field(min_length=1, max_length=8)
    provider: str | None = None
    status: DataStatus = "complete"
