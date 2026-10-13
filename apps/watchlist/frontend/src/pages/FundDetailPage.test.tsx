// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import { emptyInstrumentResearchResponse } from '../lib/api'
import FundDetailPage from './FundDetailPage'
import { announceResearchPublication } from '../lib/researchUpdates'

const api = vi.hoisted(() => ({ summary: vi.fn(), library: vi.fn(), nav: vi.fn(), research: vi.fn(), reference: vi.fn(), attributes: vi.fn(),
  performance: vi.fn(), risk: vi.fn(), portfolio: vi.fn(), documents: vi.fn(), strategy: vi.fn(), taxonomy: vi.fn(), resolve: vi.fn() }))
vi.mock('../lib/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../lib/api')>(),
  getInstrumentSummary: api.summary, getInstrumentLibrary: api.library, getInstrumentNavSeries: api.nav,
  getInstrumentResearch: api.research, getInstrumentReferenceData: api.reference, getInstrumentAttributes: api.attributes,
  getInstrumentExposureSummary: api.portfolio, getInstrumentPerformance: api.performance, getInstrumentRisk: api.risk, getInstrumentDocuments: api.documents, getInstrumentStrategy: api.strategy,
  getInstrumentTaxonomyTree: api.taxonomy, resolveInstrumentDetail: api.resolve,
}))
vi.mock('../components/InstrumentRiskPanel', () => ({ default: ({ mode }: { mode: string }) => <div data-testid="fund-price-risk" data-mode={mode} /> }))
vi.mock('../components/InstrumentRiskMonitoringPanel', () => ({ default: ({ instrumentId, onAskAssistant }: {
  instrumentId: string; onAskAssistant: (id: string, question: string) => void
}) => <section aria-label="当前风险与事项复核" data-instrument={instrumentId}>
  <button onClick={() => onAskAssistant(instrumentId, '解释当前尾部风险')}>研究当前风险</button>
</section> }))
vi.mock('../components/InstrumentRiskDrawer', () => ({ default: ({ instrumentId, instrumentName, watchlistId, onClose, onAskAssistant }: {
  instrumentId: string; instrumentName: string; watchlistId?: string; onClose: () => void; onAskAssistant: (question: string) => void
}) => <div role="dialog" aria-label="风险提示" data-instrument={instrumentId} data-name={instrumentName} data-watchlist={watchlistId}>
  <button onClick={onClose}>关闭风险提示</button><button onClick={() => onAskAssistant('这只基金的流动性风险有何变化？')}>追问当前风险</button>
</div> }))
vi.mock('../components/SectorResearchPanel', () => ({ default: ({ instrumentId, variant = 'timeline', readingMode = false }: { instrumentId: string; variant?: string; readingMode?: boolean }) => <div data-testid="fund-research-tracking" data-reading={String(readingMode)} data-instrument={instrumentId} data-variant={variant} /> }))
// Keep InstrumentAssistantDrawer's actual close/URL behavior; isolate only its conversation view.
vi.mock('./ResearchPage', () => ({ default: ({ instrumentId, watchlistId, initialQuestion, onClose }: { instrumentId: string; watchlistId?: string; initialQuestion?: string; onClose: () => void }) =>
  <div role="dialog" aria-label="研究助手" data-instrument={instrumentId} data-watchlist={watchlistId} data-question={initialQuestion}><button onClick={onClose}>关闭研究助手</button></div> }))

function intervalEvidence(points: Array<{ date: string; value: number }>, unavailable = false) {
  return { metadata: { return_kind: 'total_return', return_series_status: 'complete' }, currency: 'CNY', wealth_points: points,
    intervals: points.slice(1).map((point, index) => ({ start_date: points[index].date, date: point.date,
      value: point.value / points[index].value - 1, log_return: null, status: unavailable ? 'unavailable' : 'complete', reason: unavailable ? 'missing_observation' : null })) }
}

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
    return_segment_breaks: [], calculation_frequency_profile: { requested_frequency: 'event_driven', resolved_frequency: 'event_driven', inferred_frequency: 'event_driven', source_frequency_counts: { event_driven: 3 },
      raw_observation_count: 3, observation_count: 3, start_date: dates[0], end_date: dates[2], annualization_periods_per_year: 252, largest_gap_days: 243, gap_count: 0, gap_status: 'aligned', status_label: '日频' },
    series: points, calculation_series: points, return_series: intervalEvidence(points), rows: points.map((point) => ({ as_of_date: point.date, nav: point.value, nav_with_dividend: point.value,
      selected_basis_type: 'nav_with_dividend', selected_value: point.value, calculation_included: true, cumulative_distribution: 0, distribution_amount: null, currency: 'CNY', frequency: 'daily', adopted_at: null })) })
  api.performance.mockResolvedValue({ growth_chart_series: [], annual_returns: [], trailing_returns: [], ranking: null, peer_comparison: null, calculation_frequency_profile: null, snapshot_metadata: null })
  api.risk.mockResolvedValue({ risk_overview: null, current_drawdown: null, scatter_points: [], risk_metrics: [], drawdown_summary: null, calculation_frequency_profile: null, snapshot_metadata: null })
  api.documents.mockResolvedValue({ current_documents: [{ file_name: '基金合同.pdf', title: '基金合同', document_type: 'contract', download_url: '/api/file/contract', status: 'uploaded' }], recent_imports: [], extraction_reviews: [], notes: [] })
  api.portfolio.mockResolvedValue({ allocation_blocks: {}, style_box: null, liquidity_leverage: null, valuation_statistics: null, holdings_summary: null, snapshot_metadata: null })
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

const savedOpinion = (body: string) => ({ note_id: body, note_date: '2026-09-23', note_type: 'thesis_update', title: '基金观察', body, summary: '', importance: 'normal', tags: [], source_refs: '', people: '', author: 'Shaw', follow_up_date: null, completed_at: null, created_at: '2026-09-23T09:00:00Z', updated_at: '2026-09-23T09:00:00Z', updated_by: 'terminal_ui', revision_number: 1 })

