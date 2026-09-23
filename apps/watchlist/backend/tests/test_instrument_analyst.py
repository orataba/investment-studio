from datetime import UTC, datetime

import pytest


def register_fund(client):
    watchlist = client.post("/api/watchlists", json={"name": "分析师测试"}).json()
    response = client.post(f"/api/watchlists/{watchlist['watchlist_id']}/items", json={"instrument_ids": ["sxv264"]})
    assert response.status_code == 200, response.text


def start_analysis(client, monkeypatch, instrument_id="sxv264", page_context=None):
    import watchlist_app.services.research_runner as runner
    monkeypatch.setattr(runner, "harness_available", lambda: True)
    monkeypatch.setattr(runner, "run_analysis", lambda run_id, token=None, issuer=None: None)
    topic_response = client.post("/api/research/topics", json={"title": "标的分析", "instrument_ids": [instrument_id]})
    assert topic_response.status_code == 201, topic_response.text
    topic = topic_response.json()
    response = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json={"question": "分析已有资料，哪些还需要核实？", "page_context": page_context})
    assert response.status_code == 202, response.text
    return response.json()


def test_assistant_retains_originating_page_and_reads_prepared_research(client, monkeypatch):
    register_fund(client)
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from watchlist_app.db.session import get_session_factory

    with get_session_factory()() as session:
        topic_id = "instrument-events:sxv264"
        session.add(ResearchTopic(topic_id=topic_id, title="研究追踪", instrument_ids=["sxv264"]))
        session.flush()
        session.add(ResearchEntry(entry_id="prepared-research", topic_id=topic_id, kind="analysis", title="研究追踪", body="已有结论", status="completed",
                                 context_json={"sector_run": True, "instrument_ids": ["sxv264"], "reviews": {"sxv264": {"status": "limited", "summary": "已有结论",
                                     "research": {"investment_view": {"direction": "已有结论"}}, "coverage": ["未披露底层敞口"]}}}))
        session.commit()
    page = {"surface": "instrument", "instrument_id": "sxv264", "tab": "events", "currency": "CNY", "start": "2026-08-01", "end": "2026-09-06"}
    run = start_analysis(client, monkeypatch, page_context=page)
    assert run["context_json"]["page_context"] == page
    evidence = client.post(f"/api/research/runs/{run['entry_id']}/tools", json={"tool": "instruments", "instrument_ids": ["sxv264"]}).json()["result"]["assets"][0]
    assert evidence["research_tracking"]["run_id"] == "prepared-research"
    assert evidence["research_tracking"]["summary"] == "已有结论"
    assert "不是本轮最新核实" in evidence["research_tracking_note"]


def test_assistant_rejects_page_scope_that_disagrees_with_linked_topic(client, monkeypatch):
    import watchlist_app.services.research_runner as runner
    from watchlist_app.services import research_workbench
    monkeypatch.setattr(runner, "harness_available", lambda: True)
    monkeypatch.setattr(runner, "run_analysis", lambda run_id, token=None, issuer=None: None)
    monkeypatch.setattr(research_workbench, "external_json", lambda service, path:
                        {"research_enabled": True} if path == "/capabilities" else [{"portfolio_id": "linked"}])
    topic = client.post("/api/research/topics", json={"title": "组合对话", "portfolio_id": "linked"}).json()
    response = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json={"question": "当前风险是什么？",
        "page_context": {"surface": "portfolio", "portfolio_id": "another"}})
    assert response.status_code == 422 and "组合不一致" in response.json()["detail"]
    response = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json={"question": "当前风险是什么？",
        "page_context": {"surface": "portfolio", "portfolio_id": "linked"}})
    assert response.status_code == 202
    assert response.json()["context_json"]["portfolio_id"] == "linked"


def test_private_fund_analyst_reads_product_evidence_with_source_clocks(client, monkeypatch):
    register_fund(client)
    from watchlist_app.db.models import InstrumentManualProfile, InstrumentExposureHoldingsReadModel
    from watchlist_app.db.session import get_session_factory

    clock = datetime(2026, 8, 31, tzinfo=UTC)
    with get_session_factory()() as session:
        manual = session.get(InstrumentManualProfile, "sxv264")
        if manual is None:
            manual = InstrumentManualProfile(instrument_id="sxv264", updated_at=clock)
            session.add(manual)
        manual.price_payload_json = {"redemption": "每季度开放一次", "report_period": "2026-06-30"}
        manual.strategy_payload_json = {"strategy": "管理人描述，尚无底层敞口"}
        holding = session.get(InstrumentExposureHoldingsReadModel, "sxv264")
        if holding is None:
            holding = InstrumentExposureHoldingsReadModel(instrument_id="sxv264")
            session.add(holding)
        holding.payload_json = {"as_of_date": "2026-06-30", "holdings": []}
        holding.data_freshness_status = "stale"
        holding.source_cutoff_at = clock
        session.commit()
    run = start_analysis(client, monkeypatch)
    context = run["context_json"]
    assert context["analyst_focus"][0]["instrument_type"] == "private_fund"
    assert "未知持仓" in context["analyst_focus"][0]["guidance"]
    assert datetime.fromisoformat(context["requested_at"]).tzinfo is not None
    response = client.post(f"/api/research/runs/{run['entry_id']}/tools", json={"tool": "instruments", "instrument_ids": ["sxv264"]})
    assert response.status_code == 200, response.text
    evidence = response.json()["result"]["assets"][0]
    assert evidence["product_information"]["terms_and_fees"]["redemption"] == "每季度开放一次"
    assert evidence["holdings"]["data"]["as_of_date"] == "2026-06-30"
    assert evidence["holdings"]["freshness"] == "stale"
    plan = evidence["research_plan"]
    assert plan["scope"] in context["analyst_focus"][0]["guidance"]
    methods = {item["id"]: item for item in plan["modules"]}
    assert "未披露" in methods["fund-strategy"]["body"]
    assert "前填" in methods["market-quantitative"]["body"]


