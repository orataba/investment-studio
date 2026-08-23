from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


MaterializableSecurityType = Literal["equity", "etf"]
SecurityCatalogProvider = Literal["fmp"]


class SecuritySearchResult(BaseModel):
    instrument_type: MaterializableSecurityType
    symbol: str
    catalog_provider: SecurityCatalogProvider
    catalog_symbol: str
    name: str
    exchange_code: str = Field(pattern=r"^[A-Z]{4}$")
    exchange_label: str
    market: str
    currency: str
    currency_verified: bool = True
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    existing_instrument_id: str | None = None


class SecuritySearchResponse(BaseModel):
    results: list[SecuritySearchResult]
    catalog_errors: dict[MaterializableSecurityType, str] = Field(default_factory=dict)


class SecurityMaterializeRequest(BaseModel):
    instrument_type: MaterializableSecurityType
    catalog_provider: SecurityCatalogProvider
    catalog_symbol: str = Field(min_length=1)
    refresh_eod: bool = True

    @field_validator("catalog_symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().upper()
            if not normalized:
                raise ValueError("catalog_symbol must not be blank.")
            return normalized
        return value
