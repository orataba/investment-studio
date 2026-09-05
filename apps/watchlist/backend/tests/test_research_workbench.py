from datetime import date
import pytest
from sqlalchemy import select


def seed(client):
    watchlist = client.post('/api/watchlists', json={'name': 'Research test'}).json()
    response = client.post(f"/api/watchlists/{watchlist['watchlist_id']}/items", json={'instrument_ids': ['sxv264', 'savf63']})
    assert response.status_code == 200
    return watchlist['watchlist_id']


def test_topics_share_evidence_keep_conclusion_history_and_complete_followups(client):
    seed(client)
    catalogue = client.get('/api/research/catalogue')
    assert catalogue.status_code == 200
    assert 'sxv264' in {x['instrument_id'] for x in catalogue.json()['instruments']}
    topic = client.post('/api/research/topics', json={'title': '互补策略', 'instrument_ids': ['sxv264', 'savf63']})
    assert topic.status_code == 201, topic.text
    tid = topic.json()['topic_id']
    entry = client.post(f'/api/research/topics/{tid}/entries', json={'kind': 'task', 'title': '复核敞口', 'body': '向管理人了解持仓重合度', 'follow_up_date': '2026-01-01'}).json()
    completed = client.put(f"/api/research/entries/{entry['entry_id']}/completion", json={'completed': True})
    assert completed.json()['completed_at']
    for conclusion in ['可能互补，需要更多数据', '共同下跌较多，需要再复核']:
        assert client.post(f'/api/research/topics/{tid}/entries', json={'kind': 'conclusion', 'title': '当前观点', 'body': conclusion}).status_code == 201
    material = client.post(f'/api/research/topics/{tid}/files', files={'file': ('meeting.txt', '访谈证据：尚未取得完整底层敞口'.encode(), 'text/plain')})
    assert material.status_code == 200, material.text
    assert client.get(material.json()['source']).content.decode() == '访谈证据：尚未取得完整底层敞口'
    detail = client.get(f'/api/research/topics/{tid}').json()
    assert detail['topic']['conclusion'] == '共同下跌较多，需要再复核'
    revisions = [x for x in detail['entries'] if x['kind'] == 'conclusion']
    assert any(x['context_json']['previous_conclusion'] == '可能互补，需要更多数据' for x in revisions)
    assert len(client.get('/api/research/topics?instrument_id=savf63').json()) == 1
    # Reading workspaces does not append revisions or mutate state.
    assert detail == client.get(f'/api/research/topics/{tid}').json()


