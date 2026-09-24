"""Publish explicitly selected, source-bound researcher work without running a model.

The import package is an external research artifact, not a ticker template. Preview
is read-only; publication uses the same validation, locks and retained versions as
the harness, with honest authorship instead of a fabricated model fact review.
"""
from copy import deepcopy
from datetime import UTC, date, datetime
import hashlib
import json
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import select

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services import sector_research
from watchlist_app.services.research_access import require_team_write
from watchlist_app.services.research_dossier import read_dossier
from watchlist_app.services.research_identity import research_identity
from watchlist_app.services.research_quant import QuantOutput


class ImportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    published_at: str | None = None
    retrieved_at: AwareDatetime
    text: str = Field(min_length=1, max_length=1000000)
    body_kind: Literal["original_excerpt", "original_full"]
    locator: str = Field(min_length=1, max_length=2000)
    date_evidence: str = Field(default="", max_length=2000)

    @field_validator("published_at")
    @classmethod
    def publication_precision(cls, value):
        return sector_research.SectorEvent.retain_time_precision(value)

    @model_validator(mode="after")
    def readable_original(self):
        if not self.text.strip() or "\ufffd" in self.text or "\x00" in self.text:
            raise ValueError("导入依据须为可读原文；不可用来源摘要代替原文")
        if self.url.username or self.url.password:
            raise ValueError("来源地址不得包含凭证")
        return self


class ImportFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(pattern=r"^computed:[a-zA-Z0-9:_-]+$", max_length=250)
    title: str = Field(min_length=1, max_length=200)
    as_of: date
    source_ids: list[str] = Field(min_length=1, max_length=12)
    methodology: str = Field(min_length=1, max_length=6000)
    preparation: Literal["source_transcription"] = "source_transcription"
    output: QuantOutput


class ResearchImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    import_id: UUID
    instrument_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=250)
    research_as_of: date
    baseline: bool = True
    verification_note: str = Field(min_length=1, max_length=4000)
    sources: list[ImportSource] = Field(min_length=1, max_length=60)
    figures: list[ImportFigure] = Field(default_factory=list, max_length=12)
    review: sector_research.SectorReview

    @model_validator(mode="after")
    def single_scope(self):
        if self.review.instrument_id != self.instrument_id:
            raise ValueError("导入内容与明确选择的标的不一致")
        ids = [source.source_id for source in [*self.sources, *self.figures]]
        if len(ids) != len(set(ids)):
            raise ValueError("导入来源标识不能重复")
        source_ids = {source.source_id for source in self.sources}
        for figure in self.figures:
            if not set(figure.source_ids).issubset(source_ids):
                raise ValueError("导入图表须绑定本包实际取得的原文，不接受孤立数值")
        if not self.review.research or not self.review.research.investment_view:
            raise ValueError("编制研究需要研究底稿及研究员当前判断")
        if not self.review.research.modules:
            raise ValueError("编制研究需要有依据的深度研究模块")
        if not {"key_drivers", "next_research"}.issubset(self.review.research.model_fields_set):
            raise ValueError("完整编制报告须明确提供核心驱动及下一验证；无条目时提交空列表，不能继承旧摘要")
        if self.review.events:
            raise ValueError("编制入口只发布研究报告与主题；披露日期保留为证据，不补写事件研究历史")
        if self.review.research.mandate_update is not None:
            raise ValueError("编制入口不能修改用户研究设置；请通过原有研究设置入口维护")
        if self.baseline and self.review.research.important_changes:
            raise ValueError("首次基线不能伪装成相对此前研究的变化")
        if sum(len(source.text.encode()) for source in self.sources) > 5000000:
            raise ValueError("单标的原文包过大，请只保留本次实际引用的原文和表格")
        return self


class ImportPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: ResearchImport
    expected_versions: dict


def _access():
    principal = require_team_write()
    if principal.resource_scope:
        raise HTTPException(403, "模型运行凭证不能自行编制发布团队研究")
    return principal


def base_versions(dossier):
    return {"team_id": research_identity()["team_id"],
        "notebook": (dossier.get("notebook") or {}).get("version_id"),
        "mandate": (dossier.get("mandate") or {}).get("version_id"),
        "themes": {row["theme_id"]: row["revision_number"] for row in dossier.get("themes", [])},
        "methods": [[row["id"], row["version"], row["applicability"]]
                    for row in dossier.get("research_plan", {}).get("modules", [])]}


