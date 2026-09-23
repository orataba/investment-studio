"""Instrument research materials and methods, using existing research records."""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from watchlist_app.db.models import InstrumentDetail, InstrumentManualProfile
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_methods import SUPPORTED_TYPES, TYPE_LABELS, build_research_plan, method_library, module_ids

DATA_ROOT = Path(__file__).resolve().parents[5] / "data" / "research"
TEXT_LIMIT = 60000


class ResearchModuleFocus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_id: str
    reason: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(default_factory=list)
    selected_by: Literal["user", "research", "initial"] | None = Field(default=None,
        description="保存时由服务端确定；研究员不得声明为用户选择。")

    @field_validator("module_id")
    @classmethod
    def known_module(cls, value):
        if value not in module_ids():
            raise ValueError("请选择方法库中的研究模块")
        return value

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value):
        if not value.strip():
            raise ValueError("请说明该研究模块与本标的的关系")
        return value.strip()


class ResearchMandateInput(BaseModel):
    """A specific research assignment, never an independent factual source."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    background: str = Field(min_length=1, max_length=6000,
        description="区分已登记资料、注明原始依据的事实和待验证假设；不把方法或AI判断写成已核实事实。")
    mechanisms: list[str] = Field(default_factory=list, max_length=30)
    research_approach: list[str] = Field(default_factory=list, max_length=30)
    focus: list[str] = Field(default_factory=list, max_length=30)
    source_plan: list[str] = Field(default_factory=list, max_length=30,
        description="计划取得的原始材料/数据与日期口径；来源计划不代表已经读取。")
    gaps: list[str] = Field(default_factory=list, max_length=30)
    user_constraints: list[str] = Field(default_factory=list, max_length=30,
        description="用户明确的范围、比较基准与方法限制；研究员更新不可覆盖。")
    module_focus: list[ResearchModuleFocus] = Field(default_factory=list, max_length=20,
        description="本标的需要补充的适用方法；说明原因并引用原始依据，不改写公共方法。")

    @field_validator("title", "background")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入研究底稿标题和背景")
        return value.strip()

    @field_validator("mechanisms", "research_approach", "focus", "source_plan", "gaps", "user_constraints")
    @classmethod
    def concise_items(cls, values):
        if any(not value.strip() or len(value) > 2000 for value in values):
            raise ValueError("研究底稿条目不能为空，且每项最多2000字")
        return [value.strip() for value in values]

    @field_validator("module_focus")
    @classmethod
    def unique_modules(cls, values):
        if len({item.module_id for item in values}) != len(values):
            raise ValueError("同一方法模块不能重复选择")
        return values


def _registration(session: Session, instrument: InstrumentDetail) -> dict:
    from investment_studio_instrument_core.db_models import Instrument, InstrumentReferenceSnapshot
    from watchlist_app.db.models import InstrumentTaxonomyAssignment, InstrumentTaxonomyNode
    from watchlist_app.reference_data.instrument_taxonomy import INSTRUMENT_TAXONOMY_CODE
    from watchlist_app.services.instrument_taxonomy import build_taxonomy_context
    shared = session.get(Instrument, instrument.instrument_id)
    reference = session.get(InstrumentReferenceSnapshot, instrument.instrument_id)
    reference = reference.value_json if reference else {}
    sections = reference.get("sections") or {}
    profile = sections.get("profile") or {}
    fund = sections.get("fund_info") or {}
    index = sections.get("index_info") or {}
    manual = session.get(InstrumentManualProfile, instrument.instrument_id)
    assignment = session.get(InstrumentTaxonomyAssignment, (instrument.instrument_id, INSTRUMENT_TAXONOMY_CODE))
    node = session.get(InstrumentTaxonomyNode, assignment.node_id) if assignment and assignment.node_id else None
    strategy = manual.strategy_payload_json if manual else None
    if strategy and not any(value for key, value in strategy.items() if key != "notes"):
        strategy = None
    return serialize_payload({
        "name": instrument.instrument_name, "instrument_type": instrument.instrument_type,
        "identifier": instrument.primary_identifier_value,
        "currency": shared.currency if shared else profile.get("currency"),
        "exchange": shared.exchange_code if shared else (instrument.metadata_json or {}).get("exchange_code"),
        "industry": profile.get("industry"), "sector": profile.get("sector"),
        "website": profile.get("website") or fund.get("website"), "benchmark": fund.get("benchmark"),
        "manager": fund.get("management") or fund.get("etfCompany"), "investment_type": fund.get("invest_type"),
        "asset_class": fund.get("assetClass") or fund.get("asset_class") or index.get("asset_class"),
        "fund_type": fund.get("fund_type"),
        "taxonomy": {**build_taxonomy_context(node), "assigned_at": assignment.assigned_at if assignment else None},
        "nav_settings": manual.nav_settings_json if manual else None,
        "disclosed_strategy": strategy,
        "provider": reference.get("provider"), "reference_fetched_at": reference.get("fetched_at"),
        "manual_updated_at": manual.updated_at if manual else None,
    })


def _initial_mandate(instrument: InstrumentDetail, registration: dict) -> ResearchMandateInput:
    """Create only this instrument's assignment; shared method prose stays shared."""
    name, kind = instrument.instrument_name, instrument.instrument_type
    facts = [f"{name}（{instrument.instrument_id}），登记类型为{TYPE_LABELS[kind]}"]
    for key, label in (("currency", "币种"), ("exchange", "市场"), ("industry", "行业"),
                       ("benchmark", "登记基准"), ("manager", "登记管理人"),
                       ("investment_type", "登记投资类型"), ("asset_class", "资料中的底层类别")):
        if registration.get(key):
            facts.append(f"{label}：{registration[key]}")
    taxonomy = registration.get("taxonomy") or {}
    if taxonomy.get("path_labels"):
        facts.append("已登记分类：" + " / ".join(taxonomy["path_labels"]) + "（研究线索，不是持仓证明）")
    focus = [f"为{name}建立当前研究基线：哪些事实支持回报前景，价格要求什么兑现，以及关键风险和反证。"]
    if registration.get("industry"):
        focus.append(f"核实{name}在登记行业“{registration['industry']}”中的实际业务及关键回报驱动。")
    if registration.get("benchmark"):
        focus.append(f"核对{name}的登记基准“{registration['benchmark']}”与真实投资范围及用户比较对象的区别。")
    source_plan = [f"从{registration.get('website') or registration.get('manager') or name + '的正式披露入口（待核实）'}取得本标的原始材料，并注明归属和适用日期。"]
    if kind == "private_fund":
        source_plan = [f"仅使用有权访问的{name}净值、合同和管理人报告；公开检索不发送私有材料或账户内容。"]
        if not registration.get("disclosed_strategy"):
            focus.append(f"{name}尚未取得已登记策略正文；结合已登记分类安排核查，不能由名称推断持仓、杠杆或对冲。")
    plan = build_research_plan(registration)
    initial = ResearchMandateInput(title=f"{name} · 专属研究安排",
        background="已登记资料（并非本轮原文核证）：" + "；".join(facts) + "。有原文支持的背景与工作假设待研究后分别补充。",
        focus=focus, source_plan=source_plan,
        gaps=["这是初始研究安排，尚不代表已完成专属研究。", *plan["gaps"]])
    # Retain existing instrument-specific work for environments that have not yet
    # persisted their mandate. Shared module prose is never copied into it.
    supplements = json.loads((DATA_ROOT / "mandate_seeds.json").read_text(encoding="utf-8"))
    supplement = supplements.get(instrument.instrument_id)
    if supplement:
        value = initial.model_dump(mode="json")
        value.update({key: item for key, item in supplement.items() if key != "background"})
        value["background"] += "\n" + supplement.get("background", "")
        return ResearchMandateInput.model_validate(value)
    return initial


