from datetime import date, timedelta
import importlib.util
from pathlib import Path
import sys

import pytest
from sqlalchemy import text

from portfolio_app.db.models import (
    PortfolioDailyHoldingSnapshotModel, PortfolioDailySnapshotModel,
    PortfolioRecordModel, TaxonomyRecordModel, TaxonomyNodeRecordModel, TaxonomyAssignmentRecordModel,
)
from portfolio_app.db.session import get_session_factory
from .test_postgres_instrument_registry_constraints import postgres_portfolio_env


pytestmark = pytest.mark.postgresql_integration


def test_planning_audit_reads_current_assignments_against_latest_complete_holdings(postgres_portfolio_env, monkeypatch):
    path = Path(__file__).resolve().parents[4] / 'infra/scripts/audit_live_data.py'
    spec = importlib.util.spec_from_file_location('planning_audit_test', path)
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit
    spec.loader.exec_module(audit)
    monkeypatch.setenv('INVESTMENT_STUDIO_PORTFOLIO_DEFAULT_TRADE_TIMEZONE', 'Asia/Shanghai')
    with get_session_factory()() as session:
        today = date(2026, 9, 19)
        yesterday = today - timedelta(days=1)
        session.add(PortfolioRecordModel(portfolio_id='planning-audit', portfolio_name='Planning audit',
            base_currency='USD', valuation_timezone='Asia/Shanghai', valuation_cutoff_policy='close',
            inception_date=yesterday, as_of_date=yesterday))
        session.flush()
        for day, coverage in [(yesterday, 'complete'), (today, 'unavailable')]:
            session.add(PortfolioDailySnapshotModel(portfolio_id='planning-audit', as_of_date=day,
                coverage_state=coverage, valuation_coverage_state=coverage,
                nav=100 if coverage == 'complete' else None, snapshot_json={}, calculated_at='fixture'))
            session.add(PortfolioDailyHoldingSnapshotModel(portfolio_id='planning-audit', as_of_date=day,
                account_id='broker', position_reference_id='fixture', holding_kind='position',
                instrument_id=postgres_portfolio_env['instrument_id'], currency='USD', quantity=1 if day == yesterday else 0,
                market_value_base=100, holding_json={'valuation_basis': 'market_quote'}, calculated_at='fixture'))
        session.add(TaxonomyRecordModel(taxonomy_id='planning', portfolio_id='planning-audit',
            name='Planning', taxonomy_type='custom', primary_assignment_scope='instrument'))
        session.flush()
        node = TaxonomyNodeRecordModel(taxonomy_node_id='node', taxonomy_id='planning', node_name='Current')
        assignment = TaxonomyAssignmentRecordModel(assignment_id='assignment', taxonomy_id='planning',
            target_scope='instrument', target_entity_id=postgres_portfolio_env['instrument_id'], taxonomy_node_id='node')
        session.add_all([node, assignment])
        session.flush()
        count = lambda: session.scalar(text(audit._current_unassigned_planning_holdings_query().replace(
            'CURRENT_TIMESTAMP', "TIMESTAMPTZ '2026-09-18 16:30:00+00'")))
        assert count() == 0  # Current membership also classifies yesterday's holdings.
        monkeypatch.setenv('INVESTMENT_STUDIO_PORTFOLIO_DEFAULT_TRADE_TIMEZONE', 'America/New_York')
        assert count() == 0  # A timezone change cannot select another target configuration.
        assignment.status = 'inactive'
        session.flush()
        assert count() == 1  # Ignore the newer incomplete valuation with zero quantity.
        assignment.status = 'active'
        node.status = 'inactive'
        session.flush()
        assert count() == 1  # A dangling/inactive assignment is not solver coverage.
        node.status = 'active'
        session.flush()
        assert count() == 0
