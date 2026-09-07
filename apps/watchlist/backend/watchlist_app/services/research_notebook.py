"""Dated analyst working papers, distinct from the investment manager's views."""
from datetime import date, datetime
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from watchlist_app.services.research_dossier import ResearchMandateInput


class ResearchCatalyst(BaseModel):
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
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    question: str = Field(min_length=1, max_length=1000)
    assessment: str = Field(max_length=4000)
    evidence_for: list[str] = Field(default_factory=list)
    evidence_against: list[str] = Field(default_factory=list)
    next_check: str = Field(max_length=2000)
    status: Literal["open", "supported", "refuted"] = "open"
    source_ids: list[str] = Field(default_factory=list)


class ResearchFact(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    metric: str = Field(min_length=1, max_length=300)
    value: str = Field(min_length=1, max_length=1000)
    unit: str = Field(max_length=300)
    period: str = Field(max_length=500)
    comparison: str = Field(default="", max_length=1500)
    uncertainty: str = Field(default="", max_length=1500)
    source_ids: list[str] = Field(min_length=1)


class ResearchNotebook(BaseModel):
    fundamental_view: str = Field(max_length=6000)
    key_drivers: list[str] = Field(default_factory=list)
    valuation_view: str = Field(max_length=4000)
    questions: list[ResearchQuestion] = Field(default_factory=list)
    important_changes: list[str] = Field(default_factory=list)
    next_research: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    catalysts: list[ResearchCatalyst] = Field(default_factory=list)
    facts: list[ResearchFact] = Field(default_factory=list)
    mandate_update: ResearchMandateInput | None = None


def retained_public_sources(session, instrument_id: str) -> list[dict]:
    """Keep fetched originals usable even when the associated AI draft was rejected."""
    from sqlalchemy import select
    from watchlist_app.db.models.workbench import ResearchEntry
    records = session.scalars(select(ResearchEntry).where(ResearchEntry.kind == "analysis").order_by(ResearchEntry.created_at.desc()))
    by_url = {}
    for record in records:
        context = record.context_json or {}
        scope = context.get("instrument_ids", [])
        if (not context.get("sector_run") or instrument_id not in scope
                or record.topic_id not in {f"instrument-events:{instrument_id}", "us-sector-daily-review"}):
            continue
        # Batch fetches have no instrument attribution: use explicit draft/review references.
        references = set()
        reviews = [*context.get("submitted_draft", {}).get("reviews", []),
                   {"instrument_id": instrument_id, **context.get("reviews", {}).get(instrument_id, {})}]
        for review in reviews:
            if review.get("instrument_id") != instrument_id:
                continue
            research = review.get("research") or {}
            references.update(research.get("source_ids", []))
            for row in [*review.get("events", []), *research.get("questions", []),
                        *research.get("catalysts", []), *research.get("facts", [])]:
                references.update(row.get("source_ids", []))
        for capture in reversed(context.get("web_evidence", [])):
            if capture.get("operation") != "fetch":
                continue
            for source in capture.get("sources", []):
                if len(scope) != 1 and source.get("source_id") not in references:
                    continue
                original = {**source, "source_type": "public_source", "source_run_id": record.entry_id,
                    "instrument_id": instrument_id, "role": "retained_original",
                    "verification_note": "已取得的原文，不代表其主张已核实；须重读正文及原始日期。所属报告的模型结论不作为依据。"}
                if _original_source(original, instrument_id):
                    by_url.setdefault(source.get("url") or source["source_id"], original)
    return list(by_url.values())


def dossier_outline(dossier: dict) -> dict:
    """Read the working view first; retrieve long original materials and cases on demand."""
    notebook = dossier.get("notebook")
    return {**dossier,
        "prior_sources": [{k: s.get(k) for k in ("source_id", "title", "url", "published_at", "retrieved_at", "source_run_id")} | {"body_available": bool(s.get("text"))}
                          for s in dossier.get("prior_sources", [])],
        "prior_sources_note": "已取得原文的索引，不表示已核实其主张。按source_id重读正文与日期；原报告失败或撤回不影响原文留存。",
        "materials": [{k: v for k, v in m.items() if k != "body"} | {"body_available": bool(m.get("body"))}
                      for m in dossier.get("materials", [])],
        "historical_cases": [{k: c.get(k) for k in ("source_id", "case_id", "case_title", "role", "current_use", "limitations")}
                             for c in dossier.get("historical_cases", [])],
        "notebook": {**notebook, "sources": [{k: s.get(k) for k in
            ("source_id", "title", "url", "source_type", "source_run_id", "published_at", "retrieved_at", "run_cutoff")}
            for s in notebook.get("sources", [])]} if notebook else None}


def dossier_source(dossier: dict, source_id: str) -> dict:
    for source in [*dossier.get("materials", []), *dossier.get("historical_cases", []),
                   *dossier.get("prior_sources", []),
                   *(dossier.get("notebook") or {}).get("sources", [])]:
        if source.get("source_id") == source_id:
            if source.get("instrument_id") != dossier["instrument_id"] and not (
                    source.get("source_type") == "public_source" and source.get("instrument_id") is None):
                raise ValueError("这份依据不属于当前标的研究档案")
            return source
    raise ValueError("当前研究档案没有这份材料或历史依据")


def _original_source(source: dict, iid: str, cutoff: datetime | None = None) -> bool:
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
                       "analyst_estimate_changes": "current_snapshot"}.get(kind)
        readable = bool(payload_key and source.get(payload_key))
        scope_matches = source.get("instrument_id") == iid
    if not readable or not scope_matches or source.get("time_status") == "future":
        return False
    published = source.get("published_at") or (source.get("metadata") or {}).get("published_at")
    if cutoff is not None and published:
        try:
            if len(published) == 10:
                return date.fromisoformat(published) <= cutoff.date()
            stamp = datetime.fromisoformat(published)
            if stamp.tzinfo is not None:
                return stamp <= cutoff
        except ValueError:
            pass  # Unknown publication precision does not become an invented timestamp.
    return True


