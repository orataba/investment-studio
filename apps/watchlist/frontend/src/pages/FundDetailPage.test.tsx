// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import { emptyInstrumentResearchResponse } from '../lib/api'
import FundDetailPage from './FundDetailPage'

const api = vi.hoisted(() => ({ summary: vi.fn(), library: vi.fn(), nav: vi.fn(), research: vi.fn(), reference: vi.fn(), attributes: vi.fn(),
  performance: vi.fn(), risk: vi.fn(), documents: vi.fn(), strategy: vi.fn(), taxonomy: vi.fn() }))
vi.mock('../lib/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../lib/api')>(),
  getInstrumentSummary: api.summary, getInstrumentLibrary: api.library, getInstrumentNavSeries: api.nav,
  getInstrumentResearch: api.research, getInstrumentReferenceData: api.reference, getInstrumentAttributes: api.attributes,
  getInstrumentPerformance: api.performance, getInstrumentRisk: api.risk, getInstrumentDocuments: api.documents, getInstrumentStrategy: api.strategy,
  getInstrumentTaxonomyTree: api.taxonomy,
}))
vi.mock('../components/InstrumentRiskPanel', () => ({ default: ({ mode }: { mode: string }) => <div data-testid="fund-price-risk" data-mode={mode} /> }))
vi.mock('../components/InstrumentRiskDrawer', () => ({ default: ({ instrumentId, instrumentName, watchlistId, onClose, onAskAssistant }: {
  instrumentId: string; instrumentName: string; watchlistId?: string; onClose: () => void; onAskAssistant: (question: string) => void
}) => <div role="dialog" aria-label="风险提示" data-instrument={instrumentId} data-name={instrumentName} data-watchlist={watchlistId}>
  <button onClick={onClose}>关闭风险提示</button><button onClick={() => onAskAssistant('这只基金的流动性风险有何变化？')}>追问当前风险</button>
</div> }))
vi.mock('../components/SectorResearchPanel', () => ({ default: ({ instrumentId, variant = 'timeline' }: { instrumentId: string; variant?: string }) => <div data-testid="fund-research-tracking" data-instrument={instrumentId} data-variant={variant} /> }))
// Keep InstrumentAssistantDrawer's actual close/URL behavior; isolate only its conversation view.
vi.mock('./ResearchPage', () => ({ default: ({ instrumentId, watchlistId, initialQuestion, onClose }: { instrumentId: string; watchlistId?: string; initialQuestion?: string; onClose: () => void }) =>
  <div role="dialog" aria-label="研究助手" data-instrument={instrumentId} data-watchlist={watchlistId} data-question={initialQuestion}><button onClick={onClose}>关闭研究助手</button></div> }))

beforeEach(() => {
  vi.resetAllMocks()
  window.history.replaceState({}, '', '?lang=zh-Hans')
  const taxonomy = { taxonomy_code: 'instrument_taxonomy', assigned_node_id: null, assigned_label: null, path_labels: [], path_node_ids: [], depth: 0, derived_values: {} }
  api.summary.mockResolvedValue({ instrument_id: 'fund-1', instrument_name: '测试基金', ticker_or_isin: 'TEST', management_firm_name: null,
    instrument_attributes: {}, taxonomy, key_stats: [], freshness: { data_freshness_status: 'fresh', last_fact_update_at: null, last_recalculated_at: null,
      last_successful_snapshot_at: null, staleness_reason: null }, quick_monitoring_items: [], tabs: ['overview', 'performance', 'risk', 'documents'] })
  api.library.mockResolvedValue([])
  api.reference.mockResolvedValue(null)
  api.attributes.mockResolvedValue({ instrument_id: 'fund-1', values: {}, definitions: [], taxonomy })
  api.taxonomy.mockResolvedValue({ nodes: [], max_depth: 3 })
  api.research.mockResolvedValue(emptyInstrumentResearchResponse())
  const dates = ['2025-12-31', '2026-08-31', '2026-09-04']
  const points = dates.map((date, index) => ({ date, value: 1 + index * 0.02 }))
  api.nav.mockResolvedValue({ fund_id: 'fund-1', count: 3, nav_basis_preference: 'auto', nav_basis_type: 'nav_with_dividend', nav_basis_source: 'shared', nav_basis_status: 'ready',
    return_segment_breaks: [], calculation_frequency_profile: { requested_frequency: 'daily', resolved_frequency: 'daily', inferred_frequency: 'daily', source_frequency_counts: { daily: 3 },
      raw_observation_count: 3, observation_count: 3, start_date: dates[0], end_date: dates[2], annualization_periods_per_year: 252, largest_gap_days: 243, gap_count: 0, gap_status: 'aligned', status_label: '日频' },
    series: points, calculation_series: points, rows: points.map((point) => ({ as_of_date: point.date, nav: point.value, nav_with_dividend: point.value,
      selected_basis_type: 'nav_with_dividend', selected_value: point.value, calculation_included: true, cumulative_distribution: 0, distribution_amount: null, currency: 'CNY', frequency: 'daily', adopted_at: null })) })
  api.performance.mockResolvedValue({ growth_chart_series: [], annual_returns: [], trailing_returns: [], ranking: null, peer_comparison: null, calculation_frequency_profile: null, snapshot_metadata: null })
  api.risk.mockResolvedValue({ risk_overview: null, current_drawdown: null, scatter_points: [], risk_metrics: [], drawdown_summary: null, calculation_frequency_profile: null, snapshot_metadata: null })
  api.documents.mockResolvedValue({ current_documents: [{ file_name: '基金合同.pdf', title: '基金合同', document_type: 'contract', download_url: '/api/file/contract', status: 'uploaded' }], recent_imports: [], extraction_reviews: [], notes: [] })
  api.strategy.mockResolvedValue({ summary: '以绝对收益为目标。', investment_objective: '', process_bullets: [], risk_controls: [], notes: [] })
})
afterEach(cleanup)

