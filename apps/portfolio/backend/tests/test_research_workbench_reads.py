from datetime import date

from portfolio_app.services import ledger, performance, research, research_solver, valuation_fx
from .test_research_api import _create_planning_taxonomy


def test_research_context_batches_security_details_and_shares_one_fx_read(client, monkeypatch):
    taxonomy_id, _ = _create_planning_taxonomy(client)
    research._run_portfolio_daily_snapshot_recalculation_synchronously('investment-studio')
    fx_calls, detail_batches = [], []
    load_fx = research.get_shared_fx_rates
    load_details = research.get_registry_instrument_details

    def fx():
        fx_calls.append(True)
        return load_fx()

    def details(ids):
        detail_batches.append(set(ids))
        return load_details(ids)

    def redundant_fx():
        raise AssertionError('Context financial views must reuse the supplied FX map.')

    monkeypatch.setattr(research, 'get_shared_fx_rates', fx)
    monkeypatch.setattr(research, 'get_registry_instrument_details', details)
    monkeypatch.setattr(performance, 'get_shared_fx_rates', redundant_fx)
    monkeypatch.setattr(ledger, 'get_shared_fx_rates', redundant_fx)
    context = research._build_research_context('investment-studio', planning_taxonomy_id=taxonomy_id,
        as_of_date=date(2026, 4, 15), lookback_days=30)
    assert context['as_of_date'] == '2026-04-15'
    assert len(fx_calls) == 1
    assert len(detail_batches) == 1
    transaction_ids = {row['instrument_id'] for row in research.list_transactions('investment-studio')
                       if row.get('instrument_id')}
    assert transaction_ids <= detail_batches[0]


def test_research_scope_and_daily_frequency_metadata_never_convert_fx(client, monkeypatch):
    taxonomy_id, _ = _create_planning_taxonomy(client)

    def no_fx(*args, **kwargs):
        raise AssertionError('Scope and daily source-frequency metadata do not value or convert assets.')

    monkeypatch.setattr(research_solver, 'get_shared_fx_rates', no_fx)
    monkeypatch.setattr(valuation_fx, 'convert_amount_on', no_fx)
    monkeypatch.setattr(valuation_fx, 'resolve_fx_rate_on', no_fx)
    options = research_solver.build_research_scope_options('investment-studio',
        planning_taxonomy_id=taxonomy_id, as_of_date=date(2026, 4, 15))
    frequency = research_solver.build_research_calculation_frequency_profile('investment-studio',
        planning_taxonomy_id=taxonomy_id, comparator_taxonomy_node_id=None,
        as_of_date=date(2026, 4, 15), lookback_days=30)
    assert len(options) > 1
    assert frequency['resolved_frequency'] == 'daily'
    assert frequency['source_frequency_counts']['daily'] > 0
