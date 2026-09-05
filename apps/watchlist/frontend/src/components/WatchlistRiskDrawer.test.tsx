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
const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ fetchJson: request }))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
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
  expect(screen.getByText('b 风险事项')).toBeTruthy()
  expect(request).toHaveBeenCalledWith('/api/risk?watchlist_id=3', undefined)
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
