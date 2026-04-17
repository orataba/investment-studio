from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class PlatformAppCard(BaseModel):
    app_id: str
    name: str
    url: str
    api_url: str | None = None
    eyebrow: str
    description: str
    availability: str = "ready"


class PlatformAppsResponse(BaseModel):
    platform_name: str
    apps: list[PlatformAppCard]


AssetType = Literal["fund", "bond", "equity", "cash", "fx", "other"]
IdentifierType = Literal["ticker", "isin", "cusip", "sedol", "internal", "other"]
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
SourceMode = Literal["manual", "email", "api"]
SupportedCurrency = Literal["USD", "HKD", "CNY"]
FxRateSourceKind = Literal["direct", "inverse", "cross"]
InstrumentLifecycleStatus = Literal["active", "archived"]
EmailParserProfile = Literal[
    "generic_nav_table",
    "label_nav_snapshot",
]

SUPPORTED_FX_CURRENCIES: tuple[SupportedCurrency, ...] = ("USD", "HKD", "CNY")


class PlatformAssetIdentifier(BaseModel):
    identifier_type: IdentifierType
    identifier_value: str = Field(min_length=1)
    is_primary: bool = False


class PlatformMarketDataPoint(BaseModel):
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: date
    value: Decimal
    currency: str = Field(min_length=1, max_length=8)
    provider: str | None = None
    status: DataStatus = "complete"


class PlatformQuoteSelectionPolicy(BaseModel):
    trading: list[QuoteBasis] = Field(default_factory=list)
    valuation: list[QuoteBasis] = Field(default_factory=list)
    total_return: list[QuoteBasis] = Field(default_factory=list)
    chart: list[QuoteBasis] = Field(default_factory=list)
    reference: list[QuoteBasis] = Field(default_factory=list)


class PlatformEmailRule(BaseModel):
    sender_equals: list[str] = Field(default_factory=list)
    subject_contains: list[str] = Field(default_factory=list)
    subject_excludes: list[str] = Field(default_factory=list)
    attachment_name_contains: list[str] = Field(default_factory=list)
    attachment_name_excludes: list[str] = Field(default_factory=list)
    attachment_extensions: list[str] = Field(default_factory=list)
    attachment_content_contains: list[str] = Field(default_factory=list)
    row_code_equals: list[str] = Field(default_factory=list)
    row_name_equals: list[str] = Field(default_factory=list)
    row_name_contains: list[str] = Field(default_factory=list)
    row_name_excludes: list[str] = Field(default_factory=list)
    parser_profile: EmailParserProfile = "generic_nav_table"

    @field_validator(
        "sender_equals",
        "subject_contains",
        "subject_excludes",
        "attachment_name_contains",
        "attachment_name_excludes",
        "attachment_content_contains",
        "row_code_equals",
        "row_name_equals",
        "row_name_contains",
        "row_name_excludes",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return value

    @field_validator("attachment_extensions", mode="before")
    @classmethod
    def normalize_extensions(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                text = str(item).strip().lower().lstrip(".")
                if text and text not in normalized:
                    normalized.append(text)
            return normalized
        return value


class PlatformSourceSettings(BaseModel):
    source_mode: SourceMode = "manual"
    source_email: str = ""
    source_location: str = "Shared data ops"
    source_api_profile: str = ""
    source_email_rules: list[PlatformEmailRule] = Field(default_factory=list)


class PlatformRefreshStatus(BaseModel):
    status: str = "idle"
    message: str = ""
    requested_at: str | None = None
    requested_by: str | None = None
    mode: SourceMode = "manual"


class PlatformLifecycleState(BaseModel):
    status: InstrumentLifecycleStatus = "active"
    changed_at: str | None = None
    changed_by: str | None = None


class PlatformInstrumentRecord(BaseModel):
    asset_id: str
    asset_name: str
    asset_type: AssetType
    currency: str
    identifiers: list[PlatformAssetIdentifier]
    latest_market_data: list[PlatformMarketDataPoint]
    quote_selection_policy: PlatformQuoteSelectionPolicy
    coverage_state: DataStatus
    source_settings: PlatformSourceSettings
    refresh_status: PlatformRefreshStatus
    lifecycle_state: PlatformLifecycleState


class PlatformInstrumentDetail(PlatformInstrumentRecord):
    market_data: list[PlatformMarketDataPoint]


class PlatformInstrumentsResponse(BaseModel):
    registry_name: str
    instruments: list[PlatformInstrumentRecord]


class PlatformInstrumentCreateRequest(BaseModel):
    asset_name: str = Field(min_length=1)
    asset_type: AssetType
    currency: str = Field(min_length=1, max_length=8)
    identifiers: list[PlatformAssetIdentifier] = Field(default_factory=list)


class PlatformMarketDataUpsertRequest(BaseModel):
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: date
    value: Decimal
    currency: str = Field(min_length=1, max_length=8)
    provider: str | None = None
    status: DataStatus = "complete"

    @model_validator(mode="after")
    def validate_quote_basis(self) -> "PlatformMarketDataUpsertRequest":
        allowed_bases: dict[str, set[str]] = {
            "price": {"last", "close", "adjusted_close", "clean_price", "dirty_price", "par"},
            "nav": {"official_nav", "total_return_nav"},
            "fx": {"spot"},
        }
        if self.quote_basis not in allowed_bases[self.metric_family]:
            raise ValueError("quote_basis does not match metric_family.")
        return self


class PlatformFxRateRecord(BaseModel):
    base_currency: SupportedCurrency
    quote_currency: SupportedCurrency
    rate: Decimal
    as_of_date: date
    source_kind: FxRateSourceKind
    asset_id: str | None = None
    source_asset_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    status: DataStatus = "complete"


class PlatformFxRatesResponse(BaseModel):
    supported_currencies: list[SupportedCurrency]
    maintained_pairs: list[str]
    rates: list[PlatformFxRateRecord]


class PlatformFxRateUpsertRequest(BaseModel):
    base_currency: SupportedCurrency
    quote_currency: SupportedCurrency
    rate: Decimal = Field(gt=0)
    as_of_date: date
    provider: str | None = None
    status: DataStatus = "complete"

    @field_validator("base_currency", "quote_currency", mode="before")
    @classmethod
    def uppercase_currency(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def validate_pair(self) -> "PlatformFxRateUpsertRequest":
        if self.base_currency == self.quote_currency:
            raise ValueError("FX rate requires distinct base and quote currencies.")
        return self


class PlatformSourceSettingsUpdateRequest(BaseModel):
    source_mode: SourceMode = "manual"
    source_email: str | None = None
    source_location: str | None = None
    source_api_profile: str | None = None
    source_email_rules: list[PlatformEmailRule] | None = None


class PlatformRefreshTriggerRequest(BaseModel):
    updated_by: str | None = None
    full_history: bool = False


class PlatformLifecycleTransitionRequest(BaseModel):
    updated_by: str | None = None


class PlatformNavImportRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    provider: str | None = None
    status: DataStatus = "complete"
    updated_by: str | None = None
