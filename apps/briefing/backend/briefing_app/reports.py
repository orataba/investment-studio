from datetime import datetime, UTC
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy import func, select, text

from briefing_app.contracts import MacroRelease, ReportDraft
from briefing_app.db import Report
from briefing_app.evidence import report_window, read_bound_source


def begin_report(session, report_type: str, cutoff: datetime, timezone: str, *, edition_role: str, principal=None) -> tuple[Report, bool]:
    if cutoff > datetime.now(UTC):
        raise ValueError("不能生成尚未到达截止时间的报告")
    window = report_window(report_type, cutoff, timezone)
    # All revisions of one period share this lock; version numbering and duplicate
    # clicks must be atomic across web and scheduled workers.
    if session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"briefing:{principal.team_id if principal else 'default'}:{report_type}:{window['report_date']}"})
    scope = (Report.report_type == report_type, Report.report_date == window["report_date"], Report.team_id == (principal.team_id if principal else "default"))
    active = session.scalar(select(Report).where(*scope, Report.status.in_(["queued", "running"])))
    if active:
        return active, False
    version = (session.scalar(select(func.max(Report.version)).where(*scope)) or 0) + 1
    report = Report(report_type=report_type, report_date=window["report_date"], version=version,
                    cutoff=cutoff, status="queued", team_id=principal.team_id if principal else "default",
                    created_by_user_id=principal.user_id if principal else None,
                    created_by_service_id=principal.service_id if principal else None, input_json={**window, "edition_role": edition_role})
    session.add(report)
    session.commit()
    return report, True


def report_items(draft: ReportDraft):
    for section in draft.sections:
        if section.kind == "macro_data_calendar":
            yield from section.rows
        elif section.kind == "opportunity_leads":
            yield from section.items
        else:
            for group in section.groups:
                yield from group.items


def _number(value):
    try:
        parsed = Decimal(str(value).replace(",", ""))
        return parsed if parsed.is_finite() else None
    except InvalidOperation:
        return None


def validate_draft(draft: ReportDraft, snapshot: dict) -> dict:
    if draft.report_type != snapshot["report_type"]:
        raise ValueError("报告类型与当前资料范围不一致")
    kinds = [section.kind for section in draft.sections]
    expected = ["takeaway_section"] if draft.report_type == "daily" else ["topic_recommendations", "opportunity_leads"]
    if kinds not in (expected, [*expected, "macro_data_calendar"]):
        raise ValueError("日报使用重点信息；周报依次使用本周话题推荐与新机会线索；可在末尾附本期重要宏观发布")
    if draft.report_type == "daily" and [g.title for g in draft.sections[0].groups] != ["宏观", "微观"]:
        raise ValueError("日报重点信息依次包含宏观与微观，信息不足的分组可为空")
    sources = {source["source_id"]: source for source in snapshot["sources"]}
    symbols = {row["symbol"] for row in snapshot["market_rows"]}
    titles = set()
    originals = {}
    errors = []
    for item in report_items(draft):
        title_key = (isinstance(item, MacroRelease), item.title)
        if title_key in titles:
            raise ValueError("同一标题不能重复进入报告")
        titles.add(title_key)
        try:
            _validate_item(item, snapshot, sources, symbols, originals)
            if isinstance(item, MacroRelease):
                _validate_macro_release(item, snapshot, sources)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("\n".join(errors))
    return draft.model_dump(mode="json")