function show(path = '/instruments/fund-1', type: 'private_fund' | 'public_fund' = 'private_fund') {
  return render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={[path]}><FundDetailPage fundId="fund-1" fundType={type} watchlistContext={{ watchlistId: 'fund-list' }} /><LocationProbe /></MemoryRouter></LanguageProvider>)
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="fund-detail-location">{location.search}</output>
}

it('places the three instrument tools beside the fund title and keeps only breadcrumbs and language in the topbar', async () => {
  const { container } = show()
  const title = await screen.findByRole('heading', { level: 1, name: '测试基金 TEST' })
  const tools = within(title.parentElement!).getByRole('group', { name: '当前对象工具' })
  expect(within(tools).getAllByRole('button').map((button) => button.textContent)).toEqual(['设置', '风险提示', '研究助手'])
  const topbar = container.querySelector('.instrument-detail-topbar') as HTMLElement
  expect(within(topbar).getByRole('link', { name: 'Watchlist' })).toBeTruthy()
  expect(within(topbar).getByRole('combobox')).toBeTruthy()
  expect(within(topbar).queryAllByRole('button')).toHaveLength(0)
  fireEvent.click(within(tools).getByRole('button', { name: '标的设置' }))
  expect(await screen.findByRole('dialog', { name: '设置' })).toBeTruthy()
  await waitFor(() => expect(api.taxonomy).toHaveBeenCalledOnce())
})

