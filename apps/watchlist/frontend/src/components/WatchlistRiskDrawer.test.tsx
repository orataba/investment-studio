// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import WatchlistRiskDrawer from './WatchlistRiskDrawer'
import InstrumentRiskDrawer from './InstrumentRiskDrawer'
const request = vi.hoisted(() => vi.fn())
const permission = vi.hoisted(() => ({ canWrite: true }))
vi.mock('./AccountBoundary', () => ({ useCanWriteTeam: () => permission.canWrite }))
vi.mock('../lib/api', () => ({ fetchJson: request }))
vi.mock('./SectorResearchPanel', () => ({ default: () => null }))
vi.mock('../../../../../packages/ui/src/RiskOfficerPanel', () => ({ default: ({ scopeQuery, canRun }: { scopeQuery: string; canRun: boolean }) => <span data-testid="officer-scope" data-can-run={String(canRun)}>{scopeQuery}</span> }))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  permission.canWrite = true
})
const caseFor = (id: string, severity = 'attention') => ({
  case_id: id,
  instrument_id: id,
  title: `${id} 风险事项`,
  body: '复核价格影响',
  signal: 'manual',
  severity,
  trigger_active: true,
  status: 'open',
  observed_on: '2026-09-04',
  created_at: '2026-09-04',
  updated_at: '2026-09-04',
  follow_up_date: null,
  evidence_json: {},
  history_json: [],
})
it('lets team readers inspect evidence and ask privately without shared risk mutations', async () => {
  permission.canWrite = false
  request.mockResolvedValue({ instruments: [{ instrument_id: 'a', name: '标的 A' }], cases: [caseFor('a')] })
  render(<InstrumentRiskDrawer instrumentId="a" instrumentName="标的 A" onClose={vi.fn()} onAskAssistant={vi.fn()} />)
  await screen.findByText('a 风险事项')
  expect(screen.getByRole('button', { name: '问助手' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: '补充记录' })).toBeNull()
  expect(screen.queryByRole('button', { name: '保存跟进' })).toBeNull()
  expect(screen.queryByRole('button', { name: '保存提醒设置' })).toBeNull()
  expect(screen.getByTestId('officer-scope').getAttribute('data-can-run')).toBe('false')
  expect(request.mock.calls.every(([, init]) => !init?.method)).toBe(true)
})
it('scopes instrument detail risk to the instrument even when opened from a list', async () => {
  request.mockResolvedValue({ instruments: [{ instrument_id: 'a', name: '标的 A' }], cases: [caseFor('a')] })
  const ask = vi.fn()
  const { container } = render(<InstrumentRiskDrawer instrumentId="a" instrumentName="标的 A" watchlistId="source-list" onClose={vi.fn()} onAskAssistant={ask} />)
  await screen.findByText('a 风险事项')
  expect(request).toHaveBeenCalledWith('/api/risk?instrument_id=a', undefined)
  expect(screen.getByTestId('officer-scope').textContent).toBe('instrument_id=a')
  expect(screen.getByRole('link', { name: 'a 风险事项' }).getAttribute('href')).toBe('/instruments/a?tab=risk&watchlist=source-list')
  expect(container.querySelector('.risk-readings')?.hasAttribute('open')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '问助手' }))
  expect(ask).toHaveBeenCalledWith(expect.stringContaining('标的 A'))
})
it('keeps the full originating list when locating an instrument and closes only from outside', async () => {
  request.mockResolvedValue({
    instruments: [
      { instrument_id: 'a', name: '标的 A' },
      { instrument_id: 'b', name: '标的 B' },
    ],
    cases: [caseFor('a'), caseFor('b')],
  })
  const close = vi.fn(),
    ask = vi.fn()
  render(
    <WatchlistRiskDrawer
      watchlistId="3"
      watchlistName="当前测试列表"
      focusInstrumentId="b"
      onClose={close}
      onAskAssistant={ask}
      onChanged={vi.fn()}
    />,
  )
  await screen.findByText('a 风险事项')
  expect(screen.getByRole('dialog', { name: '风险提示' })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'a 风险事项' }).getAttribute('href')).toBe('/instruments/a?tab=risk&watchlist=3')
  expect(screen.getByText('b 风险事项')).toBeTruthy()
  expect(request).toHaveBeenCalledWith('/api/risk?watchlist_id=3', undefined)
  expect(screen.getByTestId('officer-scope').textContent).toBe('watchlist_id=3')
  expect(screen.queryByRole('combobox', { name: '筛选标的' })).toBeNull()
  expect(screen.queryByRole('combobox', { name: '关联组合' })).toBeNull()
  fireEvent.click(screen.getAllByRole('button', { name: '问助手' })[0])
  expect(ask).toHaveBeenCalledWith('b', expect.stringContaining('标的 B'))
  expect(close).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('dialog').parentElement!)
  expect(close).toHaveBeenCalledOnce()
})
it('keeps ordinary observations out of attention and retains due follow-ups after a trigger clears', async () => {
  request.mockResolvedValue({
    instruments: ['a', 'b', 'c'].map((id) => ({
      instrument_id: id,
      name: `标的 ${id}`,
    })),
    cases: [
      caseFor('a', 'observation'),
      caseFor('b', 'coverage'),
      { ...caseFor('c'), trigger_active: false, follow_up_date: '2020-01-01' },
    ],
  })
  render(
    <WatchlistRiskDrawer
      watchlistId="3"
      watchlistName="当前测试列表"
      onClose={vi.fn()}
      onAskAssistant={vi.fn()}
      onChanged={vi.fn()}
    />,
  )
  await screen.findByText('当前范围没有触发中的重点风险事项。')
  expect(screen.queryByText('a 风险事项')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '到期待跟进 1' }))
  expect(screen.getByText('c 风险事项')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '监测受限 1' }))
  expect(screen.getByText('b 风险事项')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '全部记录 3' }))
  expect(screen.getByText('a 风险事项')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '补充记录' }))
  expect(screen.getByRole('combobox', { name: '事件涉及的标的' })).toBeTruthy()
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1))
})
it('shows rolling losses and saves instrument-wide review lines, including an explicit disabled period', async () => {
  let asset = {
    instrument_id: 'a', name: '标的 A', instrument_type: 'equity', as_of_date: '2026-09-04',
    freshness: 'fresh', risk: { data_quality: { status: 'ready' }, current_drawdown: -4 },
    drawdown_limit: 10,
    period_limits: { day: 1, week: 3, month: 5, quarter: 8 } as Record<string, number | null>,
    period_readings: [{ period: 'week', label: '近1周', observations: 5, start_date: '2026-08-28', end_date: '2026-09-04', return_pct: -4, limit_pct: 3, breached: true, limitation: null }],
  }
  request.mockImplementation(async (_path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') { asset = { ...asset, ...JSON.parse(init.body as string) }; return {} }
    return { instruments: [{ ...asset }], cases: [] }
  })
  render(<WatchlistRiskDrawer watchlistId="3" watchlistName="当前列表" focusInstrumentId="a" onClose={vi.fn()} onAskAssistant={vi.fn()} onChanged={vi.fn()} />)
  await screen.findByText('价格风险与提醒设置')
  fireEvent.click(screen.getByText('价格风险与提醒设置'))
  expect(screen.getByRole('table', { name: '标的 A 区间跌幅监测' }).textContent).toContain('-4.00%')
  expect(screen.getByText('需复核')).toBeTruthy()
  fireEvent.click(screen.getByText('提醒设置'))
  fireEvent.change(screen.getByRole('spinbutton', { name: '标的 A 日跌幅复核线' }), { target: { value: '' } })
  fireEvent.change(screen.getByRole('spinbutton', { name: '标的 A 周跌幅复核线' }), { target: { value: '2' } })
  fireEvent.click(screen.getByRole('button', { name: '保存提醒设置' }))
  await screen.findByText('提醒设置已保存')
  expect(request).toHaveBeenCalledWith('/api/risk/rules/a', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ drawdown_limit: 10, period_limits: { day: null, week: 2, month: 5, quarter: 8 } }) }))
})

