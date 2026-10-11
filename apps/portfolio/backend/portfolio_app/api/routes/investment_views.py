from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from portfolio_app.services import investment_views

router = APIRouter()


class InvestmentViewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view_date: date
    title: str = Field(default="", max_length=240)
    body: str = Field(min_length=1, max_length=100_000)
    context: str = Field(min_length=1, max_length=100_000)
    source: str = Field(default="", max_length=10_000)
    instrument_ids: list[str] = Field(default_factory=list)

    @field_validator("title", "body", "context", "source", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("instrument_ids")
    @classmethod
    def validate_instruments(cls, values):
        if any(not value.strip() or value != value.strip() for value in values) or len(set(values)) != len(values):
            raise ValueError("关联资产 ID 不能为空或重复")
        return values


class InvestmentViewCorrection(InvestmentViewInput):
    expected_revision: int = Field(ge=1)


class InvestmentStanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: str | None
    revision_number: int | None = Field(ge=1)
    expected_version: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_reference(self):
        if (self.note_id is None) != (self.revision_number is None):
            raise ValueError("选择观点须同时提供记录和版本；取消时二者均为空")
        return self


class InvestmentViewInstrument(BaseModel):
    instrument_id: str
    instrument_name: str
    instrument_type: str


class InvestmentViewRecord(BaseModel):
    portfolio_id: str
    note_id: str
    revision_number: int
    view_date: date
    title: str
    body: str
    context: str
    source: str
    instrument_ids: list[str]
    instruments: list[InvestmentViewInstrument]
    author_user_id: str
    author_name: str
    created_at: str
    revised_by_user_id: str
    revised_by_name: str
    updated_at: str
    can_edit: bool


class InvestmentViewInstrumentsResponse(BaseModel):
    portfolio_id: str
    instruments: list[InvestmentViewInstrument]


class InvestmentCurrentStance(BaseModel):
    note: InvestmentViewRecord
    selected_at: str
    selected_by_user_id: str
    selected_by_name: str


class InvestmentStanceResponse(BaseModel):
    current_stance: InvestmentCurrentStance | None
    stance_version: int


class InvestmentViewsResponse(InvestmentStanceResponse):
    portfolio_id: str
    notes: list[InvestmentViewRecord]


class InvestmentViewRevisionsResponse(BaseModel):
    portfolio_id: str
    note_id: str
    revisions: list[InvestmentViewRecord]


@router.get("/{portfolio_id}/investment-views", response_model=InvestmentViewsResponse)
def list_views(portfolio_id: str, instrument_id: str | None = Query(default=None, min_length=1)):
    return investment_views.list_investment_views(portfolio_id, instrument_id)


@router.post("/{portfolio_id}/investment-views", response_model=InvestmentViewRecord, status_code=201)
def create_view(portfolio_id: str, payload: InvestmentViewInput):
    return investment_views.save_investment_view(portfolio_id, payload.model_dump())


@router.get("/{portfolio_id}/investment-views/instruments", response_model=InvestmentViewInstrumentsResponse)
def view_instruments(portfolio_id: str):
    return investment_views.investment_view_instruments(portfolio_id)


@router.put("/{portfolio_id}/investment-views/current-stance", response_model=InvestmentStanceResponse)
def select_stance(portfolio_id: str, payload: InvestmentStanceInput):
    return investment_views.select_investment_stance(portfolio_id, **payload.model_dump())


@router.put("/{portfolio_id}/investment-views/{note_id}", response_model=InvestmentViewRecord)
def correct_view(portfolio_id: str, note_id: str, payload: InvestmentViewCorrection):
    return investment_views.save_investment_view(portfolio_id,
        payload.model_dump(exclude={"expected_revision"}), note_id=note_id, expected_revision=payload.expected_revision)


@router.get("/{portfolio_id}/investment-views/{note_id}/revisions", response_model=InvestmentViewRevisionsResponse)
def view_revisions(portfolio_id: str, note_id: str):
    return investment_views.investment_view_revisions(portfolio_id, note_id)