def effective_mandate(payload: ResearchMandateInput, previous: dict | None, *, origin: str) -> dict:
    """Resolve writer-owned method choices before both validation and publication."""
    value = payload.model_dump(mode="json")
    previous = ResearchMandateInput.model_validate(previous).model_dump(mode="json") if previous else None
    if origin == "research" or "user_constraints" not in payload.model_fields_set:
        value["user_constraints"] = deepcopy((previous or {}).get("user_constraints", []))
    prior_modules = {item["module_id"]: item for item in (previous or {}).get("module_focus", [])}
    if "module_focus" not in payload.model_fields_set and previous:
        value["module_focus"] = deepcopy(previous["module_focus"])
    else:
        selected = []
        for item in value["module_focus"]:
            old = prior_modules.get(item["module_id"])
            if origin == "research" and old and old.get("selected_by") == "user":
                selected.append(deepcopy(old))
            else:
                same = old and all(item.get(key) == old.get(key) for key in ("module_id", "reason", "source_ids"))
                selected.append({**item, "selected_by": old.get("selected_by") if same else origin})
        if origin == "research":
            selected_ids = {item["module_id"] for item in selected}
            selected += [deepcopy(item) for key, item in prior_modules.items()
                         if item.get("selected_by") == "user" and key not in selected_ids]
        value["module_focus"] = selected
    return value