it.each([
  ['risk', '风险', 'confirmed', '已确认'],
  ['opportunity', '机会', 'reported', '报道线索'],
  ['uncertain', '重大不确定性', 'unverified', '待证实'],
])('shows sector %s direction, evidence timing, and the next observation without changing price rules', async (direction, label, confidence, confidenceLabel) => {
  request.mockResolvedValue({
    instruments: [{ instrument_id: 'xlk-us', name: 'XLK 信息技术' }],
    cases: [{ ...caseFor('xlk-us'), signal: 'sector:cloud-demand', evidence_json: {
      direction, confidence, next_watch: '关注下一次云业务指引。',
      sources: [
        { title: '公司最新指引', url: 'https://example.com/earnings', published_at: '2026-09-06T08:00:00+08:00' },
        { title: '不支持的链接', url: 'javascript:alert(1)', published_at: null },
      ],
    } }],
  })
  const ask = vi.fn()
  render(<WatchlistRiskDrawer watchlistId="sectors" watchlistName="美股行业ETF" onClose={vi.fn()} onAskAssistant={ask} onChanged={vi.fn()} />)
  if (direction === 'opportunity') {
    await screen.findByRole('button', { name: '全部记录 1' })
    expect(screen.queryByText(label)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '全部记录 1' }))
  }
  expect(await screen.findByText(label)).toBeTruthy()
  expect(screen.getByRole('link', { name: 'xlk-us 风险事项' }).getAttribute('href')).toBe('/instruments/xlk-us?tab=events&watchlist=sectors')
  expect(screen.getByText('关注下一次云业务指引。')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '问助手' }))
  expect(ask).toHaveBeenCalledWith('xlk-us', expect.stringContaining(`的${label}事项`))
  fireEvent.click(screen.getByText('跟进与证据'))
  expect(screen.getByText(`证据状态：${confidenceLabel}`)).toBeTruthy()
  expect(screen.getByRole('link', { name: '公司最新指引' }).getAttribute('href')).toBe('https://example.com/earnings')
  expect(screen.getByText('发布时间 2026-09-06T08:00:00+08:00')).toBeTruthy()
  expect(screen.getByText('发布时间待核实')).toBeTruthy()
  expect(screen.queryByRole('link', { name: /不支持的链接/ })).toBeNull()
})

