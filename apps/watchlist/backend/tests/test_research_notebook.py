from copy import deepcopy
from datetime import UTC, datetime

import pytest

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_dossier import material_record, read_dossier
from watchlist_app.services.research_notebook import (
    ResearchNotebook, dossier_outline, dossier_source, research_sources, retain_notebook, validate_notebook,
)

CUTOFF = "2026-09-07T00:00:00+00:00"


def question(key="cash", **changes):
    return {"key": key, "question": "现金流是否支持投入？", "assessment": "待后续披露核实",
            "evidence_for": [], "evidence_against": [], "next_check": "下一份现金流披露", "status": "open",
            "source_ids": ["old-original"], **changes}


def notebook(**changes):
    return ResearchNotebook(fundamental_view="有依据的当前判断", valuation_view="估值证据不足", **changes)


def original(**changes):
    return {"source_id": "old-original", "source_type": "public_source", "source_run_id": "old-run",
            "url": "https://issuer.example/report", "text": "已取得的原始公告正文",
            "published_at": "2026-06-01", "retrieved_at": "2026-08-01T12:00:00+00:00", **changes}


def dossier(**changes):
    return {"instrument_id": "xlk", "materials": [], "historical_cases": [], "frameworks": [], "notebook": None, **changes}


def context(asset=None, **changes):
    return {"cutoff": CUTOFF, "research_dossiers": [asset or dossier()], **changes}


def test_unmentioned_questions_and_old_original_clocks_survive_but_explicit_refutation_replaces():
    previous = {"questions": [question(), question("demand", status="supported")], "sources": [original()]}
    untouched = deepcopy(previous)
    sources = research_sources(context(dossier(notebook=previous)), "new-run")
    current = notebook(questions=[question("demand", status="refuted", assessment="新证据否定原假设")])
    validate_notebook(current, "xlk", sources)
    retained = retain_notebook(current, previous, sources, "new-run", CUTOFF)
    by_key = {item["key"]: item for item in retained["questions"]}
    assert by_key["cash"] == previous["questions"][0]
    assert by_key["demand"]["status"] == "refuted"
    assert by_key["demand"]["assessment"] == "新证据否定原假设"
    assert retained["sources"] == [original()]
    assert retained["sources"][0]["source_run_id"] == "old-run"
    assert retained["sources"][0]["retrieved_at"] == "2026-08-01T12:00:00+00:00"
    assert previous == untouched and retained["checked_at"] == CUTOFF


def test_only_acquired_originals_can_be_referenced_and_private_sources_require_exact_instrument():
    sources = {
        "method": {"source_id": "method", "source_type": "research_method", "text": "分析框架"},
        "summary": {"source_id": "summary", "source_type": "ai_summary", "text": "模型以前的判断"},
        "foreign": {"source_id": "foreign", "source_type": "company_snapshot", "instrument_id": "xlf", "company": {"revenue": 100}},
        "unscoped": {"source_id": "unscoped", "source_type": "research_material", "body": "其他资料"},
    }
    for sid in [*sources, "not-fetched"]:
        with pytest.raises(ValueError):
            validate_notebook(notebook(source_ids=[sid]), "xlk", sources)
    old = dossier(notebook={"questions": [], "sources": list(sources.values())}, frameworks=[sources["method"]])
    assert research_sources(context(old), "new-run") == {}
    with pytest.raises(ValueError, match="不属于"):
        dossier_source(old, "foreign")
    assert dossier_source(dossier(notebook={"sources": [original()]}), "old-original") == original()