def _validate_item(item, snapshot, sources, symbols, originals):
    if not set(item.source_ids).issubset(sources):
        raise ValueError(f"{item.title} 引用了本轮未保留的证据")
    for source_id in item.source_ids:
        if sources[source_id]["source_type"] == "public_document" and source_id not in originals:
            originals[source_id] = read_bound_source(snapshot, source_id)
    if not set(item.related_market_symbols).issubset(symbols):
        raise ValueError(f"{item.title} 关联了没有本轮行情的资产")
    for citation in item.number_citations:
        if _number(citation.value) is None:
            raise ValueError(f"{item.title}：数字引用 value 必须是纯数字，不含百分号、货币或单位；请修正 {citation.value!r}，单位保留在正文和 quote 中")
        if citation.source_id not in item.source_ids:
            raise ValueError(f"{item.title}：数字引用必须属于本条信息的来源")
        source = sources[citation.source_id]
        if source["source_type"] == "public_document":
            source = originals[citation.source_id]
            if not citation.quote or not any(citation.quote in source.get(field, "") for field in ("title", "content_text")) or citation.value not in citation.quote:
                raise ValueError(f"{item.title}：数值 {citation.value!r} 的文本引用必须完整位于 {citation.source_id} 的原始标题或正文之一，且是包含该数值的原文逐字引用；请重新读取该来源核对 quote")
        else:
            if not citation.field or citation.field not in source:
                raise ValueError(f"{item.title}：数值引用必须指定已保留数据行的字段")
            actual = source[citation.field]
            if _number(actual) is None or _number(citation.value) != _number(actual):
                raise ValueError(f"{item.title}：数字与本轮原始或程序计算结果不一致，不得自行改写或补零")
    # A concrete numeric claim cannot bypass its evidence citation by remaining
    # in prose. Dates and identifiers should be written naturally or cited too.
    prose = " ".join(str(value) for key, value in item.model_dump().items()
                     if key not in {"source_ids", "number_citations", "related_market_symbols"})
    claimed = {_number(token) for token in re.findall(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?(?=%|％|bp|BP|亿元|万元|亿美元|万亿美元|美元|港元|欧元|日元|人民币|万桶|桶|万人|人|万亿|十亿|亿|万|million|billion|trillion|倍|个百分点)", prose)}
    if isinstance(item, MacroRelease):
        # Release tables can contain bare levels such as PMI 51.2. The prose
        # unit matcher above must not let those values bypass source checking.
        for value in (item.actual, item.expected, item.previous):
            claimed.update(_number(token) for token in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", value or ""))
    cited = {_number(citation.value) for citation in item.number_citations}
    missing = claimed - cited
    if missing:
        values = "、".join(str(value) for value in sorted(missing))
        raise ValueError(f"{item.title}：正文数字 {values} 缺少数字引用。value 使用不含单位的原始数字；不能自行换算数量级后引用另一数值")


def _validate_macro_release(item: MacroRelease, snapshot: dict, sources: dict):
    start = datetime.fromisoformat(snapshot["period_start"])
    end = datetime.fromisoformat(snapshot["period_end"])
    if not start.date() <= item.date <= end.date():
        raise ValueError(f"{item.title}：宏观发布必须已在本期发生，不能列入历史发布或未来日程")
    if not any(sources[source_id]["source_type"] == "public_document"
               and sources[source_id].get("window_scope", "current") == "current"
               for source_id in item.source_ids):
        raise ValueError(f"{item.title}：宏观发布须有本期原文依据；存量行情或本机补录的历史材料不能充当新发布")


def report_summary(report: Report) -> dict:
    title = "投研日报" if report.report_type == "daily" else "投研周报"
    return {"report_id": report.report_id, "report_type": report.report_type, "report_date": report.report_date,
            "title": f"{title} | {report.report_date}", "version": report.version, "status": report.status,
            "cutoff": report.cutoff.isoformat(), "created_at": report.created_at.isoformat(),
            "completed_at": report.completed_at.isoformat() if report.completed_at else None,
            "error": report.error, "source_count": report.input_json.get("source_count", 0),
            "edition_role": report.input_json.get("edition_role", "preview")}


def report_detail(report: Report) -> dict:
    snapshot = report.input_json
    return {**report_summary(report), "window": {key: snapshot.get(key) for key in
            ("period_start", "period_end", "timezone", "period_label")},
            "market_rows": snapshot.get("market_rows", []), "macro_rows": snapshot.get("macro_rows", []),
            "coverage": {"text": snapshot.get("text_coverage", {}), "numeric": snapshot.get("numeric_coverage", []), "documents": snapshot.get("document_counts", {})},
            "sources": [{key: source.get(key) for key in ("source_id", "source_type", "title", "source_name", "url",
                        "published_at", "occurred_at", "observed_at", "received_at", "content_completeness", "symbol", "date")}
                        for source in snapshot.get("sources", [])],
            "report": report.result_json}
