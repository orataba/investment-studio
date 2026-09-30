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


def test_sdk_advertises_the_same_strict_models_as_runtime_without_changing_storage_schema():
    from copy import deepcopy
    original = MCPServer('Original research schema')
    @original.tool()
    def submit(result: mcp.ReviewResult) -> dict:
        return {}

    tool = next(t for t in asyncio.run(mcp.mcp.list_tools()) if t.name == 'submit_research_review')
    baseline = deepcopy(asyncio.run(original.list_tools())[0].input_schema)
    # Only model extra-field policy differs; all financial fields, required
    # values, limits, enums and reference definitions remain the same.
    for model in baseline['$defs'].values():
        if model.get('type') == 'object':
            model['additionalProperties'] = False
    assert tool.input_schema['properties']['result'] == baseline['$defs']['ReviewResult']
    assert tool.input_schema['$defs'] == {key: value for key, value in baseline['$defs'].items() if key != 'ReviewResult'}
    assert tool.input_schema['required'] == baseline['required']
    assert 'additionalProperties' not in mcp.ReviewResult.model_json_schema()
    tool.input_schema['$defs']['SectorReview']['properties'].clear()
    fresh = next(t for t in asyncio.run(mcp.mcp.list_tools()) if t.name == 'submit_research_review')
    assert 'instrument_id' in fresh.input_schema['$defs']['SectorReview']['properties']


def test_strict_submission_schema_preserves_free_dictionary_keys():
    from pydantic import BaseModel, ValidationError
    from jsonschema import Draft202012Validator
    class Nested(BaseModel):
        value: str
    class Proposal(BaseModel):
        metadata: dict[str, str]
        raw: dict
        nested: Nested
    schema = Proposal.model_json_schema(schema_generator=mcp._SubmissionSchema)
    validator = Draft202012Validator(schema)
    payload = {'metadata': {'arbitrary-source-key': 'kept'}, 'raw': {'arbitrary': {'nested': 1}}, 'nested': {'value': 'kept'}}
    assert not list(validator.iter_errors(payload))
    assert Proposal.model_validate(payload, extra='forbid').raw == payload['raw']
    payload['nested']['not-a-model-field'] = 'rejected'
    assert list(validator.iter_errors(payload))
    with pytest.raises(ValidationError):
        Proposal.model_validate(payload, extra='forbid')


def test_actual_event_reflection_and_brief_shape_errors_return_together_without_saving(monkeypatch):
    from copy import deepcopy
    from jsonschema import Draft202012Validator
    event = {'event_key': 'development', 'action': 'new', 'direction': 'uncertain', 'title': 'Development',
        'body': 'PRIVATE_EVENT', 'confidence': 'confirmed', 'information_type': 'fact', 'recording_type': 'new',
        'source_ids': ['original'], 'market_reaction': {'status': 'unavailable', 'source_ids': ['PRIVATE_WRONG_SOURCE']}}
    reflection = {'status': 'reviewed', 'summary': 'PRIVATE_REFLECTION', 'reviewed_update_ids': [], 'source_ids': ['original']}
    brief = {'recommendation': 'PRIVATE_BRIEF', 'rationale': 'PRIVATE_RATIONALE'}
    payload = {'reviews': [{'instrument_id': 'xlk', 'events': [event],
        'decision_brief': brief, 'research': {'reflection': reflection}}]}
    calls = []
    monkeypatch.setattr(mcp, 'request', lambda suffix, value: calls.append((suffix, value)) or {'status': 'pending_fact_review'})
    wire = next(t for t in asyncio.run(mcp.mcp.list_tools()) if t.name == 'submit_research_review').input_schema
    assert len(list(Draft202012Validator(wire).iter_errors({'result': payload}))) == 3
    with pytest.raises(ToolError) as raised:
        asyncio.run(mcp.mcp.call_tool('submit_research_review', {'result': payload}))
    message = str(raised.value)
    diagnostic = json.loads(message[message.index('{'):])
    issues = {tuple(item['loc']): item for item in diagnostic['issues']}
    reaction = ('result', 'reviews', 0, 'events', 0, 'market_reaction', 'source_ids')
    reflection_path = ('result', 'reviews', 0, 'research', 'reflection')
    brief_path = ('result', 'reviews', 0, 'decision_brief')
    assert set(issues) == {reaction, reflection_path, brief_path}
    assert issues[reaction]['parent_fields'] == ['status', 'figure_source_ids', 'explanation']
    assert issues[reaction]['parent_required_fields'] == ['status']
    assert 'decision_brief' in issues[reflection_path]['parent_fields']
    assert 'reflection' not in issues[reflection_path]['parent_fields']
    assert 'reflection' in issues[brief_path]['parent_fields']
    assert 'decision_brief' not in issues[brief_path]['parent_fields']
    assert diagnostic['field_levels']['result.reviews[].research.decision_brief'][:2] == ['recommendation', 'rationale']
    assert calls == [] and 'PRIVATE_' not in message
    corrected = deepcopy(payload)
    row = corrected['reviews'][0]
    row['events'][0]['market_reaction'] = {'status': 'unavailable', 'figure_source_ids': [], 'explanation': 'No retained price reaction'}
    row['reflection'] = row['research'].pop('reflection')
    row['research']['decision_brief'] = row.pop('decision_brief')
    assert not list(Draft202012Validator(wire).iter_errors({'result': corrected}))
    asyncio.run(mcp.mcp.call_tool('submit_research_review', {'result': corrected}))
    assert len(calls) == 1 and calls[0][0] == 'sector-draft'
    assert calls[0][1]['reviews'][0]['research']['decision_brief'] == brief
    assert calls[0][1]['reviews'][0]['reflection'] == reflection
