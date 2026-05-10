from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class WatchlistCreateRequest(BaseModel):
    name: str
    description: str | None = None


class WatchlistItemsCreateRequest(BaseModel):
    instrument_ids: list[str] = Field(default_factory=list)


class WatchlistItemsDeleteRequest(BaseModel):
    instrument_ids: list[str] = Field(default_factory=list)


class WatchlistItemsTransferRequest(BaseModel):
    instrument_ids: list[str] = Field(default_factory=list)
    target_watchlist_id: str


class WatchlistItemsCopyRequest(WatchlistItemsTransferRequest):
    pass


class WatchlistItemsMoveRequest(WatchlistItemsTransferRequest):
    pass


class WatchlistReorderRequest(BaseModel):
    watchlist_ids: list[str] = Field(default_factory=list)


class ManualFundCreateRequest(BaseModel):
    ticker: str
    name: str
    product_type: str = "instrument"
    fund_type: str = "generic"


class AdvancedFilterRuleInput(BaseModel):
    type: Literal["rule"] = "rule"
    field: str
    operator: Literal[
        "eq",
        "neq",
        "in",
        "not_in",
        "contains",
        "gte",
        "lte",
        "gt",
        "lt",
        "exists",
    ]
    value: Any = None


class AdvancedFilterGroupInput(BaseModel):
    type: Literal["group"] = "group"
    logic: Literal["and", "or"] = "and"
    conditions: list[AdvancedFilterRuleInput | AdvancedFilterGroupInput] = Field(
        default_factory=list
    )


AdvancedFilterGroupInput.model_rebuild()


class SortRule(BaseModel):
    field: str
    direction: str = "asc"


class WatchlistViewColumnInput(BaseModel):
    field_key: str
    display_order: int
    width: int | None = None
    is_visible: bool = True


class WatchlistViewCreateRequest(BaseModel):
    name: str
    description: str | None = None
    default_group_by: str | None = "none"
    default_sort: list[SortRule] = Field(default_factory=list)
    default_filters: dict[str, Any] = Field(default_factory=dict)
    default_advanced_filters: AdvancedFilterGroupInput | None = None
    columns: list[WatchlistViewColumnInput] = Field(default_factory=list)


class PaginationInput(BaseModel):
    page: int = 1
    page_size: int = 50


class ScreenerQueryRequest(BaseModel):
    watchlist_id: str
    view_id: str | None = None
    selected_fields: list[str] = Field(default_factory=list)
    filters: dict[str, list[Any]] = Field(default_factory=dict)
    advanced_filters: AdvancedFilterGroupInput | None = None
    sort: list[SortRule] = Field(default_factory=list)
    group_by: str | None = "none"
    pagination: PaginationInput = Field(default_factory=PaginationInput)


class InstrumentAttributeDefinitionCreateRequest(BaseModel):
    attribute_key: str
    label: str
    description: str | None = None
    data_type: Literal[
        "single_select",
        "multi_select",
        "boolean",
        "number",
        "text",
        "date",
    ]
    domain_code: Literal["overview", "research", "monitoring"]
    group_code: str
    display_order: int = 999
    options: list[str] = Field(default_factory=list)
    instrument_scope_json: list[str] = Field(default_factory=lambda: ["fund"])
    applicability_json: dict[str, list[str]] = Field(default_factory=dict)
    rubric_json: dict[str, Any] = Field(default_factory=dict)
    is_groupable: bool = True
    is_filterable: bool = True
    is_view_column: bool = True
    default_visible: bool = False
    required_for_monitoring: bool = False


class FundAttributeValueInput(BaseModel):
    attribute_key: str
    value: Any


class FundAttributesUpsertRequest(BaseModel):
    values: list[FundAttributeValueInput] = Field(default_factory=list)


class TaxonomyAssignmentUpsertRequest(BaseModel):
    node_id: str | None = None
    updated_by: str | None = None


class HoldingPositionInput(BaseModel):
    holding_name: str
    holding_type: str
    security_identifier: str | None = None
    issuer_name: str | None = None
    issuer_type: str | None = None
    portfolio_weight: Decimal | None = None
    market_value: Decimal | None = None
    quantity: Decimal | None = None
    currency: str | None = None
    market_price: Decimal | None = None
    share_change_pct: Decimal | None = None
    maturity_date: date | None = None
    coupon_rate: Decimal | None = None
    credit_rating: str | None = None
    effective_duration: Decimal | None = None
    modified_duration: Decimal | None = None
    yield_to_worst: Decimal | None = None
    sector: str | None = None
    country_code: str | None = None


class FundHoldingSnapshotIngestRequest(BaseModel):
    as_of_date: date
    source_cutoff_at: datetime
    methodology_version: str = "valuation-statement/v1"
    source_record_id: str | None = None
    positions: list[HoldingPositionInput] = Field(default_factory=list)
    auto_recalculate: bool = True


class RecalcExecuteRequest(BaseModel):
    job_type: Literal["performance", "exposure", "ratings", "all"] = "all"
    trigger_type: str = "manual_api"
    trigger_ref_type: str | None = "api_request"
    trigger_ref_id: str | None = None


class ManualProfileUpsertRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    updated_by: str | None = None


class NavSettingsUpsertRequest(BaseModel):
    nav_basis_preference: Literal["auto", "nav_with_dividend"] | None = None
    default_benchmark_instrument_id: str | None = None
    peer_baseline_instrument_ids: list[str] | None = None
    updated_by: str | None = None


class CopilotMessageInput(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class WatchlistCopilotChatRequest(BaseModel):
    question: str
    view_id: str | None = None
    selected_fields: list[str] = Field(default_factory=list)
    filters: dict[str, list[Any]] = Field(default_factory=dict)
    advanced_filters: AdvancedFilterGroupInput | None = None
    sort: list[SortRule] = Field(default_factory=list)
    group_by: str | None = "none"
    history: list[CopilotMessageInput] = Field(default_factory=list)


class FundCopilotChatRequest(BaseModel):
    question: str
    active_tab: str | None = None
    history: list[CopilotMessageInput] = Field(default_factory=list)