it('keeps recent unresolved risks, uncertainty and price triggers ahead of history across instruments', async () => {
  const cases = [
    { ...caseFor('older'), updated_at: '2026-09-02' },
    { ...caseFor('price'), signal: 'period_loss', updated_at: '2026-09-05' },
    { ...caseFor('uncertain'), signal: 'sector:uncertain', evidence_json: { direction: 'uncertain' }, updated_at: '2026-09-04' },
    { ...caseFor('opportunity'), signal: 'sector:opportunity', evidence_json: { direction: 'opportunity' }, updated_at: '2026-09-06' },
    { ...caseFor('handled'), status: 'handled', updated_at: '2026-09-06' },
    { ...caseFor('resolved'), trigger_active: false, updated_at: '2026-09-06' },
  ]
  request.mockResolvedValue({ instruments: cases.map((item) => ({ instrument_id: item.instrument_id, name: item.instrument_id })), cases })
  const { container } = render(<WatchlistRiskDrawer watchlistId="3" watchlistName="当前列表" onClose={vi.fn()} onAskAssistant={vi.fn()} onChanged={vi.fn()} />)
  await screen.findByRole('button', { name: '重点关注 3' })
  expect(Array.from(container.querySelectorAll('.risk-case h3')).map((node) => node.textContent)).toEqual(['price 风险事项', 'uncertain 风险事项', 'older 风险事项'])
  fireEvent.click(screen.getByRole('button', { name: '全部记录 6' }))
  expect(screen.getByText('opportunity 风险事项')).toBeTruthy()
  expect(screen.getByText('handled 风险事项')).toBeTruthy()
  expect(screen.getByText('resolved 风险事项')).toBeTruthy()
})

it('keeps attention selected when a located instrument only has an opportunity or handled history', async () => {
  request.mockResolvedValue({ instruments: [{ instrument_id: 'a', name: '标的 A' }], cases: [
    { ...caseFor('a'), signal: 'sector:opportunity', evidence_json: { direction: 'opportunity' } },
    { ...caseFor('a'), case_id: 'handled', title: '已处理事项', status: 'handled' },
  ] })
  render(<WatchlistRiskDrawer watchlistId="3" watchlistName="当前列表" focusInstrumentId="a" onClose={vi.fn()} onAskAssistant={vi.fn()} onChanged={vi.fn()} />)
  expect(await screen.findByText('当前范围没有触发中的重点风险事项。')).toBeTruthy()
  expect(screen.getByRole('button', { name: '重点关注 0' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.queryByText('a 风险事项')).toBeNull()
  expect(screen.queryByText('已处理事项')).toBeNull()
})