def test_future_publications_are_excluded_without_confusing_fiscal_periods_or_retrieval_times():
    material = {"source_id": "material:known", "instrument_id": "xlk", "body": "实际公告",
                "metadata": {"published_at": "2026-09-06", "effective_date": "2027-12-31"},
                "recorded_at": "2026-09-06T10:00:00+00:00"}
    future_material = {**material, "source_id": "material:future", "metadata": {"published_at": "2026-09-08"}}
    future_old = original(source_id="old-future", published_at="2026-09-08T08:00:00+08:00")
    sources = research_sources(context(dossier(materials=[material, future_material], notebook={"sources": [future_old]}),
        web_evidence=[{"operation": "fetch", "sources": [
            original(source_id="web-future", published_at="2026-09-08", time_status="verified"),
            original(source_id="automated-digest", text="Research digest\nCompiled by Adalytica Engine\nAI汇编内容", published_at="2026-09-06"),
            original(source_id="web-current", published_at="2026-09-06", retrieved_at="2026-09-07T00:02:00+00:00"),
            original(source_id="web-unknown", published_at=None),
        ]}]), "new-run")
    assert set(sources) == {"material:known", "web-current", "web-unknown"}
    assert sources["material:known"]["metadata"]["effective_date"] == "2027-12-31"
    assert sources["material:known"]["recorded_at"] == material["recorded_at"]
    assert sources["web-current"]["retrieved_at"] == "2026-09-07T00:02:00+00:00"
    with pytest.raises(ValueError, match="未取得"):
        validate_notebook(notebook(source_ids=["material:future"]), "xlk", sources)
    # A filtered prior source must not re-enter through retain_notebook's history merge.
    retained = retain_notebook(notebook(), {"questions": [question(source_ids=["old-future"])], "sources": [future_old]}, sources, "new-run", CUTOFF)
    assert retained["sources"] == []


def test_snapshot_sources_exclude_ai_context_and_do_not_label_run_cutoff_as_collection_time():
    asset = {"instrument_id": "xlk", "name": "XLK", "summary": {"source_cutoff_at": "2026-09-04"},
             "research": "PM观点", "research_tracking": "AI总结", "analyst_focus": "框架",
             "materials": [{"title": "只有目录"}], "risk_cases": [{"title": "旧判断"}]}
    sources = research_sources(context(instrument_inputs=[asset], sector_inputs=[{
        "instrument_id": "xlk", "holdings_as_of": "2026-09-04", "research_focus": ["行业框架"]}]), "new-run")
    observed = sources["instrument:new-run:xlk"]
    assert observed["run_cutoff"] == CUTOFF and "retrieved_at" not in observed
    assert observed["snapshot"] == {"instrument_id": "xlk", "name": "XLK", "summary": {"source_cutoff_at": "2026-09-04"}}
    assert "research_focus" not in sources["sector:new-run:xlk"]["snapshot"]
    validate_notebook(notebook(source_ids=list(sources)), "xlk", sources)


def test_material_without_body_including_scanned_pdf_page_labels_cannot_be_read_evidence():
    entry = ResearchEntry(entry_id="scan", topic_id="dossier:xlk", kind="evidence", title="扫描件",
        body="[第 1 页]\n\n[第 2 页]\n", source="/api/research/entries/scan/file",
        context_json={"file_name": "scan.pdf"}, created_at=datetime.now(UTC))
    empty = material_record(entry, "xlk")
    assert empty["body"] == "" and empty["metadata"]["extraction_status"] == "empty"
    available = dossier(materials=[empty, {"source_id": "material:directory", "instrument_id": "xlk", "title": "目录", "body": ""}])
    assert all(not item["body_available"] for item in dossier_outline(available)["materials"])
    assert research_sources(context(available), "new-run") == {}
    assert entry.body == "[第 1 页]\n\n[第 2 页]\n"  # Read-only projection does not rewrite original storage.


def test_history_is_retained_as_conditional_reference_without_new_event_or_publication_clock():
    history = {"source_id": "historical:case", "instrument_id": "xlk", "role": "historical_research",
        "case_id": "case", "case_title": "旧冲击", "sources": [{"url": "https://issuer.example/old"}],
        "event": {"information_window": {"start": "2020-03-01", "end": "2020-03-16"}},
        "current_use": {"lesson": "只有机制和背景相似时类比"}, "reuse_limitations": ["不是当期事实"]}
    sources = research_sources(context(dossier(historical_cases=[history])), "new-run")
    source = sources["historical:case"]
    assert source["source_type"] == "historical_case" and source["role"] == "historical_research"
    assert source["event"] == history["event"]
    assert not any(field in source for field in ("text", "occurred_at", "published_at", "retrieved_at"))
    retained = retain_notebook(notebook(source_ids=["historical:case"]), None, sources, "new-run", CUTOFF)
    assert "events" not in retained and retained["sources"][0]["reuse_limitations"] == ["不是当期事实"]


