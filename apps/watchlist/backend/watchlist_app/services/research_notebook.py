"""Dated analyst working papers, distinct from the investment manager's views."""
from copy import deepcopy
from datetime import UTC, date, datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from watchlist_app.services.research_dossier import ResearchMandateInput
from watchlist_app.services.market_evidence import hydrate_source, retained_sources, source_reference


class ResearchCatalyst(BaseModel):
    theme_id: str | None = None
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=250)
    scheduled_at: str = Field(description="YYYY-MM-DD日期，或含数字时区偏移的ISO8601时间。不得包含ET/盘前等缩写、括号或解释；说明写入relevance。",
        json_schema_extra={"examples": ["2026-09-16", "2026-09-11T08:30:00-04:00"],
                           "anyOf": [{"type": "string", "format": "date"}, {"type": "string", "format": "date-time"}]})
    status: Literal["scheduled", "released", "cancelled"] = "scheduled"
    relevance: str = Field(min_length=1, max_length=2000)
    scenarios: list[str] = Field(default_factory=list)
    next_check: str = Field(min_length=1, max_length=2000)
    outcome: str = Field(default="", max_length=3000)
    source_ids: list[str] = Field(min_length=1)

    @field_validator("scheduled_at")
    @classmethod
    def retain_schedule_precision(cls, value):
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("事件日程的具体时刻必须包含时区；仅知道日期时保留日期")
        return parsed.isoformat()

from watchlist_app.services.sector_estimates import retained_estimate_sources


class ResearchQuestion(BaseModel):
    module_key: str | None = None
    theme_id: str | None = None
    event_key: str | None = None
    pm_note_id: str | None = None
    pm_note_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def pm_reference(self):
        if bool(self.pm_note_id) != bool(self.pm_note_revision):
            raise ValueError("研究员复核投资经理观点须同时关联原观点和版本")
        return self

    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    question: str = Field(min_length=1, max_length=1000)
    assessment: str = Field(max_length=4000)
    evidence_for: list[str] = Field(default_factory=list)
    evidence_against: list[str] = Field(default_factory=list)
    next_check: str = Field(max_length=2000)
    status: Literal["open", "supported", "refuted"] = Field(default="open",
        description="证据判断，与是否继续跟踪无关；支持或反驳原假设不自动结束研究。")
    tracking_status: Literal["active", "paused", "closed"] = Field(default="active",
        description="跟踪安排。仅显式提交才改变已有安排；暂停或结束须说明原因，恢复须显式 active。")
    tracking_reason: str = Field(default="", max_length=2000,
        description="暂停、结束或重新审视的原因；观察条件写 next_check，不要求每天生成新结论。")
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def tracking_decision(self):
        if self.tracking_status in {"paused", "closed"} and not self.tracking_reason.strip():
            raise ValueError("暂停或结束研究问题须明确说明跟踪安排的原因")
        return self


class ResearchFact(BaseModel):
    module_key: str | None = None
    subject: str = Field(min_length=1, max_length=300)
    metric: str = Field(min_length=1, max_length=300)
    value: str = Field(min_length=1, max_length=1000)
    unit: str = Field(max_length=300)
    period: str = Field(max_length=500)
    comparison: str = Field(default="", max_length=1500)
    uncertainty: str = Field(default="", max_length=1500)
    source_ids: list[str] = Field(min_length=1)


class ResearchAttentionItem(BaseModel):
    """A sourced opportunity or risk within the single current analyst view."""
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=3000)
    next_watch: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(default_factory=list)
    event_keys: list[str] = Field(default_factory=list)
    theme_ids: list[str] = Field(default_factory=list)
    figure_source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def traceable(self):
        if not any((self.source_ids, self.event_keys, self.theme_ids, self.figure_source_ids)):
            raise ValueError("机会与风险条目须引用对应事件、主题或已取得的依据")
        return self


class InvestmentView(BaseModel):
    """The analyst's current judgment, separate from the portfolio manager's views."""
    direction: str = Field(default="", max_length=3000)
    horizon: str = Field(default="", max_length=1000)
    attractiveness: str = Field(default="", max_length=3000)
    risk: str = Field(default="", max_length=3000)
    conviction: str = Field(default="", max_length=1000)
    assumptions: list[str] = Field(default_factory=list)
    invalidation: str = Field(default="", max_length=3000, description="哪些可观察的证据会要求改变或撤回当前判断；不是任意价格止损。")
    next_check: str = Field(default="", max_length=2000, description="下一次需要验证的关键事实或条件；可关联持续研究问题，不要求每日产生新结论。")
    source_ids: list[str] = Field(default_factory=list)
    opportunities: list[ResearchAttentionItem] | None = Field(default=None,
        description="Current sourced opportunities. Omit to preserve; [] explicitly clears. Not an exclusive rating.")
    risks: list[ResearchAttentionItem] | None = Field(default=None,
        description="Current unresolved risks. No news, event expiry or theme closure does not remove a risk.")
    coverage_status: Literal["assessed", "limited", "not_established"] | None = None
    coverage_note: str = Field(default="", max_length=3000,
        description="Actual checked scope and limitations, separate from investment direction and run success.")

    @model_validator(mode="after")
    def unique_attention(self):
        for items in (self.opportunities, self.risks):
            keys = [item.key for item in items or []]
            if len(set(keys)) != len(keys):
                raise ValueError("同一机会或风险条目在当前判断中重复出现")
        return self


