// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import RiskOfficerPanel, { type RiskOfficerReview } from '../../../../../packages/ui/src/RiskOfficerPanel'

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { cleanup(); vi.useRealTimers() })

function payload(id: string, overrides: Partial<RiskOfficerReview> = {}): RiskOfficerReview {
  return {
    available: true, scope: { kind: 'instrument', id, name: `标的 ${id}` }, input_as_of: '2026-09-06',
    counts: { research: 2, quantitative: 1, coverage: 1 },
    instruments: [{ instrument_id: id, name: `标的 ${id}`, as_of_date: '2026-09-06' }],
    limitations: ['底层敞口仍待核实。'], latest_completed: null, latest_run: null, ...overrides,
  }
}

function completed(id: string): NonNullable<RiskOfficerReview['latest_completed']> {
  return { run_id: `saved-${id}`, status: 'completed', completed_at: '2026-09-06T08:00:00+08:00',
    input_as_of: '2026-09-06', summary: `${id} 的既有研判结论。`, stale: false, limitations: [],
    priorities: Array.from({ length: 4 }, (_, index) => ({ title: `优先事项 ${index + 1}`,
      analysis: `第 ${index + 1} 项分析。`, next_watch: `第 ${index + 1} 项后续证据。`,
      instrument_ids: [id], case_ids: [`case-${index}`] })),
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
const load = async () => { await act(async () => {}) }

it('isolates late reads and submissions when the scope changes', async () => {
  const oldRead = deferred<RiskOfficerReview>()
  const oldPost = deferred<{ run_id: string; status: string }>()
  const request = vi.fn().mockImplementation((path: string, init?: RequestInit) => {
    if (init?.method === 'POST') return oldPost.promise
    if (path === '/risk/review?instrument_id=a') return oldRead.promise
    const id = path.includes('instrument_id=b') ? 'b' : 'c'
    return Promise.resolve(payload(id, { latest_completed: completed(id) }))
  })
  const onCompleted = vi.fn()
  const { rerender } = render(<RiskOfficerPanel request={request} scopeQuery="instrument_id=a" onCompleted={onCompleted} />)
  rerender(<RiskOfficerPanel request={request} scopeQuery="instrument_id=b&ignored=extra" onCompleted={onCompleted} />)
  await load()
  expect(screen.getByText('b 的既有研判结论。')).toBeTruthy()
  await act(async () => { oldRead.resolve(payload('a', { latest_completed: completed('a') })) })
  expect(screen.queryByText('a 的既有研判结论。')).toBeNull()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '更新研判' })) })
  expect(request).toHaveBeenCalledWith('/risk/review/runs', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ instrument_id: 'b' }),
  }))
  rerender(<RiskOfficerPanel request={request} scopeQuery="watchlist_id=c" onCompleted={onCompleted} />)
  expect(screen.queryByText('b 的既有研判结论。')).toBeNull()
  await load()
  const calls = request.mock.calls.length
  await act(async () => { oldPost.resolve({ run_id: 'obsolete-b', status: 'queued' }) })
  await act(async () => { await vi.advanceTimersByTimeAsync(8000) })
  expect(screen.getByText('c 的既有研判结论。')).toBeTruthy()
  expect(request).toHaveBeenCalledTimes(calls)
  expect(onCompleted).not.toHaveBeenCalled()
})