def plan_for_mandate(registration: dict, mandate: dict | ResearchMandateInput, *,
                     origin: str | None = None, previous_mandate: dict | None = None) -> dict:
    if origin is not None:
        payload = mandate if isinstance(mandate, ResearchMandateInput) else ResearchMandateInput.model_validate(mandate)
        # A read dossier includes metadata which is not part of the writer schema.
        previous = ({key: previous_mandate[key] for key in ResearchMandateInput.model_fields if key in previous_mandate}
                    if previous_mandate else None)
        value = effective_mandate(payload, previous, origin=origin)
    else:
        value = mandate.model_dump(mode="json") if isinstance(mandate, ResearchMandateInput) else mandate
    return build_research_plan(registration, user_constraints=value.get("user_constraints", []),
                               module_focus=value.get("module_focus", []))


def read_research_plan(session: Session, instrument_id: str) -> dict:
    mandate = read_mandate(session, instrument_id)
    return plan_for_mandate(mandate["registration"], mandate)


def _mandate_entry(session: Session, instrument_id: str) -> ResearchEntry | None:
    entry = session.get(ResearchEntry, f"dossier-mandate:{instrument_id}")
    if entry:
        topic = session.get(ResearchTopic, entry.topic_id)
        if (entry.topic_id != f"dossier:{instrument_id}" or entry.kind != "note"
                or not topic or topic.instrument_ids != [instrument_id] or topic.portfolio_id is not None
                or (entry.context_json or {}).get("instrument_id") != instrument_id
                or (entry.context_json or {}).get("role") != "research_mandate"):
            raise ValueError("专属研究底稿的标的归属不一致")
    return entry


def read_mandate(session: Session, instrument_id: str) -> dict:
    instrument = require_instrument(session, instrument_id)
    registration = _registration(session, instrument)
    entry = _mandate_entry(session, instrument_id)
    if entry:
        payload = ResearchMandateInput.model_validate(entry.context_json["mandate"])
    else:
        payload = _initial_mandate(instrument, registration)
    return serialize_payload({**payload.model_dump(), "instrument_id": instrument_id,
        "role": "research_method", "registration": registration,
        "entry_id": entry.entry_id if entry else None, "updated_at": entry.updated_at if entry else None,
        "created_at": entry.created_at if entry else None,
        "version_id": f"{entry.entry_id}:v{entry.context_json.get('revision', 1)}" if entry else None,
        "author": deepcopy(entry.context_json.get("author")) if entry else {"origin": "initial"},
        "user_focus": deepcopy(entry.context_json.get("user_focus", [])) if entry else [],
        "versions": deepcopy(entry.context_json.get("versions", [])) if entry else [],
        "usage_note": "专属方法与工作假设不是独立事实依据；背景中的事实仍须引用原文。登记资料和来源计划不代表已读或已核实。"})


