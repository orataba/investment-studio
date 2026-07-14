from base64 import b64decode
from datetime import date, datetime
from decimal import Decimal
import binascii
import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from portfolio_ops_instrument_core.models import (
    InstrumentIdentifier as PlatformInstrumentIdentifier,
)
from portfolio_ops_instrument_core.models import (
    CorporateActionEvent as PlatformCorporateActionEvent,
)
from portfolio_ops_instrument_core.models import (
    CanonicalCurrencyCode,
    CanonicalQuoteResolution,
    DataStatus,
    InstrumentType,
    MetricFamily,
    QuoteBasis,
    QuoteIngestionTimeState,
    QuoteNumericScaleState,
    QuoteRole,
    QuoteFreshnessPolicy,
    SourceObservationStatus,
)
from portfolio_ops_instrument_core.models import (
    QuoteSelectionPolicy as PlatformQuoteSelectionPolicy,
)


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


SourceMode = Literal["manual", "email", "api"]
RefreshChannel = Literal["configured", "email", "tushare", "all"]
SupportedCurrency = Literal["USD", "HKD", "CNY"]
FxRateSourceKind = Literal["direct", "inverse", "cross"]
InstrumentLifecycleStatus = Literal["active", "archived"]
EmailParserProfile = Literal[
    "generic_nav_table",
    "label_nav_snapshot",
]

SUPPORTED_FX_CURRENCIES: tuple[SupportedCurrency, ...] = ("USD", "HKD", "CNY")
MAX_NAV_IMPORT_BYTES = 25 * 1024 * 1024
MAX_NAV_IMPORT_BASE64_CHARS = ((MAX_NAV_IMPORT_BYTES + 2) // 3) * 4
PLAIN_DECIMAL_INPUT_PATTERN = r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?$"


def _require_plain_decimal_json_string(value: object) -> object:
    if not isinstance(value, str):
        raise ValueError("Decimal inputs must be JSON strings.")
    normalized = value.strip()
    if not re.fullmatch(PLAIN_DECIMAL_INPUT_PATTERN, normalized):
        raise ValueError("Decimal inputs must use plain decimal notation.")
    return normalized


PlainDecimalInput = Annotated[
    Decimal,
    BeforeValidator(_require_plain_decimal_json_string),
    WithJsonSchema(
        {"type": "string", "pattern": PLAIN_DECIMAL_INPUT_PATTERN},
        mode="validation",
    ),
]


class PlatformMarketDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_series_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: date
    value: Decimal
    value_input_scale: int = Field(ge=0)
    numeric_scale_state: QuoteNumericScaleState
    payload_schema_version: Literal[1, 2]
    currency: CanonicalCurrencyCode
    source_ref: str | None = None
    status: Literal["complete"] = "complete"
    source_published_at: datetime | None = None
    ingested_at: datetime
    ingestion_time_state: QuoteIngestionTimeState
    payload_hash: str = Field(min_length=1)


class PlatformQuoteObservationRevision(BaseModel):
    instrument_id: str = Field(min_length=1)
    quote_series_id: str = Field(min_length=1)
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    currency: CanonicalCurrencyCode
    observation_id: str = Field(min_length=1)
    as_of_date: date
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=1)
    value: Decimal | None = None
    value_input_scale: int | None = Field(default=None, ge=0)
    numeric_scale_state: QuoteNumericScaleState | None = None
    payload_schema_version: Literal[1, 2]
    source_ref: str | None = None
    status: Literal["complete", "partial", "rejected", "withdrawn"]
    source_published_at: datetime | None = None
    ingested_at: datetime
    ingestion_time_state: QuoteIngestionTimeState
    payload_hash: str = Field(min_length=1)
    is_current: bool
    superseded_at: datetime | None = None


class PlatformQuoteObservationRevisionsResponse(BaseModel):
    instrument_id: str = Field(min_length=1)
    limit: int = Field(ge=1, le=5000)
    truncated: bool
    revisions: list[PlatformQuoteObservationRevision]


class PlatformExplicitQuoteResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolver_strategy_version: Literal["canonical_quote_resolver.v1"]
    instrument_id: str = Field(min_length=1)
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    currency: CanonicalCurrencyCode
    requested_as_of_date: date
    freshness_policy: QuoteFreshnessPolicy


class PlatformRoleQuoteResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolver_strategy_version: Literal["canonical_quote_resolver.v1"]
    quote_selection_policy_version: Literal["quote_selection_policy.v1"]
    instrument_id: str = Field(min_length=1)
    role: QuoteRole
    currency: CanonicalCurrencyCode
    requested_as_of_date: date
    freshness_policy: QuoteFreshnessPolicy


class PlatformCanonicalQuoteResolution(CanonicalQuoteResolution):
    pass


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
    source_location: str = "Database Dashboard"
    source_api_profile: str = ""
    source_email_rules: list[PlatformEmailRule] = Field(default_factory=list)


class PlatformRefreshStatus(BaseModel):
    status: str = "idle"
    message: str = ""
    requested_at: str | None = None
    requested_by: str | None = None
    mode: SourceMode = "manual"
    last_successful_requested_at: str | None = None


class PlatformLifecycleState(BaseModel):
    status: InstrumentLifecycleStatus = "active"
    changed_at: str | None = None
    changed_by: str | None = None
    canonical_instrument_id: str | None = None


class PlatformInstrumentRecord(BaseModel):
    instrument_id: str
    instrument_name: str
    instrument_type: InstrumentType
    currency: CanonicalCurrencyCode
    identifiers: list[PlatformInstrumentIdentifier]
    latest_market_data: list[PlatformMarketDataPoint]
    quote_selection_policy: PlatformQuoteSelectionPolicy
    quote_selection_policy_version: Literal["quote_selection_policy.v1"]
    quote_selection_policy_revision: str = Field(min_length=1)
    coverage_state: DataStatus
    source_settings: PlatformSourceSettings
    refresh_status: PlatformRefreshStatus
    lifecycle_state: PlatformLifecycleState
    market_data_updated_at: str | None = None
    corporate_actions: list[PlatformCorporateActionEvent] = Field(default_factory=list)


class PlatformInstrumentDetail(PlatformInstrumentRecord):
    market_data: list[PlatformMarketDataPoint]


class PlatformInstrumentsResponse(BaseModel):
    registry_name: str
    instruments: list[PlatformInstrumentRecord]