def test_daily_notebook_material_and_assistant_share_the_same_instrument_evidence(client, monkeypatch):
    import json
    import pytest
    from watchlist_app.services import sector_research
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.db.models.workbench import ResearchEntry
    register_fund(client)
    material = client.post('/api/research/instruments/sxv264/dossier/materials', json={
        'title': '管理人说明', 'body': '管理人说明策略机制，未披露底层持仓。', 'source': '用户提供的管理人说明', 'published_at': '2026-08-31'}).json()
    sid = material['source_id']
    with get_session_factory()() as session:
        run, created = sector_research.begin_run(session, ["sxv264"])
        assert created
        run_id = run.entry_id
    sector_research.prepare_run(run_id)
    outline = client.get(f'/api/research/runs/{run_id}/context').json()
    assert 'body' not in outline['research_dossiers'][0]['materials'][0]
    original = client.get(f'/api/research/runs/{run_id}/dossier/sxv264', params={'source_id': sid}).json()
    assert original['body'] == material['body']
    assert client.get(f'/api/research/runs/{run_id}/dossier/another').status_code == 404
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        run.context_json = {**run.context_json, 'web_evidence': [{'operation': 'search', 'sources': []}]}
        # A quiet check does not require an invented notebook or opinion.
        sector_research.validate_result(session, run, sector_research.ReviewResult.model_validate({'reviews': [
            {'instrument_id': 'sxv264', 'change_kind': 'none', 'summary': '', 'coverage': [], 'events': []}]}))
        paper = {'modules': [{'key': 'fund-strategy', 'summary': '策略来自管理人说明，底层敞口仍待核实。', 'analysis': '暂没有可比估值资料。'}], 'key_drivers': [],
            'questions': [{'key': 'exposure', 'question': '底层敞口是什么？', 'assessment': '尚未披露。', 'next_check': '取得持仓说明。',
                          'status': 'open', 'source_ids': [sid]}], 'source_ids': [sid]}
        sector_research.apply_result(session, run, json.dumps({'reviews': [{'instrument_id': 'sxv264', 'summary': '继续核实敞口。',
              'coverage': [], 'events': [], 'research': paper}]}))
        session.commit()
    dossier = client.get('/api/research/instruments/sxv264/dossier').json()
    assert dossier['notebook']['run_id'] == run_id
    assert dossier['notebook']['questions'][0]['status'] == 'open'
    assert 'body' not in dossier['notebook']['sources'][0]
    saved_source = client.get('/api/research/instruments/sxv264/dossier', params={
        'source_id': sid, 'version_id': dossier['notebook']['version_id']})
    assert saved_source.status_code == 200
    assert saved_source.json()['body'] == material['body']
    conversation = start_analysis(client, monkeypatch)
    path = f"/api/research/runs/{conversation['entry_id']}/tools"
    found = client.post(path, json={'tool': 'dossier', 'instrument_ids': ['sxv264']}).json()['result']
    assert found['notebook']['questions'][0]['key'] == 'exposure'
    found = client.post(path, json={'tool': 'dossier', 'instrument_ids': ['sxv264'], 'source_id': sid}).json()['result']
    assert found['body'] == material['body']
    assert all(not topic['topic_id'].startswith(('dossier:', 'instrument-events:')) for topic in client.get('/api/research/topics').json())


