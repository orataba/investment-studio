from copy import deepcopy
from datetime import UTC, datetime
import json

import pytest

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_dossier import material_record, read_dossier
from watchlist_app.services.research_notebook import (
    ResearchNotebook, dossier_outline, dossier_source, notebook_source_ids, research_sources, retain_notebook, validate_notebook,
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


@pytest.mark.parametrize("provenance", [
    {"source_kind": "generated_source_summary"}, {"source_kind": "internal_computed_summary"},
    {"extraction_status": "summary"}, {"extraction_status": "computed_summary"},
])
def test_new_summaries_remain_readable_but_old_source_dates_do_not_turn_them_into_originals(provenance):
    record = ResearchEntry(entry_id="summary", topic_id="dossier:xlk", kind="evidence", title="2026年补建历史摘要",
        body="依据2022年原件所作的摘要和本次研究推论。", source="https://issuer.example/2022-original", status="recorded",
        created_at=datetime(2026, 9, 8, tzinfo=UTC), context_json={"published_at": "2022-12-14",
            "original_published_at": "2022-12-14", "extraction_status": "provided", **provenance})
    summary = material_record(record, "xlk")
    assert summary["role"] == "derived_reference"
    packet = dossier(materials=[summary])
    assert dossier_source(packet, summary["source_id"])["body"] == record.body
    assert dossier_outline(packet)["materials"][0]["body_available"]
    real_original = {"source_id": "material:original", "instrument_id": "xlk", "body": "原始披露全文",
                     "metadata": {"published_at": "2022-12-14", "extraction_status": "provided"}}
    metric = {"source_id": "computed:fund", "source_type": "computed_metric", "scope": "instrument",
        "instrument_id": "xlk", "as_of": "2026-09-08T00:00:00+00:00", "methodology": "实际共同观察区间",
        "data": {"status": "available", "return_pct": 5}}
    for cutoff in [CUTOFF, "2026-09-09T00:00:00+00:00"]:
        sources = research_sources(context(dossier(materials=[summary, real_original], notebook={"sources": [
            {**summary, "source_type": "research_material"}]}), cutoff=cutoff, computed_metrics=[metric]), "run")
        assert summary["source_id"] not in sources
        assert "material:original" in sources
        assert ("computed:fund" in sources) == (cutoff > metric["as_of"])
        with pytest.raises(ValueError, match="未取得"):
            validate_notebook(notebook(source_ids=[summary["source_id"]]), "xlk", sources)


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


def forecast(**changes):
    return {"key": "margin-recovery", "claim": "成本下降有望先于需求恢复带动利润率改善", "horizon": "下两次季度披露",
            "assumptions": ["竞争格局未明显恶化"], "invalidation": "成本下降但利润率持续恶化",
            "source_ids": ["old-original"], **changes}


def test_knowledge_delta_keeps_view_dates_and_forecasts_have_immutable_versions():
    sources = {"old-original": original()}
    first = retain_notebook(notebook(investment_view={"direction": "中期改善", "attractiveness": "当前价格吸引力一般",
        "risk": "短期不确定性上升", "source_ids": ["old-original"]}, forecasts=[forecast()]), None, sources, "first", CUTOFF)
    first_copy = deepcopy(first)
    quiet = retain_notebook(ResearchNotebook(), first, sources, "quiet", "2026-09-08T00:00:00+00:00")
    assert quiet["fundamental_view"] == first["fundamental_view"]
    assert quiet["investment_view"] == first["investment_view"]
    assert quiet["forecasts"] == first["forecasts"]
    assert quiet["version_id"] == first["version_id"]
    assert quiet["updated_at"] == first["updated_at"] and quiet["checked_at"] != first["checked_at"]
    revised = retain_notebook(ResearchNotebook(forecasts=[forecast(claim="成本下降尚不足抵消价格竞争，改善或推迟")]),
        quiet, sources, "revised", "2026-09-09T00:00:00+00:00")
    saved = revised["forecasts"][0]
    assert saved["version_id"] != first["forecasts"][0]["version_id"]
    assert saved["created_at"] == first["forecasts"][0]["created_at"]
    assert saved["versions"] == [{key: value for key, value in first["forecasts"][0].items() if key != "versions"}]
    assert first == first_copy
    # An explicit unchanged object also preserves its own clock.
    same = retain_notebook(ResearchNotebook(forecasts=[forecast(claim=saved["claim"])]), revised, sources, "same", CUTOFF)
    assert same["forecasts"] == revised["forecasts"]


def test_forecast_reviews_require_a_previously_saved_version_and_keep_outcome_separate():
    sources = {"old-original": original()}
    first = retain_notebook(ResearchNotebook(forecasts=[forecast()]), None, sources, "first", CUTOFF)
    old = first["forecasts"][0]
    review = {"key": "margin-review", "forecast_key": old["key"], "forecast_version_id": old["version_id"],
              "outcome": "股价上涨，但利润率尚未披露", "mechanism_assessment": "尚不能据此确认成本传导机制",
              "alternative_explanations": ["估值因利率下降而抬升"], "source_ids": ["old-original"]}
    current = ResearchNotebook(forecast_reviews=[review], lessons=[{"key": "separate-driver", "lesson": "价格与经营验证须分开",
        "applicability": "存在独立估值变化时", "limitations": "单一案例", "forecast_key": old["key"],
        "forecast_version_id": old["version_id"], "source_ids": ["old-original"]}])
    validate_notebook(current, "xlk", sources)
    saved = retain_notebook(current, first, sources, "review", CUTOFF)
    assert saved["forecast_reviews"][0]["mechanism_assessment"] == review["mechanism_assessment"]
    assert saved["forecasts"][0] == old
    assert notebook_source_ids(saved) == {"old-original"}
    with pytest.raises(ValueError, match="事后"):
        retain_notebook(ResearchNotebook(forecasts=[forecast()], forecast_reviews=[review]), None, sources, "backfill", CUTOFF)
    with pytest.raises(ValueError, match="事后"):
        retain_notebook(ResearchNotebook(forecast_reviews=[{**review, "forecast_version_id": "invented"}]), first, sources, "bad", CUTOFF)
    with pytest.raises(ValueError, match="原始依据"):
        validate_notebook(ResearchNotebook(investment_view={"direction": "向上", "source_ids": ["unread"]}), "xlk", sources)
    with pytest.raises(ValueError, match="期限"):
        ResearchNotebook(forecasts=[forecast(horizon="")])
    assert ResearchNotebook(forecasts=[forecast(review_on="2026-10-01")]).model_dump(mode="json")["forecasts"][0]["review_on"] == "2026-10-01"


def test_partial_investment_view_changes_risk_independently_and_explicit_null_withdraws():
    sources = {"old-original": original()}
    first = retain_notebook(ResearchNotebook(investment_view={"direction": "中期向上", "horizon": "一年",
        "attractiveness": "估值吸引力有限", "risk": "风险稳定", "source_ids": ["old-original"]}), None, sources, "first", CUTOFF)
    changed = retain_notebook(ResearchNotebook(investment_view={"risk": "波动上升，需要复核"}), first, sources, "risk", CUTOFF)
    view = changed["investment_view"]
    assert view["direction"] == "中期向上" and view["horizon"] == "一年"
    assert view["attractiveness"] == "估值吸引力有限" and view["source_ids"] == ["old-original"]
    assert view["risk"] == "波动上升，需要复核"
    assert view["versions"][0]["risk"] == "风险稳定"
    assert retain_notebook(ResearchNotebook(), changed, sources, "quiet", CUTOFF)["investment_view"] == view
    cleared_dimension = retain_notebook(ResearchNotebook(investment_view={"attractiveness": ""}), changed, sources, "clear-one", CUTOFF)
    assert cleared_dimension["investment_view"]["attractiveness"] == ""
    assert cleared_dimension["investment_view"]["direction"] == "中期向上"
    withdrawn = retain_notebook(ResearchNotebook(investment_view=None), changed, sources, "withdrawn", CUTOFF)
    assert withdrawn["investment_view"] is None and withdrawn["version_id"] == "withdrawn"
    assert first["investment_view"]["direction"] == "中期向上"  # The published prior version is not rewritten.


@pytest.mark.parametrize("field", ["forecasts", "questions", "catalysts", "forecast_reviews", "lessons"])
def test_keyed_partial_updates_preserve_omitted_fields_and_clear_only_explicit_values(field):
    sources = {"old-original": original()}
    forecast_base = forecast(variable="利润率", observation_condition="同口径季度毛利率披露", review_on="2026-10-01", status="confirmed")
    base = retain_notebook(ResearchNotebook(forecasts=[forecast_base]), None, sources, "forecast", CUTOFF)
    forecast_ref = {"forecast_key": forecast_base["key"], "forecast_version_id": base["forecasts"][0]["version_id"]}
    records = {
        "forecasts": forecast_base,
        "questions": question(evidence_for=["成本下行"], evidence_against=["价格竞争"], status="supported"),
        "catalysts": {"key": "earnings", "title": "季度披露", "scheduled_at": "2026-09-01", "relevance": "核实盈利变化",
            "next_check": "核对实际发布数据", "source_ids": ["old-original"], "status": "released",
            "scenarios": ["需求恢复", "仅成本下行"], "outcome": "已披露初步数据"},
        "forecast_reviews": {"key": "margin-review", **forecast_ref, "outcome": "初步改善", "mechanism_assessment": "仍需核对价格因素",
            "alternative_explanations": ["产品组合变化"], "source_ids": ["old-original"]},
        "lessons": {"key": "margin-lesson", "lesson": "区分量价成本", "applicability": "同口径比较", "limitations": "单个观察窗口",
            **forecast_ref, "source_ids": ["old-original"]},
    }
    patches = {
        "forecasts": {"key": forecast_base["key"], "claim": forecast_base["claim"], "horizon": forecast_base["horizon"], "status": "withdrawn"},
        "questions": {"key": "cash", "question": records["questions"]["question"], "assessment": "继续检验竞争影响", "next_check": "下次披露"},
        "catalysts": {key: records["catalysts"][key] for key in ("key", "title", "scheduled_at", "relevance", "next_check", "source_ids")}
            | {"next_check": "取得完整附注"},
        "forecast_reviews": {"key": "margin-review", **forecast_ref, "outcome": "改善幅度低于原预测"},
        "lessons": {"key": "margin-lesson", "lesson": "按同口径拆开量价成本"},
    }
    clears = {
        "forecasts": {"assumptions": [], "invalidation": "", "source_ids": [], "review_on": None},
        "questions": {"evidence_for": [], "evidence_against": [], "source_ids": []},
        "catalysts": {"scenarios": [], "outcome": ""},
        "forecast_reviews": {"mechanism_assessment": "", "alternative_explanations": [], "source_ids": []},
        "lessons": {"applicability": "", "limitations": "", "forecast_key": None, "forecast_version_id": None, "source_ids": []},
    }
    previous = retain_notebook(ResearchNotebook(**{field: [records[field]]}), base, sources, "first", CUTOFF)
    untouched = deepcopy(previous)
    patch = ResearchNotebook(**{field: [patches[field]]})
    changed = retain_notebook(patch, previous, sources, "changed", CUTOFF)
    old, current = previous[field][0], changed[field][0]
    for key in records[field]:
        assert current[key] == (patches[field][key] if key in patches[field] else old[key])
    assert changed["version_id"] != previous["version_id"]
    if field in {"forecasts", "forecast_reviews", "lessons"}:
        assert current["created_at"] == old["created_at"]
        assert current["versions"][-1] == {key: value for key, value in old.items() if key != "versions"}
    quiet = retain_notebook(patch, changed, sources, "quiet", CUTOFF)
    assert quiet[field] == changed[field] and quiet["version_id"] == changed["version_id"]
    cleared = retain_notebook(ResearchNotebook(**{field: [{**patches[field], **clears[field]}]}), changed, sources, "cleared", CUTOFF)
    assert all(cleared[field][0][key] == value for key, value in clears[field].items())
    assert previous == untouched


def test_ai_dossier_outline_indexes_old_versions_and_full_context_keeps_them(client):
    from watchlist_app.api.routes.workbench import run_context
    from watchlist_app.services.research_dossier import read_dossier_version

    def versioned(key, text_field, current):
        return {"key": key, text_field: current, "version_id": f"{key}:current", "source_ids": [],
            "versions": [{"version_id": f"{key}:v{index}", "created_at": "2026-09-01T00:00:00+00:00",
                "updated_at": f"2026-09-0{index + 1}T00:00:00+00:00", "source_run_id": f"run-{index}",
                "author": {"origin": "research"}, text_field: "旧版本正文" + "x" * 3000} for index in range(6)]}

    saved = dossier(prior_sources=[], mandate=versioned("mandate", "background", "当前研究背景"), notebook={
        "version_id": "notebook:current", "fundamental_view": "当前工作判断", "sources": [],
        "investment_view": versioned("view", "direction", "中期改善"),
        "forecasts": [versioned("forecast", "claim", "下一季需求可能改善")],
        "forecast_reviews": [versioned("review", "outcome", "价格变化尚不能确认原因")],
        "lessons": [versioned("lesson", "lesson", "区分结果与机制")],
    })
    untouched = deepcopy(saved)
    assert len(json.dumps(saved, ensure_ascii=False).encode()) > 50_000
    outline = dossier_outline(saved)
    assert len(json.dumps(outline, ensure_ascii=False).encode()) < 50_000
    pairs = [(saved["mandate"], outline["mandate"]), (saved["notebook"]["investment_view"], outline["notebook"]["investment_view"])]
    pairs += [(saved["notebook"][field][0], outline["notebook"][field][0]) for field in ("forecasts", "forecast_reviews", "lessons")]
    for full, short in pairs:
        assert {key: value for key, value in short.items() if key != "versions"} == {key: value for key, value in full.items() if key != "versions"}
        assert short["versions"] == [{key: version[key] for key in ("version_id", "created_at", "updated_at", "source_run_id", "author")}
                                    for version in full["versions"]]
    assert "read_research_dossier" in outline["version_history_note"] and "version_id" in outline["version_history_note"]
    assert saved == untouched
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="xlk", instrument_name="XLK", instrument_type="etf", detail_view_type="etf", metadata_json={}))
        session.add(ResearchTopic(topic_id="instrument-events:xlk", title="已有研究", instrument_ids=["xlk"]))
        session.flush()
        session.add(ResearchEntry(entry_id="outline-run", topic_id="instrument-events:xlk", kind="analysis", title="研究", status="completed",
            context_json={"sector_run": True, "instrument_ids": ["xlk"], "cutoff": CUTOFF, "research_dossiers": [saved],
                "reviews": {"xlk": {"status": "completed", "research": saved["notebook"]}}}))
        session.commit()
        brief = run_context("outline-run", originals=False, session=session)["research_dossiers"][0]
        complete = run_context("outline-run", originals=True, session=session)["research_dossiers"][0]
        assert brief == outline and complete == saved
        version = read_dossier_version(session, "xlk", "forecast:v0")
        assert version["value"] == saved["notebook"]["forecasts"][0]["versions"][0]


