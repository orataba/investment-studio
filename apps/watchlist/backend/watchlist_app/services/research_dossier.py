"""Instrument research materials and methods, using existing research records."""
from copy import deepcopy
from datetime import UTC, datetime
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

DATA_ROOT = Path(__file__).resolve().parents[5] / "data" / "research"
SUPPORTED_TYPES = {"equity", "etf", "index", "crypto", "public_fund", "private_fund"}
TEXT_LIMIT = 60000


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

    @field_validator("title", "background")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入研究底稿标题和背景")
        return value.strip()

    @field_validator("mechanisms", "research_approach", "focus", "source_plan", "gaps")
    @classmethod
    def concise_items(cls, values):
        if any(not value.strip() or len(value) > 2000 for value in values):
            raise ValueError("研究底稿条目不能为空，且每项最多2000字")
        return [value.strip() for value in values]


def _registration(session: Session, instrument: InstrumentDetail) -> dict:
    from investment_studio_instrument_core.db_models import Instrument, InstrumentReferenceSnapshot
    shared = session.get(Instrument, instrument.instrument_id)
    reference = session.get(InstrumentReferenceSnapshot, instrument.instrument_id)
    reference = reference.value_json if reference else {}
    sections = reference.get("sections") or {}
    profile = sections.get("profile") or {}
    fund = sections.get("fund_info") or {}
    manual = session.get(InstrumentManualProfile, instrument.instrument_id)
    return serialize_payload({
        "name": instrument.instrument_name, "instrument_type": instrument.instrument_type,
        "identifier": instrument.primary_identifier_value,
        "currency": shared.currency if shared else profile.get("currency"),
        "exchange": shared.exchange_code if shared else (instrument.metadata_json or {}).get("exchange_code"),
        "industry": profile.get("industry"), "sector": profile.get("sector"),
        "website": profile.get("website"), "benchmark": fund.get("benchmark"),
        "manager": fund.get("management"), "investment_type": fund.get("invest_type"),
        "nav_settings": manual.nav_settings_json if manual else None,
        "disclosed_strategy": manual.strategy_payload_json if manual else None,
        "provider": reference.get("provider"), "reference_fetched_at": reference.get("fetched_at"),
        "manual_updated_at": manual.updated_at if manual else None,
    })


