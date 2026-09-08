from datetime import UTC, datetime


def register_fund(client):
    watchlist = client.post("/api/watchlists", json={"name": "分析师测试"}).json()
    response = client.post(f"/api/watchlists/{watchlist['watchlist_id']}/items", json={"instrument_ids": ["sxv264"]})
    assert response.status_code == 200, response.text


def start_analysis(client, monkeypatch, instrument_id="sxv264", page_context=None):
    import watchlist_app.services.research_runner as runner
    monkeypatch.setattr(runner, "harness_available", lambda: True)
    monkeypatch.setattr(runner, "run_analysis", lambda run_id, token=None: None)
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
                                 context_json={"sector_run": True, "instrument_ids": ["sxv264"], "reviews": {"sxv264": {"status": "limited", "summary": "已有结论", "coverage": ["未披露底层敞口"]}}}))
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
    monkeypatch.setattr(runner, "run_analysis", lambda run_id, token=None: None)
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
    from watchlist_app.services.research_workbench import ANALYST_FOCUS

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
    assert "未披露" in context["analyst_focus"][0]["guidance"]
    assert datetime.fromisoformat(context["requested_at"]).tzinfo is not None
    response = client.post(f"/api/research/runs/{run['entry_id']}/tools", json={"tool": "instruments", "instrument_ids": ["sxv264"]})
    assert response.status_code == 200, response.text
    evidence = response.json()["result"]["assets"][0]
    assert evidence["product_information"]["terms_and_fees"]["redemption"] == "每季度开放一次"
    assert evidence["holdings"]["data"]["as_of_date"] == "2026-06-30"
    assert evidence["holdings"]["freshness"] == "stale"
    assert "披露滞后" in ANALYST_FOCUS["public_fund"]
    assert "A股ETF" in ANALYST_FOCUS["etf"]
    assert "预期上修或下修" in ANALYST_FOCUS["equity"]


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
        # Ordinary funds no longer launch active research. Retained historical
        # research still provides the same evidence to today's personal assistant.
        from watchlist_app.db.models.workbench import ResearchTopic
        topic = ResearchTopic(topic_id="instrument-events:sxv264", title="已有基金研究", instrument_ids=["sxv264"], visibility="team")
        session.add(topic)
        session.flush()
        run = ResearchEntry(entry_id="retained-fund-research", topic_id=topic.topic_id, kind="analysis", title="已保存的基金研究", status="queued",
                            context_json={"sector_run": True, "instrument_ids": ["sxv264"], "reviews": {}})
        session.add(run)
        session.commit()
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
        paper = {'fundamental_view': '策略来自管理人说明，底层敞口仍待核实。', 'key_drivers': [], 'valuation_view': '暂没有可比估值资料。',
            'questions': [{'key': 'exposure', 'question': '底层敞口是什么？', 'assessment': '尚未披露。', 'next_check': '取得持仓说明。',
                          'status': 'open', 'source_ids': [sid]}], 'source_ids': [sid]}
        sector_research.apply_result(session, run, json.dumps({'reviews': [{'instrument_id': 'sxv264', 'summary': '继续核实敞口。',
              'coverage': [], 'events': [], 'research': paper}]}))
        session.commit()
    dossier = client.get('/api/research/instruments/sxv264/dossier').json()
    assert dossier['notebook']['run_id'] == run_id
    assert dossier['notebook']['questions'][0]['status'] == 'open'
    assert dossier['notebook']['sources'][0]['body'] == material['body']
    conversation = start_analysis(client, monkeypatch)
    path = f"/api/research/runs/{conversation['entry_id']}/tools"
    found = client.post(path, json={'tool': 'dossier', 'instrument_ids': ['sxv264']}).json()['result']
    assert found['notebook']['questions'][0]['key'] == 'exposure'
    found = client.post(path, json={'tool': 'dossier', 'instrument_ids': ['sxv264'], 'source_id': sid}).json()['result']
    assert found['body'] == material['body']
    assert all(not topic['topic_id'].startswith(('dossier:', 'instrument-events:')) for topic in client.get('/api/research/topics').json())


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
