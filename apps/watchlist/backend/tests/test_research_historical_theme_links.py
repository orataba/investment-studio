"""A retrospective keeps original ownership without resuming a stopped theme."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_activity, sector_research
from .test_research_themes import research_client, _theme, _themes, _note, _notes


def bound_run(status='paused'):
    themes = {'old-theme': {'theme_id': 'old-theme', 'status': status, 'lifecycle_owner': 'user'},
              'current-theme': {'theme_id': 'current-theme', 'status': 'active'}}
    context = {'sector_run': True, 'cutoff': '2026-09-27T12:00:00+00:00',
        'input_snapshot_cutoff': '2026-09-27T00:00:00+00:00',
        'research_dossiers': [{'instrument_id': 'fund-us-agg', 'themes': list(themes.values()),
            'notebook': {'forecasts': [{'key': 'memory-margin', 'theme_id': 'old-theme',
                'version_id': 'prior:forecasts:memory-margin', 'status': 'active'}]}}]}
    return SimpleNamespace(entry_id='current-run', context_json=context), themes


def review(**kwargs):
    return sector_research.SectorReview.model_validate({'instrument_id': 'fund-us-agg', **kwargs})


@pytest.mark.parametrize('status', ['paused', 'closed'])
@pytest.mark.parametrize('field', ['forecast_reviews', 'lessons'])
def test_prior_forecast_can_be_reviewed_under_current_theme_without_resuming_old_theme(research_client, status, field):
    run, themes = bound_run(status)
    original = deepcopy(run.context_json)
    item = {'key': 'memory-review', 'theme_id': 'current-theme', 'forecast_key': 'memory-margin',
        'forecast_version_id': 'prior:forecasts:memory-margin',
        **({'outcome': '经营结果仍待披露确认'} if field == 'forecast_reviews' else {'lesson': '区分技术需求与兑现的利润'})}
    with get_session_factory()() as session:
        sector_research._validate_research_links(session, run, review(research={field: [item]}), themes)
        assert run.context_json == original and themes['old-theme']['status'] == status
        with pytest.raises(ValueError, match='原版本'):
            sector_research._validate_research_links(session, run, review(research={field: [
                {**item, 'forecast_version_id': 'invented-version'}]}), themes)
        with pytest.raises(ValueError, match='未读取.*old-theme'):
            sector_research._validate_research_links(session, run, review(research={field: [item]}),
                                                     {'current-theme': themes['current-theme']})


@pytest.mark.parametrize('field', ['forecast_reviews', 'lessons', 'reflection'])
def test_prior_judgment_reference_preserves_cutoff_and_kind_checks(research_client, monkeypatch, field):
    run, themes = bound_run()
    original = {'kind': 'forecast', 'run_id': 'prior-run', 'recorded_at': '2026-09-26T00:00:00+00:00',
                'reference': {'theme_id': 'old-theme'}}
    monkeypatch.setattr(research_activity, 'research_activity', lambda *args, **kwargs: {
        'instrument_id': 'fund-us-agg', 'updates': [{'update_id': 'research:prior', **original}]})
    if field == 'reflection':
        submission = review(reflection={'status': 'reviewed', 'reviewed_update_ids': ['research:prior']})
    else:
        submission = review(research={field: [{'key': 'old-evidence-review', 'theme_id': 'current-theme',
            'related_research_update_id': 'research:prior',
            **({'outcome': '核对事前判断'} if field == 'forecast_reviews' else {'lesson': '区分机制与价格变化'})}]})
    with get_session_factory()() as session:
        sector_research._validate_research_links(session, run, submission, themes)
        original['recorded_at'] = '2026-09-27T06:00:00+00:00'  # After bound input cutoff, before run cutoff.
        with pytest.raises(ValueError, match='本轮开始前'):
            sector_research._validate_research_links(session, run, submission, themes)
        original['recorded_at'], original['run_id'] = '2026-09-26T00:00:00+00:00', run.entry_id
        with pytest.raises(ValueError, match='本轮开始前'):
            sector_research._validate_research_links(session, run, submission, themes)
        original['run_id'], original['kind'] = 'prior-run', 'theme'
        with pytest.raises(ValueError, match='不是事前判断'):
            sector_research._validate_research_links(session, run, submission, themes)


def test_multiple_references_share_one_authorized_history_only_within_validation(research_client, monkeypatch):
    run, themes = bound_run()
    originals = [{'update_id': f'research:{key}', 'kind': 'forecast', 'run_id': 'prior-run',
        'recorded_at': '2026-09-26T00:00:00+00:00', 'body': f'原始\x00依据 {key}',
        'reference': {'instrument_id': 'fund-us-agg', 'theme_id': 'old-theme'}} for key in ('first', 'second')]
    activity = {'instrument_id': 'fund-us-agg', 'updates': originals}
    before = deepcopy(activity)
    reads = []
    def read_activity(session, instrument_id, *, source_metadata_only=False):
        assert source_metadata_only is True
        reads.append(instrument_id)
        return deepcopy(activity)
    monkeypatch.setattr(research_activity, 'research_activity', read_activity)
    submission = review(research={'forecast_reviews': [{'key': 'review', 'theme_id': 'current-theme',
        'related_research_update_id': 'research:first', 'outcome': '对照原判断'}],
        'lessons': [{'key': 'lesson', 'theme_id': 'current-theme',
            'related_research_update_id': 'research:second', 'lesson': '保留原适用条件'}]},
        reflection={'status': 'reviewed', 'reviewed_update_ids': ['research:first', 'research:second']})
    with get_session_factory()() as session:
        sector_research._validate_research_links(session, run, submission, themes)
        assert reads == ['fund-us-agg']
        assert activity == before
        assert research_activity.resolve_research_update(session, 'fund-us-agg', 'research:first', activity=activity) == originals[0]
        with pytest.raises(ValueError, match='当前标的'):
            research_activity.resolve_research_update(session, 'sxv264', 'research:first', activity=activity)
        # A new validation reads again and still checks every reference's PIT
        # clock, even if an earlier reference in the same proposal is valid.
        originals[1]['recorded_at'] = '2026-09-27T06:00:00+00:00'
        with pytest.raises(ValueError, match='本轮开始前'):
            sector_research._validate_research_links(session, run, submission, themes)
        assert reads == ['fund-us-agg', 'fund-us-agg']


def test_invalid_view_event_error_identifies_the_item_and_reference(research_client):
    run, themes = bound_run()
    submission = review(research={'investment_view': {'opportunities': [
        {'key': 'capacity-opportunity', 'title': '产能兑现', 'explanation': '需要后续披露', 'next_watch': '核对公告',
         'event_keys': ['unknown-event']}]}})
    with get_session_factory()() as session, pytest.raises(ValueError) as raised:
        sector_research._validate_research_links(session, run, submission, themes)
    message = str(raised.value)
    assert 'research.investment_view.opportunities[key=capacity-opportunity].event_keys' in message
    assert 'unknown-event' in message and '当前标的' in message


def test_missing_catalyst_theme_error_identifies_the_scheduled_item(research_client):
    run, themes = bound_run()
    submission = review(research={'catalysts': [{'key': 'calendar-release', 'title': '下次披露',
        'scheduled_at': '2026-09-30', 'relevance': '检验原判断', 'next_check': '核对公告', 'source_ids': ['calendar']}]})
    with get_session_factory()() as session, pytest.raises(ValueError) as raised:
        sector_research._validate_research_links(session, run, submission, themes)
    assert 'research.catalysts[key=calendar-release].theme_id' in str(raised.value)


@pytest.mark.parametrize('field,item', [
    ('questions', {'key': 'margin-question', 'question': '利润是否兑现？', 'assessment': '继续核查', 'next_check': '核对披露'}),
    ('forecasts', {'key': 'margin-forecast', 'claim': '利润可能改善', 'horizon': '下一季度'}),
    ('catalysts', {'key': 'margin-catalyst', 'title': '财报披露', 'scheduled_at': '2026-09-30',
                   'relevance': '核对利润', 'next_check': '读取披露', 'source_ids': ['calendar']}),
    ('forecast_reviews', {'key': 'margin-review', 'forecast_key': 'memory-margin',
                          'forecast_version_id': 'prior:forecasts:memory-margin', 'outcome': '尚待确认'}),
])
def test_current_item_cannot_resume_stopped_theme_and_error_identifies_field(research_client, field, item):
    run, themes = bound_run()
    with get_session_factory()() as session:
        with pytest.raises(ValueError) as raised:
            sector_research._validate_research_links(session, run,
                review(research={field: [{**item, 'theme_id': 'old-theme'}]}), themes)
        message = str(raised.value)
        assert f'research.{field}[key={item["key"]}].theme_id' in message
        assert 'theme_id=old-theme' in message and 'paused' in message


@pytest.mark.parametrize('theme_status', ['paused', 'closed'])
@pytest.mark.parametrize('prior_status,update', [
    ('active', {'tracking_status': 'paused', 'tracking_reason': '同步所属主题的暂停安排'}),
    ('paused', {}),
    ('closed', {}),
])
def test_existing_question_can_correct_evidence_without_resuming_its_theme(research_client, theme_status, prior_status, update):
    run, themes = bound_run(theme_status)
    prior = {'key': 'xlk-volatility-regime', 'theme_id': 'old-theme', 'question': '波动是否持续处于高位？',
        'assessment': '旧波动率27.5%', 'next_check': '继续观察', 'tracking_status': prior_status,
        'tracking_reason': '' if prior_status == 'active' else '已停止独立跟踪'}
    run.context_json['research_dossiers'][0]['notebook']['questions'] = [prior]
    original = deepcopy(run.context_json)
    item = {'key': prior['key'], 'theme_id': prior['theme_id'], 'question': prior['question'],
        'assessment': '现有记录为25.74%，纠正旧证据，未恢复独立跟踪', 'next_check': '由市场量化模块观察', **update}
    with get_session_factory()() as session:
        sector_research._validate_research_links(session, run, review(research={'questions': [item]}), themes)
    assert run.context_json == original
    assert themes['old-theme']['status'] == theme_status


@pytest.mark.parametrize('change', ['new_key', 'different_theme', 'resume'])
def test_inactive_question_exception_cannot_create_reassign_or_resume_tracking(research_client, change):
    run, themes = bound_run()
    prior = {'key': 'existing-question', 'theme_id': 'old-theme', 'question': '原问题',
        'assessment': '保留判断', 'next_check': '现有观察', 'tracking_status': 'paused', 'tracking_reason': '暂停跟踪'}
    run.context_json['research_dossiers'][0]['notebook']['questions'] = [prior]
    themes['another-stopped-theme'] = {'theme_id': 'another-stopped-theme', 'status': 'closed'}
    item = {**prior, **({'key': 'new-question'} if change == 'new_key' else
                      {'theme_id': 'another-stopped-theme'} if change == 'different_theme' else
                      {'tracking_status': 'active', 'tracking_reason': '拟恢复'})}
    with get_session_factory()() as session:
        with pytest.raises(ValueError, match=r'research.questions\[key=.*\].theme_id.*(paused|closed)'):
            sector_research._validate_research_links(session, run, review(research={'questions': [item]}), themes)


def test_pm_review_uses_original_revision_ownership_after_note_moves_to_another_theme(research_client):
    from datetime import UTC, datetime
    from watchlist_app.services.research_dossier import read_dossier
    from watchlist_app.services.research_views import note_version
    old_theme, current_theme = _theme(research_client), _theme(research_client)
    note = _note(research_client, {'theme_id': old_theme['theme_id']})
    moved = research_client.put(f"{_notes()}/{note['note_id']}", json={'note': {
        'note_date': '2026-09-08', 'title': '现在归入另一个主题',
        'research_context': {'theme_id': current_theme['theme_id']}}})
    assert moved.status_code == 200, moved.text
    stopped = research_client.patch(f"{_themes()}/{old_theme['theme_id']}", json={'status': 'paused'})
    assert stopped.status_code == 200, stopped.text
    with get_session_factory()() as session:
        dossier = read_dossier(session, 'fund-us-agg')
        themes = {theme['theme_id']: theme for theme in dossier['themes']}
        run = SimpleNamespace(entry_id='pm-review-run', context_json={'sector_run': True,
            'cutoff': datetime.now(UTC).isoformat(), 'research_dossiers': [dossier]})
        item = {'key': 'prior-pm-judgment', 'question': '回看当时判断', 'assessment': '保留当时判断的归属与依据',
            'next_check': '', 'tracking_status': 'closed', 'tracking_reason': '仅复盘历史，不重新跟踪',
            'pm_note_id': note['note_id'], 'pm_note_revision': 1}
        sector_research._validate_research_links(session, run, review(research={'questions': [item]}), themes)
        assert note_version(session, 'fund-us-agg', note['note_id'], 1).research_context['theme_id'] == old_theme['theme_id']
        assert note_version(session, 'fund-us-agg', note['note_id'], 2).research_context['theme_id'] == current_theme['theme_id']
        with pytest.raises(ValueError, match='原观点不一致'):
            sector_research._validate_research_links(session, run,
                review(research={'questions': [{**item, 'theme_id': current_theme['theme_id']}]}), themes)
        with pytest.raises(ValueError, match='原始版本'):
            sector_research._validate_research_links(session, run,
                review(research={'questions': [{**item, 'pm_note_revision': 99}]}), themes)
        with pytest.raises(ValueError, match='暂停'):
            sector_research._validate_research_links(session, run,
                review(research={'questions': [{**item, 'theme_id': old_theme['theme_id'], 'tracking_status': 'active'}]}), themes)
