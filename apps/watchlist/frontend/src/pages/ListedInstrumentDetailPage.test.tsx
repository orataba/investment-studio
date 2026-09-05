// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import ListedInstrumentDetailPage from './ListedInstrumentDetailPage'

const apiMocks = vi.hoisted(() => ({
  getInstrumentAttributes: vi.fn(),
  getInstrumentChart: vi.fn(),
  getInstrumentMonitoring: vi.fn(),
  getInstrumentPerformance: vi.fn(),
  getInstrumentPriceBars: vi.fn(),
  getInstrumentReferenceData: vi.fn(),
  getInstrumentResearch: vi.fn(),
  getInstrumentRisk: vi.fn(),
  getInstrumentSummary: vi.fn(),
}))

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  getInstrumentAttributes: apiMocks.getInstrumentAttributes,
  getInstrumentChart: apiMocks.getInstrumentChart,
  getInstrumentMonitoring: apiMocks.getInstrumentMonitoring,
  getInstrumentPerformance: apiMocks.getInstrumentPerformance,
  getInstrumentPriceBars: apiMocks.getInstrumentPriceBars,
  getInstrumentReferenceData: apiMocks.getInstrumentReferenceData,
  getInstrumentResearch: apiMocks.getInstrumentResearch,
  getInstrumentRisk: apiMocks.getInstrumentRisk,
  getInstrumentSummary: apiMocks.getInstrumentSummary,
}))

const taxonomy = {
  taxonomy_code: 'instrument_taxonomy',
  assigned_node_id: null,
  assigned_label: null,
  path_labels: ['Fixed Income', 'Broad Bond'],
  path_node_ids: [],
  depth: 0,
  derived_values: {},
}

const chartPoints = [
  { date: '2025-12-31', value: 100 },
  { date: '2026-01-30', value: 101 },
  { date: '2026-02-27', value: 100.5 },
  { date: '2026-03-31', value: 103 },
  { date: '2026-04-30', value: 104 },
  { date: '2026-05-29', value: 105 },
  { date: '2026-06-30', value: 106 },
  { date: '2026-07-31', value: 107 },
  { date: '2026-08-28', value: 108 },
]

beforeEach(() => {
  apiMocks.getInstrumentPriceBars.mockResolvedValue({
    instrument_id: 'h11001-csi',
    instrument_type: 'index',
    currency: 'CNY',
    adjustment_mode: 'raw',
    factor_coverage: 0,
    source_refresh_status: 'no_new_data',
    source_refresh_message: 'No changed close rows were returned.',
    count: 0,
    bars: [],
  })
  apiMocks.getInstrumentSummary.mockResolvedValue({
    instrument_id: 'h11001-csi',
    instrument_name: '中证全债',
    ticker_or_isin: 'H11001.CSI',
    management_firm_name: null,
    instrument_attributes: {},
    taxonomy,
    key_stats: [],
    freshness: {
      data_freshness_status: 'fresh',
      last_fact_update_at: '2026-08-31T13:00:00Z',
      last_recalculated_at: null,
      last_successful_snapshot_at: null,
      staleness_reason: null,
    },
    quick_monitoring_items: [],
    tabs: [],
    series_snapshot: { return_kind: 'total_return' },
  })
  apiMocks.getInstrumentChart.mockResolvedValue({
    instrument_id: 'h11001-csi',
    base_series_type: 'index_close',
    selected_series: {
      label: 'Total Return Index',
      return_kind: 'total_return',
    },
    currency: 'CNY',
    date_range: { start: '2025-12-31', end: '2026-08-28' },
    series: [{ name: '中证全债', points: chartPoints }],
    available_compare_targets: [],
  })
  apiMocks.getInstrumentPerformance.mockResolvedValue({
    growth_chart_series: [],
    annual_returns: [],
    trailing_returns: [],
    ranking: null,
    peer_comparison: null,
    calculation_frequency_profile: null,
    snapshot_metadata: null,
  })
  apiMocks.getInstrumentRisk.mockResolvedValue({
    risk_overview: null,
    scatter_points: [],
    risk_metrics: [],
    current_drawdown: null,
    drawdown_summary: null,
    data_quality: null,
    calculation_frequency_profile: null,
    snapshot_metadata: null,
  })
  apiMocks.getInstrumentResearch.mockResolvedValue({
    profile: {
      thesis: '', current_view: '', why_now: '', edge_assessment: '', valuation_framework: '',
      catalysts: '', key_risks: '', disconfirming_evidence: '', open_questions: '',
      monitoring_plan: '', people_assessment: '', portfolio_role: '', time_horizon: '',
      decision_rationale: '', primary_analyst: '', next_review_date: null, dd_status: '',
      odd_status: '', ic_status: '', manual_rating: null, created_at: null, updated_at: null,
      updated_by: null, revision_number: 0,
    },
    notes: [],
  })
  apiMocks.getInstrumentMonitoring.mockRejectedValue(new Error('Not on a watchlist'))
  apiMocks.getInstrumentAttributes.mockResolvedValue({
    instrument_id: 'h11001-csi',
    definitions: [],
    values: {},
    taxonomy,
  })
  apiMocks.getInstrumentReferenceData.mockReturnValue(new Promise(() => {}))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ListedInstrumentDetailPage index view', () => {
  it('keeps operational market data off the index overview without waiting for provider reference data', async () => {
    apiMocks.getInstrumentPriceBars.mockResolvedValueOnce({
      instrument_id: 'h11001-csi',
      instrument_type: 'index',
      currency: 'CNY',
      adjustment_mode: 'raw',
      factor_coverage: 0,
      source_refresh_status: 'no_new_data',
      source_refresh_message: 'No changed close rows were returned.',
      count: 1,
      bars: [{
        date: '2026-08-28',
        open: '999',
        high: '999',
        low: '999',
        close: '999',
        previous_close: '998',
        volume: null,
        turnover: null,
        adjustment_factor: null,
        currency: 'CNY',
        volume_unit: null,
        turnover_unit: null,
        provider: 'test',
        status: 'complete',
      }],
    })
    render(
      <LanguageProvider enableDomTranslation={false}>
        <MemoryRouter>
          <ListedInstrumentDetailPage
            instrument={{
              requested_instrument_id: 'h11001-csi',
              canonical_instrument_id: 'h11001-csi',
              instrument_name: '中证全债',
              instrument_type: 'index',
              primary_identifier: 'H11001.CSI',
              detail_view_type: 'index',
              detail_subject_id: 'h11001-csi',
              detail_supported: true,
              support_reason: '',
              corporate_actions: [],
            }}
            watchlistContext={null}
          />
        </MemoryRouter>
      </LanguageProvider>,
    )

    await waitFor(() => expect(screen.queryAllByText('Index Level').length).toBeGreaterThan(0))
    expect(screen.queryByText('Index Data')).toBeNull()
    expect(screen.queryByText('Price & Volume')).toBeNull()
    expect(screen.queryByText('OHLCV pending')).toBeNull()
    expect(screen.queryAllByText('Total Return Index').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('108.00').length).toBeGreaterThan(0)
    expect(screen.queryByText('999.00')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Details|资料与明细/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Monitoring' }))
    await waitFor(() => expect(screen.queryByText('Market Data Status')).not.toBeNull())

    fireEvent.click(screen.getByRole('button', { name: 'Performance' }))

    expect(screen.queryByText('Metrics Matrix')).not.toBeNull()
    expect(screen.queryByText('Monthly Return Matrix')).not.toBeNull()
    expect(screen.queryByText('Growth of Price')).toBeNull()
  })
})