it('opens investment research from summary without requesting unrelated NAV, opinions, library or provider data', async () => {
  for (const request of [api.library, api.nav, api.research, api.reference]) request.mockImplementation(() => new Promise(() => {}))
  show('/instruments/fund-1?tab=investment-research&currency=HKD&benchmark=benchmark-1')
  expect(await screen.findByTestId('fund-research-tracking')).toBeTruthy()
  expect(screen.getByRole('heading', { name: '测试基金 TEST' })).toBeTruthy()
  for (const request of [api.library, api.nav, api.research, api.reference, api.resolve]) expect(request).not.toHaveBeenCalled()
  expect(new URLSearchParams(screen.getByTestId('fund-detail-location').textContent || '').get('currency')).toBe('HKD')
})

it('loads NAV independently while pending opinions remain explicitly pending and are not fetched twice across tabs', async () => {
  let finishOpinions!: (value: unknown) => void
  api.research.mockImplementation(() => new Promise(resolve => { finishOpinions = resolve }))
  show()
  const overview = await screen.findByRole('region', { name: '基金总览' })
  expect((await within(overview).findAllByText('1.0400')).length).toBeGreaterThan(0)
  expect(within(overview).queryByText('尚未记录投资观点。')).toBeNull()
  expect(api.nav).toHaveBeenCalledOnce()
  expect(api.library).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', { name: '投资观点' }))
  expect(screen.queryByRole('region', { name: '投资观点时间线' })).toBeNull()
  expect(screen.getByText('正在加载标的资料…').closest('[role="status"]')).toBeTruthy()
  expect(api.research).toHaveBeenCalledOnce()
  await act(async () => finishOpinions({ ...emptyInstrumentResearchResponse(), notes: [savedOpinion('已经保存的独立观点。')] }))
  expect(await screen.findByText('已经保存的独立观点。')).toBeTruthy()
})

it('shows an opinion read failure without presenting an empty record and retries only the failed resource', async () => {
  api.research.mockRejectedValueOnce(new Error('观点服务暂不可用'))
  show('/instruments/fund-1?tab=views')
  const failure = await screen.findByRole('alert')
  expect(failure.textContent).toContain('观点服务暂不可用')
  expect(screen.queryByRole('region', { name: '投资观点时间线' })).toBeNull()
  api.research.mockResolvedValue({ ...emptyInstrumentResearchResponse(), notes: [savedOpinion('恢复后可读的观点。')] })
  fireEvent.click(within(failure).getByRole('button', { name: '重试' }))
  expect(await screen.findByText('恢复后可读的观点。')).toBeTruthy()
  expect(api.summary).toHaveBeenCalledOnce()
  expect(api.research).toHaveBeenCalledTimes(2)
  expect(api.nav).not.toHaveBeenCalled()
})

it('rejects late opinions from the previous fund while retaining the new fund record', async () => {
  const summary = await api.summary()
  api.summary.mockImplementation(async (id: string) => ({ ...summary, instrument_id: id, instrument_name: id }))
  let finishOld!: (value: unknown) => void
  api.research.mockImplementation((id: string) => id === 'fund-1' ? new Promise(resolve => { finishOld = resolve }) : Promise.resolve({ ...emptyInstrumentResearchResponse(), notes: [savedOpinion('第二只基金的观点。')] }))
  const page = (id: string) => <LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={['/?tab=views']}><FundDetailPage fundId={id} fundType="private_fund" /></MemoryRouter></LanguageProvider>
  const { rerender } = render(page('fund-1'))
  await waitFor(() => expect(api.research).toHaveBeenCalledWith('fund-1'))
  rerender(page('fund-2'))
  expect(await screen.findByText('第二只基金的观点。')).toBeTruthy()
  await act(async () => finishOld({ ...emptyInstrumentResearchResponse(), notes: [savedOpinion('迟到的旧基金观点。')] }))
  expect(screen.queryByText('迟到的旧基金观点。')).toBeNull()
  expect(screen.getByText('第二只基金的观点。')).toBeTruthy()
})

it('rejects an in-flight opinion response superseded by a research publication', async () => {
  const pending: Array<(value: unknown) => void> = []
  api.research.mockImplementation(() => new Promise(resolve => { pending.push(resolve) }))
  show('/instruments/fund-1?tab=views')
  await waitFor(() => expect(api.research).toHaveBeenCalledOnce())
  await act(async () => announceResearchPublication(['fund-1']))
  expect(api.research).toHaveBeenCalledTimes(2)
  await act(async () => pending[1]({ ...emptyInstrumentResearchResponse(), notes: [savedOpinion('本轮发布后的观点。')] }))
  expect(await screen.findByText('本轮发布后的观点。')).toBeTruthy()
  await act(async () => pending[0]({ ...emptyInstrumentResearchResponse(), notes: [savedOpinion('发布前仍在读取的旧观点。')] }))
  expect(screen.queryByText('发布前仍在读取的旧观点。')).toBeNull()
  expect(screen.getByText('本轮发布后的观点。')).toBeTruthy()
})

it('refreshes published fund opinions across research and investment-view tabs', async () => {
  show('/instruments/fund-1?tab=views')
  await screen.findByRole('region', { name: '投资观点时间线' })
  fireEvent.click(screen.getByRole('button', { name: '投资研究' }))
  await screen.findByTestId('fund-research-tracking')
  const note = { note_id: 'new-view', note_date: '2026-09-23', note_type: 'thesis_update', title: '基金观察', body: '新增主题观点应立即出现在基金观点页。', summary: '', importance: 'normal', tags: [], source_refs: '', people: '', author: 'Shaw', follow_up_date: null, completed_at: null, created_at: '2026-09-23T09:00:00Z', updated_at: '2026-09-23T09:00:00Z', updated_by: 'terminal_ui', revision_number: 1 }
  api.research.mockResolvedValue({ ...emptyInstrumentResearchResponse(), notes: [note] })
  await act(async () => announceResearchPublication(['other']))
  expect(api.research).toHaveBeenCalledTimes(1)
  await act(async () => announceResearchPublication(['fund-1']))
  expect(api.research).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: '投资观点' }))
  expect(await screen.findByText(note.body)).toBeTruthy()
  expect(api.research).toHaveBeenCalledTimes(2)
  expect(api.summary).toHaveBeenCalledTimes(1)
})