class DecisionBrief(BaseModel):
    """PM-facing implications of the current view, not a second asset rating."""
    model_config = ConfigDict(extra="forbid")
    recommendation: str = Field(min_length=1, max_length=2000,
        description="Actionable research recommendation, including maintain/wait when appropriate; not a trade instruction or invented position size.")
    rationale: str = Field(min_length=1, max_length=3000)
    conditions: list[str] = Field(default_factory=list, max_length=12)
    horizon: str = Field(default="", max_length=1000)
    next_decision: str = Field(default="", max_length=2000)
    source_ids: list[str] = Field(default_factory=list)
    basis_view_version_id: str | None = Field(default=None,
        description="Server binds this to the effective investment_view version at publication; do not invent a version.")


class ResearchChange(BaseModel):
    """A dated change with an explicit comparison basis and investment consequence."""
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=300)
    before: str = Field(min_length=1, max_length=2500)
    after: str = Field(min_length=1, max_length=2500)
    baseline_as_of: date | None = None
    mechanism: str = Field(min_length=1, max_length=3000)
    decision_implication: str = Field(min_length=1, max_length=2500)
    condition: str = Field(default="", max_length=2000)
    source_ids: list[str] = Field(min_length=1)


class ResearchForecast(BaseModel):
    theme_id: str | None = None
    event_key: str | None = None
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    claim: str = Field(min_length=1, max_length=4000)
    variable: str = Field(default="", max_length=1000)
    horizon: str = Field(default="", max_length=1000)
    observation_condition: str = Field(default="", max_length=2000)
    review_on: date | None = Field(default=None, description="明确安排复核的日期；到期只表示需要检查，不代表预测已兑现。")
    assumptions: list[str] = Field(default_factory=list)
    invalidation: str = Field(default="", max_length=2000)
    status: Literal["active", "confirmed", "refuted", "expired", "withdrawn"] = "active"
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def observable_forecast(self):
        if not self.claim.strip() or not (self.horizon.strip() or self.observation_condition.strip()):
            raise ValueError("预测需要明确主张，以及期限或可观察的检验条件")
        return self


class ForecastReview(BaseModel):
    theme_id: str | None = None
    event_key: str | None = None
    related_research_update_id: str | None = None
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    forecast_key: str | None = Field(default=None, min_length=1)
    forecast_version_id: str | None = Field(default=None, min_length=1)
    outcome: str = Field(min_length=1, max_length=4000)
    mechanism_assessment: str = Field(default="", max_length=4000)
    alternative_explanations: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def forecast_reference(self):
        if bool(self.forecast_key) != bool(self.forecast_version_id):
            raise ValueError("复盘关联预测时须同时给出预测标识和原始版本")
        return self


class ResearchLesson(BaseModel):
    theme_id: str | None = None
    event_key: str | None = None
    related_research_update_id: str | None = None
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    lesson: str = Field(min_length=1, max_length=4000)
    applicability: str = Field(default="", max_length=3000)
    limitations: str = Field(default="", max_length=3000)
    status: Literal["active", "withdrawn"] = Field(default="active", description="可继续使用或已停用；停用保留原版本及其依据，不再作为当前经验复用。")
    withdrawal_reason: str = Field(default="", max_length=2000)
    forecast_key: str | None = None
    forecast_version_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def forecast_reference(self):
        if bool(self.forecast_key) != bool(self.forecast_version_id):
            raise ValueError("经验关联预测时须同时给出预测标识和原始版本")
        if self.status == "withdrawn" and not self.withdrawal_reason.strip():
            raise ValueError("停用研究经验须说明原因")
        return self