def test_conversation_uses_tools_retains_history_and_never_adopts_view(client, monkeypatch):
    wid = seed(client)
    import watchlist_app.services.research_runner as runner
    original_run = runner.run_analysis
    monkeypatch.setattr(runner, 'harness_available', lambda: True)
    monkeypatch.setattr(runner, 'run_analysis', lambda run_id: None)
    topic = client.post('/api/research/topics', json={'title': '基金研究', 'instrument_ids': ['sxv264']}).json()
    path = f"/api/research/topics/{topic['topic_id']}/analysis"
    response = client.post(path, json={'question': '有哪些风险待核查？', 'watchlist_id': wid})
    assert response.status_code == 202, response.text
    run_id = response.json()['entry_id']
    context_path = f'/api/research/runs/{run_id}/context'
    context = client.get(context_path).json()
    assert context['selected_instrument_ids'] == ['sxv264']
    assert context['watchlist_id'] == wid
    assert not context['history']
    assert wid in next(x for x in context['catalogue'] if x['instrument_id'] == 'sxv264')['watchlist_ids']
    assert client.post(path, json={'question': '同时重复提交'}).status_code == 409
    tool = f'/api/research/runs/{run_id}/tools'
    assert client.post(tool, json={'tool': 'instruments', 'instrument_ids': ['unknown']}).status_code == 422
    evidence = client.post(tool, json={'tool': 'instruments', 'instrument_ids': ['sxv264']}).json()
    assert evidence['result']['assets'][0]['research']['profile']['research_stage'] == 'watching'
    comparison = client.post(tool, json={'tool': 'comparison', 'instrument_ids': ['sxv264', 'savf63'], 'start_date': '2026-01-01', 'end_date': '2026-08-31', 'target_id': 'sxv264'})
    assert comparison.status_code == 200, comparison.text
    assert client.post(tool, json={'tool': 'comparison', 'instrument_ids': ['sxv264'], 'start_date': '2099-01-01', 'end_date': '2099-02-01'}).status_code == 422
    class HeadlessProcess:
        returncode = 0
        def communicate(self, timeout):
            return '需要补充底层风险证据。', None
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *args, **kwargs: HeadlessProcess())
    original_run(run_id)
    result = client.get(f"/api/research/topics/{topic['topic_id']}").json()['entries'][0]
    assert result['status'] == 'draft' and result['body'] == '需要补充底层风险证据。'
    original_run(run_id)  # A completed reply cannot be run over or overwritten.
    assert client.post(tool, json={'tool': 'instruments', 'instrument_ids': ['sxv264']}).status_code == 409
    saved = client.get(context_path).json()
    assert len(saved['tool_evidence']) == 2
    assert client.get(f"/api/research/topics/{topic['topic_id']}").json()['topic']['conclusion'] == ''
    followup = client.post(path, json={'question': '继续比较与另一个标的的互补性'}).json()['context_json']
    assert followup['history'][0]['answer'] == '需要补充底层风险证据。'
    assert followup['watchlist_id'] == wid
    assert client.get(context_path).json() == saved


def test_risk_workspace_scope_never_expands_an_empty_portfolio(client):
    wid = seed(client)
    assert len(client.get(f'/api/risk?watchlist_id={wid}').json()['instruments']) == 2
    narrow = client.get('/api/risk?instrument_ids=sxv264').json()
    assert [x['instrument_id'] for x in narrow['instruments']] == ['sxv264']
    assert all(x['instrument_id'] == 'sxv264' for x in narrow['cases'])
    assert client.get('/api/risk?instrument_ids=').json() == {'instruments': [], 'cases': []}
    assert client.get('/api/risk?watchlist_id=unknown').status_code == 404


def test_common_sample_math_never_fills_or_mixes_currency():
    from watchlist_app.services.research_workbench import compare_series
    def series(values, currency='USD'):
        return {'metadata': {'return_kind': 'total_return', 'return_series_status': 'ready'}, 'frequency': {'gap_count': 0}, 'currency': currency, 'points': [{'date': d, 'value': v} for d, v in values]}
    assets = {'a': series([('2026-01-01',100),('2026-01-08',90),('2026-01-15',99)]), 'b': series([('2026-01-01',100),('2026-01-05',500),('2026-01-08',110),('2026-01-15',100)])}
    result = compare_series(assets, date(2026,1,1), date(2026,1,31), target_id='a', benchmark_id='b')
    assert result['observations'] == 3
    rows = {r['instrument_id']: r for r in result['rows']}
    assert rows['a']['return_pct'] == pytest.approx(-1)
    assert rows['a']['max_drawdown_pct'] == pytest.approx(-10)
    assert rows['b']['correlation_to_target'] == pytest.approx(-1)
    assert rows['b']['average_return_when_target_down_pct'] == pytest.approx(10)
    assert rows['a']['excess_return_pp'] == pytest.approx(-1)
    assets['b']['currency'] = 'CNY'
    assert compare_series(assets, date(2026,1,1), date(2026,1,31))['rows'] == []


