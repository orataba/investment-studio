from copy import deepcopy
from datetime import UTC, datetime
import json

import pytest
from sqlalchemy import select

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_fact_review, sector_research
from .test_research_triggers import store


def searched_context():
    return {"cutoff": "2026-09-12T15:00:00+00:00", "instrument_ids": ["h11001"],
        "market_coverage": {"latest_bundle_window_end": "2026-09-08T23:50:00+00:00",
            "latest_received_at": "2026-09-09T13:09:00+00:00"},
        "market_queries": [{"instrument_id": "h11001", "query": "中国债券政策变化", "total": total,
            "published_after": "2026-09-05T00:00:00+00:00", "cutoff": cutoff} for total, cutoff in [
                (0, "2026-09-12T14:30:00+00:00"), (6, "2026-09-12T14:34:00+00:00"),
                (0, "2026-09-12T22:34:00+08:00")]],
        "instrument_inputs": [{"instrument_id": "h11001", "name": "中债指数", "instrument_type": "index",
            "as_of_date": "2026-09-08"}],
        "tool_evidence": [{"tool": "market", "source_id": "market:read", "result": {
            "as_of": "2026-09-11", "instruments": ["xlk", "xlc"], "limitations": ["仅覆盖已配置美股行业ETF"]}}]}


def test_known_channel_gap_uses_actual_query_clock_and_is_shared_with_reviewer():
    context = searched_context()
    before = deepcopy(context)
    gaps = sector_research.shared_market_coverage_gaps(context, instrument_id="h11001")
    assert len(gaps) == 1
    assert "2026-09-08T23:50:00+00:00" in gaps[0]
    assert "2026-09-09T13:09:00+00:00" in gaps[0]
    assert "2026-09-12T14:34:00+00:00" in gaps[0]
    assert context["cutoff"] not in gaps[0]
    assert "该渠道期间覆盖尚未确认" in gaps[0]
    packet = sector_fact_review._evidence_packet(context, [{"instrument_id": "h11001", "events": [],
        "reflection": {"status": "reviewed", "summary": "已复核待验证事项", "reviewed_update_ids": []}}], "coverage-run")
    assert packet["acquisition"]["market_channel_gaps"] == {"h11001": gaps}
    assert packet["acquisition"]["market_queries"] == context["market_queries"]
    assert packet["tool_evidence"] == context["tool_evidence"]
    assert context == before


@pytest.mark.parametrize("coverage_case", ["unsearched", "unknown_window", "unknown_query_clock", "covered",
    "other_instrument", "ordinary_nav_lag"])
def test_channel_gap_does_not_invent_missing_windows_or_treat_empty_results_as_failure(coverage_case):
    context = searched_context()
    if coverage_case == "unsearched":
        context["market_queries"] = []
    elif coverage_case == "unknown_window":
        context["market_coverage"].pop("latest_bundle_window_end")
    elif coverage_case == "unknown_query_clock":
        for query in context["market_queries"]:
            query.pop("cutoff")
    elif coverage_case == "other_instrument":
        for query in context["market_queries"]:
            query["instrument_id"] = "xlk"
    else:
        context["market_coverage"]["latest_bundle_window_end"] = "2026-09-12T14:34:00+00:00"
        for query in context["market_queries"]:
            query["total"] = 0
        if coverage_case == "ordinary_nav_lag":
            context["instrument_inputs"][0].update(instrument_type="public_fund", as_of_date="2026-09-08")
    assert sector_research.shared_market_coverage_gaps(context, instrument_id="h11001") == []


@pytest.mark.parametrize("coverage_case", ["known_gap", "unknown_window", "unsearched", "monitor_scanned"])
def test_publication_preserves_actual_channel_limit_without_failing_quiet_reflection(client, coverage_case, store, monkeypatch):
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="h11001", instrument_type="index", detail_view_type="index",
            instrument_name="中债指数", metadata_json={}))
        session.commit()
        run, _ = sector_research.begin_run(session, ["h11001"])
        context = {**run.context_json, **searched_context()}
        if coverage_case == "unknown_window":
            context["market_coverage"].pop("latest_bundle_window_end")
        elif coverage_case in {"unsearched", "monitor_scanned"}:
            context["market_queries"] = []
        if coverage_case == "monitor_scanned":
            from watchlist_app.services import market_evidence, research_monitor
            monkeypatch.setattr(market_evidence, "text_store", lambda: store)
            original = store.capture_public_source({"url": "https://issuer.example/bond",
                "title": "Bond disclosure", "text": "Bond purchase disclosure", "published_at": "2026-09-11",
                "retrieved_at": "2026-09-11T12:00:00+00:00"}, received_at=datetime(2026, 9, 11, 12, tzinfo=UTC))
            state, trigger = research_monitor.scan(session, "h11001", {
                "cutoff": "2026-09-10T12:00:00+00:00", "instrument_ids": ["h11001"],
                "market_queries": [{"instrument_id": "h11001", "query": "Bond", "entities": [],
                    "cutoff": "2026-09-10T12:00:00+00:00"}]},
                team_id=run.team_id, now=datetime.fromisoformat(context["cutoff"]))
            assert trigger["reasons"][0]["sources"][0]["source_id"] == original["source_id"]
            assert trigger["reasons"][0]["sources"][0]["matched_query"]["query"] == "Bond"
            research_monitor.mark_attempt(state, run.entry_id)
            context["incremental_trigger"] = {"h11001": trigger}
            document = store.read(original["source_id"], as_of=datetime.fromisoformat(context["cutoff"]))
            context["market_text_sources"] = [market_evidence.source_reference(market_evidence.original_source(document))]
            frozen = deepcopy(context)
        run.context_json = context
        gaps = sector_research.shared_market_coverage_gaps(context, instrument_id="h11001")
        # A reviewer may return no acquisition gap; publication retains known
        # application limits. Repeated prose from the reviewer is deduplicated.
        for supplied_coverage in ([], gaps):
            sector_research.apply_result(session, run, json.dumps({"reviews": [{"instrument_id": "h11001",
                "events": [], "coverage": supplied_coverage, "reflection": {"status": "reviewed",
                    "summary": "已复核既有待验证事项，尚无新增判断", "reviewed_update_ids": []}}]}))
            saved = run.context_json["reviews"]["h11001"]
            assert run.status == "completed" and saved["change_kind"] == "none" and saved["summary"] == ""
            assert saved["reflection"]["status"] == "reviewed" and saved["research"] is None
            assert session.scalar(select(RiskCase).where(RiskCase.instrument_id == "h11001")) is None
            if coverage_case == "known_gap":
                assert saved["coverage"] == gaps and saved["status"] == "limited"
            elif coverage_case in {"unsearched", "monitor_scanned"}:
                assert saved["status"] == "limited" and any("未追加检索" in gap for gap in saved["coverage"])
                if coverage_case == "monitor_scanned":
                    assert not any(gap.startswith("本轮未检索") for gap in saved["coverage"])
                    for field in ("cutoff", "market_queries", "incremental_trigger", "market_text_sources"):
                        assert run.context_json[field] == frozen[field]
                    assert state.pending_changes_json == {}
                    assert state.applied_at == datetime.fromisoformat(trigger["monitor_snapshot"]["scanned_at"])
            else:
                assert saved["status"] == "completed" and saved["coverage"] == []
