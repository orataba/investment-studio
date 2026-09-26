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