def test_comparison_allows_fund_price_index_and_adjusted_stock_on_common_dates():
    from watchlist_app.services.research_workbench import compare_series
    def series(kind, basis, values):
        return {'metadata': {'return_kind': kind, 'quote_basis': basis, 'return_series_status': 'ready'},
            'currency': 'CNY', 'points': [{'date': d, 'value': v} for d, v in zip(['2026-01-01', '2026-01-08', '2026-01-15'], values)]}
    assets = {
        'fund': series('total_return', 'total_return_nav', [100, 90, 99]),
        'index': series('price_return', 'close', [100, 110, 100]),
        'stock': series('total_return', 'adjusted_close', [100, 105, 110]),
    }
    result = compare_series(assets, date(2026, 1, 1), date(2026, 1, 31), target_id='fund', benchmark_id='index')
    rows = {row['instrument_id']: row for row in result['rows']}
    assert result['return_kind'] == 'mixed'
    assert result['observations'] == 3
    assert rows['fund']['excess_return_pp'] == pytest.approx(-1)
    assert rows['index']['correlation_to_target'] == pytest.approx(-1)
    assert rows['stock']['excess_return_pp'] == pytest.approx(10)
    assert rows['index']['return_kind'] == 'price_return'
    assert rows['stock']['quote_basis'] == 'adjusted_close'
    assert any('口径逐项列示' in note for note in result['limitations'])


def test_comparison_uses_available_observations_despite_gaps_and_missing_semantics():
    from watchlist_app.services.research_workbench import compare_series
    dates = ['2026-01-01', '2026-01-08', '2026-01-15']
    assets = {
        'a': {'metadata': {'return_series_status': 'partial'}, 'currency': 'USD', 'frequency': {'gap_count': 1},
            'points': [{'date': d, 'value': v} for d, v in zip(dates, [100, 90, 99])]},
        'b': {'metadata': {'return_series_status': 'ready', 'return_kind': 'price_return'}, 'currency': 'USD',
            'points': [{'date': d, 'value': v} for d, v in zip(dates, [100, 110, 100])]},
    }
    result = compare_series(assets, date(2026, 1, 1), date(2026, 1, 31), benchmark_id='b')
    assert len(result['rows']) == 2
    assert result['rows'][0]['excess_return_pp'] == pytest.approx(-1)
    assert any('缺失观察值' in note for note in result['limitations'])
    assets['a']['metadata']['return_segment_breaks'] = [{'date': '2026-01-08'}]
    result = compare_series(assets, date(2026, 1, 1), date(2026, 1, 31))
    assert result['excluded'] == [{'instrument_id': 'a', 'reason': '收益序列存在待确认的断点'}]


def test_risk_breach_updates_once_followup_is_not_recovery_and_recurrence_is_new(client):
    seed(client)
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.db.models import InstrumentRiskReadModel, InstrumentSummaryReadModel
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    def observed(dd, coverage=True):
        with get_session_factory()() as session:
            risk = session.get(InstrumentRiskReadModel, 'sxv264')
            risk.payload_json = {'current_drawdown': dd, 'data_quality': {'status': 'ready' if coverage else 'partial_missing_observations'}, 'drawdown_summary': {'maximum': -30, 'valley_date': '2025-01-01'}}
            summary = session.get(InstrumentSummaryReadModel, 'sxv264')
            summary.payload_json = {'freshness': {'data_freshness_status': 'fresh', 'latest_observation_date': '2026-09-04'}}
            refresh_risk_cases(session, ['sxv264'])
            session.commit()
    observed(-12)
    assert client.put('/api/risk/rules/sxv264', json={'drawdown_limit': 10}).status_code == 200
    def breaches():
        return [c for c in client.get('/api/risk?instrument_id=sxv264').json()['cases'] if c['signal'] == 'drawdown_limit']
    cases = breaches()
    assert len(cases) == 1 and cases[0]['trigger_active']
    case_id = cases[0]['case_id']
    client.put(f'/api/risk/cases/{case_id}', json={'status': 'handled', 'note': '已联系管理人'})
    history_size = len(breaches()[0]['history_json'])
    observed(-13)
    assert len(breaches()[0]['history_json']) == history_size
    assert len(breaches()) == 1
    assert breaches()[0]['status'] == 'handled' and breaches()[0]['trigger_active']
    observed(None, False)
    assert breaches()[0]['trigger_active'] # missing observations cannot prove recovery
    observed(-5)
    assert not breaches()[0]['trigger_active']
    observed(-15)
    assert len(breaches()) == 2
    assert sum(c['trigger_active'] for c in breaches()) == 1