it('keeps the last completed result after failure and honestly shows stale and unavailable state', async () => {
  const saved = { ...completed('a'), stale: true }
  saved.priorities[0].source_ids = ['risk-performance:a']
  saved.evidence_sources = { 'risk-performance:a': { title: '标的 A · 已留存业绩', start_date: '2026-06-26', end_date: '2026-09-03', currency: 'CNY', frequency: 'daily' } }
  const request = vi.fn().mockResolvedValue(payload('a', { available: false, latest_completed: saved,
    latest_run: { run_id: 'failed-a', status: 'failed', created_at: '2026-09-07T08:00:00+08:00',
      completed_at: '2026-09-07T08:01:00+08:00', message: '本次核证未完成。' },
  }))
  const onCompleted = vi.fn()
  render(<RiskOfficerPanel request={request} scopeQuery="instrument_id=a" onCompleted={onCompleted} />)
  await load()
  expect(screen.getByText('本次研判失败：本次核证未完成。')).toBeTruthy()
  expect(screen.getByText('已有结论已过期，输入发生变化，请更新研判。')).toBeTruthy()
  expect(screen.getByText('风险研判服务不可用，暂时无法更新。')).toBeTruthy()
  expect((screen.getByRole('button', { name: '更新研判' }) as HTMLButtonElement).disabled).toBe(true)
  const result = screen.getByRole('region', { name: '最近完成的研判' })
  expect(within(result).getByText(saved.summary).closest('details')).toBeNull()
  expect(within(screen.getByRole('list', { name: '优先事项' })).getAllByRole('heading', { level: 4 })).toHaveLength(3)
  const remaining = screen.getByText('其余研判 · 1 项').closest('details')!
  expect(remaining.open).toBe(false)
  expect(within(remaining).getByText('优先事项 4')).toBeTruthy()
  fireEvent.click(screen.getByText('其余研判 · 1 项'))
  expect(within(result).getAllByRole('heading', { level: 4 })).toHaveLength(4)
  expect(screen.getByText('第 1 项分析。').closest('details')?.open).toBe(false)
  fireEvent.click(screen.getAllByText('分析与下一步')[0])
  expect(screen.getByText('第 1 项后续证据。')).toBeTruthy()
  expect(screen.getByText('标的 A · 已留存业绩').parentElement?.textContent).toBe('依据：标的 A · 已留存业绩 · 2026-06-26 至 2026-09-03 · CNY · 日度')
  expect(screen.queryByRole('link')).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(12000) })
  expect(request).toHaveBeenCalledTimes(1)
  expect(onCompleted).not.toHaveBeenCalled()
})

it('starts only on request, polls running work every four seconds and notifies once at completion', async () => {
  let started = false
  let finished = false
  const request = vi.fn().mockImplementation(async (_path: string, init?: RequestInit) => {
    if (init?.method === 'POST') { started = true; return { run_id: 'run-p', status: 'queued' } }
    return payload('p', { scope: { kind: 'portfolio', id: 'p', name: '组合 P' },
      latest_completed: finished ? completed('p') : null,
      latest_run: started ? { run_id: 'run-p', status: finished ? 'completed' : 'running',
        created_at: '2026-09-07T08:00:00+08:00', completed_at: finished ? '2026-09-07T08:01:00+08:00' : null, message: '' } : null,
    })
  })
  const onCompleted = vi.fn()
  render(<RiskOfficerPanel request={request} scopeQuery="portfolio_id=p" onCompleted={onCompleted} />)
  await load()
  expect(screen.getByText('尚未研判。点击“更新研判”发起首次研判。')).toBeTruthy()
  expect(screen.getByText('研究上报').nextElementSibling?.textContent).toBe('2')
  expect(screen.getByText('量化触发').nextElementSibling?.textContent).toBe('1')
  expect(screen.getByText('监测受限').nextElementSibling?.textContent).toBe('1')
  expect(request).toHaveBeenCalledTimes(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(8000) })
  expect(request).toHaveBeenCalledTimes(1)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '更新研判' })) })
  expect(request).toHaveBeenCalledWith('/risk/review/runs', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ portfolio_id: 'p' }),
  }))
  expect((screen.getByRole('button', { name: '研判进行中…' }) as HTMLButtonElement).disabled).toBe(true)
  const calls = request.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(3999) })
  expect(request).toHaveBeenCalledTimes(calls)
  finished = true
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  expect(screen.getByText('p 的既有研判结论。')).toBeTruthy()
  expect(onCompleted).toHaveBeenCalledOnce()
  expect((screen.getByRole('button', { name: '更新研判' }) as HTMLButtonElement).disabled).toBe(false)
  const completedCalls = request.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(12000) })
  expect(request).toHaveBeenCalledTimes(completedCalls)
  expect(onCompleted).toHaveBeenCalledOnce()
})

