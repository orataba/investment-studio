"""The risk list projects on PostgreSQL without transferring retained originals."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from watchlist_app.api.routes.workbench import risk_workspace, risk_case_detail
from watchlist_app.db.models import InstrumentDetail, InstrumentChartReadModel, InstrumentRiskReadModel
from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.db.session import get_engine, get_session_factory
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env
from .test_price_risk import series

pytestmark = pytest.mark.postgresql_integration


def test_home_alerts_project_large_nul_evidence_without_reading_private_run(postgres_watchlist_env):
    from studio_identity import Principal, principal_context
    from watchlist_app.services.risk_alerts import important_risk_alerts
    from .test_risk_alerts import seed_alerts
    with get_session_factory()() as session:
        seed_alerts(session, postgres_watchlist_env["instrument_id"])
    def deserialize(value):
        value = value.decode("utf-8") if isinstance(value, bytes) else value
        assert "UNSELECTED" not in value and "DO_NOT_READ_PRIVATE" not in value
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    try:
        with principal_context(Principal("reader", "Reader", "default")), Session(engine) as session:
            result = important_risk_alerts(session, limit=3, offset=1)
            assert result["total"] == 4 and not result["has_more"]
            assert [row["case_id"] for row in result["alerts"]] == ["urgent", "major", "opportunity-active"]
            assert result["alerts"][0]["as_of"] == "2026-10-11T21:00:00Z"
            assert not session.identity_map
    finally:
        engine.dispose()


def test_review_status_reads_projected_report_without_failed_run_evidence(postgres_watchlist_env, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from watchlist_app.services import risk_officer
    from watchlist_app.services.risk_review_state import current_scope, input_version
    iid = postgres_watchlist_env['instrument_id']
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type='public_fund', detail_view_type='public_fund',
            instrument_name='Retained review', metadata_json={}))
        session.add(ResearchTopic(topic_id=f'risk-officer:instrument:{iid}', title='Risk', visibility='team', instrument_ids=[iid]))
        session.commit()
        version = input_version(session, current_scope(session, {'instrument_id': iid}))
        session.add(ResearchEntry(entry_id='review-completed', topic_id=f'risk-officer:instrument:{iid}',
            kind='analysis', title='Review', status='completed', created_at=datetime.now(UTC) - timedelta(days=1),
            completed_at=datetime.now(UTC) - timedelta(days=1), context_json={
                'risk_run': True, 'huge_original': 'unselected' * 100000,
                'result': {'summary': '原研判', 'priorities': [], 'limitations': []},
                'risk_input_version': version,
                'risk_review_view': {'input_as_of': '2026-09-25', 'evidence_sources': {'original': {'title': '原来源'}}}}))
        session.add(ResearchEntry(entry_id='review-failed', topic_id=f'risk-officer:instrument:{iid}',
            kind='analysis', title='Failed', status='failed', body='保留失败状态',
            context_json={'risk_run': True, 'huge_original': 'unselected\x00' * 100000}))
        session.commit()
    loaded_full = []
    def deserialize(value):
        result = json.loads(value)
        if isinstance(result, dict) and 'huge_original' in result:
            loaded_full.append(result)
        return result
    monkeypatch.setattr(risk_officer, 'read_snapshot', lambda *a, **k: pytest.fail('GET rebuilt model input'))
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={'options': '-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on'})
    try:
        with Session(engine) as session:
            review = risk_officer.review_workspace(session, instrument_id=iid)
            assert review['latest_completed']['summary'] == '原研判'
            assert review['latest_completed']['stale'] is False
            assert review['latest_completed']['evidence_sources'] == {'original': {'title': '原来源'}}
            assert review['latest_run']['status'] == 'failed'
            assert review['latest_run']['message'] == '保留失败状态'
            assert not loaded_full
    finally:
        engine.dispose()


def test_risk_summary_projects_evidence_and_preserves_nul_and_current_authority(postgres_watchlist_env):
    iid = postgres_watchlist_env['instrument_id']
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type='public_fund', detail_view_type='public_fund',
            instrument_name='Risk projection test', metadata_json={'huge_original': 'unselected' * 10000}))
        session.flush()
        session.add(InstrumentChartReadModel(instrument_id=iid, data_freshness_status='fresh',
            payload_json={'research_returns': series([100, 90, 80]), 'huge_original': 'unselected' * 10000 + '\x00'}))
        session.add(InstrumentRiskReadModel(instrument_id=iid, data_freshness_status='fresh',
            payload_json={'current_drawdown': -20, 'data_quality': {'status': 'ready'}, 'huge_original': 'unselected' * 10000 + '\x00'}))
        evidence = {'direction': 'opportunity', 'risk_assessment': {'status': 'pending'}, 'importance': 'high',
            'next_watch': 'exact\x00condition', 'sources': [{'text': 'retained\x00original' * 10000}]}
        history = [{'at': '2026-09-25T08:00:00Z', 'action': 'review', 'detail': 'PM跟进', 'snapshot': deepcopy(evidence)}]
        session.add(RiskCase(case_id='projected-risk', instrument_id=iid, signal='sector:current', title='Current risk',
            body='完整当前判断', status='investigating', trigger_active=True, evidence_json=evidence, history_json=history))
        session.commit()

    loaded_full = []
    def deserialize(value):
        result = json.loads(value)
        if isinstance(result, dict) and 'huge_original' in result:
            loaded_full.append(result)
        return result
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={'options': '-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on'})
    try:
        with Session(engine) as session:
            result = risk_workspace(instrument_id=iid, summary=True, session=session)
            case = result['cases'][0]
            assert case['trigger_active'] and case['status'] == 'investigating'
            assert case['evidence_json']['next_watch'] == evidence['next_watch']
            assert case['evidence_json']['risk_assessment'] == {'status': 'pending'}
            assert case['evidence_json']['event_version_id'] == 'projected-risk:1'
            assert case['history_count'] == 1 and 'history_json' not in case
            assert result['instruments'][0]['drawdown_change_pp'] == pytest.approx(-10)
            assert not loaded_full and not session.identity_map
            detail = risk_case_detail('projected-risk', updated_at=case['updated_at'], session=session)
            assert detail['history_json'] == history
            assert detail['evidence_json']['sources'] == evidence['sources']
    finally:
        engine.dispose()


def test_postgres_home_keeps_assessed_version_while_latest_revision_is_pending(postgres_watchlist_env):
    from studio_identity import Principal, principal_context
    from watchlist_app.services.risk_alerts import important_risk_alerts
    from watchlist_app.services.risk_case_state import advance_event_risk
    from .test_risk_alerts import seed_alerts
    iid = postgres_watchlist_env["instrument_id"]
    with get_session_factory()() as session:
        seed_alerts(session, iid)
        case = session.get(RiskCase, "major")
        snapshot = {**case.evidence_json, "title": "尚未核实的新正文", "event_version_id": "new-version"}
        case.evidence_json = {**snapshot, **advance_event_risk(case, snapshot, material_change=True,
            sources=[{"source_type": "public_source"}])}
        case.title = snapshot["title"]
        session.commit()
    def deserialize(value):
        value = value.decode("utf-8") if isinstance(value, bytes) else value
        assert "UNSELECTED" not in value and "DO_NOT_READ_PRIVATE" not in value
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    try:
        with principal_context(Principal("reader", "Reader", "default")), Session(engine) as session:
            result = important_risk_alerts(session, limit=10)
            alert = next(row for row in result["alerts"] if row["case_id"] == "major")
            assert alert["title"] == "major" and alert["event_version_id"] == "current-version"
            assert alert["alert_state"] == "assessment_pending"
            assert alert["pending_revision"]["event_version_id"] == "new-version"
            assert not session.identity_map
    finally:
        engine.dispose()