def save_mandate(session: Session, instrument_id: str, payload: ResearchMandateInput, *, commit: bool = True,
                 origin: Literal["user", "research", "initial"] = "user") -> dict:
    from watchlist_app.services.research_identity import research_identity
    research_actor = research_identity()
    topic = dossier_topic(session, instrument_id)
    entry = _mandate_entry(session, instrument_id)
    if entry is not None:
        # dossier_topic holds the instrument lock shared with research publication.
        # Refresh an identity-map entry read before waiting for that lock.
        session.refresh(entry)
    previous = ResearchMandateInput.model_validate(entry.context_json["mandate"]).model_dump(mode="json") if entry else None
    value = effective_mandate(payload, previous, origin=origin)
    user_focus = deepcopy(value["focus"] if origin == "user" else (entry.context_json.get("user_focus", []) if entry else []))
    if origin == "user":
        # The editor's focus field is the user's instruction; the researcher owns
        # the evolving focus in the mandate document.
        value["focus"] = deepcopy(entry.context_json["mandate"].get("focus", []) if entry else read_mandate(session, instrument_id)["focus"])
    versions = []
    revision = 1
    if entry is not None:
        if previous == value and user_focus == entry.context_json.get("user_focus", []):
            return read_mandate(session, instrument_id)
        revision = entry.context_json.get("revision", 1) + 1
        versions = [*deepcopy(entry.context_json.get("versions", [])), serialize_payload({
            **previous, "version_id": f"{entry.entry_id}:v{revision - 1}",
            "author": deepcopy(entry.context_json.get("author")),
            "user_focus": deepcopy(entry.context_json.get("user_focus", [])),
            "created_at": entry.created_at, "updated_at": entry.updated_at})]
    else:
        entry = ResearchEntry(entry_id=f"dossier-mandate:{instrument_id}", topic_id=topic.topic_id,
                              kind="note", title=payload.title, status="recorded")
        session.add(entry)
    entry.title, entry.body = payload.title, payload.background
    entry.team_id = research_actor["team_id"]
    entry.author_user_id = research_actor["user_id"] if origin == "user" else None
    author = {"origin": origin, "user_id": entry.author_user_id,
              "display_name": research_actor["display_name"] if origin == "user" else ("研究员" if origin == "research" else "系统初始化"),
              "requested_by_user_id": research_actor["user_id"], "service_id": research_actor.get("service_id")}
    entry.context_json = {"role": "research_mandate", "instrument_id": instrument_id,
                          "mandate": value, "revision": revision, "versions": versions,
                          "author": author, "user_focus": user_focus}
    entry.updated_at = topic.updated_at = datetime.now(UTC)
    session.flush()
    if commit:
        session.commit()
    return read_mandate(session, instrument_id)


def ensure_mandate(session: Session, instrument_id: str) -> dict:
    """Persist the initial assignment once, within the caller's existing transaction."""
    current = read_mandate(session, instrument_id)
    if current["entry_id"] is not None:
        return current
    payload = ResearchMandateInput.model_validate({key: current[key] for key in ResearchMandateInput.model_fields})
    return save_mandate(session, instrument_id, payload, commit=False, origin="initial")


def require_instrument(session: Session, instrument_id: str) -> InstrumentDetail:
    instrument = session.get(InstrumentDetail, instrument_id)
    if instrument is None:
        raise LookupError("标的尚未登记")
    if instrument.instrument_type not in SUPPORTED_TYPES:
        raise ValueError("该标的类型尚不支持研究档案")
    return instrument


def dossier_topic(session: Session, instrument_id: str) -> ResearchTopic:
    instrument = require_instrument(session, instrument_id)
    # Serialize creation and revisions with automatic/interactive publication.
    # The instrument exists even before the first dossier or mandate does.
    session.refresh(instrument, with_for_update=True)
    topic_id = f"dossier:{instrument_id}"
    topic = session.get(ResearchTopic, topic_id)
    if topic is None:
        topic = ResearchTopic(topic_id=topic_id, title=f"{instrument.instrument_name} · 研究档案",
                              question="原始材料与资料沿革", instrument_ids=[instrument_id],
                              portfolio_id=None, status="active", conclusion="", visibility="team")
        session.add(topic)
        session.flush()
    elif topic.instrument_ids != [instrument_id] or topic.portfolio_id is not None:
        raise ValueError("研究档案的标的归属不一致")
    return topic


def material_record(entry: ResearchEntry, instrument_id: str) -> dict:
    metadata = dict(entry.context_json or {})
    derived = (metadata.get("source_kind") in {"generated_source_summary", "internal_computed_summary"}
               or metadata.get("extraction_status") in {"summary", "computed_summary"})
    body = entry.body
    if str(metadata.get("file_name", "")).lower().endswith(".pdf") and not re.sub(r"\[第 \d+ 页\]\s*", "", body).strip():
        body = ""
        metadata.update(extraction_status="empty", extraction="PDF未提取到文字正文；页码不代表已读原文，扫描件需补充文字")
    return serialize_payload({"source_id": f"material:{entry.entry_id}", "entry_id": entry.entry_id,
        "instrument_id": instrument_id, "source_type": "instrument_material", "role": "derived_reference" if derived else "source_material",
        "title": entry.title, "body": body, "source": entry.source,
        "metadata": metadata, "recorded_at": entry.created_at})