it('places the three instrument tools beside the fund title and keeps workspace switching and breadcrumbs in the topbar', async () => {
  const { container } = show()
  const title = await screen.findByRole('heading', { level: 1, name: '测试基金 TEST' })
  const tools = within(title.parentElement!).getByRole('group', { name: '当前对象工具' })
  expect(within(tools).getAllByRole('button').map((button) => button.textContent)).toEqual(['设置', '风险提示', '研究助手'])
  const topbar = container.querySelector('.instrument-detail-topbar') as HTMLElement
  expect(within(topbar).getByRole('link', { name: '自选表' })).toBeTruthy()
  expect(within(topbar).getByRole('combobox', { name: '切换工作区' })).toBeTruthy()
  expect(within(topbar).getByRole('combobox', { name: '语言' })).toBeTruthy()
  expect(within(topbar).queryAllByRole('button')).toHaveLength(0)
  fireEvent.click(within(tools).getByRole('button', { name: '标的设置' }))
  expect(await screen.findByRole('dialog', { name: '设置' })).toBeTruthy()
  await waitFor(() => expect(api.taxonomy).toHaveBeenCalledOnce())
})

it('keeps section loading in the content region while retaining the instrument header', async () => {
  let resolvePerformance!: (value: unknown) => void
  api.performance.mockImplementationOnce(() => new Promise(resolve => { resolvePerformance = resolve }))
  const { container } = show('/instruments/fund-1?tab=overview')
  const title = await screen.findByRole('heading', { level: 1, name: '测试基金 TEST' })
  const status = (await screen.findByText('正在加载标的资料…')).closest('[role="status"]')!
  expect(container.contains(status)).toBe(true)
  expect(status.closest('.instrument-detail-shell')).toBeNull()
  expect(status.closest('#investment-studio-notices')).toBeNull()
  resolvePerformance({ growth_chart_series: [], annual_returns: [], trailing_returns: [], ranking: null, peer_comparison: null, calculation_frequency_profile: null, snapshot_metadata: null })
  await waitFor(() => expect(screen.queryByText('正在加载标的资料…')).toBeNull())
  expect(screen.getByRole('heading', { level: 1, name: '测试基金 TEST' })).toBe(title)
})

it('keeps risk alerts scoped to the fund and returns to them after an assistant follow-up', async () => {
  show('/instruments/fund-1?tab=events&currency=CNY')
  await screen.findByTestId('fund-research-tracking')
  fireEvent.click(screen.getByRole('button', { name: '研究助手' }))
  expect((await screen.findByRole('dialog', { name: '研究助手' }))).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '风险提示' }))
  expect(screen.queryByRole('dialog', { name: '研究助手' })).toBeNull()
  const risk = screen.getByRole('dialog', { name: '风险提示' })
  expect(risk.getAttribute('data-instrument')).toBe('fund-1')
  expect(risk.getAttribute('data-name')).toBe('测试基金')
  expect(risk.getAttribute('data-watchlist')).toBe('fund-list')
  fireEvent.click(within(risk).getByRole('button', { name: '追问当前风险' }))
  expect(screen.getByRole('dialog', { name: '风险提示' })).toBe(risk)
  const assistant = await screen.findByRole('dialog', { name: '研究助手' })
  expect(assistant.getAttribute('data-instrument')).toBe('fund-1')
  expect(assistant.getAttribute('data-question')).toBe('这只基金的流动性风险有何变化？')
  fireEvent.click(within(assistant).getByRole('button', { name: '关闭研究助手' }))
  expect(screen.getByRole('dialog', { name: '风险提示' })).toBe(risk)
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
  expect((await screen.findByRole('dialog', { name: '研究助手' })).getAttribute('data-instrument')).toBe('fund-2')
  fireEvent.click(screen.getByRole('button', { name: '切换基金' }))
  await screen.findByRole('heading', { level: 1, name: '测试基金 TEST' })
  expect(screen.queryByRole('dialog')).toBeNull()
})


it.each(['public_fund', 'private_fund'] as const)('keeps %s NAV gaps visible beside the overview metrics without loading the archive', async (type) => {
  const nav = await api.nav()
  api.nav.mockResolvedValue({ ...nav, return_series: intervalEvidence(nav.calculation_series, true), calculation_frequency_profile: { ...nav.calculation_frequency_profile, gap_count: 2 } })
  const { container } = show('/instruments/fund-1', type)
  const overview = await screen.findByRole('region', { name: '基金总览' })
  expect(await within(overview).findByText('净值历史存在缺口，路径风险指标暂不可用。')).toBeTruthy()
  await waitFor(() => expect(container.querySelector('.instrument-chart-stage')).toBeTruthy())
  expect(within(overview).getByText('当前回撤').parentElement?.querySelector('strong')?.textContent).toBe('—')
  expect(within(overview).queryByRole('region', { name: '当前总体观点' })).toBeNull()
  expect(screen.queryByTestId('fund-research-tracking')).toBeNull()
  expect(api.library).toHaveBeenCalledOnce()
  expect(api.reference).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: '资料' })).toBeTruthy()
})