def _initial_mandate(instrument: InstrumentDetail, registration: dict, frameworks: list[dict]) -> ResearchMandateInput:
    name, kind = instrument.instrument_name, instrument.instrument_type
    labels = {"equity": "股票", "etf": "ETF", "index": "指数", "crypto": "加密资产现货", "public_fund": "公募基金", "private_fund": "私募基金"}
    facts = [f"{name}（{instrument.instrument_id}），登记类型为{labels[kind]}"]
    for key, label in (("currency", "币种"), ("exchange", "市场"), ("industry", "行业"),
                       ("benchmark", "登记基准"), ("manager", "登记管理人"), ("investment_type", "登记投资类型")):
        if registration.get(key):
            facts.append(f"{label}：{registration[key]}")
    methods = [line for framework in frameworks if framework["id"] != "shared-evidence-discipline"
               for line in framework["body"].splitlines()]
    approach = [f"先为{name}核对当前登记信息和适用口径，原始材料、工作假设与市场叙事分别记录。",
                "重要数字按主体、单位、币种、预测或报告期、对照期和原文归属整理后，再判断机制与变化。",
                "每次研究先更新本标的已有重点与反证；资料不足明确列出缺口，不用通用背景冒充专属研究。"]
    focus, source_plan, gaps = [], [], ["这是依据登记资料和适用方法形成的初始研究框架，尚不代表已完成专属深度研究。"]
    if kind == "equity":
        industry = registration.get("industry")
        focus.append(f"核实{name}{'在登记行业“' + industry + '”中' if industry else ''}的经营分部、需求、竞争及现金流驱动，并建立同财期比较。")
        focus.append(f"核对{name}当前研究证券的股本单位、上市结构、融资与每股价值；尚未取得的股权换算关系不可猜测。")
        source_plan = [f"{registration.get('website') or name + '公司官方披露入口（地址待核实）'}：定位业绩原文、资本结构公告和正式日程。",
                       "相关交易所/监管原件及公司原始经营披露；机构观点用于检验假设并注明归属。"]
        if not industry:
            gaps.append("尚无已登记行业资料；需先识别真实业务，不能只按公司名推断经营模式。")
    elif kind in {"etf", "public_fund", "index"}:
        benchmark = registration.get("benchmark")
        focus.append(f"围绕{name}的登记基准“{benchmark}”核实实际覆盖和传导，基准不是实时持仓。" if benchmark
                     else f"先取得{name}的编制/投资规则和真实基准，尚不能假定其市场、底层资产或具体敞口。")
        if kind == "etf":
            focus.append(f"依据{name}的正式文件及已登记投资类型“{registration.get('investment_type') or '未取得'}”，区分股票、债券、商品和跨境传导；再选用盈利、久期信用或供需等适用指标。")
        elif kind == "public_fund":
            focus.append(f"跟踪{name}的份额类别、经理与风格变化；将基金相对基准的回报与实际披露期持仓、费用分开。")
            focus.append("先用有分红复投证据的总回报净值建立收益、回撤与修复路径；按真实披露频率和共同观察日比较同策略、同份额费率的产品。合同业绩基准、用户选用的比较指数与同类样本分别标注。")
        else:
            focus.append(f"跟踪{name}的选样、加权和调整机制；区分价格/全收益指数，不能套用基金经理或申赎条款。")
        manager = registration.get("manager")
        source_plan = [f"{manager or name + '官方编制/发行机构（待核实）'}的正式规则、定期披露和调整公告。",
                       "按真实底层资产取得经营/利率信用/商品供需原始资料，保留报告期、币种和公告日期。"]
        if not benchmark:
            gaps.append("登记资料未给出具体基准或编制规则；必须取得正式文件后再确定研究敞口。")
    elif kind == "crypto":
        focus = [f"核实{name}的现货资产与报价币种；使用UTC完整日线和全年交易日历，区分现货、期货、永续合约和ETF。",
                 "研究供给规则、流动性、杠杆清算、链上行为与市场结构；不能把地址视为个人，也不能将减半直接推导成价格上涨。"]
        source_plan = ["协议与开发者原始文档、公开交易场所现货数据；衍生品和链上统计必须注明口径和覆盖。",
                       "跟踪制度和基础设施变化的正式公告；现货没有公司盈利和分红，不套用股票DCF或ETF持仓分析。"]
        gaps.append("尚未建立多交易场所成交量、链上实体调整与杠杆资金基线；价格相关性不能证明资金来源和因果机制。")
    else:
        strategy = registration.get("disclosed_strategy")
        focus = [f"核实{name}已登记策略资料的实际含义及适用环境。" if strategy else f"{name}尚无已登记策略正文；先取得该产品的管理人材料，不能由名称推断持仓、杠杆或对冲职责。",
                 f"核对{name}的净值披露频率、估值与费用、锁定及赎回安排；区分资料缺失与已证实风险。"]
        source_plan = [f"仅使用{name}本人提供或有权访问的基金合同、管理人报告、净值记录和往来材料。",
                       "公开检索只使用公开管理人/策略主题，不向外部查询发送本标的私有材料或账户内容。"]
        if not strategy:
            gaps.append("未取得该产品的策略正文，尚不能将公开市场事件映射为本产品的实际敞口。")
    if kind in {"public_fund", "private_fund"}:
        approach.extend([
            "先核对单位净值、分红再投资总回报净值、币种、费用与披露频率；累计现金分红净值不能替代总回报净值，缺失观察不前填为零收益。",
            "以已取得的真实净值区间建立历史基线：收益与回撤、修复时间、上涨/下跌环境表现；条件允许时检验同类与基准的共同样本。不同频率先对齐，短样本不证明完整周期能力。",
            "滚动相关、贝塔与风格敏感性仅是样本内统计线索，不能还原实际持仓、杠杆、对冲或管理人操作；相对指数的超额也不自动等于选股能力。",
            "管理人材料、费率、申赎和策略变更有证据时持续补充；没有新净值或新资料时可保持原判断，不为了日更重复改写底稿。",
        ])
        if kind == "private_fund":
            focus.append("优先从已登记策略分类和可用净值判断比较对象；未取得合同策略时，将策略名称线索写成待核实假设，不据此配置确定的风险暴露。")
        gaps.append("同类/基准统计只使用实际可访问的序列与共同期间；尚未配置、口径不一致或观察不足时保留缺口，不编造排名、超额或相关性。")
    value = {"title": f"{name} · 专属研究底稿", "background": "已登记资料（并非本轮原文核证）：" + "；".join(facts) + "。\n有原文支持的经营/策略背景及工作假设待研究后分别补充。",
             "mechanisms": methods, "research_approach": approach, "focus": focus,
             "source_plan": source_plan, "gaps": gaps}
    seed = json.loads((DATA_ROOT / "mandate_seeds.json").read_text(encoding="utf-8")).get(instrument.instrument_id)
    if seed:
        value.update({key: val for key, val in seed.items() if key != "background"})
        value["background"] += "\n" + seed["background"]
    return ResearchMandateInput.model_validate(value)


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