def add_material(session: Session, instrument_id: str, *, title: str, body: str, source: str,
                 published_at=None, effective_date=None) -> dict:
    from studio_identity import current_principal
    principal = current_principal()
    topic = dossier_topic(session, instrument_id)
    record = ResearchEntry(author_user_id=principal.user_id, team_id=principal.team_id, entry_id=uuid4().hex, topic_id=topic.topic_id, kind="evidence",
        title=title.strip(), body=body.strip(), source=source.strip(), status="recorded",
        context_json=serialize_payload({"published_at": published_at, "effective_date": effective_date,
                                       "extraction": "用户提供的材料正文", "extraction_status": "provided"}))
    topic.updated_at = datetime.now(UTC)
    session.add(record)
    session.commit()
    return material_record(record, instrument_id)


def _file_text(path: Path) -> tuple[str, str, str]:
    from pypdf.errors import PdfReadError
    try:
        if not path.is_file():
            return "", "missing", "本地原件不存在，未读取正文"
        if path.suffix.lower() in {".txt", ".md", ".csv"}:
            with path.open(encoding="utf-8-sig") as stream:
                text = stream.read(TEXT_LIMIT)
            return text, "extracted" if text.strip() else "empty", "文本最多保留前 60000 字符；完整原件保留"
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            parts, length = [], 0
            for index, page in enumerate(PdfReader(path).pages):
                content = page.extract_text() or ""
                if content.strip():
                    part = f"[第 {index + 1} 页]\n{content}"
                    parts.append(part)
                    length += len(part)
                if length >= TEXT_LIMIT:
                    break
            text = "\n".join(parts)[:TEXT_LIMIT]
            return text, "extracted" if text.strip() else "empty", "PDF 文字层，最多前 60000 字符；扫描件需补充文字"
        return "", "unsupported", "此文件格式尚未提取正文；请补充文字材料"
    except (OSError, UnicodeError, ValueError, PdfReadError):
        return "", "failed", "本地原件正文提取失败；目录信息不代表已读原文"


def _document_materials(session: Session, instrument_id: str) -> list[dict]:
    # Match the existing fund-document download route's actual storage location.
    from watchlist_app.api.routes.funds import _instrument_document_dir, _safe_file_segment
    manual = session.get(InstrumentManualProfile, instrument_id)
    rows = (manual.documents_payload_json or {}).get("current_documents", []) if manual else []
    folder = _instrument_document_dir(instrument_id).resolve()
    result = []
    for index, row in enumerate(rows):
        stored_name = row.get("stored_file_name")
        body, status, extraction = "", "metadata_only", "只有资料目录，未读取原文"
        if stored_name and _safe_file_segment(stored_name) == stored_name:
            path = folder / stored_name
            if path.resolve().is_relative_to(folder):
                body, status, extraction = _file_text(path)
        title = row.get("title") or row.get("file_name") or "登记资料"
        source = row.get("download_url") or row.get("source") or ""
        # A directory row can be edited while keeping its original filename.
        # Bind citations to the read text and its attribution, without read clocks
        # or extraction diagnostics making an unchanged original a new version.
        identity = {"body": body, "title": title, "source": source, "attribution": {
            key: row.get(key) for key in ("document_type", "file_name", "as_of_date", "published_at",
                                         "source", "version_label", "uploaded_at", "notes")}}
        version = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
        result.append({"source_id": f"material:document:{instrument_id}:{stored_name or index}:{version}",
            "entry_id": None, "instrument_id": instrument_id, "source_type": "instrument_material",
            "role": "source_material", "title": title,
            "body": body, "source": source,
            "metadata": {**row, "effective_date": row.get("as_of_date"),
                         "extraction": extraction, "extraction_status": status},
            "recorded_at": row.get("uploaded_at")})
    return result