def test_completed_instrument_notes_leave_due_projection_and_retain_revision(client):
    wid = seed(client)
    request = {'note': {'note_date': '2026-01-01', 'title': '检查经理变更', 'note_type': 'review', 'follow_up_date': '2026-01-02'}}
    created = client.post('/api/instruments/sxv264/research/notes', json=request).json()
    note = created['notes'][0]
    request['note']['completed_at'] = '2026-01-03T00:00:00Z'
    response = client.put(f"/api/instruments/sxv264/research/notes/{note['note_id']}", json=request)
    assert response.status_code == 200
    assert response.json()['notes'][0]['completed_at']
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.research_projection import build_research_watchlist_attribute_overrides
    with get_session_factory()() as session:
        values = build_research_watchlist_attribute_overrides(session, instrument_ids=['sxv264'])['sxv264']
        assert 'research_next_follow_up_date' not in values
    history = client.get('/api/instruments/sxv264/research/history').json()
    assert len(history['note_revisions']) == 2
    stage = client.put('/api/instruments/sxv264/research', json={'profile': {'research_stage': 'candidate', 'current_view': '继续核查'}})
    assert stage.status_code == 200
    screener = client.post('/api/screener/query', json={'watchlist_id': wid, 'view_id': 'overview', 'selected_fields': ['instrument_name', 'attr.research_stage', 'attr.risk_attention'], 'filters': {'attr.research_stage': ['candidate']}, 'group_by': 'none'})
    assert screener.status_code == 200, screener.text
    assert screener.json()['total_rows'] == 1


def test_minor_sample_lows_and_low_importance_notes_do_not_raise_attention(client):
    seed(client)
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.db.models import InstrumentRiskReadModel, InstrumentSummaryReadModel
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    from watchlist_app.services.research_projection import build_research_watchlist_attribute_overrides
    with get_session_factory()() as session:
        risk = session.get(InstrumentRiskReadModel, 'sxv264')
        risk.payload_json = {'current_drawdown': -0.01, 'data_quality': {'status': 'ready'}, 'drawdown_summary': {'maximum': -0.01, 'valley_date': '2026-09-04'}}
        summary = session.get(InstrumentSummaryReadModel, 'sxv264')
        summary.payload_json = {'freshness': {'data_freshness_status': 'fresh', 'latest_observation_date': '2026-09-04'}}
        refresh_risk_cases(session, ['sxv264'])
        session.commit()
    request = {'note': {'note_date': '2026-09-04', 'title': '普通波动观察', 'note_type': 'risk', 'importance': 'low', 'body': '变化很小，继续观察'}}
    response = client.post('/api/instruments/sxv264/research/notes', json=request)
    assert response.status_code == 200, response.text
    def active():
        return [c for c in client.get('/api/risk?instrument_id=sxv264').json()['cases'] if c['trigger_active']]
    assert all(c['severity'] != 'attention' for c in active())
    assert any(c['severity'] == 'observation' for c in active())
    with get_session_factory()() as session:
        assert build_research_watchlist_attribute_overrides(session, instrument_ids=['sxv264'])['sxv264']['risk_attention'] == 'no_trigger'
    note_id = response.json()['notes'][0]['note_id']
    request['note'].update(importance='high', title='新增重要证据，需要核查')
    assert client.put(f'/api/instruments/sxv264/research/notes/{note_id}', json=request).status_code == 200
    assert any(c['title'] == '新增重要证据，需要核查' and c['severity'] == 'attention' for c in active())
    manual = client.post('/api/risk/cases', json={'instrument_id': 'sxv264', 'title': '待核对的新闻线索', 'importance': 'low', 'source': '公开公告 2026-09-04'})
    assert manual.status_code == 201
    assert manual.json()['severity'] == 'observation'
    assert manual.json()['evidence_json']['source'] == '公开公告 2026-09-04'