it.each(['public_fund', 'private_fund'] as const)('combines %s overview and performance with one main chart and keeps research separate', async (type) => {
  const { container } = show('/instruments/fund-1', type)
  const overview = await screen.findByRole('region', { name: '基金总览' })
  const navigation = container.querySelector('.instrument-detail-tabs')!
  await waitFor(() => expect(within(navigation as HTMLElement).getAllByRole('button').map((button) => button.textContent)).toEqual(['总览', '风险监测', '投资研究', '投资观点', '资料']))
  expect(await within(overview).findByText('Rolling Risk')).toBeTruthy()
  expect(overview.querySelectorAll('.instrument-chart-stage')).toHaveLength(1)
  expect(overview.querySelectorAll('.instrument-quote-primary-block')).toHaveLength(1)
  expect(overview.querySelector('.fund-overview-snapshot')).toBeNull()
  expect(overview.querySelectorAll('.instrument-quote-fact')).toHaveLength(3)
  expect(overview.querySelector('.instrument-metrics-table')).toBeTruthy()
  expect(overview.querySelector('.instrument-heatmap-table')).toBeTruthy()
  expect(overview.querySelector('.instrument-drawdown-chart')).toBeTruthy()
  expect(screen.queryByRole('region', { name: '当前风险与事项复核' })).toBeNull()
  expect(screen.queryByTestId('fund-research-tracking')).toBeNull()
  fireEvent.click(within(navigation as HTMLElement).getByRole('button', { name: '投资观点' }))
  expect(await screen.findByRole('region', { name: '投资观点时间线' })).toBeTruthy()
  expect(screen.queryByText('资料与明细')).toBeNull()
  fireEvent.click(within(navigation as HTMLElement).getByRole('button', { name: '投资研究' }))
  expect(await screen.findByTestId('fund-research-tracking')).toHaveProperty('dataset.instrument', 'fund-1')
  expect(screen.getByTestId('fund-research-tracking').getAttribute('data-variant')).toBe('timeline')
})

it.each(['public_fund', 'private_fund'] as const)('opens the %s research assistant without losing the current research tab or currency on close', async (type) => {
  const { container } = show('/instruments/fund-1?tab=analyst&currency=CNY&assistant=1&topic=old-topic&question=old-question&instruments=other', type)
  const tracking = await screen.findByTestId('fund-research-tracking')
  expect(tracking.getAttribute('data-variant')).toBe('timeline')
  const navigation = container.querySelector('.instrument-detail-tabs') as HTMLElement
  expect(within(navigation).getByRole('button', { name: '投资研究' }).classList.contains('instrument-detail-tab-active')).toBe(true)
  fireEvent.click(within(navigation).getByRole('button', { name: '投资观点' }))
  await screen.findByRole('region', { name: '投资观点时间线' })
  fireEvent.click(within(navigation).getByRole('button', { name: '投资研究' }))
  await screen.findByTestId('fund-research-tracking')
  fireEvent.click(screen.getByRole('button', { name: '研究助手' }))
  const drawer = await screen.findByRole('dialog', { name: '研究助手' })
  expect(drawer.getAttribute('data-instrument')).toBe('fund-1')
  expect(drawer.getAttribute('data-watchlist')).toBe('fund-list')
  fireEvent.click(within(drawer).getByRole('button', { name: '关闭研究助手' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '研究助手' })).toBeNull())
  const params = new URLSearchParams(screen.getByTestId('fund-detail-location').textContent || '')
  expect(params.get('tab')).toBe('investment-research')
  expect(params.get('currency')).toBe('CNY')
  for (const key of ['assistant', 'topic', 'question', 'instruments']) expect(params.has(key)).toBe(false)
  expect(screen.getByTestId('fund-research-tracking').getAttribute('data-instrument')).toBe('fund-1')
})

it.each(['public_fund', 'private_fund'] as const)('opens the %s risk page separately and preserves its URL during an assistant follow-up', async (type) => {
  const { container } = show('/instruments/fund-1?tab=risk&start=2025-12-31&end=2026-09-04&currency=CNY', type)
  const risk = await screen.findByRole('region', { name: '当前风险与事项复核' })
  expect(risk.getAttribute('data-instrument')).toBe('fund-1')
  expect(container.querySelector('.instrument-chart-stage')).toBeNull()
  expect(container.querySelector('.instrument-metrics-table')).toBeNull()
  expect(screen.queryByRole('region', { name: '基金总览' })).toBeNull()
  for (const request of [api.nav, api.library, api.performance, api.risk]) expect(request).not.toHaveBeenCalled()
  fireEvent.click(within(risk).getByRole('button', { name: '研究当前风险' }))
  const assistant = await screen.findByRole('dialog', { name: '研究助手' })
  expect(assistant.getAttribute('data-instrument')).toBe('fund-1')
  expect(assistant.getAttribute('data-question')).toBe('解释当前尾部风险')
  fireEvent.click(within(assistant).getByRole('button', { name: '关闭研究助手' }))
  const params = new URLSearchParams(screen.getByTestId('fund-detail-location').textContent || '')
  expect(params.get('tab')).toBe('risk')
  expect(params.get('start')).toBe('2025-12-31')
  expect(params.get('end')).toBe('2026-09-04')
  expect(params.get('currency')).toBe('CNY')
  expect(screen.getByRole('region', { name: '当前风险与事项复核' })).toBe(risk)
})

it('explains a benchmark failure from the shared hint and retries only through the text action', async () => {
  const nav = await api.nav()
  api.resolve.mockResolvedValue({ detail_supported: true, canonical_instrument_id: 'benchmark-1', instrument_name: '测试基准', primary_identifier: 'BENCH', instrument_type: 'index' })
  api.nav.mockImplementation((id: string) => id === 'benchmark-1' ? Promise.reject(new Error('价格服务暂时不可用')) : Promise.resolve(nav))
  const { container } = show('/instruments/fund-1?tab=overview&benchmark=benchmark-1')
  const failure = await screen.findByRole('alert')
  expect(failure.textContent).toContain('价格服务暂时不可用')
  const hint = screen.getByRole('button', { name: /基准比较口径: 基准加载失败/ })
  expect(hint.textContent).toBe('!')
  expect(hint.closest('.instrument-chart-compare-search-box')).not.toBeNull()
  const requestCount = () => api.nav.mock.calls.filter(([id]) => id === 'benchmark-1').length
  expect(requestCount()).toBe(1)
  fireEvent.pointerEnter(hint, { pointerType: 'mouse' })
  expect(screen.getByRole('tooltip').textContent).toContain('价格服务暂时不可用')
  expect(container.contains(screen.getByRole('tooltip'))).toBe(false)
  fireEvent.click(hint)
  expect(requestCount()).toBe(1)
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(within(failure).getByRole('button', { name: '重试' }))
  await waitFor(() => expect(requestCount()).toBe(2))
  expect(await screen.findByRole('alert')).toBeTruthy()
})