class ResearchModule(BaseModel):
    """A maintained analytical section, not a daily article or a layout definition."""
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="Use a module id from this instrument's research_plan; retain the same key across updates.")
    summary: str = Field(default="", max_length=3000)
    analysis: str = Field(default="", max_length=16000,
        description="Readable paragraphs explaining current conditions, mechanisms, opposing evidence and implications. No HTML or invented chart data.")
    coverage: Literal["supported", "partial", "insufficient"] = Field(default="insufficient",
        description="Evidence coverage of this analysis, not investment conviction or a quality score.")
    gaps: list[str] = Field(default_factory=list)
    next_check: str = Field(default="", max_length=2000)
    source_ids: list[str] = Field(default_factory=list)
    figure_source_ids: list[str] = Field(default_factory=list,
        description="Optional retained computed/holdings/estimate source IDs for source-rendered figures; no arbitrary model-generated chart values.")
    evidence_as_of: date | None = Field(default=None,
        description="Material evidence date if one common date is meaningful; otherwise leave null and retain dates on the individual sources.")


def notebook_current_view(notebook: dict | None) -> dict | None:
    """Decode historical working papers at one read boundary, without rewriting originals.

    New submissions use modules only. Old general analysis remains explicitly
    identified as preceding the modular method; no new judgment date is created.
    """
    if notebook is None:
        return None
    result = deepcopy(notebook)
    if isinstance(result.get("investment_view"), dict):
        # None means a historical assessment never supplied structured lists;
        # it must not be rendered as an assessed absence of risk.
        for field in ("opportunities", "risks", "coverage_status"):
            result["investment_view"].setdefault(field, None)
        result["investment_view"].setdefault("coverage_note", "")
    if result.get("decision_brief"):
        result["decision_brief"]["needs_review"] = (
            result["decision_brief"].get("basis_view_version_id") != (result.get("investment_view") or {}).get("version_id"))
    # Earlier papers were sparse and may predate the structured lists. Supply
    # read defaults here so every reader consumes the same current contract.
    for field in ("modules", "key_drivers", "questions", "important_changes", "next_research",
                  "source_ids", "sources", "catalysts", "facts", "forecasts", "forecast_reviews", "lessons", "changes"):
        result.setdefault(field, [])
    previous_analysis = result.pop("fundamental_view", "")
    previous_valuation = result.pop("valuation_view", "")
    if previous_analysis or previous_valuation:
        result["prior_analysis"] = {
            "fundamental_view": previous_analysis, "valuation_view": previous_valuation,
            "source_ids": deepcopy(result.get("source_ids", [])),
            "sources": [source_reference(source) for source in result.get("sources", [])
                        if source.get("source_id") in result.get("source_ids", [])],
            "updated_at": result.get("updated_at") or result.get("checked_at"),
            "version_id": result.get("version_id") or result.get("run_id"),
            "note": "此前保存的综合分析，保留原始日期与依据；适用模块尚待逐项建立。",
        }
    return result


class ResearchNotebook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modules: list[ResearchModule] = Field(default_factory=list)
    key_drivers: list[str] = Field(default_factory=list)
    questions: list[ResearchQuestion] = Field(default_factory=list)
    important_changes: list[str] = Field(default_factory=list)
    next_research: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    catalysts: list[ResearchCatalyst] = Field(default_factory=list)
    facts: list[ResearchFact] = Field(default_factory=list)
    mandate_update: ResearchMandateInput | None = None
    investment_view: InvestmentView | None = None
    decision_brief: DecisionBrief | None = None
    changes: list[ResearchChange] = Field(default_factory=list, max_length=20)
    forecasts: list[ResearchForecast] = Field(default_factory=list)
    forecast_reviews: list[ForecastReview] = Field(default_factory=list)
    lessons: list[ResearchLesson] = Field(default_factory=list)


def notebook_source_ids(notebook: ResearchNotebook | dict) -> set[str]:
    """Collect evidence for current research and its retained original versions."""
    value = notebook.model_dump(mode="json") if isinstance(notebook, BaseModel) else notebook
    refs = set()

    def collect(item):
        if isinstance(item, dict):
            refs.update(item.get("source_ids", []))
            refs.update(item.get("figure_source_ids", []))
            for key, child in item.items():
                if key != "sources":
                    collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)

    collect(value)
    return refs