def _research_records(session: Session, instrument_id: str, *, oldest_first: bool = False):
    from contextlib import closing
    from sqlalchemy import Boolean, JSON, String, true
    from types import SimpleNamespace
    from studio_identity import current_principal
    from watchlist_app.services.research_access import instrument_run_scope, research_context_projection, iter_research_projection_rows, topic_portfolio_ids_by_topic
    principal = current_principal()
    team_id = principal.team_id
    relation, context = research_context_projection(session, {"instrument_ids": JSON, "sector_run": Boolean,
        "research_run": Boolean, "cutoff": String, "recordkeeping_only": Boolean,
        "citation_correction": JSON, "organization_revision": JSON, "reviews": JSON})
    review = context["reviews"][instrument_id]
    query = select(ResearchEntry.entry_id, ResearchEntry.team_id, ResearchEntry.topic_id,
        ResearchEntry.status, ResearchEntry.created_at, ResearchEntry.completed_at,
        ResearchTopic.portfolio_id,
        context["sector_run"].label("sector_run"), context["research_run"].label("research_run"),
        context["cutoff"].label("cutoff"), context["recordkeeping_only"].label("recordkeeping_only"),
        context["citation_correction"].label("citation_correction"), context["organization_revision"].label("organization_revision"),
        review["status"].as_string().label("review_status"), review["research"].label("research"),
    ).select_from(ResearchEntry).join(ResearchTopic)
    if relation is not None:
        query = query.join(relation, true())
    query = query.where(
        ResearchEntry.kind == "analysis", ResearchEntry.status.in_(["completed", "draft"]),
        True if principal.local_unrestricted else ResearchEntry.team_id == team_id,
        True if principal.local_unrestricted else ResearchTopic.team_id == team_id,
        instrument_run_scope(session, instrument_id), review["research"].as_string().is_not(None),
    )
    topics = session.execute(query.with_only_columns(
        ResearchEntry.topic_id, ResearchTopic.portfolio_id, maintain_column_froms=True).distinct()).all()
    portfolios = topic_portfolio_ids_by_topic(session, topics)
    stamp = func.coalesce(ResearchEntry.completed_at, ResearchEntry.created_at)
    records = iter_research_projection_rows(session, query.where(ResearchEntry.topic_id.in_(
        [topic_id for topic_id, ids in portfolios.items() if not ids]))
        .order_by(stamp.asc() if oldest_first else stamp.desc()),
        {**{name: (name,) for name in ("sector_run", "research_run", "cutoff", "recordkeeping_only", "citation_correction", "organization_revision")},
         "review_status": ("reviews", instrument_id, "status"), "research": ("reviews", instrument_id, "research")})
    with closing(records):
        for row in records:
            research = row.research
            if (not (row.sector_run or row.research_run)
                    or (not row.research_run and (row.status != "completed" or row.topic_id not in {
                        "us-sector-daily-review", f"instrument-events:{instrument_id}"}))
                    or row.review_status not in {"completed", "limited"} or not isinstance(research, dict) or not research
                    or portfolios[row.topic_id]):
                continue
            # Do not overwrite an ORM identity's full context with an abbreviated one:
            # PM source/version reads may legitimately fetch that original run later.
            record = SimpleNamespace(**{key: getattr(row, key) for key in
                ("entry_id", "team_id", "topic_id", "status", "created_at", "completed_at")},
                context_json={"cutoff": row.cutoff, "recordkeeping_only": row.recordkeeping_only,
                              "citation_correction": row.citation_correction, "organization_revision": row.organization_revision})
            yield record, research


def _notebooks(session: Session, instrument_id: str, include_history: bool):
    from contextlib import closing
    from watchlist_app.services.research_notebook import notebook_current_view
    notebook, history = None, {}
    with closing(_research_records(session, instrument_id)) as records:
        for record, research in records:
            stamp = {"run_id": record.entry_id, "checked_at": research.get("checked_at") if record.context_json.get("recordkeeping_only") else record.context_json.get("cutoff"),
                     "version_id": research.get("version_id", record.entry_id),
                     "created_at": research.get("created_at") or record.completed_at or record.created_at,
                     "updated_at": research.get("updated_at") or record.completed_at or record.created_at}
            if notebook is None:
                notebook = notebook_current_view({**deepcopy(research), **stamp})
            if not include_history:
                break
            # Preserve the original saved snapshot of each actual revision; later quiet
            # checks still supply the current notebook's checked_at above.
            history[stamp["version_id"]] = {**stamp, "important_changes": research.get("important_changes", []),
                                          "notebook": notebook_current_view({**deepcopy(research), **stamp})}
    return notebook, list(history.values())