def test_scheduled_catalyst_retains_original_time_and_remains_pending_until_release_evidence():
    catalyst = {"key": "cpi-august", "title": "美国8月CPI", "scheduled_at": "2026-09-11T08:30:00-04:00",
                "status": "scheduled", "relevance": "观察通胀对实际利率与黄金的影响。", "scenarios": ["高于预期时核实实际利率与美元反应。"],
                "next_check": "取得正式首发数据和发布前共识。", "outcome": "", "source_ids": ["old-original"]}
    sources = {"old-original": original()}
    paper = notebook(catalysts=[catalyst])
    validate_notebook(paper, "xlk", sources)
    previous = retain_notebook(paper, None, sources, "first-run", CUTOFF)
    quiet = retain_notebook(notebook(), previous, sources, "later-run", "2026-09-12T00:00:00+00:00")
    assert quiet["catalysts"] == [catalyst]  # Passing the date alone cannot manufacture an actual release.
    released = {**catalyst, "status": "released", "outcome": "已取得首发值；此前共识仍待核实。"}
    current = retain_notebook(notebook(catalysts=[released]), quiet, sources, "release-run", "2026-09-12T01:00:00+00:00")
    assert current["catalysts"] == [released] and current["sources"] == [original()]
    with pytest.raises(ValueError, match="时区"):
        notebook(catalysts=[{**catalyst, "scheduled_at": "2026-09-11T08:30:00"}])
    with pytest.raises(ValueError, match="日程"):
        validate_notebook(notebook(catalysts=[{**catalyst, "source_ids": ["history"]}]), "xlk",
                          {"history": {"source_type": "historical_case", "sources": ["old report"], "instrument_id": "xlk"}})


def test_notebook_from_other_instrument_topic_is_not_relabelled_as_current_dossier(client):
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="notebook-current", instrument_name="当前标的", instrument_type="equity", detail_view_type="equity"))
        session.add(ResearchTopic(topic_id="instrument-events:another", title="另一个标的", instrument_ids=["another"]))
        session.flush()
        session.add(ResearchEntry(entry_id="wrong-topic", topic_id="instrument-events:another", kind="analysis", title="旧研究", status="completed",
            context_json={"sector_run": True, "instrument_ids": ["notebook-current"], "reviews": {"notebook-current": {
                "status": "completed", "research": {"fundamental_view": "不应混入"}}}}))
        session.commit()
        assert read_dossier(session, "notebook-current")["notebook"] is None


def test_failed_report_keeps_readable_originals_separate_from_its_rejected_conclusions(client):
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="archive", instrument_name="归档标的", instrument_type="equity", detail_view_type="equity"))
        session.add(ResearchTopic(topic_id="instrument-events:archive", title="研究", instrument_ids=["archive"]))
        session.flush()
        source = original()
        session.add(ResearchEntry(entry_id="rejected", topic_id="instrument-events:archive", kind="analysis", title="失败研究", status="failed",
            body="错误结论", context_json={"sector_run": True, "instrument_ids": ["archive"], "web_evidence": [
                {"operation": "search", "sources": [{"source_id": "snippet", "text": "搜索摘要"}]},
                {"operation": "fetch", "sources": [source, original(source_id="garbled", url="https://bad.example", text="无法还原\ufffd正文")]}],
                "submitted_draft": {"reviews": [{"instrument_id": "archive", "summary": "错误结论"}]}}))
        session.commit()
        current = read_dossier(session, "archive")
        assert current["notebook"] is None
        assert [s["source_id"] for s in current["prior_sources"]] == [source["source_id"]]
        assert current["prior_sources"][0]["published_at"] == source["published_at"]
        assert current["prior_sources"][0]["source_run_id"] == "rejected"
        assert "错误结论" not in str(current)
        outline = dossier_outline(current)
        assert "text" not in outline["prior_sources"][0]
        assert dossier_source(current, source["source_id"])["text"] == source["text"]
        evidence = research_sources(context(current), "today")
        assert set(evidence) == {source["source_id"]}


def test_fact_sheet_preserves_comparison_basis_and_requires_instrument_evidence():
    fact = {"subject": "公司", "metric": "自由现金流", "value": "-200", "unit": "CNY million", "period": "2026Q2",
            "comparison": "2025Q2: -100 CNY million; both are outflows", "uncertainty": "标题声称转负与正文冲突",
            "source_ids": ["old-original"]}
    current = notebook(facts=[fact])
    evidence = {"old-original": original()}
    validate_notebook(current, "xlk", evidence)
    saved = retain_notebook(current, None, evidence, "today", CUTOFF)
    assert saved["facts"] == [fact] and saved["sources"] == [original()]
    with pytest.raises(ValueError, match="原始依据"):
        validate_notebook(current, "xlk", {})