def retained_public_sources(session, instrument_id: str) -> list[dict]:
    """Keep fetched originals usable even when the associated AI draft was rejected."""
    from sqlalchemy import Boolean, JSON, select, true
    from types import SimpleNamespace
    from studio_identity import current_principal
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from watchlist_app.services.research_access import instrument_run_scope, research_context_projection, research_projection_rows, topic_portfolio_ids_by_topic
    principal = current_principal()
    relation, payload = research_context_projection(session, {"instrument_ids": JSON, "sector_run": Boolean,
        "research_run": Boolean, "web_evidence": JSON, "market_text_sources": JSON, "submitted_draft": JSON, "reviews": JSON})
    query = select(ResearchEntry.entry_id, ResearchEntry.topic_id, ResearchTopic.portfolio_id,
        payload["instrument_ids"].label("instrument_ids"), payload["sector_run"].label("sector_run"),
        payload["research_run"].label("research_run"), payload["web_evidence"].label("web_evidence"),
        payload["market_text_sources"].label("market_text_sources"), payload["submitted_draft"].label("submitted_draft"),
        payload["reviews"][instrument_id].label("review"),
    ).select_from(ResearchEntry).join(ResearchTopic)
    if relation is not None:
        query = query.join(relation, true())
    records = research_projection_rows(session, query.where(ResearchEntry.kind == "analysis", ResearchTopic.visibility == "team",
        True if principal.local_unrestricted else ResearchEntry.team_id == principal.team_id,
        True if principal.local_unrestricted else ResearchTopic.team_id == principal.team_id,
        instrument_run_scope(session, instrument_id),
    ).order_by(ResearchEntry.created_at.desc()),
        {**{name: (name,) for name in ("instrument_ids", "sector_run", "research_run", "web_evidence", "market_text_sources", "submitted_draft")},
         "review": ("reviews", instrument_id)})
    topics = {row.topic_id: SimpleNamespace(topic_id=row.topic_id, portfolio_id=row.portfolio_id) for row in records}
    portfolios = topic_portfolio_ids_by_topic(session, topics.values())
    by_url = {}
    for record in records:
        if portfolios[record.topic_id]:
            continue
        scope = record.instrument_ids or []
        if (not (record.sector_run or record.research_run)
                or (not record.research_run and record.topic_id not in {
                    f"instrument-events:{instrument_id}", "us-sector-daily-review"})):
            continue
        # Batch fetches have no instrument attribution: use explicit draft/review references.
        references = set()
        reviews = [*(record.submitted_draft or {}).get("reviews", []),
                   {"instrument_id": instrument_id, **(record.review or {})}]
        for review in reviews:
            if review.get("instrument_id") != instrument_id:
                continue
            research = review.get("research") or {}
            references.update(notebook_source_ids(research))
            for row in [*review.get("events", []), *review.get("themes", [])]:
                references.update(row.get("source_ids", []))
        for capture in reversed(record.web_evidence or []):
            if capture.get("operation") not in {"fetch", "author_import"}:
                continue
            for source in capture.get("sources", []):
                if len(scope) != 1 and source.get("source_id") not in references:
                    continue
                identity = source.get("version_id") or source["source_id"]
                if identity in by_url:
                    continue
                source = hydrate_source(source)
                original = {**source, "source_type": "public_source", "source_run_id": record.entry_id,
                    "instrument_id": instrument_id, "role": "retained_original",
                    "verification_note": "已取得的原文，不代表其主张已核实；须重读正文及原始日期。所属报告的模型结论不作为依据。"}
                if _original_source(original, instrument_id):
                    by_url.setdefault(source.get("version_id") or source["source_id"], original)
        for source in record.market_text_sources or []:
            if len(scope) != 1 and source.get("source_id") not in references:
                continue
            if source["version_id"] in by_url:
                continue
            original = {**hydrate_source(source), "instrument_id": instrument_id,
                        "source_run_id": record.entry_id, "role": "retained_original"}
            by_url.setdefault(source["version_id"], original)
    return [source_reference(source) for source in by_url.values()]


def dossier_outline(dossier: dict) -> dict:
    """Read the working view first; retrieve long original materials and cases on demand."""
    def index_versions(record):
        if "versions" not in record:
            return record
        return {**record, "versions": [{key: version[key] for key in
            ("version_id", "created_at", "updated_at", "source_run_id", "author") if key in version}
            for version in record["versions"]]}

    notebook = dict(dossier["notebook"]) if dossier.get("notebook") else None
    if notebook:
        for field in ("investment_view", "decision_brief"):
            if notebook.get(field):
                notebook[field] = index_versions(notebook[field])
        for field in ("modules", "forecasts", "forecast_reviews", "lessons", "changes"):
            if field in notebook:
                notebook[field] = [index_versions(item) for item in notebook[field]]
    return {**dossier,
        **({"mandate": index_versions(dossier["mandate"])} if dossier.get("mandate") else {}),
        "version_history_note": "旧版本仅列版本与保存时间；需要当时的完整观点、预测、复盘或方法时，调用read_research_dossier(instrument_id=当前标的, version_id=所选版本)。当前正文保持完整，旧版本不补入后来资料。",
        "prior_sources": [{k: s.get(k) for k in ("source_id", "document_id", "version_id", "title", "url", "published_at", "retrieved_at", "source_run_id")} | {"body_available": s.get("body_available", bool(s.get("text")))}
                          for s in dossier.get("prior_sources", [])],
        "prior_sources_note": "已取得原文的索引，不表示已核实其主张。按source_id重读正文与日期；原报告失败或撤回不影响原文留存。",
        "materials": [{k: v for k, v in m.items() if k != "body"} | {"body_available": bool(m.get("body"))}
                      for m in dossier.get("materials", [])],
        "historical_cases": [{k: c.get(k) for k in ("source_id", "case_id", "case_title", "role", "current_use", "limitations")}
                             for c in dossier.get("historical_cases", [])],
        "notebook": {**{key: value for key, value in notebook.items() if key != "method_plan"}, "sources": [{k: s.get(k) for k in
            ("source_id", "title", "url", "source_type", "source_run_id", "published_at", "retrieved_at", "run_cutoff")}
            for s in notebook.get("sources", [])]} if notebook else None}