it('rereads current inputs after a case changes without creating another analysis', async () => {
  const request = vi.fn().mockResolvedValue(payload('a', { latest_completed: completed('a') }))
  const { rerender } = render(<RiskOfficerPanel request={request} scopeQuery="instrument_id=a" refreshToken={0} />)
  await load()
  request.mockResolvedValue(payload('a', { latest_completed: { ...completed('a'), stale: true } }))
  rerender(<RiskOfficerPanel request={request} scopeQuery="instrument_id=a" refreshToken={1} />)
  await load()
  expect(screen.getByText('已有结论已过期，输入发生变化，请更新研判。')).toBeTruthy()
  expect(request).toHaveBeenCalledTimes(2)
  expect(request.mock.calls.every(([, init]) => !init?.method)).toBe(true)
})

it('supports portfolio-level and local derivative priorities without inventing Watchlist instruments', async () => {
  const saved = completed('p')
  saved.priorities = [
    { title: '组合预算偏离', analysis: '依据已保存的组合风险口径复核偏离。', next_watch: '查看当前目标与风险贡献。', instrument_ids: [], holding_ids: [], case_ids: [], source_ids: ['portfolio-risk'] },
    { title: 'FCN 接票义务', analysis: '需核对票据条款与实际观察结果。', next_watch: '查看票据合同。', instrument_ids: [], holding_ids: ['fcn-local'], case_ids: [], source_ids: ['fcn-terms'] },
    { title: '期权行权义务', analysis: '需核对实物交割义务。', next_watch: '查看期权合同。', instrument_ids: [], holding_ids: ['option-local'], case_ids: [], source_ids: ['option-terms'] },
    { title: '相关性联动变化', analysis: '比较相同窗口与口径。', next_watch: '查看组合风险页。', instrument_ids: [], holding_ids: [], case_ids: [], source_ids: ['portfolio-risk'] },
  ]
  saved.evidence_sources = {
    'portfolio-risk': { title: '已保存的组合风险与目标', detail_path: '/portfolios/p/risk', end_date: '2026-09-06', currency: 'USD', frequency: 'daily' },
    'fcn-terms': { title: 'FCN 条款与义务', detail_path: '/portfolios/p/holdings/fcn-local?as_of_date=2026-09-06', end_date: '2026-09-06' },
    'option-terms': { title: '期权条款与义务', detail_path: '/portfolios/p/holdings/option-local', end_date: '2026-09-06' },
  }
  const request = vi.fn().mockResolvedValue(payload('p', {
    scope: { kind: 'portfolio', id: 'p', name: '组合 P' }, instruments: [], latest_completed: saved,
    holdings: [
      { holding_id: 'fcn-local', name: '本地 FCN 票据', detail_path: '/portfolios/p/holdings/fcn-local' },
      { holding_id: 'option-local', name: '本地卖出期权', detail_path: '/portfolios/p/holdings/option-local' },
    ],
  }))
  render(<RiskOfficerPanel request={request} scopeQuery="portfolio_id=p" />)
  await load()
  const priorities = screen.getByRole('list', { name: '优先事项' })
  expect(within(priorities).getByText('组合整体')).toBeTruthy()
  expect(within(priorities).getByRole('link', { name: '本地 FCN 票据' }).getAttribute('href')).toBe('http://localhost:5174/portfolios/p/holdings/fcn-local')
  expect(within(priorities).getByRole('link', { name: '本地卖出期权' }).getAttribute('href')).toBe('http://localhost:5174/portfolios/p/holdings/option-local')
  fireEvent.click(within(priorities).getAllByText('分析与下一步')[0])
  expect(within(priorities).getByRole('link', { name: '已保存的组合风险与目标' }).getAttribute('href')).toBe('http://localhost:5174/portfolios/p/risk')
  fireEvent.click(within(priorities).getAllByText('分析与下一步')[1])
  expect(screen.getByRole('link', { name: 'FCN 条款与义务' }).getAttribute('href')).toContain('/portfolios/p/holdings/fcn-local?as_of_date=2026-09-06')
  fireEvent.click(screen.getByText('其余研判 · 1 项'))
  expect(screen.getByRole('heading', { name: '相关性联动变化' })).toBeTruthy()
  expect(screen.getAllByRole('link').every((link) => !link.getAttribute('href')?.includes('/instruments/'))).toBe(true)
})