def read_dossier_version(session: Session, instrument_id: str, version_id: str, *, actor=None) -> dict:
    """Read saved research and its own information set, never reconstruct a past forecast."""
    from watchlist_app.services.research_notebook import notebook_source_ids
    from watchlist_app.services.market_evidence import hydrate_source
    require_instrument(session, instrument_id)
    if version_id.startswith("theme:"):
        from watchlist_app.services.research_themes import get_theme, theme_record
        try:
            identifier, revision = version_id.removeprefix("theme:").rsplit(":", 1)
            current = theme_record(get_theme(session, instrument_id, identifier, actor=actor))
            value = next(row for row in [*current.get("versions", []), current]
                         if row.get("revision_number", 1) == int(revision))
        except (ValueError, StopIteration) as error:
            raise LookupError("当前标的没有这个已保存的主题版本") from error
        cutoff = value.get("updated_at") or value.get("created_at")
        sources = [hydrate_source(source, cutoff=datetime.fromisoformat(cutoff) if cutoff else None)
                   for source in value.get("sources", [])]
        return serialize_payload({"instrument_id": instrument_id, "version_id": version_id, "kind": "theme",
            "value": {key: deepcopy(item) for key, item in value.items() if key != "versions"}, "sources": sources,
            "information_cutoff": cutoff, "recorded_at": cutoff,
            "usage_note": "当时保存的主题判断与依据；不使用后来研究中的同名来源替换。"})
    if version_id.startswith("pm:"):
        from watchlist_app.services.research_views import note_version, note_sources
        from watchlist_app.api.routes.research import _serialize_note_revision
        try:
            _, note_id, revision = version_id.split(":")
            record = note_version(session, instrument_id, note_id, int(revision), actor=actor)
        except (TypeError, ValueError) as error:
            raise LookupError("当前标的没有这个投资经理观点版本") from error
        value = _serialize_note_revision(record)
        return {"instrument_id": instrument_id, "version_id": version_id, "kind": "pm_view", "value": value,
                "information_cutoff": (value.get("research_context") or {}).get("information_cutoff") or value["recorded_at"],
                "recorded_at": value["recorded_at"], "sources": note_sources(session, instrument_id, value.get("research_context") or {}),
                "usage_note": "投资经理当时保存的观点及出处；结果与机制需分别验证，不能作为已经核实的事实。"}
    # Read oldest first: a quiet later run may retain the same version and add newer sources.
    from contextlib import closing
    with closing(_research_records(session, instrument_id, oldest_first=True)) as records:
        for record, research in records:
            candidates = [("notebook", {**research, "version_id": research.get("version_id", record.entry_id)})]
            view = research.get("investment_view")
            if view:
                candidates += [("investment_view", item) for item in [*view.get("versions", []), view]]
            for field in ("modules", "questions", "catalysts", "forecasts", "forecast_reviews", "lessons"):
                for item in research.get(field, []):
                    candidates += [(field, version) for version in [*item.get("versions", []), item]]
            for kind, value in candidates:
                if value.get("version_id") != version_id:
                    continue
                value = {key: deepcopy(item) for key, item in value.items() if key != "versions"}
                refs = notebook_source_ids(value)
                cutoff = record.context_json.get("cutoff")
                sources = [hydrate_source(source, cutoff=datetime.fromisoformat(cutoff) if cutoff else None)
                           for source in research.get("sources", []) if source.get("source_id") in refs]
                return serialize_payload({"instrument_id": instrument_id, "version_id": version_id, "kind": kind,
                    "value": value, "sources": sources, "information_cutoff": cutoff,
                    "recorded_at": record.completed_at or record.created_at,
                    "usage_note": "这是当时保存的研究及其引用依据，不补入后来资料；预测与复盘均不是独立原始事实证据。"})
    mandate = read_mandate(session, instrument_id)
    for value in [*mandate.get("versions", []), mandate]:
        if value.get("version_id") == version_id:
            return serialize_payload({"instrument_id": instrument_id, "version_id": version_id, "kind": "mandate",
                "value": {key: deepcopy(item) for key, item in value.items() if key not in {"versions", "registration"}},
                "sources": [], "information_cutoff": value.get("updated_at"),
                "usage_note": "这是保存的研究方法与背景版本，登记资料没有按当前值回填；方法和AI判断不是原始证据。"})
    raise LookupError("当前标的没有这个已保存的研究版本")