def dossier_source(dossier: dict, source_id: str) -> dict:
    for source in [*dossier.get("materials", []), *dossier.get("historical_cases", []),
                   *dossier.get("prior_sources", []),
                   *(source for theme in dossier.get("themes", []) for source in theme.get("sources", [])),
                   *(source for view in dossier.get("pm_views", []) for source in view.get("sources", [])),
                   *(dossier.get("notebook") or {}).get("sources", [])]:
        if source.get("source_id") == source_id:
            if source.get("instrument_id") != dossier["instrument_id"] and not (
                    (source.get("source_type") == "public_source" and source.get("instrument_id") is None)
                    or (source.get("source_type") == "computed_metric" and source.get("scope") == "public_market")):
                raise ValueError("这份依据不属于当前标的研究档案")
            return hydrate_source(source)
    raise ValueError("当前研究档案没有这份材料或历史依据")


def _original_source(source: dict, iid: str, cutoff: datetime | None = None) -> bool:
    metadata = source.get("metadata") or {}
    if (metadata.get("source_kind") in {"generated_source_summary", "internal_computed_summary"}
            or metadata.get("extraction_status") in {"summary", "computed_summary"}):
        # A newly written synopsis stays useful context, but an old source date
        # cannot make its paraphrases or later interpretations an original.
        return False
    kind = source.get("source_type")
    if kind == "public_source":
        readable = bool(source.get("text", "").strip()) and "\ufffd" not in source.get("text", "")
        opening = [line.strip() for line in source.get("text", "").splitlines() if line.strip()][:5]
        if any(re.search(r"(?:^|[·|])\s*Compiled by\s+.+?\s+Engine\b", line, re.IGNORECASE) for line in opening):
            readable = False
        scope_matches = source.get("instrument_id") in {None, iid}
    elif kind == "research_material":
        readable = bool((source.get("text") or source.get("body") or "").strip())
        readable = readable and (source.get("metadata") or {}).get("extraction_status") not in {
            "empty", "missing", "unsupported", "metadata_only", "failed"}
        scope_matches = source.get("instrument_id") == iid
    else:
        payload_key = {"historical_case": "sources", "instrument_snapshot": "snapshot",
                       "sector_snapshot": "snapshot", "company_snapshot": "company",
                       "analyst_estimate_changes": "current_snapshot", "computed_metric": "data"}.get(kind)
        readable = bool(payload_key and source.get(payload_key))
        scope_matches = source.get("instrument_id") == iid or (kind == "computed_metric" and source.get("scope") == "public_market")
        if kind == "computed_metric":
            readable = readable and bool(source.get("methodology") and source.get("as_of"))
    if not readable or not scope_matches or source.get("time_status") == "future":
        return False
    published = source.get("as_of") if kind == "computed_metric" else source.get("published_at") or (source.get("metadata") or {}).get("published_at")
    if cutoff is not None and published:
        try:
            if len(published) == 10:
                return date.fromisoformat(published) <= cutoff.date()
            stamp = datetime.fromisoformat(published)
            if stamp.tzinfo is not None:
                return stamp <= cutoff
        except ValueError:
            if kind == "computed_metric":
                return False
            pass  # Unknown publication precision does not become an invented timestamp.
        if kind == "computed_metric":
            return False
    return True


def company_source(run_id: str, instrument_id: str, symbol: str, company: dict) -> dict:
    """The retained company read and full-run source index share one identity."""
    return {"source_id": f"fmp:{run_id}:{instrument_id}:{symbol}",
        "instrument_id": instrument_id, "source_run_id": run_id,
        "source_type": "company_snapshot", "title": f"FMP · {symbol} 公司资料与预期", "company": company}


