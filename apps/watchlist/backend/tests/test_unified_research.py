"""Investment research updates share versions and publication semantics across entrances."""
import json
from datetime import UTC, datetime

import pytest

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service
from watchlist_app.services.research_dossier import read_dossier, read_dossier_version


def run_for(session, rid, *, conversation=False, dossier=None):
    topic_id = 'conversation' if conversation else 'instrument-events:gold-test'
    if session.get(ResearchTopic, topic_id) is None:
        session.add(ResearchTopic(topic_id=topic_id, title='黄金研究', instrument_ids=['gold-test']))
        session.flush()
    run = ResearchEntry(entry_id=rid, topic_id=topic_id, kind='analysis', title='研究', status='running',
        context_json={'research_run': True, 'sector_run': not conversation, 'instrument_ids': ['gold-test'],
            'team_publication_instructions': {'gold-test': '请保存黄金团队研究'},
            'cutoff': datetime.now(UTC).isoformat(), 'research_dossiers': [dossier or read_dossier(session, 'gold-test')],
            'market_queries': [{'instrument_id': 'gold-test', 'query': 'Gold'}],
            'instrument_inputs': [{'instrument_id': 'gold-test', 'name': '黄金研究样本', 'instrument_type': 'etf'}],
            'web_evidence': [{'operation': 'fetch', 'sources': [{'source_id': 'original',
                'url': 'https://example.com/gold', 'title': 'Gold demand', 'text': 'Reserve demand remains supported.',
                'published_at': '2026-01-01'}]}]})
    session.add(run)
    session.commit()
    return run


@pytest.fixture
def instrument(client):
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id='gold-test', instrument_type='etf', detail_view_type='etf',
                                     instrument_name='黄金研究样本', metadata_json={}))
        session.commit()


def apply(session, run, **delta):
    bound = next((d for d in run.context_json.get('research_dossiers', []) if d['instrument_id'] == 'gold-test'), {})
    delta.setdefault('themes', [{'theme_id': item['theme_id'], 'theme_key': item['theme_key'] or item['theme_id']}
                                for item in bound.get('themes', []) if item['status'] == 'active'])
    service.apply_result(session, run, json.dumps({'reviews': [{'instrument_id': 'gold-test', **delta}]}))
    if not run.context_json['sector_run']:
        run.status = 'draft'
        run.body = '已完成本轮讨论。'
    session.commit()


def test_chat_publication_quiet_check_and_risk_only_update_share_the_same_view(instrument):
    with get_session_factory()() as session:
        chat = run_for(session, 'chat-1', conversation=True)
        apply(session, chat, change_kind='investment', summary='中期偏多；短期方向尚无新增判断。',
            themes=[{'theme_key': 'gold-demand', 'title': '黄金需求持续性', 'question': '储备需求能否持续支持价格？',
                     'priority_reason': '需求持续性是中期判断的关键未决条件，需要跟踪后续原始披露。'}], research={
            'investment_view': {'direction': '中期偏多', 'horizon': '未来一季度', 'attractiveness': '仍需比较当前风险补偿',
                'risk': '观察波动变化', 'source_ids': ['original']},
            'forecasts': [{'key': 'demand', 'claim': '需求支持有望延续', 'horizon': '未来一季度',
                           'theme_id': 'gold-demand', 'source_ids': ['original']}]})
        original = read_dossier(session, 'gold-test')['notebook']
        first_review = service.latest_reviews(session, completed_only=True)['gold-test']
        quiet = run_for(session, 'quiet')
        apply(session, quiet, change_kind='none')
        latest = service.latest_reviews(session)['gold-test']
        assert latest['run_id'] == 'quiet' and latest['change_kind'] == 'none'
        assert latest['summary'] == first_review['summary']
        assert latest['view_run_id'] == 'chat-1' and latest['view_updated_at'] == first_review['view_updated_at']
        assert read_dossier(session, 'gold-test')['notebook']['investment_view'] == original['investment_view']
        chat2 = run_for(session, 'chat-2', conversation=True)
        apply(session, chat2, change_kind='knowledge', research={'investment_view': {'risk': '短期波动上升，方向判断维持'}})
        current = read_dossier(session, 'gold-test')['notebook']
        assert current['investment_view']['direction'] == '中期偏多'
        assert current['investment_view']['attractiveness'] == '仍需比较当前风险补偿'
        assert current['forecasts'][0]['version_id'] == original['forecasts'][0]['version_id']
        prior = read_dossier_version(session, 'gold-test', original['investment_view']['version_id'])
        assert prior['value']['risk'] == '观察波动变化'
        assert prior['sources'][0]['source_id'] == 'original'


