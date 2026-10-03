from copy import deepcopy

import pytest

from watchlist_app.db.models.workbench import ResearchEntry, RiskReviewRule
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import risk_officer
from watchlist_app.services.price_risk import price_rule_summary
from .test_price_risk import observe, seed, series, workspace
from .test_risk_mcp_context import bind_context
from watchlist_app import research_mcp


def test_default_and_custom_rules_count_evaluations_not_cases(client):
    seed(client)
    observe(series([100] * 80))
    asset = workspace(client)['instruments'][0]
    summary = asset['price_rule_summary']
    assert summary['counts'] == dict(configured=4, evaluable=4, triggered=0, unavailable=0, default=4, custom=0, unknown=0)
    assert summary['settings_updated_at']
    assert [row['observations'] for row in summary['rules'][:4]] == [1, 5, 21, 63]
    assert all(row['state'] == 'not_triggered' and row['source'] == 'default' for row in summary['rules'][:4])
    limits = {**asset['period_limits'], 'day': 2}
    assert client.put('/api/risk/rules/sxv264', json={'period_limits': limits, 'drawdown_limit': 5}).status_code == 200
    observe(series([100] * 79 + [95]))
    result = workspace(client)
    counts = result['instruments'][0]['price_rule_summary']['counts']
    assert counts == dict(configured=5, evaluable=5, triggered=4, unavailable=0, default=3, custom=2, unknown=0)
    # Four windows crossing the line are represented by one period-loss case.
    assert len([c for c in result['cases'] if c['signal'] == 'period_loss' and c['trigger_active']]) == 1
    observe(series([100] * 3))
    partial = workspace(client)['instruments'][0]
    assert partial['price_risk_note'] is None  # Existing lines do not disappear with a shorter sample.
    counts = partial['price_rule_summary']['counts']
    assert counts['configured'] == 5 and counts['evaluable'] == 2 and counts['unavailable'] == 3
    observe(series([100] * 80), fresh=False)
    assert workspace(client)['instruments'][0]['price_rule_summary']['counts']['unavailable'] == 5
    client.put('/api/risk/rules/sxv264', json={'drawdown_limit': None, 'period_limits': dict.fromkeys(limits)})
    summary = workspace(client)['instruments'][0]['price_rule_summary']
    assert not any(summary['counts'].values())
    assert all(row['state'] == 'not_configured' for row in summary['rules'])


def test_legacy_manual_origins_remain_unknown_and_freshness_does_not_mean_safe():
    asset = {'freshness': 'fresh', 'risk': {'data_quality': {'status': 'ready'}, 'current_drawdown': -1},
        'period_limits': {'day': 5}, 'price_risk_calibration': {'method': 'volatility_review_v1', 'manually_edited': True},
        'period_readings': [{'period': 'day', 'limit_pct': 5, 'return_pct': -7, 'end_date': '2026-10-02', 'observations': 1}]}
    summary = price_rule_summary(asset)
    assert summary['settings_updated_at'] is None
    assert summary['counts']['unknown'] == summary['counts']['triggered'] == 1
    asset['freshness'] = 'unrecognized'
    assert price_rule_summary(asset)['counts']['unavailable'] == 1
    assert price_rule_summary(asset)['counts']['triggered'] == 0


def test_rules_freeze_before_settings_change_and_old_reports_are_not_reconstructed(client, monkeypatch):
    seed(client)
    observe(series([100] * 80))
    with get_session_factory()() as session:
        run, _ = risk_officer.begin_run(session, instrument_id='sxv264')
        run_id = run.entry_id
    risk_officer.prepare_run(run_id)
    with get_session_factory()() as session:
        context = deepcopy(session.get(ResearchEntry, run_id).context_json)
        frozen = context['risk_inputs']['instruments'][0]
    client.put('/api/risk/rules/sxv264', json={'period_limits': dict.fromkeys(['day', 'week', 'month', 'quarter']), 'drawdown_limit': 8})
    assert workspace(client)['instruments'][0]['price_rule_summary']['counts']['configured'] == 1
    bind_context(monkeypatch, context)
    # Exercise the actual compact MCP tool payload, not just a service dictionary.
    import asyncio
    tool = asyncio.run(research_mcp.mcp.call_tool('read_risk_instrument', {'instrument_id': 'sxv264'}))
    delivered = tool.structured_content['current']['instrument']
    assert delivered['period_limits'] == frozen['period_limits']
    assert delivered['price_rule_summary'] == frozen['price_rule_summary']
    assert delivered['price_rule_summary']['counts']['default'] == 4
    assert research_mcp.read_research_context()['risk_inputs']['price_rule_counts']['configured'] == 4
    with pytest.raises(ValueError, match='本次风控范围'):
        research_mcp.read_risk_instrument('outside')
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        risk_officer.apply_result(session, run, {'summary': '当时四条默认规则未触发；不代表无风险。', 'priorities': [], 'limitations': []})
        session.commit()
        saved = risk_officer.review_workspace(session, instrument_id='sxv264')['latest_completed']
        assert saved['price_rule_summary']['counts']['configured'] == 4 and saved['stale'] is True
        assert saved['prepared_at'] == context['prepared_at']
        # Legacy payload has only its retained raw inputs, never current rules.
        old = {**run.context_json['risk_inputs']}
        old.pop('price_rule_summary')
        run.context_json = {**run.context_json, 'risk_inputs': old}
        run.context_json = {key: value for key, value in run.context_json.items() if key != 'risk_review_view'}
        session.commit()
        legacy = risk_officer.review_workspace(session, instrument_id='sxv264')['latest_completed']
        assert legacy['price_rule_summary'] is None
        assert legacy['summary'] == saved['summary']


def test_get_does_not_generate_missing_rules_or_change_legacy_origins(client):
    seed(client)
    with get_session_factory()() as session:
        before = session.get(RiskReviewRule, 'sxv264')
        before = deepcopy(before.calibration_json) if before else None
    for _ in range(2):
        assert workspace(client)['instruments'][0]['price_rule_summary']['counts']['configured'] == 0
    with get_session_factory()() as session:
        after = session.get(RiskReviewRule, 'sxv264')
        assert (after.calibration_json if after else None) == before