class PlatformInstrumentCreateRequest(BaseModel):
    instrument_name: str = Field(min_length=1)
    instrument_type: InstrumentType
    currency: CanonicalCurrencyCode
    identifiers: list[PlatformInstrumentIdentifier] = Field(min_length=1)
    quote_selection_policy: PlatformQuoteSelectionPolicy | None = None

    @field_validator("instrument_name", mode="before")
    @classmethod
    def normalize_instrument_name(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("instrument_name must not be blank.")
            return normalized
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().upper()
            if not normalized:
                raise ValueError("currency must not be blank.")
            return normalized
        return value

    @model_validator(mode="after")
    def validate_primary_identifier(self) -> "PlatformInstrumentCreateRequest":
        if sum(1 for item in self.identifiers if item.is_primary) != 1:
            raise ValueError("Exactly one identifier must be primary.")
        return self


class PlatformQuoteSelectionPolicyUpdateRequest(BaseModel):
    quote_selection_policy: PlatformQuoteSelectionPolicy


class PlatformMarketDataUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: date
    value: PlainDecimalInput
    currency: CanonicalCurrencyCode
    source_ref: str | None = None
    status: SourceObservationStatus = "complete"
    source_published_at: datetime | None = None

    @model_validator(mode="after")
    def validate_quote_basis(self) -> "PlatformMarketDataUpsertRequest":
        allowed_bases: dict[str, set[str]] = {
            "price": {
                "last",
                "close",
                "adjusted_close",
                "clean_price",
                "dirty_price",
                "par",
            },
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
    instrument_id: str | None = None
    source_instrument_ids: list[str] = Field(default_factory=list)
    source_ref: str | None = None
    status: DataStatus = "complete"


class PlatformFxRatesResponse(BaseModel):
    supported_currencies: list[SupportedCurrency]
    maintained_pairs: list[str]
    rates: list[PlatformFxRateRecord]


class PlatformFxRateUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: SupportedCurrency
    quote_currency: SupportedCurrency
    rate: PlainDecimalInput = Field(gt=0)
    as_of_date: date
    source_ref: str | None = None
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
    source: RefreshChannel = "configured"


class PlatformBulkRefreshRequest(BaseModel):
    source: RefreshChannel = "all"
    updated_by: str | None = None
    full_history: bool = False
    include_inactive: bool = False


class PlatformBulkRefreshResult(BaseModel):
    instrument_id: str
    instrument_name: str
    instrument_type: InstrumentType
    source_mode: SourceMode
    source_api_profile: str = ""
    status: str
    message: str


class PlatformBulkRefreshResponse(BaseModel):
    source: RefreshChannel
    refreshed_count: int
    skipped_count: int
    results: list[PlatformBulkRefreshResult]


class PlatformLifecycleTransitionRequest(BaseModel):
    updated_by: str | None = None


class PlatformNavImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=1, max_length=MAX_NAV_IMPORT_BYTES)
    source_ref: str | None = None
    status: DataStatus = "complete"
    updated_by: str | None = None


class PlatformNavImportFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1, max_length=255)
    file_content_base64: str = Field(
        min_length=1,
        max_length=MAX_NAV_IMPORT_BASE64_CHARS,
    )
    source_ref: str | None = None
    status: DataStatus = "complete"
    updated_by: str | None = None

    @field_validator("file_name")
    @classmethod
    def normalize_file_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("file_content_base64")
    @classmethod
    def normalize_base64(cls, value: str) -> str:
        return value.strip()

    def decoded_bytes(self) -> bytes:
        try:
            decoded = b64decode(self.file_content_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("Invalid base64 file payload.") from error
        if len(decoded) > MAX_NAV_IMPORT_BYTES:
            raise ValueError(
                f"NAV import file exceeds the {MAX_NAV_IMPORT_BYTES}-byte limit."
            )
        return decoded


class PlatformNavImportPreviewRequest(BaseModel):
    raw_text: str | None = Field(default=None, max_length=MAX_NAV_IMPORT_BYTES)
    file_name: str | None = Field(default=None, max_length=255)
    file_content_base64: str | None = Field(
        default=None,
        max_length=MAX_NAV_IMPORT_BASE64_CHARS,
    )

    @model_validator(mode="after")
    def validate_source(self) -> "PlatformNavImportPreviewRequest":
        has_text = bool((self.raw_text or "").strip())
        has_file = bool((self.file_name or "").strip()) and bool(
            (self.file_content_base64 or "").strip()
        )
        if has_text == has_file:
            raise ValueError(
                "Provide either raw_text or file_name + file_content_base64."
            )
        return self

    def decoded_bytes(self) -> bytes | None:
        if not self.file_content_base64:
            return None
        try:
            decoded = b64decode(self.file_content_base64.strip(), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("Invalid base64 file payload.") from error
        if len(decoded) > MAX_NAV_IMPORT_BYTES:
            raise ValueError(
                f"NAV import file exceeds the {MAX_NAV_IMPORT_BYTES}-byte limit."
            )
        return decoded


class PlatformNavImportPreviewRow(BaseModel):
    as_of_date: date
    nav: Decimal | None = None
    cumulative_nav: Decimal | None = None
    nav_with_dividend: Decimal | None = None
    currency: CanonicalCurrencyCode
    frequency: str = Field(min_length=1)
    instrument_code: str | None = None
    instrument_name: str | None = None


class PlatformNavImportPreviewResponse(BaseModel):
    row_count: int
    rows: list[PlatformNavImportPreviewRow]