def test_computed_market_metrics_are_shared_evidence_but_not_unbounded_or_future_data():
    metric = {"source_id": "computed:rates", "source_type": "computed_metric", "scope": "public_market",
        "instrument_id": "treasury", "as_of": CUTOFF, "methodology": "共同观察日的收益率基点变化",
        "data": {"change_bp": -10}, "source_ids": ["numeric:original-row"]}
    sources = research_sources(context(computed_metrics=[metric, {**metric, "source_id": "computed:future", "as_of": "2026-09-08"}]), "run")
    assert set(sources) == {"computed:rates"}
    paper = ResearchNotebook(forecasts=[forecast(source_ids=["computed:rates"])])
    validate_notebook(paper, "xlk", sources)
    assert notebook_source_ids(paper) == {"computed:rates"}
    saved = retain_notebook(paper, None, sources, "run", CUTOFF)
    assert notebook_source_ids(saved) == {"computed:rates"}  # Upstream numeric rows are not model citation IDs.
    assert saved["sources"] == [metric]
    assert dossier_source(dossier(notebook=saved), "computed:rates") == metric
    with pytest.raises(ValueError, match="其他标的"):
        validate_notebook(paper, "xlk", {"computed:rates": {**metric, "scope": "private"}})


def test_learning_case_is_readable_but_cannot_be_used_as_original_evidence():
    case = {"source_id": "research-review:xlk:lesson:one", "instrument_id": "xlk", "source_type": "research_review",
            "case_id": "one", "case_title": "价格吻合不能证实因果", "sources": [{"url": "https://issuer.example"}]}
    packet = dossier(historical_cases=[case])
    assert dossier_source(packet, case["source_id"]) == case
    assert research_sources(context(packet), "run") == {}