def test_fund_research_and_assistant_bind_nav_benchmark_common_sample_and_missing_exposure(client, monkeypatch):
    from watchlist_app.db.models import InstrumentChartReadModel, InstrumentDetail, InstrumentManualProfile
    from watchlist_app.db.models.workbench import ResearchEntry
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services import sector_research

    register_fund(client)
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="fund-benchmark", instrument_name="登记基准", instrument_type="index", detail_view_type="index", is_active=True))
        session.flush()
        for iid, values in [("sxv264", [("2026-08-07", 1), ("2026-08-14", 1.04), ("2026-08-21", 1.1)]),
                            ("fund-benchmark", [("2026-08-07", 2), ("2026-08-21", 2.1)])]:
            row = session.get(InstrumentChartReadModel, iid)
            if row is None:
                row = InstrumentChartReadModel(instrument_id=iid)
                session.add(row)
            row.payload_json = {"research_returns": {"currency": "CNY", "metadata": {
                "return_kind": "total_return", "return_series_status": "complete", "quote_basis": "total_return_nav"},
                "frequency": {"resolved_frequency": "weekly"}, "points": [{"date": day, "value": value} for day, value in values]}}
            row.data_freshness_status = "fresh"
            row.source_cutoff_at = row.last_recalculated_at = datetime(2026, 8, 22, tzinfo=UTC)
        profile = session.get(InstrumentManualProfile, "sxv264")
        if profile is None:
            profile = InstrumentManualProfile(instrument_id="sxv264", updated_at=datetime.now(UTC))
            session.add(profile)
        profile.nav_settings_json = {**(profile.nav_settings_json or {}), "default_benchmark_instrument_id": "fund-benchmark"}
        session.commit()
        run, created = sector_research.begin_run(session, ["sxv264"])
        assert created
        run_id = run.entry_id
    sector_research.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        automatic_evidence = run.context_json["instrument_inputs"][0]["performance_evidence"]
        computed = run.context_json["computed_metrics"][0]
        assert computed["source_id"] == automatic_evidence["source_id"]
        assert computed["scope"] == "instrument"
        assert sector_research.usable_computed(computed, datetime.now(UTC), "sxv264")
        assert not sector_research.usable_computed(computed, datetime.now(UTC), "fund-benchmark")
        from watchlist_app.services.research_notebook import research_sources
        assert computed["source_id"] in research_sources(run.context_json, run_id)
        draft = sector_research.ReviewResult.model_validate({"reviews": [{"instrument_id": "sxv264", "change_kind": "knowledge",
            "research": {"modules": [{"key": "fund-strategy", "summary": "基于共同周度观察日的表现仍需结合策略与敞口解释。"}], "source_ids": [computed["source_id"]]},
            "events": [{"event_key": "common-sample-review", "action": "new", "direction": "uncertain",
                "title": "共同样本表现待解释", "body": "共同样本超额收益5个百分点，底层敞口仍未知。",
                "next_watch": "核实策略来源及更长区间表现。", "confidence": "confirmed", "information_type": "fact",
                "recording_type": "new", "source_ids": [computed["source_id"]]}]}]})
        sector_research.validate_result(session, run, draft)
    assert automatic_evidence["frequency"] == "weekly"
    comparison = automatic_evidence["comparisons"][0]["comparison"]
    assert comparison["dates"] == ["2026-08-07", "2026-08-21"]
    fund = next(item for item in comparison["rows"] if item["instrument_id"] == "sxv264")
    assert fund["excess_return_pp"] == pytest.approx(5)
    assert automatic_evidence["observations"] == 3

    conversation = start_analysis(client, monkeypatch)
    response = client.post(f"/api/research/runs/{conversation['entry_id']}/tools",
        json={"tool": "instruments", "instrument_ids": ["sxv264"]})
    assert response.status_code == 200, response.text
    evidence = response.json()["result"]["assets"][0]
    assert evidence["performance_evidence"]["sample_return_pct"] == automatic_evidence["sample_return_pct"]
    assert evidence["performance_evidence"]["comparisons"][0]["comparison"] == comparison
    assert evidence["performance_evidence"]["source_id"] != automatic_evidence["source_id"]
    assert "未知持仓、杠杆与对冲保持未知" in evidence["analyst_focus"]


def test_public_search_preserves_original_publication_and_failed_coverage(client, monkeypatch):
    register_fund(client)
    run = start_analysis(client, monkeypatch)
    path = f"/api/research/runs/{run['entry_id']}/tools"
    assert client.post(path, json={"tool": "search"}).status_code == 422
    assert client.post(path, json={"tool": "source"}).status_code == 422
    assert client.post(path, json={"tool": "search", "query": "公开基金公告"}).status_code == 422
    search = client.post(path, json={"tool": "search", "query": "公开基金公告", "public_result": {"sources": [{"url": "https://example.com/filing", "title": "披露"}], "coverage": ["仅公开检索"]}})
    assert search.status_code == 200
    source = client.post(path, json={"tool": "source", "url": "https://example.com/filing", "public_result": {"url": "https://example.com/filing", "published_at": "2026-08-20", "text": "旧公告原文", "discovered_at": datetime.now(UTC).isoformat()}}).json()
    assert source["result"]["published_at"] == "2026-08-20"
    assert source["result"]["published_at"] != source["retrieved_at"]
    missing = client.post(path, json={"tool": "source", "url": "https://example.com/blocked", "public_result": {"available": False, "reason": "Original source unavailable"}}).json()
    assert missing["result"]["available"] is False
    saved = client.get(f"/api/research/runs/{run['entry_id']}/context").json()["tool_evidence"]
    assert len(saved) == 3
    assert saved[1] == source
    assert saved[2] == missing