def _digest(package):
    return hashlib.sha256(json.dumps(package.model_dump(mode="json"), sort_keys=True,
                                    ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _run_id(package):
    return f"compiled:{research_identity()['team_id']}:{package.import_id.hex}"


def _originals(package, cutoff):
    originals = []
    if package.research_as_of > cutoff.date():
        raise ValueError("研究基线日期不能晚于导入日期")
    for source in package.sources:
        if source.retrieved_at > cutoff:
            raise ValueError("原文取得时间不能晚于当前时间")
        if source.published_at and date.fromisoformat(source.published_at[:10]) > package.research_as_of:
            raise ValueError("来源首发日期晚于声明的研究时点")
        if not source.published_at and source.retrieved_at.date() > package.research_as_of:
            raise ValueError("未确认首发日期的来源不能追溯认定为此前研究时点已可得")
        originals.append({**source.model_dump(mode="json"), "source_type": "public_source",
            "instrument_id": package.instrument_id, "text_truncated": source.body_kind == "original_excerpt",
            "coverage": ["明确摘录，仅核对所引段落及表格"] if source.body_kind == "original_excerpt" else [],
            "time_status": "date_only" if source.published_at and len(source.published_at) == 10
                           else "verified" if source.published_at else "unknown"})
    return originals


def _context(package, dossier, originals, cutoff):
    by_id = {source["source_id"]: source for source in originals}
    figures = []
    for figure in package.figures:
        if figure.as_of > package.research_as_of:
            raise ValueError("图表资料日期不能晚于研究时点")
        figures.append({"source_id": figure.source_id, "source_type": "computed_metric", "scope": "instrument",
            "instrument_id": package.instrument_id, "title": figure.title, "as_of": figure.as_of.isoformat(),
            "source_ids": figure.source_ids,
            "methodology": {"analysis_kind": "source_transcription", "description": figure.methodology,
                "preparation": "source_transcription", "code": None, "params": {},
                "input_sources": [deepcopy(by_id[sid]) for sid in figure.source_ids],
                "runtime": None, "executed_at": None,
                "result_status": "Codex编制并核对来源的表格；未声称执行Python或通过独立模型核证。"},
            # Existing renderer accepts source-bound tables/charts through this
            # data contract. Methodology above records how these values arose.
            "data": {"analysis_kind": "python_quant", "status": "available", **figure.output.model_dump(mode="json")}})
    return {"research_run": True, "instrument_ids": [package.instrument_id], "cutoff": cutoff.isoformat(),
        "research_actor": research_identity(), "research_dossiers": [dossier],
        "web_evidence": [{"operation": "author_import", "sources": originals}], "computed_metrics": figures,
        "publication": {"origin": "codex_compiled", "display_name": "Codex", "baseline": package.baseline,
            "research_as_of": package.research_as_of.isoformat(), "submitted_by": research_identity(),
            "verification_note": package.verification_note, "review_method": "author_source_check",
            "independent_model_review": False, "model_execution": False},
        "import_package_sha256": _digest(package), "reviews": {}}


def _draft_run(package, dossier, originals, cutoff):
    return SimpleNamespace(entry_id=_run_id(package),
        topic_id=f"{sector_research.INSTRUMENT_TOPIC_PREFIX}compiled:{research_identity()['team_id']}:{package.instrument_id}",
        context_json=_context(package, dossier, originals, cutoff), team_id=research_identity()["team_id"])


def preview_import(session, package: ResearchImport):
    _access()
    if not sector_research.scoped_ids(session, instrument_id=package.instrument_id):
        raise ValueError("请选择当前已登记且有效的研究标的")
    dossier = read_dossier(session, package.instrument_id)
    cutoff = datetime.now(UTC)
    run = _draft_run(package, dossier, _originals(package, cutoff), cutoff)
    sector_research.validate_result(session, run, sector_research.ReviewResult(reviews=[package.review]))
    previous = {row["theme_id"]: row for row in dossier.get("themes", [])}
    from watchlist_app.services.research_themes import analyst_theme_target
    transitions = []
    for update in package.review.themes:
        old = analyst_theme_target(session, package.instrument_id, update)
        transitions.append({"theme_id": (old or {}).get("theme_id"), "theme_key": update.theme_key,
            "title": update.title or (old or {}).get("title"), "before": (old or {}).get("status"),
            "after": update.status or (old or {}).get("status", "active"), "reason": update.close_reason,
            "operation": "update" if old else "create"})
        if old:
            previous.pop(old["theme_id"], None)
    return {"instrument_id": package.instrument_id, "name": dossier["name"], "dry_run": True,
        "import_id": str(package.import_id), "expected_versions": {**base_versions(dossier), "import_package_sha256": _digest(package)},
        "publication": run.context_json["publication"],
        "review": sector_research.draft_payload(sector_research.ReviewResult(reviews=[package.review]))["reviews"][0],
        "sources": [{key: source.get(key) for key in ("source_id", "title", "url", "published_at", "retrieved_at", "body_kind", "locator")}
                    for source in run.context_json["web_evidence"][0]["sources"]],
        "figures": [{"source_id": figure.source_id, "title": figure.title, "methodology": figure.methodology,
                     "output": figure.output.model_dump(mode="json")} for figure in package.figures],
        "theme_transitions": transitions,
        "unchanged_themes": [{"theme_id": row["theme_id"], "title": row["title"], "status": row["status"]} for row in previous.values()],
        "note": "预览没有创建研究记录、改变主题或保存原文；发布仍须匹配当前版本。旧档案保留，不写投资经理观点。"}


def _replace_source_ids(value, mapping):
    if isinstance(value, list):
        return [_replace_source_ids(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: [mapping.get(sid, sid) for sid in item] if key in {"source_ids", "figure_source_ids"}
                else _replace_source_ids(item, mapping) for key, item in value.items()}
    return value


def publish_import(session, request: ImportPublication):
    _access()
    package = request.package
    # Serialize with every other instrument publication before checking preview
    # versions or idempotency. No original documents are written on conflict.
    session.scalar(select(InstrumentDetail).where(InstrumentDetail.instrument_id == package.instrument_id).with_for_update())
    existing = session.get(ResearchEntry, _run_id(package))
    if existing is not None:
        if existing.context_json.get("import_package_sha256") != _digest(package):
            raise sector_research.ResearchVersionConflict("同一导入编号已用于不同内容，请重新预览并使用新的导入编号")
        return {"run_id": existing.entry_id, "instrument_id": package.instrument_id, "status": existing.status, "already_published": True}
    preview = preview_import(session, package)
    if request.expected_versions != preview["expected_versions"]:
        raise sector_research.ResearchVersionConflict("预览后研究档案、方法或主题已变化，请重新预览后发布")
    run, created = sector_research.begin_run(session, [package.instrument_id], commit=False, compiled=True)
    if not created:
        raise sector_research.ReviewInProgress("本标的已有研究运行，请完成后再导入基线")
    run.entry_id = _run_id(package)
    run.team_id = research_identity()["team_id"]
    run.title, run.source = package.title, "Codex · 研究员编制"
    run.author_user_id = None
    from watchlist_app.services.market_evidence import original_source, text_store
    cutoff = datetime.now(UTC)
    raw = _originals(package, cutoff)
    captured = [original_source(text_store().capture_public_source(source, origin="author_provided")) for source in raw]
    mapping = {before["source_id"]: after["source_id"] for before, after in zip(raw, captured)}
    converted = _replace_source_ids(package.model_dump(mode="json", exclude_unset=True), mapping)
    # The author package and original IDs remain auditable; the publication
    # itself references canonical immutable source versions.
    figure_package = package.model_copy(update={"figures": [ImportFigure.model_validate(row) for row in converted.get("figures", [])]})
    context = _context(figure_package, read_dossier(session, package.instrument_id), captured, datetime.now(UTC))
    context.update(import_package_sha256=_digest(package), imported_source_ids=mapping)
    run.context_json = context
    review = sector_research.SectorReview.model_validate(converted["review"])
    sector_research.apply_result(session, run, sector_research.ReviewResult(reviews=[review]).model_dump_json(exclude_unset=True))
    session.flush()
    return {"run_id": run.entry_id, "instrument_id": package.instrument_id, "status": run.status,
            "already_published": False, "publication": context["publication"]}