def test_stale_conversation_cannot_overwrite_a_newer_automatic_view(instrument):
    with get_session_factory()() as session:
        stale = run_for(session, 'stale', conversation=True)
        fresh = run_for(session, 'fresh')
        apply(session, fresh, change_kind='investment', summary='新资料改变了方向判断。',
              research={'investment_view': {'direction': '暂缓判断', 'source_ids': ['original']}})
        with pytest.raises(service.ResearchVersionConflict):
            apply(session, stale, change_kind='investment', summary='旧资料上的乐观判断',
                  research={'investment_view': {'direction': '偏多'}})
        session.rollback()
        assert read_dossier(session, 'gold-test')['notebook']['investment_view']['direction'] == '暂缓判断'


def test_computed_volatility_can_support_risk_without_an_external_news_story(instrument):
    with get_session_factory()() as session:
        run = run_for(session, 'risk-observation')
        run.context_json = {**run.context_json, 'computed_metrics': [{
            'source_id': 'computed:risk', 'source_type': 'computed_metric', 'scope': 'public_market',
            'instrument_id': 'gold-test', 'as_of': run.context_json['cutoff'], 'title': 'EWMA observation',
            'methodology': {'half_life_sessions': 21}, 'data': {'status': 'available', 'current': {'volatility_pct': 25}},
        }]}
        session.commit()
        apply(session, run, themes=[{'theme_key': 'gold-volatility', 'kind': 'quantitative', 'title': '黄金波动风险',
            'question': '当前波动变化是否持续影响风险承受水平？', 'priority_reason': '实际波动变化会改变风险敞口，需继续观察持续性。'}],
            events=[{'event_key': 'volatility', 'action': 'new', 'direction': 'risk',
            'title': '波动风险上升', 'body': '已计算的波动观察需要重新评估风险承受水平，不代表未来必然下跌。',
            'next_watch': '观察波动持续性', 'confidence': 'confirmed', 'information_type': 'fact',
            'recording_type': 'new', 'theme_ids': ['gold-volatility'], 'source_ids': ['computed:risk']}])
        assert service.events_for_instruments(session, ['gold-test'])[0]['direction'] == 'risk'


def test_repeated_metric_read_is_quiet_but_changed_measurement_updates_same_risk(instrument):
    from watchlist_app.db.models.workbench import RiskCase
    from sqlalchemy import select
    event = {'event_key': 'volatility', 'action': 'new', 'direction': 'risk', 'title': '波动风险上升',
        'body': '波动风险仍需跟踪。', 'next_watch': '观察持续性', 'confidence': 'confirmed',
        'information_type': 'fact', 'recording_type': 'new', 'theme_ids': ['gold-volatility'], 'source_ids': ['computed:risk']}
    with get_session_factory()() as session:
        for index, value in enumerate([25, 25, 30]):
            run = run_for(session, f'metric-{index}')
            run.context_json = {**run.context_json, 'computed_metrics': [{
                'source_id': 'computed:risk', 'source_type': 'computed_metric', 'scope': 'public_market',
                'instrument_id': 'gold-test', 'as_of': run.context_json['cutoff'], 'title': 'EWMA observation',
                'methodology': {'half_life_sessions': 21},
                'data': {'status': 'available', 'current': {'date': '2026-09-07', 'volatility_pct': value}},
            }]}
            session.commit()
            theme = {'themes': [{'theme_key': 'gold-volatility', 'kind': 'quantitative', 'title': '黄金波动风险',
                'question': '波动上升是否持续并需要重新评估风险承受水平？',
                'priority_reason': '同口径波动观察直接影响风险判断，应保留持续性验证。'}]} if index == 0 else {}
            apply(session, run, events=[{**event, 'action': 'new' if index == 0 else 'updated'}], **theme)
            case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == 'gold-test'))
            assert len(case.history_json) == (2 if index == 2 else 1)
            if index == 1:
                assert run.context_json['reviews']['gold-test']['change_kind'] == 'none'
        source = service.events_for_instruments(session, ['gold-test'])[0]['sources'][0]
        assert source['source_type'] == 'computed_metric'
        assert source['measurement']['current']['volatility_pct'] == 30
        assert source['as_of'] is not None