def read_mandate(session: Session, instrument_id: str, *, frameworks: list[dict] | None = None) -> dict:
    instrument = require_instrument(session, instrument_id)
    registration = _registration(session, instrument)
    entry = _mandate_entry(session, instrument_id)
    if entry:
        payload = ResearchMandateInput.model_validate(entry.context_json["mandate"])
    else:
        if frameworks is None:
            frameworks = _frameworks(instrument)
        payload = _initial_mandate(instrument, registration, frameworks)
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
    value = payload.model_dump(mode="json")
    user_focus = deepcopy(value["focus"] if origin == "user" else (entry.context_json.get("user_focus", []) if entry else []))
    if origin == "user":
        # The editor's focus field is the user's instruction; the researcher owns
        # the evolving focus in the mandate document.
        value["focus"] = deepcopy(entry.context_json["mandate"].get("focus", []) if entry else read_mandate(session, instrument_id)["focus"])
    versions = []
    revision = 1
    if entry is not None:
        previous = ResearchMandateInput.model_validate(entry.context_json["mandate"]).model_dump(mode="json")
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
        result.append({"source_id": f"material:document:{instrument_id}:{stored_name or index}",
            "entry_id": None, "instrument_id": instrument_id, "source_type": "instrument_material",
            "role": "source_material", "title": row.get("title") or row.get("file_name") or "登记资料",
            "body": body, "source": row.get("download_url") or row.get("source") or "",
            "metadata": {**row, "effective_date": row.get("as_of_date"),
                         "extraction": extraction, "extraction_status": status},
            "recorded_at": row.get("uploaded_at")})
    return result


def _research_records(session: Session, instrument_id: str):
    from sqlalchemy import Boolean, JSON, String, true
    from types import SimpleNamespace
    from studio_identity import current_principal
    from watchlist_app.services.research_access import instrument_run_scope, research_context_projection, research_projection_rows, topic_portfolio_ids_by_topic
    principal = current_principal()
    team_id = principal.team_id
    relation, context = research_context_projection(session, {"instrument_ids": JSON, "sector_run": Boolean,
        "research_run": Boolean, "cutoff": String, "recordkeeping_only": Boolean,
        "citation_correction": JSON, "reviews": JSON})
    review = context["reviews"][instrument_id]
    query = select(ResearchEntry.entry_id, ResearchEntry.team_id, ResearchEntry.topic_id,
        ResearchEntry.status, ResearchEntry.created_at, ResearchEntry.completed_at,
        ResearchTopic.portfolio_id,
        context["sector_run"].label("sector_run"), context["research_run"].label("research_run"),
        context["cutoff"].label("cutoff"), context["recordkeeping_only"].label("recordkeeping_only"),
        context["citation_correction"].label("citation_correction"),
        review["status"].as_string().label("review_status"), review["research"].label("research"),
    ).select_from(ResearchEntry).join(ResearchTopic)
    if relation is not None:
        query = query.join(relation, true())
    records = research_projection_rows(session, query.where(
        ResearchEntry.kind == "analysis", ResearchEntry.status.in_(["completed", "draft"]),
        True if principal.local_unrestricted else ResearchEntry.team_id == team_id,
        True if principal.local_unrestricted else ResearchTopic.team_id == team_id,
        instrument_run_scope(session, instrument_id), review["research"].as_string().is_not(None),
    ).order_by(func.coalesce(ResearchEntry.completed_at, ResearchEntry.created_at).desc()),
        {**{name: (name,) for name in ("sector_run", "research_run", "cutoff", "recordkeeping_only", "citation_correction")},
         "review_status": ("reviews", instrument_id, "status"), "research": ("reviews", instrument_id, "research")})
    topics = {row.topic_id: SimpleNamespace(topic_id=row.topic_id, portfolio_id=row.portfolio_id) for row in records}
    portfolios = topic_portfolio_ids_by_topic(session, topics.values())
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
                          "citation_correction": row.citation_correction})
        yield record, research