def research_sources(context: dict, run_id: str) -> dict[str, dict]:
    """Only original evidence is reusable; methods and AI summaries are not sources."""
    sources = {}
    cutoff = datetime.fromisoformat(context["cutoff"])
    for dossier in context.get("research_dossiers", []):
        iid = dossier["instrument_id"]
        for source in [*dossier.get("prior_sources", []), *(dossier.get("notebook") or {}).get("sources", [])]:
            if _original_source(source, iid, cutoff):
                sources[source["source_id"]] = source
        for material in dossier.get("materials", []):
            source = {**material, "text": material.get("body", ""), "source_type": "research_material"}
            if _original_source(source, iid, cutoff):
                sources[source["source_id"]] = source
        for case in dossier.get("historical_cases", []):
            source = {**case, "source_type": "historical_case"}
            if _original_source(source, iid, cutoff):
                sources[source["source_id"]] = source
    # A current, successfully read material supersedes its earlier retained copy.
    for asset in context.get("instrument_inputs", []):
        iid = asset["instrument_id"]
        sid = f"instrument:{run_id}:{iid}"
        sources[sid] = {"source_id": sid, "instrument_id": iid, "source_run_id": run_id,
            "source_type": "instrument_snapshot", "title": f"{asset['name']} · 已披露资料与指标",
            "run_cutoff": context["cutoff"], "snapshot": {k: v for k, v in asset.items()
                if k not in {"research", "research_tracking", "research_tracking_note", "research_dossier",
                             "risk_cases", "analyst_focus", "materials", "materials_note"}}}
    for asset in context.get("sector_inputs", []):
        iid = asset["instrument_id"]
        sid = f"sector:{run_id}:{iid}"
        sources[sid] = {"source_id": sid, "instrument_id": iid, "source_run_id": run_id,
            "source_type": "sector_snapshot", "title": f"{iid.upper()} · 成份与预期快照",
            "run_cutoff": context["cutoff"], "snapshot": {k: v for k, v in asset.items() if k != "research_focus"}}
    for iid, companies in context.get("sector_company_data", {}).items():
        for symbol, company in companies.items():
            sid = f"fmp:{run_id}:{iid}:{symbol}"
            sources[sid] = {"source_id": sid, "instrument_id": iid, "source_run_id": run_id,
                "source_type": "company_snapshot", "title": f"FMP · {symbol} 公司资料与预期", "company": company}
    sources.update(retained_estimate_sources(context))
    for capture in context.get("web_evidence", []):
        if capture.get("operation") == "fetch":
            for source in capture.get("sources", []):
                original = {**source, "source_run_id": run_id, "source_type": "public_source"}
                if _original_source(original, source.get("instrument_id"), cutoff):
                    sources[source["source_id"]] = original
    return sources


def validate_notebook(notebook: ResearchNotebook, iid: str, sources: dict[str, dict]):
    keys = [q.key for q in notebook.questions]
    if len(set(keys)) != len(keys):
        raise ValueError("同一研究问题在本轮重复出现")
    catalyst_keys = [c.key for c in notebook.catalysts]
    if len(set(catalyst_keys)) != len(catalyst_keys):
        raise ValueError("同一预定事件在本轮重复出现")
    for catalyst in notebook.catalysts:
        if not any(sources.get(sid, {}).get("source_type") in {"public_source", "research_material"} for sid in catalyst.source_ids):
            raise ValueError("预定事件必须有已取得的日程或披露原文，历史案例和模型判断不能证明日程")
    refs = (set(notebook.source_ids) | {sid for q in notebook.questions for sid in q.source_ids}
            | {sid for fact in notebook.facts for sid in fact.source_ids}
            | {sid for c in notebook.catalysts for sid in c.source_ids})
    for sid in refs:
        source = sources.get(sid)
        if source is None:
            raise ValueError("研究底稿引用了未取得的原始依据")
        if source.get("instrument_id") not in {None, iid}:
            raise ValueError("研究底稿引用了其他标的的私有资料或快照")
        if not _original_source(source, iid):
            raise ValueError("研究底稿依据不是已取得的原文、资料或真实快照")


def retain_notebook(notebook: ResearchNotebook, previous: dict | None, sources: dict[str, dict], run_id: str, cutoff: str):
    value = notebook.model_dump(mode="json")
    updated = {q["key"] for q in value["questions"]}
    # Silence is not resolution. Unmentioned questions keep their original assessment and evidence.
    value["questions"] += [q for q in (previous or {}).get("questions", []) if q["key"] not in updated]
    updated_catalysts = {c["key"] for c in value["catalysts"]}
    value["catalysts"] += [c for c in (previous or {}).get("catalysts", []) if c["key"] not in updated_catalysts]
    refs = (set(value["source_ids"]) | {sid for q in value["questions"] for sid in q["source_ids"]}
            | {sid for fact in value["facts"] for sid in fact["source_ids"]}
            | {sid for c in value["catalysts"] for sid in c["source_ids"]})
    # research_sources already includes admissible older originals. Do not restore rejected
    # method/summary/future-publication records through a second unfiltered history path.
    value["sources"] = [sources[sid] for sid in sorted(refs) if sid in sources]
    return {**value, "run_id": run_id, "checked_at": cutoff}