it('keeps risk alerts scoped to the fund and makes risk and assistant drawers mutually exclusive', async () => {
  show('/instruments/fund-1?tab=events&currency=CNY')
  await screen.findByTestId('fund-research-tracking')
  fireEvent.click(screen.getByRole('button', { name: '研究助手' }))
  expect(screen.getByRole('dialog', { name: '研究助手' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '风险提示' }))
  expect(screen.queryByRole('dialog', { name: '研究助手' })).toBeNull()
  const risk = screen.getByRole('dialog', { name: '风险提示' })
  expect(risk.getAttribute('data-instrument')).toBe('fund-1')
  expect(risk.getAttribute('data-name')).toBe('测试基金')
  expect(risk.getAttribute('data-watchlist')).toBe('fund-list')
  fireEvent.click(within(risk).getByRole('button', { name: '追问当前风险' }))
  expect(screen.queryByRole('dialog', { name: '风险提示' })).toBeNull()
  const assistant = screen.getByRole('dialog', { name: '研究助手' })
  expect(assistant.getAttribute('data-instrument')).toBe('fund-1')
  expect(assistant.getAttribute('data-question')).toBe('这只基金的流动性风险有何变化？')
  fireEvent.click(within(assistant).getByRole('button', { name: '关闭研究助手' }))
  fireEvent.click(screen.getByRole('button', { name: '风险提示' }))
  fireEvent.click(screen.getByRole('button', { name: '关闭风险提示' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  const params = new URLSearchParams(screen.getByTestId('fund-detail-location').textContent || '')
  expect(params.get('tab')).toBe('events')
  expect(params.get('currency')).toBe('CNY')
})

it('resets instrument drawers when switching funds', async () => {
  const summary = await api.summary()
  api.summary.mockImplementation((instrumentId: string) => Promise.resolve({ ...summary, instrument_id: instrumentId, instrument_name: instrumentId === 'fund-1' ? '测试基金' : '另一基金' }))
  function FundSwitchHarness() {
    const [id, setId] = useState('fund-1')
    return <><button onClick={() => setId((current) => current === 'fund-1' ? 'fund-2' : 'fund-1')}>切换基金</button>
      <FundDetailPage fundId={id} fundType="private_fund" watchlistContext={{ watchlistId: 'fund-list' }} /></>
  }
  render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><FundSwitchHarness /></MemoryRouter></LanguageProvider>)
  await screen.findByRole('heading', { level: 1, name: '测试基金 TEST' })
  fireEvent.click(screen.getByRole('button', { name: '风险提示' }))
  expect(screen.getByRole('dialog', { name: '风险提示' }).getAttribute('data-instrument')).toBe('fund-1')
  fireEvent.click(screen.getByRole('button', { name: '切换基金' }))
  await screen.findByRole('heading', { level: 1, name: '另一基金 TEST' })
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '研究助手' }))
  expect(screen.getByRole('dialog', { name: '研究助手' }).getAttribute('data-instrument')).toBe('fund-2')
  fireEvent.click(screen.getByRole('button', { name: '切换基金' }))
  await screen.findByRole('heading', { level: 1, name: '测试基金 TEST' })
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('keeps the overview free of duplicate charts and separates personal opinions from prepared research tracking', async () => {
  const { container } = show()
  await screen.findByRole('region', { name: '基金总览' })
  const navigation = container.querySelector('.instrument-detail-tabs')!
  expect(within(navigation as HTMLElement).getAllByRole('button').map((button) => button.textContent)).toEqual(['总览', '投资观点', '研究追踪', '业绩与风险', '基金档案'])
  expect(container.querySelector('.instrument-chart-stage')).toBeNull()
  fireEvent.click(within(navigation as HTMLElement).getByRole('button', { name: '投资观点' }))
  expect(await screen.findByRole('region', { name: '投资观点时间线' })).toBeTruthy()
  expect(screen.queryByText('资料与明细')).toBeNull()
  fireEvent.click(within(navigation as HTMLElement).getByRole('button', { name: '研究追踪' }))
  expect(await screen.findByTestId('fund-research-tracking')).toHaveProperty('dataset.instrument', 'fund-1')
  expect(screen.getByTestId('fund-research-tracking').getAttribute('data-variant')).toBe('timeline')
})

it.each(['public_fund', 'private_fund'] as const)('opens the %s research assistant without losing the current research tab or currency on close', async (type) => {
  const { container } = show('/instruments/fund-1?tab=analyst&currency=CNY&assistant=1&topic=old-topic&question=old-question&instruments=other', type)
  const tracking = await screen.findByTestId('fund-research-tracking')
  expect(tracking.getAttribute('data-variant')).toBe('timeline')
  const navigation = container.querySelector('.instrument-detail-tabs') as HTMLElement
  expect(within(navigation).getByRole('button', { name: '研究追踪' }).classList.contains('instrument-detail-tab-active')).toBe(true)
  fireEvent.click(within(navigation).getByRole('button', { name: '投资观点' }))
  await screen.findByRole('region', { name: '投资观点时间线' })
  fireEvent.click(within(navigation).getByRole('button', { name: '研究追踪' }))
  await screen.findByTestId('fund-research-tracking')
  fireEvent.click(screen.getByRole('button', { name: '研究助手' }))
  const drawer = screen.getByRole('dialog', { name: '研究助手' })
  expect(drawer.getAttribute('data-instrument')).toBe('fund-1')
  expect(drawer.getAttribute('data-watchlist')).toBe('fund-list')
  fireEvent.click(within(drawer).getByRole('button', { name: '关闭研究助手' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '研究助手' })).toBeNull())
  const params = new URLSearchParams(screen.getByTestId('fund-detail-location').textContent || '')
  expect(params.get('tab')).toBe('events')
  expect(params.get('currency')).toBe('CNY')
  for (const key of ['assistant', 'topic', 'question', 'instruments']) expect(params.has(key)).toBe(false)
  expect(screen.getByTestId('fund-research-tracking').getAttribute('data-instrument')).toBe('fund-1')
})

it('opens an existing risk link in the unified chart and risk surface with a single benchmark selector', async () => {
  const { container } = show('/instruments/fund-1?tab=risk&start=2025-12-31&end=2026-09-04')
  await screen.findByTestId('fund-price-risk')
  expect(screen.getByTestId('fund-price-risk').getAttribute('data-mode')).toBe('price')
  await waitFor(() => { expect(api.performance).toHaveBeenCalledWith('fund-1'); expect(api.risk).toHaveBeenCalledWith('fund-1') })
  expect(container.querySelectorAll('.instrument-chart-stage')).toHaveLength(1)
  expect(screen.getAllByRole('searchbox')).toHaveLength(1)
  expect(screen.queryByRole('region', { name: '基金总览' })).toBeNull()
})

it('preserves old document links and upload access inside the archive', async () => {
  const { container } = show('/instruments/fund-1?tab=documents', 'public_fund')
  expect(await screen.findByRole('link', { name: '基金合同.pdf' })).toBeTruthy()
  expect(container.querySelector('#fund-archive-documents')?.hasAttribute('open')).toBe(true)
  expect(container.querySelector('#fund-archive-documents input[type="file"]')).toBeTruthy()
  expect(container.querySelectorAll('.instrument-detail-tabs')).toHaveLength(1)
})