it('withholds affected fund calculations across missing observations', async () => {
  const nav = await api.nav()
  api.nav.mockResolvedValue({ ...nav, return_series: intervalEvidence(nav.calculation_series, true), calculation_frequency_profile: {
    ...nav.calculation_frequency_profile, gap_count: 1, gap_status: 'calendar_gaps',
  } })
  const { container } = show('/instruments/fund-1?tab=overview')
  await screen.findByText('Rolling Risk')
  const table = container.querySelector('.instrument-metrics-table') as HTMLElement
  const rowValues = (label: string) => within(table).getByText(label).closest('tr')!.querySelectorAll('td strong')
  for (const label of ['Ann. Volatility', 'Sharpe Ratio', 'Sortino Ratio', 'Calmar Ratio', 'Max DD']) {
    expect([...rowValues(label)].every((node) => node.textContent === '—')).toBe(true)
  }
  expect([...rowValues('Period Return')].every((node) => node.textContent === '—')).toBe(true)
  expect(screen.getByText(/不存在缺失观测或待确认的收益断点/)).toBeTruthy()
})

it.each(['price_return', null, 'total_return'] as const)('requires matched known return semantics for benchmark relative metrics: %s', async (returnKind) => {
  const nav = await api.nav()
  const fund = { ...nav, return_kind: 'total_return' }
  const benchmark = { ...nav, fund_id: 'benchmark-1', return_kind: returnKind }
  api.resolve.mockResolvedValue({ detail_supported: true, canonical_instrument_id: 'benchmark-1', instrument_name: '测试基准', primary_identifier: 'BENCH', instrument_type: 'index' })
  api.nav.mockImplementation((id: string) => Promise.resolve(id === 'benchmark-1' ? benchmark : fund))
  const { container } = show('/instruments/fund-1?tab=overview&benchmark=benchmark-1')
  await screen.findByText('Rolling Risk')
  await waitFor(() => expect(screen.getByRole('button', { name: /基准比较口径: 标的/ })).toBeTruthy())
  const table = container.querySelector('.instrument-metrics-table') as HTMLElement
  for (const label of ['Excess Return', 'Beta', 'Upside Capture']) {
    const values = [...within(table).getByText(label).closest('tr')!.querySelectorAll('td strong')]
    expect(values.length).toBeGreaterThan(0)
    expect(values.some((node) => node.textContent !== '—')).toBe(returnKind === 'total_return')
  }
})

it('preserves old document links and upload access inside information', async () => {
  const { container } = show('/instruments/fund-1?tab=documents', 'public_fund')
  expect(await screen.findByRole('link', { name: '基金合同.pdf' })).toBeTruthy()
  expect(container.querySelector('#fund-archive-documents')?.hasAttribute('open')).toBe(true)
  expect(container.querySelector('#fund-archive-documents input[type="file"]')).toBeTruthy()
  expect(container.querySelectorAll('.instrument-detail-tabs')).toHaveLength(1)
  expect(screen.getByRole('button', { name: '资料' }).classList.contains('instrument-detail-tab-active')).toBe(true)
})


it.each(['public_fund', 'private_fund'] as const)('keeps every %s archive section and its upload controls reachable from old archive URLs', async type => {
  const { container } = show('/instruments/fund-1?tab=archive&section=documents&currency=CNY', type)
  const info = await screen.findByRole('region', { name: '基金资料' })
  expect(await within(info).findByRole('link', { name: '基金合同.pdf' })).toBeTruthy()
  for (const section of ['price', 'exposure', 'people', 'strategy', 'documents', 'monitoring']) {
    expect(info.querySelector(`#fund-archive-${section} > summary`)).toBeTruthy()
  }
  expect(info.querySelector('#fund-archive-documents')?.hasAttribute('open')).toBe(true)
  expect(info.querySelector('input[type="file"]')).toBeTruthy()
  expect(container.querySelector('.instrument-chart-stage')).toBeNull()
  expect(screen.getByRole('button', { name: '资料' }).classList.contains('instrument-detail-tab-active')).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '总览' }))
  await screen.findByText('Rolling Risk')
  fireEvent.click(screen.getByRole('button', { name: '资料' }))
  expect((await screen.findByRole('region', { name: '基金资料' })).querySelector('#fund-archive-documents')?.hasAttribute('open')).toBe(true)
  const params = new URLSearchParams(screen.getByTestId('fund-detail-location').textContent || '')
  expect(params.get('tab')).toBe('info')
  expect(params.get('section')).toBe('documents')
  expect(params.get('currency')).toBe('CNY')
})

it.each(['public_fund', 'private_fund'] as const)('opens legacy report links in the unified %s research and preserves PM links', async type => {
  const { unmount } = show('/instruments/fund-1?tab=investment-research&mode=report&currency=CNY', type)
  expect(await screen.findByTestId('fund-research-tracking')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '研究概览' })).toBeNull()
  const params = new URLSearchParams(screen.getByTestId('fund-detail-location').textContent || '')
  expect(params.get('tab')).toBe('investment-research')
  expect(params.get('currency')).toBe('CNY')
  unmount()
  show('/instruments/fund-1?tab=research', type)
  expect(await screen.findByRole('region', { name: '投资观点时间线' })).toBeTruthy()
})

