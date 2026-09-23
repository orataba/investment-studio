from typing import Literal

from pydantic import BaseModel, Field


class ResearchSolutionTreeRow(BaseModel):
    row_id: str
    parent_row_id: str | None = None
    row_kind: Literal["portfolio", "category", "instrument", "cash", "derivatives"]
    member_type: str
    member_id: str
    label: str
    depth: int
    path: list[str] = Field(default_factory=list)
    target_risk_share: float | None = None
    solved_risk_share: float | None = None
    current_value_base: float | None = None
    current_weight: float | None = None
    target_value_base: float | None = None
    target_weight: float | None = None
    rebalance_value_base: float | None = None
    trade_constraint: Literal["adjustable", "no_trade", "mixed"]
    risk_model_status: Literal["modeled", "excluded", "mixed"]
    execution_status: Literal["ready", "manual_review_required", "no_trade"]
    execution_note: str | None = None
    min_weight: float | None = None
    max_weight: float | None = None
    bound_status: str | None = None


class ResearchSolutionTreeRecord(BaseModel):
    schema_version: int = 2
    as_of_date: str | None = None
    base_currency: str | None = None
    portfolio_nav: float | None = None
    capital_weight_basis: Literal["portfolio_nav", "saved_scope"]
    risk_attribution_scope: Literal["portfolio", "selected_research_scope"]
    hierarchy_status: Literal["complete", "recorded_groups_only"]
    configuration_captured_at: str | None = None
    rows: list[ResearchSolutionTreeRow] = Field(default_factory=list)
