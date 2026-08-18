from base64 import b64decode
from datetime import date
from decimal import Decimal
import binascii
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator
from portfolio_ops_instrument_core.fx_contract import (
    SUPPORTED_FX_CURRENCIES,
    normalize_fx_currency,
    parse_positive_fx_rate,
)
from portfolio_ops_instrument_core.models import InstrumentIdentifier as PlatformInstrumentIdentifier
from portfolio_ops_instrument_core.models import BrokerIdentifier as PlatformBrokerIdentifier
from portfolio_ops_instrument_core.models import InstrumentCore as SharedInstrumentCore
from portfolio_ops_instrument_core.models import CorporateActionEvent as PlatformCorporateActionEvent
from portfolio_ops_instrument_core.models import (
    FundNavAdjustmentFactor as PlatformFundNavAdjustmentFactor,
)
from portfolio_ops_instrument_core.models import FundNavEvent as PlatformFundNavEvent
from portfolio_ops_instrument_core.models import (
    FundNavProjectionRun as SharedFundNavProjectionRun,
)
from portfolio_ops_instrument_core.models import (
    FundNavReinvestmentEvidence as PlatformFundNavReinvestmentEvidence,
)
from portfolio_ops_instrument_core.models import (
    DataStatus,
    ExpectedFrequency,
    IdentifierType,
    InstrumentType,
    MetricFamily,
    NavLineage,
    PriceUnit,
    QuoteBasis,
    QuoteRole,
    ReturnSemantics,
    SourceSettings as SharedSourceSettings,
    canonical_price_contract,
    parse_persisted_price_contract,
    parse_positive_market_data_value,
    validate_market_data_identity,
)
from portfolio_ops_instrument_core.models import QuoteSelectionPolicy as PlatformQuoteSelectionPolicy


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
RefreshOperationMode = Literal[
    "manual",
    "email",
    "api",
    "projection_reconciliation",
]
RefreshChannel = Literal["configured", "email", "tushare", "fmp", "all"]
FxRateSourceKind = Literal["direct", "inverse", "cross"]
InstrumentLifecycleStatus = Literal["active", "archived"]
EmailParserProfile = Literal[
    "generic_nav_table",
    "label_nav_snapshot",
    "ta_virtual_performance_ledger_initial_nav",
]