def research_sources(context: dict, run_id: str) -> dict[str, dict]:
    """Only original evidence is reusable; methods and AI summaries are not sources."""
    sources = {}
    cutoff = datetime.fromisoformat(context["cutoff"])
    for dossier in context.get("research_dossiers", []):
        iid = dossier["instrument_id"]
        for source in [*dossier.get("prior_sources", []), *(dossier.get("notebook") or {}).get("sources", []),
                       *(source for theme in dossier.get("themes", []) for source in theme.get("sources", [])),
                       *(source for view in dossier.get("pm_views", []) for source in view.get("sources", []))]:
            source = hydrate_source(source, cutoff=cutoff)
            if _original_source(source, iid, cutoff):
                sources[source["source_id"]] = source
        for material in dossier.get("materials", []):
            source = {**material, "text": material.get("body", ""), "source_type": "research_material"}
            if _original_source(source, iid, cutoff):
                sources[source["source_id"]] = source
        for case in dossier.get("historical_cases", []):
            source = {**case, "source_type": case.get("source_type", "historical_case")}
            if _original_source(source, iid, cutoff):
                sources[source["source_id"]] = source
    # A current, successfully read material supersedes its earlier retained copy.
    for asset in context.get("instrument_inputs", []):
        iid = asset["instrument_id"]
        sid = f"instrument:{run_id}:{iid}"
        sources[sid] = {"source_id": sid, "instrument_id": iid, "source_run_id": run_id,
            "source_type": "instrument_snapshot", "title": f"{asset['name']} · 已披露资料与指标",
            "run_cutoff": asset.get("snapshot_cutoff", context["cutoff"]), "snapshot": {k: v for k, v in asset.items()
                if k not in {"research", "research_tracking", "research_tracking_note", "research_dossier",
                             "risk_cases", "analyst_focus", "materials", "materials_note"}}}
    for asset in context.get("sector_inputs", []):
        iid = asset["instrument_id"]
        sid = f"sector:{run_id}:{iid}"
        sources[sid] = {"source_id": sid, "instrument_id": iid, "source_run_id": run_id,
            "source_type": "sector_snapshot", "title": f"{iid.upper()} · 成份与预期快照",
            "run_cutoff": asset.get("snapshot_cutoff", context["cutoff"]), "snapshot": {k: v for k, v in asset.items() if k != "research_focus"}}
    for iid, companies in context.get("sector_company_data", {}).items():
        for symbol, company in companies.items():
            source = company_source(run_id, iid, symbol, company)
            sources[source["source_id"]] = source
    sources.update(retained_estimate_sources(context))
    for capture in context.get("web_evidence", []):
        if capture.get("operation") in {"fetch", "author_import"}:
            for source in capture.get("sources", []):
                source = hydrate_source(source, cutoff=cutoff)
                original = {**source, "source_run_id": run_id, "source_type": "public_source"}
                if _original_source(original, source.get("instrument_id"), cutoff):
                    sources[source["source_id"]] = original
    sources.update(retained_sources(context))
    for source in context.get("financial_sources", []):
        if _original_source(source, source.get("instrument_id"), cutoff):
            sources[source["source_id"]] = source
    for source in context.get("computed_metrics", []):
        if _original_source(source, source.get("instrument_id"), cutoff):
            sources[source["source_id"]] = source
    return sources