def _review_cases(instrument_id: str, notebook: dict | None) -> list[dict]:
    """Instrument-owned learning records remain analyst work, not original sources."""
    notebook = notebook or {}
    records = []
    for field in ("forecast_reviews", "lessons"):
        for item in notebook.get(field, []):
            records.append({**deepcopy(item), "source_id": f"research-review:{instrument_id}:{field}:{item['key']}",
                "case_id": item["key"], "case_title": item.get("lesson") or item.get("outcome"),
                "instrument_id": instrument_id, "source_type": "research_review", "role": "historical_research",
                "current_use": {"applicability": item.get("applicability", ""), "limitations": item.get("limitations", "")},
                "reuse_limitations": ["这是已保存的研究复盘或经验，不是原始事实；按关联预测版本和原文检查，价格吻合不证明因果。"]})
    return records


def read_dossier(session: Session, instrument_id: str, include_history: bool = False, *, actor=None) -> dict:
    """Read materials, methods and completed notebooks without creating records."""
    from watchlist_app.services.research_notebook import retained_public_sources
    from watchlist_app.services.research_themes import themes_view
    from watchlist_app.services.research_identity import research_identity
    from watchlist_app.api.routes.research import _serialize_note, research_repository
    actor = actor or research_identity()
    from studio_identity import current_principal
    local_unrestricted = current_principal().local_unrestricted
    instrument = require_instrument(session, instrument_id)
    mandate = read_mandate(session, instrument_id)
    research_plan = plan_for_mandate(mandate["registration"], mandate)
    versions = research_repository.list_note_revisions(session, instrument_id)
    from watchlist_app.services.research_views import note_sources, read_current_stance
    pm_views = [{**_serialize_note(note), "sources": note_sources(session, instrument_id, note.research_context or {}), "versions": [
        {"revision_number": v.revision_number, "version_id": f"pm:{v.note_id}:{v.revision_number}",
         "recorded_at": v.recorded_at} for v in versions if v.note_id == note.note_id]}
        for note in research_repository.list_notes(session, instrument_id)
        if local_unrestricted or note.team_id == actor["team_id"]]
    entries = session.execute(select(ResearchEntry, ResearchTopic).join(ResearchTopic).where(
        ResearchEntry.kind == "evidence", ResearchTopic.portfolio_id.is_(None),
        ResearchTopic.visibility == "team", True if local_unrestricted else ResearchTopic.team_id == actor["team_id"]
    ).order_by(ResearchEntry.created_at.desc()))
    materials = [material_record(entry, instrument_id) for entry, topic in entries
                 if topic.instrument_ids == [instrument_id]]
    materials.extend(_document_materials(session, instrument_id))
    cases, history_limitations = [], []
    atlas_path = DATA_ROOT / "historical_cases" / f"{instrument_id}.json"
    if Path(instrument_id).name == instrument_id and atlas_path.is_file():
        atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
        history_limitations = atlas["reuse_limitations"]
        cases = [{**case, "instrument_id": instrument_id, "atlas_id": atlas["metadata"]["atlas_id"],
                  "reuse_limitations": history_limitations} for case in atlas["cases"]]
    notebook, notebook_history = _notebooks(session, instrument_id, include_history)
    cases.extend(_review_cases(instrument_id, notebook))
    from watchlist_app.services.research_activity import review_agenda
    current_themes = themes_view(session, instrument_id, actor=actor)["themes"]
    return serialize_payload({"instrument_id": instrument_id, "name": instrument.instrument_name,
        "instrument_type": instrument.instrument_type, "research_plan": research_plan, "frameworks": method_library()["frameworks"],
        "available_modules": [{key: item[key] for key in ("id", "title", "version")} for item in method_library()["frameworks"]],
        "mandate": mandate, "materials": materials,
        "prior_sources": retained_public_sources(session, instrument_id),
        "historical_cases": cases, "historical_case_limitations": history_limitations,
        "notebook": notebook, "notebook_history": notebook_history,
        "themes": current_themes, "pm_views": pm_views,
        "current_stance": read_current_stance(session, instrument_id, actor=actor),
        "review_agenda": review_agenda(session, instrument_id, notebook, pm_views, actor=actor, themes=current_themes),
        "pm_views_note": "投资经理原始观点，与研究员判断分开。旧记录作者为空表示归属未确认，不能推断为当前人员。复核使用pm:<note_id>:<revision_number>读取当时版本。"})