MAX_NAV_IMPORT_BYTES = 25 * 1024 * 1024
MAX_NAV_IMPORT_BASE64_CHARS = ((MAX_NAV_IMPORT_BYTES + 2) // 3) * 4


def _validate_supported_fx_currency(value: object) -> str:
    normalized = normalize_fx_currency(value)
    if normalized not in SUPPORTED_FX_CURRENCIES:
        raise ValueError(
            "currency must be one of " + ", ".join(SUPPORTED_FX_CURRENCIES) + "."
        )
    return normalized


SupportedCurrency = Annotated[str, BeforeValidator(_validate_supported_fx_currency)]
PositiveFxRate = Annotated[Decimal, BeforeValidator(parse_positive_fx_rate)]


class PlatformMarketDataPoint(BaseModel):
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

    @model_validator(mode="after")
    def validate_price_identity(self) -> "PlatformMarketDataPoint":
        validate_market_data_identity(
            metric_family=self.metric_family,
            quote_basis=self.quote_basis,
        )
        parse_persisted_price_contract(
            price_unit=self.price_unit,
            price_scale=self.price_scale,
        )
        return self


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


class PlatformSourceSettings(SharedSourceSettings):
    source_location: str = "Database Dashboard"
    source_email_rules: list[PlatformEmailRule] = Field(default_factory=list)


class PlatformRefreshStatus(BaseModel):
    status: str = "idle"
    message: str = ""
    requested_at: str | None = None
    requested_by: str | None = None
    # This records the operation that last changed the refresh status.  It is
    # intentionally broader than SourceMode, which remains the configured data
    # source contract used by PlatformSourceSettings.
    mode: RefreshOperationMode = "manual"
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
    currency: str
    exchange_code: str | None = Field(default=None, pattern=r"^[A-Z]{4}$")
    identifiers: list[PlatformInstrumentIdentifier]
    broker_identifiers: list[PlatformBrokerIdentifier] = Field(default_factory=list)
    latest_market_data: list[PlatformMarketDataPoint]
    quote_selection_policy: PlatformQuoteSelectionPolicy
    coverage_state: DataStatus
    source_settings: PlatformSourceSettings
    refresh_status: PlatformRefreshStatus
    lifecycle_state: PlatformLifecycleState
    market_data_updated_at: str | None = None
    corporate_actions: list[PlatformCorporateActionEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_market_data_contracts(self) -> "PlatformInstrumentRecord":
        SharedInstrumentCore.model_validate(
            {
                "instrument_id": self.instrument_id,
                "instrument_name": self.instrument_name,
                "instrument_type": self.instrument_type,
                "currency": self.currency,
                "exchange_code": self.exchange_code,
                "identifiers": self.identifiers,
                "broker_identifiers": self.broker_identifiers,
            }
        )
        expected_currency = self.currency.strip().upper()
        points = [*self.latest_market_data]
        detail_points = getattr(self, "market_data", None)
        if isinstance(detail_points, list):
            points.extend(detail_points)
        for point in points:
            expected_unit, expected_scale = canonical_price_contract(
                instrument_type=self.instrument_type,
                metric_family=point.metric_family,
                quote_basis=point.quote_basis,
            )
            if (point.price_unit, point.price_scale) != (expected_unit, expected_scale):
                raise ValueError(
                    "Market-data price contract does not match the parent instrument type."
                )
            if point.currency.strip().upper() != expected_currency:
                raise ValueError(
                    "Market-data currency does not match the parent instrument currency."
                )
        return self


class PlatformFundNavProjectionRun(SharedFundNavProjectionRun):
    fund_nav_event_ids: list[str] = Field(default_factory=list)
    fund_nav_reinvestment_evidence_ids: list[str] = Field(default_factory=list)


class PlatformInstrumentDetail(PlatformInstrumentRecord):
    market_data: list[PlatformMarketDataPoint]
    fund_nav_events: list[PlatformFundNavEvent] = Field(default_factory=list)
    fund_nav_event_revisions: list[PlatformFundNavEvent] = Field(default_factory=list)
    fund_nav_reinvestment_evidence: list[
        PlatformFundNavReinvestmentEvidence
    ] = Field(default_factory=list)
    fund_nav_reinvestment_evidence_revisions: list[
        PlatformFundNavReinvestmentEvidence
    ] = Field(default_factory=list)
    fund_nav_projection_runs: list[PlatformFundNavProjectionRun] = Field(
        default_factory=list
    )
    current_fund_nav_projection_run_id: str | None = None
    fund_nav_adjustment_factors: list[PlatformFundNavAdjustmentFactor] = Field(
        default_factory=list
    )
    fund_nav_adjustment_factor_history: list[
        PlatformFundNavAdjustmentFactor
    ] = Field(default_factory=list)


class PlatformFundNavActionCandidate(BaseModel):
    fund_nav_action_candidate_id: str
    instrument_id: str
    candidate_type: Literal[
        "cash_distribution_signal",
        "cash_balance_discontinuity",
    ]
    interval_start_date: date
    interval_end_date: date
    observed_cash_balance_before: Decimal
    observed_cash_balance_after: Decimal
    observed_cash_delta: Decimal
    expected_cash_balance: Decimal | None = None
    measurement_uncertainty: Decimal = Field(gt=0)
    status: Literal[
        "open",
        "confirming",
        "superseded",
        "resolved",
        "rejected",
    ]
    source_provider: str
    source_revision: str
    source_evidence: dict[str, object]
    resolved_fund_nav_event_id: str | None = None
    rejection_reason: str | None = None
    decision_by: str | None = None
    confirmation_client_mutation_id: str | None = None
    confirmation_request_fingerprint: str | None = None
    created_at: str
    updated_at: str


class PlatformFundNavActionCandidateRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=4096)
    decision_by: str = Field(min_length=1, max_length=320)

    @field_validator("reason", "decision_by", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str:
        return str(value or "").strip()


class PlatformFundNavActionInput(BaseModel):
    event_type: Literal["cash_distribution", "unit_split"]
    announcement_date: date | None = None
    record_date: date | None = None
    effective_date: date
    payable_date: date | None = None
    sequence_order: int | None = Field(default=None, ge=1)
    cash_per_unit: Decimal | None = Field(default=None, gt=0)
    unit_ratio: Decimal | None = Field(default=None, gt=0)
    evidence_kind: Literal["provider_notice", "manual_verified"]
    source: str = Field(min_length=1, max_length=1024)
    external_event_id: str | None = Field(default=None, min_length=1, max_length=1024)
    provenance: dict[str, object] = Field(min_length=1)

    @field_validator("source", "external_event_id", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_payload(self) -> "PlatformFundNavActionInput":
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


class PlatformFundNavReinvestmentEvidenceInput(BaseModel):
    reinvestment_nav: Decimal = Field(gt=0)
    evidence_kind: Literal["provider_notice", "manual_verified"]
    source: str = Field(min_length=1, max_length=1024)
    external_evidence_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
    )
    provenance: dict[str, object] = Field(min_length=1)

    @field_validator("source", "external_evidence_id", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class PlatformFundNavMutationAudit(BaseModel):
    client_mutation_id: str = Field(min_length=1, max_length=200)
    recorded_by: str = Field(min_length=1, max_length=320)
    revision_reason: str = Field(min_length=1, max_length=4096)

    @field_validator(
        "client_mutation_id",
        "recorded_by",
        "revision_reason",
        mode="before",
    )
    @classmethod
    def normalize_audit_text(cls, value: object) -> str:
        return str(value or "").strip()


class PlatformFundNavActionCreateRequest(PlatformFundNavMutationAudit):
    action: PlatformFundNavActionInput
    reinvestment_evidence: PlatformFundNavReinvestmentEvidenceInput | None = None

    @model_validator(mode="after")
    def validate_optional_reinvestment_evidence(
        self,
    ) -> "PlatformFundNavActionCreateRequest":
        if self.action.event_type != "cash_distribution" and (
            self.reinvestment_evidence is not None
        ):
            raise ValueError(
                "reinvestment_evidence is only valid for a cash distribution"
            )
        return self


class PlatformFundNavActionRevisionRequest(PlatformFundNavMutationAudit):
    predecessor_fund_nav_event_id: str = Field(min_length=1, max_length=1024)
    revision_kind: Literal["correction", "cancellation"]
    action: PlatformFundNavActionInput | None = None
    reinvestment_evidence: PlatformFundNavReinvestmentEvidenceInput | None = None

    @field_validator("predecessor_fund_nav_event_id", mode="before")
    @classmethod
    def normalize_predecessor(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def validate_revision_payload(self) -> "PlatformFundNavActionRevisionRequest":
        if self.revision_kind == "correction":
            if self.action is None:
                raise ValueError("action is required for a correction")
            if self.action.event_type != "cash_distribution" and (
                self.reinvestment_evidence is not None
            ):
                raise ValueError(
                    "reinvestment_evidence is only valid for a cash distribution"
                )
        elif self.action is not None or self.reinvestment_evidence is not None:
            raise ValueError(
                "cancellation uses the immutable predecessor and forbids replacement fields"
            )
        return self


class PlatformFundNavReinvestmentEvidenceCreateRequest(
    PlatformFundNavMutationAudit
):
    evidence: PlatformFundNavReinvestmentEvidenceInput


class PlatformFundNavReinvestmentEvidenceRevisionRequest(
    PlatformFundNavMutationAudit
):
    predecessor_fund_nav_reinvestment_evidence_id: str = Field(
        min_length=1,
        max_length=1024,
    )
    revision_kind: Literal["correction", "cancellation"]
    evidence: PlatformFundNavReinvestmentEvidenceInput | None = None

    @field_validator(
        "predecessor_fund_nav_reinvestment_evidence_id",
        mode="before",
    )
    @classmethod
    def normalize_predecessor(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def validate_revision_payload(
        self,
    ) -> "PlatformFundNavReinvestmentEvidenceRevisionRequest":
        if self.revision_kind == "correction" and self.evidence is None:
            raise ValueError("evidence is required for a correction")
        if self.revision_kind == "cancellation" and self.evidence is not None:
            raise ValueError(
                "cancellation uses the immutable predecessor and forbids replacement fields"
            )
        return self


class PlatformFundNavMutationResponse(BaseModel):
    record: PlatformInstrumentDetail
    changed: bool
    dirty_from: date | None = None
    published_projection_run_id: str
    market_data_updated_at: str | None = None
    fund_nav_action_id: str | None = None
    fund_nav_event_id: str | None = None
    fund_nav_reinvestment_evidence_id: str | None = None
    fund_nav_event: PlatformFundNavEvent | None = None
    fund_nav_reinvestment_evidence: (
        PlatformFundNavReinvestmentEvidence | None
    ) = None
    candidate: PlatformFundNavActionCandidate | None = None
    candidate_confirmation_status: Literal["confirming", "resolved"] | None = None
    candidate_projection_synchronized: bool
    operational_warnings: list[str] = Field(default_factory=list)


class PlatformInstrumentsResponse(BaseModel):
    registry_name: str
    instruments: list[PlatformInstrumentRecord]


class PlatformInstrumentCreateRequest(BaseModel):
    instrument_name: str = Field(min_length=1)
    instrument_type: InstrumentType
    currency: str = Field(min_length=1, max_length=8)
    identifiers: list[PlatformInstrumentIdentifier] = Field(min_length=1)
    quote_selection_policy: PlatformQuoteSelectionPolicy | None = None
    broker_identifiers: list[PlatformBrokerIdentifier] = Field(default_factory=list)

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
    value: Decimal
    currency: str = Field(min_length=1, max_length=8)
    provider: str | None = None
    status: DataStatus
    nav_lineage: NavLineage | None = None

    @field_validator("value", mode="before")
    @classmethod
    def validate_positive_value(cls, value: object) -> Decimal:
        return parse_positive_market_data_value(value)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_fx_currency(value)
        return value

    @model_validator(mode="after")
    def validate_quote_basis(self) -> "PlatformMarketDataUpsertRequest":
        validate_market_data_identity(
            metric_family=self.metric_family,
            quote_basis=self.quote_basis,
        )
        if self.metric_family == "fx":
            parse_positive_fx_rate(self.value)
            if self.currency not in SUPPORTED_FX_CURRENCIES:
                raise ValueError(
                    "FX spot currency must be one of "
                    + ", ".join(SUPPORTED_FX_CURRENCIES)
                    + "."
                )
        return self


class PlatformFxRateRecord(BaseModel):
    base_currency: SupportedCurrency
    quote_currency: SupportedCurrency
    rate: PositiveFxRate
    as_of_date: date
    source_kind: FxRateSourceKind
    instrument_id: str | None = None
    source_instrument_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    status: DataStatus


class PlatformFxRatesResponse(BaseModel):
    supported_currencies: list[SupportedCurrency]
    maintained_pairs: list[str]
    rates: list[PlatformFxRateRecord]


class PlatformFxRateUpsertRequest(BaseModel):
    base_currency: SupportedCurrency
    quote_currency: SupportedCurrency
    rate: PositiveFxRate
    as_of_date: date
    provider: str | None = None
    status: DataStatus

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
    expected_frequency: ExpectedFrequency | None = None
    market_calendar: str | None = Field(default=None, min_length=1)
    release_lag_days: int | None = Field(default=None, ge=0)
    return_semantics: ReturnSemantics | None = None

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
    raw_text: str = Field(min_length=1, max_length=MAX_NAV_IMPORT_BYTES)
    provider: str | None = None
    status: DataStatus
    updated_by: str | None = None


class PlatformNavImportFileRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    file_content_base64: str = Field(
        min_length=1,
        max_length=MAX_NAV_IMPORT_BASE64_CHARS,
    )
    provider: str | None = None
    status: DataStatus
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
            raise ValueError("Provide either raw_text or file_name + file_content_base64.")
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
    nav_with_dividend: Decimal | None = None
    currency: str = Field(min_length=1, max_length=8)
    instrument_code: str | None = None
    instrument_name: str | None = None


class PlatformNavImportPreviewResponse(BaseModel):
    row_count: int
    rows: list[PlatformNavImportPreviewRow]