def validate_notebook(notebook: ResearchNotebook, iid: str, sources: dict[str, dict], *, research_plan: dict | None = None,
                      previous: dict | None = None, cutoff: datetime | None = None):
    for field in ("modules", "forecasts", "forecast_reviews", "lessons", "changes"):
        keys = [row.key for row in getattr(notebook, field)]
        if len(set(keys)) != len(keys):
            raise ValueError("同一研究模块、预测、复盘或经验在本轮重复出现")
    if research_plan is not None:
        applicable = {module["id"] for module in research_plan.get("modules", [])}
        if any(module.key not in applicable for module in notebook.modules):
            raise ValueError("研究模块不在本标的已绑定的方法范围内；请先更新专属方法，再按适用模块研究")
    keys = [q.key for q in notebook.questions]
    if len(set(keys)) != len(keys):
        raise ValueError("同一研究问题在本轮重复出现")
    catalyst_keys = [c.key for c in notebook.catalysts]
    if len(set(catalyst_keys)) != len(catalyst_keys):
        raise ValueError("同一预定事件在本轮重复出现")
    for catalyst in notebook.catalysts:
        if not any(sources.get(sid, {}).get("source_type") in {"public_source", "research_material"} for sid in catalyst.source_ids):
            raise ValueError("预定事件必须有已取得的日程或披露原文，历史案例和模型判断不能证明日程")
    old_modules = {row["key"]: row for row in (previous or {}).get("modules", [])}
    effective_modules = [ResearchModule.model_validate(_merge_partial(module, old_modules.get(module.key)))
                         for module in notebook.modules]
    refs = notebook_source_ids(notebook) | notebook_source_ids({"modules": [module.model_dump(mode="json") for module in effective_modules]})
    unknown = sorted(refs - sources.keys())
    if unknown:
        valid = sorted(sid for sid in refs if sid in sources and _original_source(sources[sid], iid))
        raise ValueError(
            "研究底稿引用了未取得的原始依据：" + ", ".join(unknown)
            + "。本次已取得且可继续引用的依据：" + (", ".join(valid) or "无")
            + "。仅更正无效引用，保留合法依据；使用读取工具返回的原始 source_id，"
              "研究任务/方法标识不是原始依据，计算结果引用返回的 computed: 标识而非底层输入标识。")
    for sid in sorted(refs):
        source = sources.get(sid)
        if source.get("instrument_id") not in {None, iid} and not (
                source.get("source_type") == "computed_metric" and source.get("scope") == "public_market"):
            raise ValueError(f"研究底稿引用了其他标的的私有资料或快照：{sid}")
        if not _original_source(source, iid):
            raise ValueError(f"研究底稿依据不是已取得的原文、资料或真实快照：{sid}")
    for module in effective_modules:
        if cutoff is not None and module.evidence_as_of is not None and module.evidence_as_of > cutoff.date():
            raise ValueError("研究模块资料截至日期不能晚于本轮研究截止；预测目标期应在预测中记录")
        if module.coverage == "supported" and not module.source_ids:
            raise ValueError("资料充分的模块结论须引用实际取得的证据")
        for sid in module.figure_source_ids:
            if sources[sid].get("source_type") not in {"computed_metric", "sector_snapshot", "analyst_estimate_changes"}:
                raise ValueError("研究图表必须引用已留存的数值计算、持仓或预期证据")
    if notebook.investment_view is not None:
        for item in [*(notebook.investment_view.opportunities or []), *(notebook.investment_view.risks or [])]:
            for sid in item.figure_source_ids:
                if sources[sid].get("source_type") not in {"computed_metric", "sector_snapshot", "analyst_estimate_changes"}:
                    raise ValueError("机会与风险的数值依据必须引用已留存的数值计算、持仓或预期证据")
    for change in notebook.changes:
        if cutoff is not None and change.baseline_as_of and change.baseline_as_of > cutoff.date():
            raise ValueError("变化的对照基准日期不能晚于本轮研究截止")


def _versioned(value: dict, previous: dict | None, model: type[BaseModel], version_id: str, run_id: str, recorded_at: str) -> dict:
    if previous and {key: previous.get(key, field.get_default(call_default_factory=True))
                     for key, field in model.model_fields.items()} == value:
        return deepcopy(previous)
    versions = deepcopy((previous or {}).get("versions", []))
    if previous:
        versions.append({key: deepcopy(item) for key, item in previous.items() if key != "versions"})
    return {**value, "version_id": version_id, "created_at": (previous or {}).get("created_at") or recorded_at,
            "updated_at": recorded_at, "source_run_id": run_id, "versions": versions}


def _forecast_versions(previous: dict) -> dict[tuple[str, str], dict]:
    return {(forecast["key"], version["version_id"]): version
            for forecast in previous.get("forecasts", [])
            for version in [forecast, *forecast.get("versions", [])] if version.get("version_id")}


def _merge_partial(item: BaseModel, previous: dict | None) -> dict:
    value = item.model_dump(mode="json")
    if previous:
        for key in value:
            if key not in item.model_fields_set and key in previous:
                value[key] = deepcopy(previous[key])
    if isinstance(item, ResearchQuestion) and "tracking_status" in item.model_fields_set and item.tracking_status == "active" and "tracking_reason" not in item.model_fields_set:
        value["tracking_reason"] = ""
    if isinstance(item, ResearchLesson) and "status" in item.model_fields_set and item.status == "active" and "withdrawal_reason" not in item.model_fields_set:
        value["withdrawal_reason"] = ""
    return type(item).model_validate(value).model_dump(mode="json")


