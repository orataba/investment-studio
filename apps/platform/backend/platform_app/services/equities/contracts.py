from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class EquitySearchResult(BaseModel):
    symbol: str
    fmp_symbol: str
    name: str
    exchange_code: str = Field(pattern=r"^[A-Z]{4}$")
    exchange_label: str
    market: str
    currency: str
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    existing_instrument_id: str | None = None


class EquitySearchResponse(BaseModel):
    results: list[EquitySearchResult]


class EquityMaterializeRequest(BaseModel):
    fmp_symbol: str = Field(min_length=1)
    refresh_eod: bool = True

    @field_validator("fmp_symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().upper()
            if not normalized:
                raise ValueError("fmp_symbol must not be blank.")
            return normalized
        return value