it('keeps all benchmark matrix periods on common endpoints and marks uncovered 1Y unavailable', async () => {
  window.history.replaceState({}, '', '?lang=en')
  const template = await api.nav()
  const dates = ['2025-09-09', '2025-12-31', '2026-06-15', '2026-09-30']
  const navFor = (id: string, prices: number[]) => ({ ...template, fund_id: id, return_kind: 'total_return',
    rows: prices.map((price, i) => ({ ...template.rows[0], as_of_date: dates[i], nav: price, nav_with_dividend: price, selected_value: price })),
    return_series: intervalEvidence(prices.map((value, i) => ({ date: dates[i], value }))), calculation_series: prices.map((value, i) => ({ date: dates[i], value })), series: prices.map((value, i) => ({ date: dates[i], value })) })
  api.resolve.mockResolvedValue({ detail_supported: true, canonical_instrument_id: 'benchmark-1', instrument_name: 'Benchmark', primary_identifier: 'BENCH', instrument_type: 'index' })
  api.nav.mockImplementation((id: string) => Promise.resolve(id === 'benchmark-1' ? navFor(id, [1, 1.01, 1.02]) : navFor(id, [1, 1.02, 1.142706, 1.051824])))
  const { container } = show('/instruments/fund-1?tab=performance&benchmark=benchmark-1')
  await screen.findByText(/Comparison matrix: all periods use common observation dates through 2026-06-15/)
  const table = container.querySelector('.instrument-metrics-table') as HTMLElement
  const headings = [...table.querySelectorAll('thead th')]
  const ytd = headings.findIndex(node => node.textContent?.startsWith('YTD'))
  const oneYear = headings.findIndex(node => node.textContent?.startsWith('1Y'))
  const values = within(table).getByText('Period Return').closest('tr')!.querySelectorAll('td')
  expect(headings[ytd].textContent).toContain('2025-12-31 → 2026-06-15')
  expect(values[ytd].textContent).toContain('12.03%')
  expect(headings[oneYear].textContent).toContain('Insufficient coverage')
  expect(values[oneYear].querySelector('strong')!.textContent).toBe('—')
  expect(screen.getByText(/Standalone instrument metrics and the monthly table are through 2026-09-30/)).toBeTruthy()
  expect(screen.getByRole('button', { name: /Benchmark comparison basis: Instrument: Total return/ })).toBeTruthy()
})
it('recovers a failed fund summary without losing the requested performance tab or benchmark', async () => {
  api.summary.mockRejectedValueOnce(new Error('统一账号服务暂时不可用'))
  show('/instruments/fund-1?tab=performance&benchmark=benchmark-1')
  fireEvent.click(await screen.findByRole('button', { name: '重试' }))
  expect(await screen.findByRole('heading', { name: '测试基金 TEST' })).toBeTruthy()
  expect(screen.getByTestId('fund-detail-location').textContent).toContain('tab=performance')
  expect(screen.getByTestId('fund-detail-location').textContent).toContain('benchmark=benchmark-1')
})