def retain_notebook(notebook: ResearchNotebook, previous: dict | None, sources: dict[str, dict], run_id: str, cutoff: str, *, research_plan: dict | None = None):
    previous = notebook_current_view(previous) or {}
    recorded_at = datetime.now(UTC).isoformat()
    value = notebook.model_dump(mode="json")
    # A knowledge-only submission leaves the existing working view intact.
    for field in ("key_drivers", "next_research", "source_ids", "facts"):
        if field not in notebook.model_fields_set and field in previous:
            value[field] = deepcopy(previous[field])
    # A same-key update changes only supplied fields, including explicit empty values.
    for field in ("modules", "questions", "catalysts", "forecasts", "forecast_reviews", "lessons", "changes"):
        old = {row["key"]: row for row in previous.get(field, [])}
        value[field] = [_merge_partial(item, old.get(item.key)) for item in getattr(notebook, field)]
    updated = {q["key"] for q in value["questions"]}
    # Silence is not resolution. Unmentioned questions keep their original assessment and evidence.
    value["questions"] += [deepcopy(q) for q in previous.get("questions", []) if q["key"] not in updated]
    updated_catalysts = {c["key"] for c in value["catalysts"]}
    value["catalysts"] += [deepcopy(c) for c in previous.get("catalysts", []) if c["key"] not in updated_catalysts]
    prior_forecasts = _forecast_versions(previous)
    for item in [*value["forecast_reviews"], *value["lessons"]]:
        if item.get("forecast_key") and (item["forecast_key"], item.get("forecast_version_id")) not in prior_forecasts:
            raise ValueError("复盘或经验必须关联此前已保存的预测原版本，不能将事后新建预测作为事前记录")
    if any(not (item.get("forecast_key") or item.get("related_research_update_id")) for item in value["forecast_reviews"]):
        raise ValueError("复盘需要关联此前已保存的预测版本或研究判断记录")
    method_versions = {module["id"]: module["version"] for module in (research_plan or {}).get("modules", [])}
    for field, model in (("modules", ResearchModule), ("forecasts", ResearchForecast), ("forecast_reviews", ForecastReview), ("lessons", ResearchLesson), ("changes", ResearchChange)):
        old = {row["key"]: row for row in previous.get(field, [])}
        incoming = {row["key"] for row in value[field]}
        value[field] = [_versioned(row, old.get(row["key"]), model, f"{run_id}:{field}:{row['key']}", run_id, recorded_at)
                        for row in value[field]]
        if field == "modules":
            for row in value[field]:
                row["checked_at"] = cutoff
                if row["key"] in method_versions:
                    row["method_version"] = method_versions[row["key"]]
        value[field] += [deepcopy(row) for key, row in old.items() if key not in incoming]
        if field == "modules":
            by_key = {row["key"]: row for row in value[field]}
            order = [*old, *[row["key"] for row in value[field] if row["key"] not in old]]
            value[field] = [by_key[key] for key in order]
    if notebook.investment_view is not None:
        view = _merge_partial(notebook.investment_view, previous.get("investment_view"))
        value["investment_view"] = _versioned(view, previous.get("investment_view"), InvestmentView,
            f"{run_id}:investment-view", run_id, recorded_at)
    elif "investment_view" not in notebook.model_fields_set:
        value["investment_view"] = deepcopy(previous.get("investment_view"))
    if notebook.decision_brief is not None:
        brief = _merge_partial(notebook.decision_brief, previous.get("decision_brief"))
        brief["basis_view_version_id"] = (value.get("investment_view") or {}).get("version_id")
        value["decision_brief"] = _versioned(brief, previous.get("decision_brief"), DecisionBrief,
            f"{run_id}:decision-brief", run_id, recorded_at)
    elif "decision_brief" not in notebook.model_fields_set:
        value["decision_brief"] = deepcopy(previous.get("decision_brief"))
    if value.get("decision_brief"):
        value["decision_brief"]["needs_review"] = (
            value["decision_brief"].get("basis_view_version_id") != (value.get("investment_view") or {}).get("version_id"))
    # Legacy prose is an explicitly dated archive, never an editable module or
    # an alternative current investment view. Its original source binding stays.
    if previous.get("prior_analysis"):
        value["prior_analysis"] = deepcopy(previous["prior_analysis"])
    refs = notebook_source_ids(value)
    # research_sources already includes admissible older originals. Do not restore rejected
    # method/summary/future-publication records through a second unfiltered history path.
    value["sources"] = [source_reference(sources[sid]) for sid in sorted(refs) if sid in sources]
    stable_fields = set(ResearchNotebook.model_fields) - {"important_changes", "mandate_update"}
    def semantic(item):
        if isinstance(item, dict):
            return {key: semantic(val) for key, val in item.items() if key not in {"checked_at", "method_version"}}
        return [semantic(val) for val in item] if isinstance(item, list) else item
    changed = not previous or any(semantic(value[key]) != semantic(previous.get(key, ResearchNotebook.model_fields[key].get_default(call_default_factory=True)))
                                  for key in stable_fields)
    return {**value, "schema_version": 2, "method_plan": deepcopy(research_plan or previous.get("method_plan")),
            "run_id": run_id, "checked_at": cutoff,
            "version_id": run_id if changed else previous.get("version_id", previous.get("run_id", run_id)),
            "created_at": previous.get("created_at") or recorded_at,
            "updated_at": recorded_at if changed else previous.get("updated_at", recorded_at)}
