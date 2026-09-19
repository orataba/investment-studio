from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import sys

import pytest
from sqlalchemy import text

from portfolio_app.db.models import (
    PortfolioDailyHoldingSnapshotModel, PortfolioDailySnapshotModel,
    PortfolioRecordModel, TaxonomyConfigurationRevisionModel,
)
from portfolio_app.db.session import get_session_factory
from .test_postgres_instrument_registry_constraints import postgres_portfolio_env


pytestmark = pytest.mark.postgresql_integration


def test_planning_audit_reads_current_revision_against_latest_complete_holdings(postgres_portfolio_env, monkeypatch):
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
                instrument_id=postgres_portfolio_env['instrument_id'], currency='USD', quantity=1,
                market_value_base=100, holding_json={'valuation_basis': 'market_quote'}, calculated_at='fixture'))
        configuration = {'taxonomy': {'status': 'active', 'planning_enabled': True},
            'taxonomy_nodes': [{'taxonomy_node_id': 'node', 'status': 'active'}],
            'taxonomy_assignments': [{'target_scope': 'instrument',
                'target_entity_id': postgres_portfolio_env['instrument_id'],
                'taxonomy_node_id': 'node', 'status': 'active'}]}
        previous = deepcopy(configuration)
        previous['taxonomy_assignments'] = []
        session.add(TaxonomyConfigurationRevisionModel(taxonomy_configuration_revision_id='previous',
            portfolio_id='planning-audit', taxonomy_id='planning', effective_from=yesterday,
            effective_to=yesterday, configuration_version=1, configuration_json=previous, created_at='fixture'))
        current = TaxonomyConfigurationRevisionModel(taxonomy_configuration_revision_id='current',
            portfolio_id='planning-audit', taxonomy_id='planning', effective_from=today,
            configuration_version=2, configuration_json=configuration, created_at='fixture')
        session.add(current)
        session.flush()
        count = lambda: session.scalar(text(audit._current_unassigned_planning_holdings_query().replace(
            'CURRENT_TIMESTAMP', "TIMESTAMPTZ '2026-09-18 16:30:00+00'")))
        # Yesterday's holding is assigned by today's revision; neither live
        # tables nor today's newer unavailable snapshot may replace these facts.
        assert count() == 0
        monkeypatch.setenv('INVESTMENT_STUDIO_PORTFOLIO_DEFAULT_TRADE_TIMEZONE', 'America/New_York')
        assert count() == 1  # Same instant/valuation, but today's planning revision is not effective there yet.
        monkeypatch.setenv('INVESTMENT_STUDIO_PORTFOLIO_DEFAULT_TRADE_TIMEZONE', 'Asia/Shanghai')
        assert count() == 0
        current.effective_from = today + timedelta(days=1)
        previous_record = session.get(TaxonomyConfigurationRevisionModel, 'previous')
        previous_record.effective_to = today
        session.flush()
        assert count() == 1  # A future assignment does not repair current coverage.
        current.effective_from = today
        previous_record.effective_to = yesterday
        inactive_node = deepcopy(configuration)
        inactive_node['taxonomy_nodes'][0]['status'] = 'inactive'
        current.configuration_json = inactive_node
        session.flush()
        assert count() == 1  # A dangling/inactive assignment is not solver coverage.
        current.configuration_json = configuration
        session.flush()
        assert count() == 0
