// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
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

vi.mock('../components/InstrumentRiskPanel', () => ({ default: ({ mode, instrumentId, onAskAssistant }: { mode?: string; instrumentId?: string; onAskAssistant?: (id: string, question: string) => void }) => <div data-testid="risk-panel" data-mode={mode} data-instrument={instrumentId}><button onClick={() => onAskAssistant?.(instrumentId!, '分析这项风险')}>追问当前风险</button></div> }))
vi.mock('../components/SectorResearchPanel', () => ({ default: ({ instrumentId, variant = 'timeline', readingMode = false }: { instrumentId: string; variant?: string; readingMode?: boolean }) => <div data-testid="sector-panel" data-reading={String(readingMode)} data-instrument={instrumentId} data-variant={variant} /> }))
vi.mock('../components/EstimateHistoryPanel', () => ({ default: () => <div data-testid="estimate-history" /> }))
// Exercise the real drawer's URL cleanup while replacing the conversational UI only.
vi.mock('./ResearchPage', () => ({ default: ({ instrumentId, watchlistId, onClose }: { instrumentId: string; watchlistId?: string; onClose: () => void }) =>
  <div role="dialog" aria-label="研究助手" data-instrument={instrumentId} data-watchlist={watchlistId}><button onClick={onClose}>关闭研究助手</button></div> }))

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
  window.history.replaceState({}, '', '?lang=en')
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
  it.each([false, true])('validates crypto risk within each window instead of inheriting an older gap (recent gap: %s)', async (recentGap) => {
    const recent = Array.from({ length: 62 }, (_, index) => ({
      date: new Date(Date.UTC(2026, 6, 1 + index)).toISOString().slice(0, 10),
      value: 100 + index + (index % 2 ? -2 : 0),
    })).filter((_, index) => !recentGap || index !== 58)
    const points = [{ date: '2025-01-01', value: 50 }, ...recent]
    apiMocks.getInstrumentChart.mockResolvedValueOnce({
      instrument_id: 'btcusd', currency: 'USD', base_series_type: 'price_close',
      selected_series: { label: 'Spot Price', return_kind: 'price_return' },
      date_range: { start: points[0].date, end: points[points.length - 1].date },
      series: [{ name: 'Bitcoin', points }], available_compare_targets: [],
    })
    apiMocks.getInstrumentRisk.mockResolvedValueOnce({
      data_quality: { status: 'partial_missing_observations', gap_count: recentGap ? 2 : 1 },
      risk_metrics: [], current_drawdown: null, calculation_frequency_profile: null,
    })
    const { container } = render(<LanguageProvider enableDomTranslation={false}><MemoryRouter>
      <ListedInstrumentDetailPage instrument={{
        requested_instrument_id: 'btcusd', canonical_instrument_id: 'btcusd',
        instrument_name: 'Bitcoin', instrument_type: 'crypto', primary_identifier: 'BTCUSD',
        detail_view_type: 'crypto', detail_subject_id: 'btcusd', detail_supported: true,
        support_reason: '', corporate_actions: [],
      }} watchlistContext={null} />
    </MemoryRouter></LanguageProvider>)
    await screen.findByRole('img', { name: 'Crypto spot price chart' })
    fireEvent.click(screen.getAllByRole('button', { name: /^Performance & Risk$/ })[0])
    const table = container.querySelector('.instrument-metrics-table') as HTMLElement
    for (const label of ['Ann. Volatility', 'Sharpe Ratio', 'Sortino Ratio', 'Max DD']) {
      const cells = within(table).getByText(label).closest('tr')!.querySelectorAll('td strong')
      // 1M is complete only without the recent missing UTC day; SI has an old
      // gap in both cases. No forward fill or lower-frequency substitute.
      expect(cells[1].textContent === '—').toBe(recentGap)
      expect(cells[cells.length - 1].textContent).toBe('—')
    }
    const returns = within(table).getByText('Period Return').closest('tr')!.querySelectorAll('td strong')
    expect(returns[1].textContent).not.toBe('—')
  })

  it('keeps index endpoint returns but withholds matrix path risk when canonical sessions are missing', async () => {
    apiMocks.getInstrumentRisk.mockResolvedValueOnce({
      data_quality: { status: 'partial_missing_observations', gap_count: 1 },
      risk_metrics: [], current_drawdown: null, calculation_frequency_profile: null,
    })
    const { container } = render(<LanguageProvider enableDomTranslation={false}><MemoryRouter>
      <ListedInstrumentDetailPage instrument={{
        requested_instrument_id: 'h11001-csi', canonical_instrument_id: 'h11001-csi',
        instrument_name: '中证全债', instrument_type: 'index', primary_identifier: 'H11001.CSI',
        detail_view_type: 'index', detail_subject_id: 'h11001-csi', detail_supported: true,
        support_reason: '', corporate_actions: [],
      }} watchlistContext={null} />
    </MemoryRouter></LanguageProvider>)
    await screen.findByRole('img', { name: 'Index level chart' })
    fireEvent.click(screen.getAllByRole('button', { name: /^Performance & Risk$/ })[0])
    const table = container.querySelector('.instrument-metrics-table') as HTMLElement
    for (const label of ['Ann. Volatility', 'Sharpe Ratio', 'Sortino Ratio', 'Calmar Ratio', 'Max DD']) {
      const values = [...within(table).getByText(label).closest('tr')!.querySelectorAll('td strong')]
      expect(values.length).toBeGreaterThan(0)
      expect(values.every((node) => node.textContent === '—')).toBe(true)
    }
    const returns = within(table).getByText('Period Return').closest('tr')!
    expect([...returns.querySelectorAll('td strong')].some((node) => node.textContent !== '—')).toBe(true)
  })

  it('renders native crypto from completed daily spot prices without stock or ETF controls', async () => {
    apiMocks.getInstrumentPriceBars.mockRejectedValueOnce(new Error('Optional OHLCV is unavailable'))
    apiMocks.getInstrumentChart.mockResolvedValueOnce({
      instrument_id: 'btcusd', currency: 'USD', base_series_type: 'price_close',
      selected_series: { label: 'Spot Price', return_kind: 'price_return' },
      date_range: { start: chartPoints[0].date, end: chartPoints[chartPoints.length - 1].date },
      series: [{ name: 'Bitcoin', points: chartPoints }], available_compare_targets: [],
    })
    render(<LanguageProvider enableDomTranslation={false}><MemoryRouter>
      <ListedInstrumentDetailPage instrument={{
        requested_instrument_id: 'btcusd', canonical_instrument_id: 'btcusd',
        instrument_name: 'Bitcoin', instrument_type: 'crypto', primary_identifier: 'BTCUSD',
        detail_view_type: 'crypto', detail_subject_id: 'btcusd', detail_supported: true,
        support_reason: '', corporate_actions: [],
      }} watchlistContext={null} />
    </MemoryRouter></LanguageProvider>)
    await waitFor(() => expect(screen.getByRole('img', { name: 'Crypto spot price chart' })).toBeTruthy())
    expect(screen.getByText('Crypto Detail')).toBeTruthy()
    expect(screen.queryByLabelText('Price adjustment')).toBeNull()
    expect(screen.queryByText('Volume')).toBeNull()
    expect(screen.queryByTestId('estimate-history')).toBeNull()
    expect(screen.queryByText('Index Level')).toBeNull()
    expect(screen.getByText('USD')).toBeTruthy()
    expect(screen.queryByText('Optional OHLCV is unavailable')).toBeNull()
  })

  it.each(['index', 'crypto'] as const)('does not substitute raw bars when the %s canonical series is unavailable', async (kind) => {
    apiMocks.getInstrumentChart.mockRejectedValueOnce(new Error('Canonical series unavailable'))
    apiMocks.getInstrumentPriceBars.mockResolvedValueOnce({
      instrument_id: 'canonical-only', instrument_type: kind, currency: 'USD',
      adjustment_mode: 'raw', count: 2,
      bars: ['2026-09-07', '2026-09-08'].map((date) => ({
        date, open: '999', high: '999', low: '999', close: '999', previous_close: null,
        adjustment_factor: null, volume: null, turnover: null, currency: 'USD',
        volume_unit: null, turnover_unit: null, provider: 'raw-test', status: 'complete',
      })),
    })
    render(<LanguageProvider enableDomTranslation={false}><MemoryRouter>
      <ListedInstrumentDetailPage instrument={{
        requested_instrument_id: 'canonical-only', canonical_instrument_id: 'canonical-only',
        instrument_name: 'Canonical only', instrument_type: kind, primary_identifier: 'CANONICAL',
        detail_view_type: kind, detail_subject_id: 'canonical-only', detail_supported: true,
        support_reason: '', corporate_actions: [],
      }} watchlistContext={null} />
    </MemoryRouter></LanguageProvider>)
    await waitFor(() => expect(screen.getByText('Canonical series unavailable')).toBeTruthy())
    expect(screen.queryByText('999.00')).toBeNull()
    expect(screen.queryByRole('img', { name: /Crypto spot price chart|Index level chart/ })).toBeNull()
  })

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
    const basisHint = screen.getByRole('button', { name: /Index chart basis|指数图表口径/ })
    expect(basisHint.closest('.listed-chart-toolbar')).not.toBeNull()
    fireEvent.click(basisHint)
    expect(screen.getByRole('tooltip')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('button', { name: /Details|资料与明细/ })).toBeNull()
    expect(apiMocks.getInstrumentReferenceData).not.toHaveBeenCalled()
    expect(apiMocks.getInstrumentMonitoring).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /^Investment Research$|^投资研究$/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Cumulative Return|累计收益/ }))
    expect(screen.queryByRole('img', { name: 'Cumulative return chart' })).not.toBeNull()
    expect(screen.queryByRole('img', { name: 'Index level chart' })).toBeNull()
    fireEvent.click(screen.getAllByRole('button', { name: /^Performance & Risk$|^业绩与风险$/ })[0])

    expect(screen.queryByText('Metrics Matrix')).not.toBeNull()
    expect(screen.queryByText('Monthly Return Matrix')).not.toBeNull()
    expect(screen.queryByText('Growth of Price')).toBeNull()
    expect(screen.queryByRole('img', { name: 'Cumulative return chart' })).toBeNull()
    expect(screen.queryByRole('img', { name: 'Historical drawdown chart' })).not.toBeNull()
    expect(screen.getByTestId('risk-panel').getAttribute('data-mode')).toBe('price')
    fireEvent.click(screen.getByRole('button', { name: /^Research assistant$|^研究助手$/ }))
    const drawer = await screen.findByRole('dialog', { name: '研究助手' })
    expect(drawer.getAttribute('data-instrument')).toBe('h11001-csi')
    fireEvent.click(within(drawer).getByRole('button', { name: '关闭研究助手' }))
    expect(screen.getByTestId('risk-panel').getAttribute('data-mode')).toBe('price')
  })
  it('keeps an ETF price chart on overview and combines monthly returns with quantitative risk', async () => {
    apiMocks.getInstrumentReferenceData.mockResolvedValueOnce({ instrument_id: 'xlk', instrument_type: 'etf', provider: 'fmp', provider_symbol: 'XLK', fetched_at: '2026-09-06T13:00:00Z', source: {}, section_errors: {}, sections: { fund_info: { etfCompany: 'SPDR', expenseRatio: 0.08, holdingsCount: 73 }, holdings: [] } })
    apiMocks.getInstrumentPriceBars.mockResolvedValueOnce({
      instrument_id: 'xlk', instrument_type: 'etf', currency: 'USD', adjustment_mode: 'raw', count: chartPoints.length,
      bars: chartPoints.map((point) => ({ date: point.date, open: String(point.value), high: String(point.value), low: String(point.value),
        close: String(point.value), previous_close: null, adjustment_factor: '1', volume: '100', turnover: null,
        currency: 'USD', volume_unit: 'shares', turnover_unit: null, provider: 'test', status: 'complete' })),
    })
    render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><ListedInstrumentDetailPage instrument={{
      requested_instrument_id: 'xlk', canonical_instrument_id: 'xlk', instrument_name: 'Technology Select Sector SPDR',
      instrument_type: 'etf', primary_identifier: 'XLK', detail_view_type: 'etf', detail_subject_id: 'xlk',
      detail_supported: true, support_reason: '', corporate_actions: [],
    }} watchlistContext={null} /></MemoryRouter></LanguageProvider>)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Technology Select Sector SPDR' })).not.toBeNull())
    await waitFor(() => expect(screen.queryByRole('button', { name: /PM Views|经理观点/ })).not.toBeNull())
    expect(await screen.findByRole('region', { name: 'ETF profile and holdings' })).toBeTruthy()
    await screen.findByText('0.08%')
    expect(apiMocks.getInstrumentReferenceData).toHaveBeenCalledWith('xlk')
    expect(screen.queryByRole('button', { name: /Details|资料与明细/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /PM Views|经理观点/ }))
    expect(screen.queryByRole('button', { name: /Add view|新增观点/ })).not.toBeNull()
    expect(screen.queryByText('Current Investment View')).toBeNull()
    fireEvent.click(screen.getAllByRole('button', { name: /^Performance & Risk$|^业绩与风险$/ })[0])
    expect(screen.queryByText('Monthly Return Matrix')).not.toBeNull()
    expect(screen.queryByRole('img', { name: 'Historical drawdown chart' })).not.toBeNull()
    expect(screen.queryByRole('img', { name: 'Cumulative return chart' })).toBeNull()
    expect(screen.getByTestId('risk-panel').getAttribute('data-mode')).toBe('price')
  })

  it.each(['etf', 'equity', 'index'] as const)('routes the old %s analyst tab to prepared research and preserves its context when closing the assistant', async (instrumentType) => {
    const instrumentId = `${instrumentType}-1`
    const { container } = render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={[
      `/instruments/${instrumentId}?tab=analyst&currency=CNY&assistant=1&topic=old-topic&question=old-question&instruments=other`,
    ]}><ListedInstrumentDetailPage instrument={{
      requested_instrument_id: instrumentId, canonical_instrument_id: instrumentId, instrument_name: 'Test security',
      instrument_type: instrumentType, primary_identifier: 'TEST', detail_view_type: instrumentType, detail_subject_id: instrumentId,
      detail_supported: true, support_reason: '', corporate_actions: [],
    }} watchlistContext={{ watchlistId: 'listed-list', watchlistName: '主要观察列表' }} /><LocationProbe /></MemoryRouter></LanguageProvider>)
    const tracking = await screen.findByTestId('sector-panel')
    expect(tracking.getAttribute('data-instrument')).toBe(instrumentId)
    expect(tracking.getAttribute('data-variant')).toBe('timeline')
    const tools = container.querySelector('.listed-detail-hero .workspace-context-tools') as HTMLElement
    expect(within(tools).getAllByRole('button').map((node) => node.getAttribute('aria-label'))).toEqual(['Instrument settings', 'Risk alerts', 'Research assistant'])
    expect(container.querySelector('.instrument-detail-topbar .language-switcher')).toBeTruthy()
    expect(container.querySelector('.instrument-detail-topbar .workspace-context-tools')).toBeNull()
    fireEvent.click(within(tools).getByRole('button', { name: 'Risk alerts' }))
    const riskDrawer = screen.getByRole('dialog', { name: '风险提示' })
    expect(within(riskDrawer).getByTestId('risk-panel').getAttribute('data-instrument')).toBe(instrumentId)
    fireEvent.click(within(riskDrawer).getByRole('button', { name: '追问当前风险' }))
    expect(screen.queryByRole('dialog', { name: '风险提示' })).toBeNull()
    expect((await screen.findByRole('dialog', { name: '研究助手' })).getAttribute('data-instrument')).toBe(instrumentId)
    fireEvent.click(screen.getByRole('button', { name: '关闭研究助手' }))
    const navigation = container.querySelector('.instrument-detail-tabs') as HTMLElement
    expect(within(navigation).getAllByRole('button').map((node) => node.textContent)).toEqual(['Overview', 'Investment Research', 'PM Views', 'Performance & Risk'])
    expect(within(navigation).getByRole('button', { name: 'Investment Research' }).classList.contains('instrument-detail-tab-active')).toBe(true)
    fireEvent.click(within(navigation).getByRole('button', { name: 'PM Views' }))
    await screen.findByRole('button', { name: 'Add view' })
    fireEvent.click(within(navigation).getByRole('button', { name: 'Investment Research' }))
    fireEvent.click(screen.getByRole('button', { name: 'Research assistant' }))
    const drawer = await screen.findByRole('dialog', { name: '研究助手' })
    expect(drawer.getAttribute('data-instrument')).toBe(instrumentId)
    expect(drawer.getAttribute('data-watchlist')).toBe('listed-list')
    fireEvent.click(within(drawer).getByRole('button', { name: '关闭研究助手' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '研究助手' })).toBeNull())
    const params = new URLSearchParams(screen.getByTestId('listed-detail-location').textContent || '')
    expect(params.get('tab')).toBe('investment-research')
    expect(params.get('currency')).toBe('CNY')
    for (const key of ['assistant', 'topic', 'question', 'instruments']) expect(params.has(key)).toBe(false)
    expect(screen.getByTestId('sector-panel').getAttribute('data-instrument')).toBe(instrumentId)
  })

  it('opens the Briefing report deep link without replacing the saved PM destination', async () => {
    const instrument = { requested_instrument_id: 'xlk', canonical_instrument_id: 'xlk', instrument_name: 'Technology ETF', instrument_type: 'etf' as const, primary_identifier: 'XLK', detail_view_type: 'etf', detail_subject_id: 'xlk', detail_supported: true, support_reason: '', corporate_actions: [] }
    const { unmount } = render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={['/instruments/xlk?tab=investment-research&mode=report']}><ListedInstrumentDetailPage instrument={instrument} watchlistContext={null} /><LocationProbe /></MemoryRouter></LanguageProvider>)
    expect((await screen.findByTestId('sector-panel')).getAttribute('data-reading')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'Research workspace' }))
    expect(screen.getByTestId('sector-panel').getAttribute('data-reading')).toBe('false')
    expect(new URLSearchParams(screen.getByTestId('listed-detail-location').textContent || '').get('tab')).toBe('investment-research')
    unmount()
    render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={['/instruments/xlk?tab=research']}><ListedInstrumentDetailPage instrument={instrument} watchlistContext={null} /></MemoryRouter></LanguageProvider>)
    expect(await screen.findByRole('button', { name: 'Add view' })).toBeTruthy()
  })

})

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="listed-detail-location">{location.search}</output>
}
