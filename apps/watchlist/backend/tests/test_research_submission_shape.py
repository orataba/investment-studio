"""Malformed research proposals must never silently lose nested content."""
import asyncio
import json

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from watchlist_app import research_mcp as mcp


@pytest.mark.parametrize('review,expected', [
    ({'instrument_id': 'xlk', 'research': {'investment_view': {
        'direction': 'PRIVATE_DIRECTION', 'questions': [{'key': 'retained-question', 'assessment': 'PRIVATE_EVIDENCE'}]}}},
     {('result', 'reviews', 0, 'research', 'investment_view', 'questions')}),
    ({'instrument_id': 'xlk', 'research': {'investment_view': {'modules': [{'key': 'market-quantitative'}]}}},
     {('result', 'reviews', 0, 'research', 'investment_view', 'modules')}),
    ({'research': {'summary': 'PRIVATE_SUMMARY', 'themes': [], 'coverage_note': 'PRIVATE_COVERAGE', 'coverage_status': 'assessed'}},
     {('result', 'reviews', 0, 'instrument_id'), *[
         ('result', 'reviews', 0, 'research', name) for name in ('summary', 'themes', 'coverage_note', 'coverage_status')]}),
    ({'instrument_id': 'xlk', 'themes': [{'theme_key': 'known', 'unexpected': 'PRIVATE_THEME'}]},
     {('result', 'reviews', 0, 'themes', 0, 'unexpected')}),
])
def test_sdk_rejects_all_misplaced_fields_without_echoing_or_sending_input(monkeypatch, review, expected):
    calls = []
    monkeypatch.setattr(mcp, 'request', lambda *args: calls.append(args))
    with pytest.raises(ToolError) as raised:
        asyncio.run(mcp.mcp.call_tool('submit_research_review', {'result': {'reviews': [review]}}))
    message = str(raised.value)
    diagnostic = json.loads(message[message.index('{'):])
    assert diagnostic['error'] == 'invalid_research_review'
    assert {tuple(item['loc']) for item in diagnostic['issues']} == expected
    assert diagnostic['required_result_fields'] == ['reviews']
    assert diagnostic['required_review_fields'] == ['instrument_id']
    assert 'themes' in diagnostic['field_levels']['result.reviews[]']
    assert 'questions' in diagnostic['field_levels']['result.reviews[].research']
    assert 'questions' not in diagnostic['field_levels']['result.reviews[].research.investment_view']
    assert 'coverage_note' in diagnostic['field_levels']['result.reviews[].research.investment_view']
    assert diagnostic['resubmit_mode'] == 'complete_result'
    assert 'PRIVATE_' not in message and 'input_value' not in message
    assert calls == []


def test_sdk_keeps_correct_sparse_fields_when_resubmitted(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp, 'request', lambda suffix, payload: calls.append((suffix, payload)) or {'status': 'pending_fact_review'})
    draft = {'reviews': [{'instrument_id': 'xlk', 'summary': '修正已有研究', 'change_kind': 'knowledge',
        'themes': [{'theme_id': 'known-theme', 'theme_key': 'known'}],
        'research': {'investment_view': {'direction': '精简判断', 'coverage_status': 'limited'},
            'questions': [{'key': 'existing-question', 'theme_id': 'known-theme', 'question': '原问题',
                'assessment': '已有证据纠正', 'next_check': '原观察条件'}]}}]}
    result = asyncio.run(mcp.mcp.call_tool('submit_research_review', {'result': draft}))
    assert json.loads(result.content[0].text) == {'status': 'pending_fact_review'}
    assert len(calls) == 1 and calls[0][0] == 'sector-draft'
    saved = calls[0][1]['reviews'][0]
    assert saved['themes'] == draft['reviews'][0]['themes']
    assert set(saved['research']) == {'investment_view', 'questions'}
    assert saved['research']['investment_view'] == draft['reviews'][0]['research']['investment_view']
    assert saved['research']['questions'] == draft['reviews'][0]['research']['questions']


def test_sdk_preserves_advertised_submission_schema():
    original = MCPServer('Original research schema')
    @original.tool()
    def submit(result: mcp.ReviewResult) -> dict:
        return {}

    tool = next(t for t in asyncio.run(mcp.mcp.list_tools()) if t.name == 'submit_research_review')
    baseline = asyncio.run(original.list_tools())[0].input_schema
    assert tool.input_schema['properties']['result'] == baseline['$defs']['ReviewResult']
    assert tool.input_schema['$defs'] == {key: value for key, value in baseline['$defs'].items() if key != 'ReviewResult'}
    assert tool.input_schema['required'] == baseline['required']
