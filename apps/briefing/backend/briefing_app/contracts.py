from datetime import date as Date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateRequest(Contract):
    report_type: Literal["daily", "weekly"]
    cutoff: datetime

    @field_validator("cutoff")
    @classmethod
    def aware_cutoff(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("报告截止时间必须包含时区")
        return value


class NumberCitation(Contract):
    source_id: str
    value: str = Field(min_length=1, description="Pure numeric token without units, currency or percent sign, e.g. '31' for 31%. Preserve the exact source number; put its units in quote/prose.")
    quote: str = ""
    field: str | None = None


class CitedItem(Contract):
    title: str = Field(min_length=1, description="A short Chinese headline naming one concrete change. Usually 14–26 Chinese characters; do not pack the summary or a list of prices into the title.")
    tags: list[str] = Field(default_factory=list, description="Usually 2–3 informative short Chinese tags naming the industry, event or theme. Avoid synonyms, generic filler and price numbers.")
    source_ids: list[str] = Field(min_length=1, description="Supporting bound source IDs, with the most direct article first. The app resolves their original URLs and source labels; do not put links or copied source text in the prose.")
    number_citations: list[NumberCitation] = Field(default_factory=list)
    related_market_symbols: list[str] = Field(default_factory=list, description="Only directly relevant symbols present in the bound market_rows. The app displays their calculated moves; do not repeat those prices in prose or substitute an unrelated benchmark for a missing stock.")


class Takeaway(CitedItem):
    summary: str = Field(min_length=1, description="One or two short sentences about the new fact or attributed view, usually 60–100 Chinese characters. Keep only decision-relevant numbers, dates and context.")
    analysis: str = Field(min_length=1, description="One concise sentence about the main market implication and its key condition or next observation, usually 40–60 Chinese characters. Do not repeat the summary or add a general risk checklist.")


class TakeawayGroup(Contract):
    title: Literal["宏观", "微观"]
    items: list[Takeaway]


class TakeawaySection(Contract):
    kind: Literal["takeaway_section"]
    title: Literal["重点信息"]
    groups: list[TakeawayGroup]


class Topic(CitedItem):
    angle: str = Field(min_length=1, description="One short sentence giving the discussion angle, usually 25–40 Chinese characters. Do not repeat the headline.")
    why_now: str = Field(min_length=1, description="One or two short sentences identifying this week's new trigger, usually 50–80 Chinese characters. Do not retell the daily report or the topic's full history.")
    debate: str = Field(min_length=1, description="One focused open research question and the evidence needed to resolve it, usually 30–60 Chinese characters. Do not invent opposing camps or attribute an author's own qualifications to opponents. Preserve the conditional nature of forecasts; do not append a full argument or risk framework.")


class TopicGroup(Contract):
    title: str = Field(min_length=1)
    items: list[Topic]


class TopicSection(Contract):
    kind: Literal["topic_recommendations"]
    title: Literal["本周话题推荐"]
    groups: list[TopicGroup]


class Opportunity(CitedItem):
    clue: str = Field(min_length=1, description="One short sentence naming the research clue or supply-chain link, usually 30–50 Chinese characters.")
    this_week: str = Field(min_length=1, description="One short sentence about the specific new trigger this week, usually 30–60 Chinese characters. Do not repeat clue or another topic's summary.")
    possible_opportunity: str = Field(min_length=1, description="One short sentence identifying what to research and the key uncertainty, usually 30–50 Chinese characters. This is an early research lead, not a trade recommendation or a full investment case.")


class OpportunitySection(Contract):
    kind: Literal["opportunity_leads"]
    title: Literal["新机会线索"]
    items: list[Opportunity]


class MacroRelease(CitedItem):
    date: Date = Field(description="Confirmed release or policy communication date in the report timezone, within this report's window. Never use the observation period, ingestion date or a future scheduled date. Verify the exact release time against the cutoff when available.")
    region: str = Field(min_length=1)
    category: Literal["宏观数据", "货币政策", "财政政策", "央行沟通"]
    actual: str = Field(min_length=1, description="Only the confirmed actual value, policy decision or concise communication. No analysis. Every number, including a bare PMI/index level, requires a number_citation.")
    expected: str | None = Field(default=None, description="Explicitly sourced pre-release expectation, including units; omit when unavailable. Never use the actual or a subsequent forecast as consensus.")
    previous: str | None = Field(default=None, description="Explicitly sourced previous value, including units; omit when unavailable.")


class MacroCalendarSection(Contract):
    kind: Literal["macro_data_calendar"]
    title: Literal["重要宏观发布"]
    rows: list[MacroRelease]


Section = Annotated[TakeawaySection | TopicSection | OpportunitySection | MacroCalendarSection, Field(discriminator="kind")]


class ReportDraft(Contract):
    report_type: Literal["daily", "weekly"]
    sections: list[Section]

    @field_validator("sections")
    @classmethod
    def unique_sections(cls, value):
        kinds = [item.kind for item in value]
        if len(kinds) != len(set(kinds)):
            raise ValueError("报告栏目不能重复")
        return value