it('excludes since-inception metrics from peer ranking and permits median-only small samples', async () => {
  const metric = (metric_key: string) => ({ metric_key, label: metric_key, domain: 'performance', format: 'percent', direction: 'higher', value: 0.1, peer_median: 0.08, peer_p25: 0.05, peer_p75: 0.12, percentile: 77, quartile: 1, rank: 777, sample_count: 1000, peer_sample_count: 999 })
  api.performance.mockResolvedValue({ growth_chart_series: [], annual_returns: [], trailing_returns: [], ranking: null,
    peer_comparison: { status: 'limited_sample', sample_count: 3, metrics: [metric('return_1m'), metric('annualized_return'), metric('volatility')], peer_path: ['Funds'] }, calculation_frequency_profile: null, snapshot_metadata: null })
  show('/instruments/fund-1?tab=performance')
  const median = await screen.findByRole('button', { name: 'vs Median' })
  await waitFor(() => expect((median as HTMLButtonElement).disabled).toBe(false))
  expect((screen.getByRole('button', { name: 'Peer Rank' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(median)
  expect(screen.getByText('同类比较仅使用实际起止日、币种、频率和收益口径一致的固定区间收益；全部可用历史收益和风险指标不参与排名或中位数比较。')).toBeTruthy()
  const annualizedRow = screen.getByRole('cell', { name: 'Ann. Return' }).closest('tr')!
  expect(annualizedRow.lastElementChild?.textContent).toBe('—')
  const volatilityRow = screen.getByRole('cell', { name: 'Ann. Volatility' }).closest('tr')!
  expect(volatilityRow.lastElementChild?.textContent).toBe('—')
})

it('explains unavailable peer windows instead of hiding missing observations', async () => {
  api.performance.mockResolvedValue({ growth_chart_series: [], annual_returns: [], trailing_returns: [], ranking: null,
    peer_comparison: { status: 'insufficient_data', sample_count: 0, metrics: [], peer_path: ['Funds'],
      excluded_windows: [{ instrument_id: 'peer', metric_key: 'return_1m', reason: '计算区间存在缺失观察值或未确认的收益区间' }] },
    calculation_frequency_profile: null, snapshot_metadata: null })
  show('/instruments/fund-1?tab=overview')
  expect(await screen.findByText('部分同类区间不可用：观察缺口或连续性未确认')).toBeTruthy()
  expect((screen.getByRole('button', { name: 'Peer Rank' }) as HTMLButtonElement).disabled).toBe(true)
})


it('closes chart settings from an internal control and trigger with Escape and restores focus', async () => {
  show('/instruments/fund-1?tab=performance&currency=CNY')
  const trigger = await screen.findByRole('button', { name: 'Chart settings' })
  fireEvent.click(trigger)
  expect(trigger.getAttribute('aria-expanded')).toBe('true')
  const menu = trigger.closest('.instrument-chart-settings-menu')!
  const control = menu.querySelectorAll<HTMLButtonElement>('button')[1]
  control.focus()
  fireEvent.keyDown(control, { key: 'Escape' })
  expect(trigger.getAttribute('aria-expanded')).toBe('false')
  expect(document.activeElement).toBe(trigger)
  fireEvent.click(trigger)
  fireEvent.keyDown(trigger, { key: 'Escape' })
  expect(trigger.getAttribute('aria-expanded')).toBe('false')
})

it('withholds return and drawdown plots for unconfirmed intervals while retaining NAV quotes', async () => {
  const nav = await api.nav()
  api.nav.mockResolvedValue({ ...nav, return_series: intervalEvidence(nav.calculation_series, true), calculation_frequency_profile: { ...nav.calculation_frequency_profile, gap_count: 8 } })
  const { container } = show('/instruments/fund-1?tab=performance&currency=CNY')
  expect(await screen.findByText(/回撤图不可用/)).toBeTruthy()
  expect(container.querySelector('.instrument-drawdown-chart')).toBeNull()
  expect(container.querySelector('.instrument-line-chart')).toBeNull()
  expect(container.querySelector('.instrument-series-legend')?.textContent).not.toContain('4.00%')
  expect(await screen.findByText(/收益图不可用/)).toBeTruthy()
})


it('distinguishes overview metrics still loading from missing values', async () => {
  api.nav.mockImplementation(() => new Promise(() => {}))
  const { container } = show('/instruments/fund-1?currency=CNY')
  await screen.findByRole('heading', { name: '测试基金 TEST' })
  expect(container.querySelector('.fund-overview-key-metrics')?.textContent).toContain('加载中…')
  expect(container.querySelector('.fund-overview-key-metrics')?.textContent).not.toContain('—')
})

it.each(['fund', 'benchmark'] as const)('keeps recent confirmed returns but rejects rebased %s history in the matrix', async segmentedSide => {
  const template = await api.nav()
  const dates = ['2025-12-31', '2026-08-31', '2026-09-04']
  const data = (id: string, segmented: boolean) => {
    const values = segmented ? [102, 1, 1.1] : [1, 1.02, 1.122]
    const points = dates.map((date, i) => ({ date, value: values[i], ...(segmented ? { segment_id: i === 0 ? 'old' : 'new' } : {}) }))
    return { ...template, fund_id: id, return_kind: 'total_return', return_series_status: segmented ? 'partial' : 'ready',
      return_segment_breaks: segmented ? [{ start_date: dates[0], end_date: dates[1] }] : [],
      series: points, calculation_series: points,
      rows: points.map((p, i) => ({ ...template.rows[0], as_of_date: p.date, nav: 1 + i * .02,
        nav_with_dividend: p.value, selected_value: p.value, segment_id: 'segment_id' in p ? p.segment_id : undefined })),
    }
  }
  api.resolve.mockResolvedValue({ detail_supported: true, canonical_instrument_id: 'benchmark-1', instrument_name: '测试基准', primary_identifier: 'BENCH', instrument_type: 'index' })
  api.nav.mockImplementation((id: string) => Promise.resolve(data(id, segmentedSide === (id === 'benchmark-1' ? 'benchmark' : 'fund'))))
  const { container } = show('/instruments/fund-1?tab=overview&benchmark=benchmark-1&basis=nav')
  await screen.findByText('Rolling Risk')
  await waitFor(() => expect(screen.getByRole('button', { name: /基准比较口径: 标的/ })).toBeTruthy())
  const table = container.querySelector('.instrument-metrics-table') as HTMLElement
  fireEvent.click(screen.getByRole('button', { name: '全部周期' }))
  const headers = [...table.querySelectorAll('thead th')]
  const cell = (label: string, period: string) => within(table).getByText(label).closest('tr')!.querySelectorAll('td')[headers.findIndex(h => period === 'SI' ? /^(共同历史|全部可用历史)/.test(h.textContent || '') : h.textContent?.startsWith(period))]
  expect(cell('Period Return', 'SI').querySelector('strong')?.textContent).toBe('—')
  expect(cell('Period Return', 'YTD').querySelector('strong')?.textContent).toBe('—')
  expect(cell('Period Return', 'MTD').querySelector('strong')?.textContent).toContain('10.00%')
  expect(cell('Excess Return', 'MTD').querySelector('strong')?.textContent).toContain('0.00%')
  // Unit NAV display does not change the matrix's canonical return basis.
  fireEvent.click(screen.getByRole('button', { name: 'Chart settings' }))
  fireEvent.click(screen.getByRole('button', { name: '复权累计净值' }))
  expect(cell('Period Return', 'SI').querySelector('strong')?.textContent).toBe('—')
  expect(cell('Period Return', 'MTD').querySelector('strong')?.textContent).toContain('10.00%')
})

it('keeps drawdown on dividend-reinvested NAV when unit NAV falls on a distribution date', async () => {
  const template = await api.nav()
  const values = [100, 110, 105]
  const points = template.calculation_series.map((p: { date: string }, i: number) => ({ ...p, value: values[i] }))
  api.nav.mockResolvedValue({ ...template, series: points, calculation_series: points,
    rows: template.rows.map((row: object, i: number) => ({ ...row, nav: [100, 110, 80][i], nav_with_dividend: values[i], selected_value: values[i] })) })
  const { container } = show('/instruments/fund-1?tab=overview&basis=nav')
  await screen.findByText('Rolling Risk')
  const current = () => container.querySelector('.instrument-drawdown-header-value-primary')?.textContent
  expect(current()).toBe('-4.55%')
  fireEvent.click(screen.getByRole('button', { name: 'Chart settings' }))
  fireEvent.click(screen.getByRole('button', { name: '复权累计净值' }))
  expect(current()).toBe('-4.55%')
})

it('retains a daily fund peak between weekly benchmark endpoints and discloses frequency alignment', async () => {
  const template = await api.nav()
  const points = [100, 100, 100, 100, 500, 110, 105, 100].map((value, index) => ({ date: `2026-09-${String(index + 1).padStart(2, '0')}`, value }))
  const source = (id: string) => {
    const isBenchmark = id === 'benchmark-1'
    const selected = isBenchmark ? [points[0], points[points.length - 1]] : points
    return { ...template, fund_id: id, return_kind: 'total_return', series: selected, calculation_series: selected,
      return_series: intervalEvidence(selected), calculation_frequency_profile: { ...template.calculation_frequency_profile,
        resolved_frequency: isBenchmark ? 'weekly' : 'daily', gap_detection_basis: 'market_calendar:24/7' },
      rows: selected.map(point => ({ ...template.rows[0], as_of_date: point.date, nav: point.value, nav_with_dividend: point.value, selected_value: point.value })) }
  }
  api.resolve.mockResolvedValue({ detail_supported: true, canonical_instrument_id: 'benchmark-1', instrument_name: 'Weekly comparison', primary_identifier: 'WEEKLY', instrument_type: 'public_fund' })
  api.nav.mockImplementation((id: string) => Promise.resolve(source(id)))
  const { container } = show('/instruments/fund-1?tab=overview&benchmark=benchmark-1')
  await screen.findByText(/披露频率：标的 daily \/ 基准 weekly/)
  const table = container.querySelector('.instrument-metrics-table') as HTMLElement
  fireEvent.click(screen.getByRole('button', { name: '全部周期' }))
  const headers = [...table.querySelectorAll('thead th')]
  const oneWeek = headers.findIndex(header => header.textContent?.startsWith('1W'))
  const maxDrawdown = within(table).getByText('Max DD').closest('tr')!.querySelectorAll('td')[oneWeek]
  expect(maxDrawdown.querySelector('strong')?.textContent).toBe('-80.00%')
})

it.each([false, true])('scopes interval completeness to the selected window; recent gap=%s', async recentGap => {
  const template = await api.nav()
  const points = [{ date: '2025-12-31', value: 100 }, { date: '2026-08-28', value: 100 },
    { date: '2026-09-15', value: 110 }, { date: '2026-09-30', value: 105 }]
  const evidence = intervalEvidence(points)
  evidence.intervals[recentGap ? 2 : 0].status = 'unavailable'
  api.nav.mockResolvedValue({ ...template, series: points, calculation_series: points, return_series: evidence,
    calculation_frequency_profile: { ...template.calculation_frequency_profile, gap_count: 1 },
    rows: points.map(point => ({ ...template.rows[0], as_of_date: point.date, nav: point.value, nav_with_dividend: point.value, selected_value: point.value })) })
  const { container } = show('/instruments/fund-1?tab=overview')
  await screen.findByText('Rolling Risk')
  const table = container.querySelector('.instrument-metrics-table') as HTMLElement
  fireEvent.click(screen.getByRole('button', { name: '全部周期' }))
  const headers = [...table.querySelectorAll('thead th')]
  const cell = (label: string, period: string) => within(table).getByText(label).closest('tr')!.querySelectorAll('td')[headers.findIndex(header => period === 'SI' ? /^(共同历史|全部可用历史)/.test(header.textContent || '') : header.textContent?.startsWith(period))]
  expect(cell('Period Return', 'SI').querySelector('strong')?.textContent).toBe('—')
  expect(cell('Period Return', '1M').querySelector('strong')?.textContent).toBe(recentGap ? '—' : '5.00%')
  expect(cell('Max DD', '1M').querySelector('strong')?.textContent).toBe(recentGap ? '—' : '-4.55%')
  if (recentGap) expect(headers.find(header => header.textContent?.startsWith('1M'))?.textContent).toContain('观察不完整或未确认')
})

it('keeps comparison loading distinct from missing common dates, and exposes all matrix periods', async () => {
  window.history.replaceState({}, '', '?lang=en')
  const template = await api.nav()
  let finish!: (value: unknown) => void
  api.resolve.mockResolvedValue({ detail_supported: true, canonical_instrument_id: 'benchmark-1', instrument_name: 'Benchmark', primary_identifier: 'BENCH', instrument_type: 'index' })
  api.nav.mockImplementation((id: string) => id === 'benchmark-1' ? new Promise(resolve => { finish = resolve }) : Promise.resolve(template))
  const { container } = show('/instruments/fund-1?tab=overview&benchmark=benchmark-1')
  await screen.findByText('Loading comparison data; common observation windows will appear when ready.')
  const table = container.querySelector('.instrument-metrics-table')!
  expect(table.getAttribute('aria-busy')).toBe('true')
  expect(table.textContent).not.toContain('Insufficient coverage')
  expect(screen.queryByText(/no common date/)).toBeNull()
  expect(table.querySelectorAll('thead th')).toHaveLength(7)
  fireEvent.click(screen.getByRole('button', { name: 'All periods' }))
  expect(table.querySelectorAll('thead th')).toHaveLength(12)
  await act(async () => finish({ ...template, fund_id: 'benchmark-1' }))
  await waitFor(() => expect(table.getAttribute('aria-busy')).toBe('false'))
  expect(screen.queryByText(/Loading comparison data/)).toBeNull()
})

it('uses current NAV provenance instead of historical reference refresh timestamps', async () => {
  api.reference.mockResolvedValue({ instrument_id: 'fund-1', instrument_type: 'private_fund', provider: 'manual', fetched_at: '2020-01-01T00:00:00Z',
    source: { source_location: 'Configured mailbox', refresh_status: 'old-channel-error', market_data_updated_at: '2020-01-01T00:00:00Z' }, sections: {}, section_errors: {},
    current_data: { latest_nav: { as_of_date: '2026-09-30', provider: 'email-route:20562', source_folder: '华能信托', source_attachment: '净值公告.xlsx', source_sent_at: '2026-10-05T01:02:03Z', imported_at: '2026-10-05T03:04:05Z' }, last_checked_at: '2026-10-13T00:05:06Z', check_status: 'success', check_message: 'No newer NAV attachment.', canonical_updated_at: '2026-10-13T00:05:07Z' } })
  show('/instruments/fund-1?tab=info&section=monitoring')
  const provenance = await screen.findByRole('region', { name: '当前净值与来源' })
  expect(provenance.textContent).toContain('2026-09-30')
  expect(provenance.textContent).toContain('华能信托')
  expect(within(provenance).getByText('邮件导入').getAttribute('title')).toBe('email-route:20562')
  expect(provenance.textContent).toContain('净值公告.xlsx')
  expect(provenance.textContent).toContain('2026-10-05 01:02:03 UTC')
  expect(provenance.textContent).toContain('2026-10-05 03:04:05 UTC')
  expect(provenance.textContent).toContain('2026-10-13 00:05:06 UTC')
  expect(provenance.textContent).not.toContain('00:05:07')
  expect(screen.queryByText('old-channel-error')).toBeNull()
  expect(screen.queryByText('Last data update')).toBeNull()
  expect(screen.getByText('Configured mailbox')).toBeTruthy()
})


it('labels the full observed fund sample as available history, without claiming fund inception', async () => {
  const { container } = show('/instruments/fund-1?currency=CNY')
  await screen.findByText('Rolling Risk')
  const table = container.querySelector('.instrument-metrics-table') as HTMLElement
  const historyHeader = within(table).getByRole('columnheader', { name: /全部可用历史/ })
  expect(historyHeader.textContent).toContain('2025-12-31')
  expect(within(table).queryByRole('columnheader', { name: /成立以来|^SI/ })).toBeNull()
})