def _notebooks(session: Session, instrument_id: str, include_history: bool):
    notebook, history = None, {}
    for record, research in _research_records(session, instrument_id):
        stamp = {"run_id": record.entry_id, "checked_at": record.context_json.get("cutoff"),
                 "version_id": research.get("version_id", record.entry_id),
                 "created_at": research.get("created_at") or record.completed_at or record.created_at,
                 "updated_at": research.get("updated_at") or record.completed_at or record.created_at}
        if notebook is None:
            notebook = {**deepcopy(research), **stamp}
        if not include_history:
            break
        # Preserve the original saved snapshot of each actual revision; later quiet
        # checks still supply the current notebook's checked_at above.
        history[stamp["version_id"]] = {**stamp, "important_changes": research.get("important_changes", []),
                                      "notebook": {**deepcopy(research), **stamp}}
    return notebook, list(history.values())


def read_dossier_version(session: Session, instrument_id: str, version_id: str, *, actor=None) -> dict:
    """Read saved research and its own information set, never reconstruct a past forecast."""
    from watchlist_app.services.research_notebook import notebook_source_ids
    from watchlist_app.services.market_evidence import hydrate_source
    require_instrument(session, instrument_id)
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
    records = list(_research_records(session, instrument_id))
    for record, research in reversed(records):
        candidates = [("notebook", {**research, "version_id": research.get("version_id", record.entry_id)})]
        view = research.get("investment_view")
        if view:
            candidates += [("investment_view", item) for item in [*view.get("versions", []), view]]
        for field in ("forecasts", "forecast_reviews", "lessons"):
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


def _frameworks(instrument: InstrumentDetail) -> list[dict]:
    framework_data = json.loads((DATA_ROOT / "frameworks.json").read_text(encoding="utf-8"))
    return [item for item in framework_data["frameworks"]
            if instrument.instrument_type in item.get("instrument_types", []) and
            (not item.get("tickers") or instrument.instrument_id.upper() in item["tickers"])]


def read_dossier(session: Session, instrument_id: str, include_history: bool = False, *, actor=None) -> dict:
    """Read materials, methods and completed notebooks without creating records."""
    from watchlist_app.services.research_notebook import retained_public_sources
    from watchlist_app.services.research_themes import theme_index
    from watchlist_app.services.research_identity import research_identity
    from watchlist_app.api.routes.research import _serialize_note, research_repository
    actor = actor or research_identity()
    from studio_identity import current_principal
    local_unrestricted = current_principal().local_unrestricted
    instrument = require_instrument(session, instrument_id)
    frameworks = _frameworks(instrument)
    versions = research_repository.list_note_revisions(session, instrument_id)
    from watchlist_app.services.research_views import note_sources
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
    return serialize_payload({"instrument_id": instrument_id, "name": instrument.instrument_name,
        "instrument_type": instrument.instrument_type, "frameworks": frameworks,
        "mandate": read_mandate(session, instrument_id, frameworks=frameworks), "materials": materials,
        "prior_sources": retained_public_sources(session, instrument_id),
        "historical_cases": cases, "historical_case_limitations": history_limitations,
        "notebook": notebook, "notebook_history": notebook_history,
        "themes": theme_index(session, instrument_id, actor=actor), "pm_views": pm_views,
        "review_agenda": review_agenda(session, instrument_id, notebook, pm_views, actor=actor),
        "pm_views_note": "投资经理原始观点，与研究员判断分开。旧记录作者为空表示归属未确认，不能推断为当前人员。复核使用pm:<note_id>:<revision_number>读取当时版本。"})
