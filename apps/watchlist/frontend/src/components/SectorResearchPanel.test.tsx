// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SectorResearchPanel, { type SectorEventRecord } from './SectorResearchPanel'
import InstrumentRiskPanel from './InstrumentRiskPanel'
const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ fetchJson: request }))
vi.mock('./ResearchDossierPanel', () => ({ default: (props: { instrumentId: string; variant: string; readingMode: boolean }) => <div data-testid="canonical-dossier" data-variant={props.variant} data-reading={String(props.readingMode)}>已保存的同源研究 · {props.instrumentId}</div> }))
vi.mock('../../../../../packages/ui/src/RiskOfficerPanel', () => ({ default: () => null }))
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-06T08:00:00+08:00')) })
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers() })
const review = (status: string) => ({ run_id: 'run-1', status, checked_at: '2026-09-06T08:00:00+08:00', summary: '运行记录不能充当当前判断', coverage: ['未取得最新持仓数据'] })
const payload = (status = 'completed') => ({ available: true, sectors: [{ instrument_id: 'xlk-us', ticker: 'XLK', sector_name: '科技', latest_review: review(status) }], events: [] })
const load = async () => { await act(async () => {}) }
const event = (): SectorEventRecord => ({ case_id: 'cloud-demand', instrument_id: 'xlk-us', event_key: 'cloud-demand', title: '云需求新增变化', body: '分析当前指引如何改变盈利预期。', direction: 'opportunity', information_type: 'fact', confidence: 'confirmed', next_watch: '', published_at: '2026-09-05', occurred_at: '2026-09-05', discovered_at: null, updated_at: null, trigger_active: true, status: 'open', sources: [{ source_id: 'source-1', title: '公司原始公告', url: 'https://example.com/earnings' }], coverage: [], history: [] })

it('uses sector responses only for status while reading the canonical dossier', async () => {
  request.mockResolvedValue({ ...payload(), events: [event()] })
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  expect(screen.getByTestId('canonical-dossier').textContent).toContain('xlk-us')
  expect(screen.getByRole('heading', { name: '研究概览' })).toBeTruthy()
  expect(screen.queryByText(review('completed').summary)).toBeNull()
  expect(screen.queryByText(event().title)).toBeNull()
  expect(request.mock.calls.some(([path]) => path.includes('/context'))).toBe(false)
})

it.each(['failed', 'running'])('keeps saved research readable when the latest update is %s', async status => {
  request.mockResolvedValue(payload(status))
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  expect(screen.getByTestId('canonical-dossier')).toBeTruthy()
  if (status === 'failed') expect(screen.getByText(`本轮未完成原因：${review(status).summary}`).closest('details')).toBeNull()
  else expect(screen.getByRole('button', { name: '研究更新中…' }).hasAttribute('disabled')).toBe(true)
})

it('does not hide saved research when the status service is unavailable or has no sector registration', async () => {
  request.mockRejectedValue(new Error('连接中断'))
  const { rerender } = render(<SectorResearchPanel instrumentId="private-fund" />)
  await load()
  expect(screen.getByTestId('canonical-dossier')).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toContain('连接中断')
  request.mockResolvedValue({ available: true, sectors: [], events: [] })
  rerender(<SectorResearchPanel instrumentId="another-fund" />)
  await load()
  expect(screen.getByTestId('canonical-dossier').textContent).toContain('another-fund')
  expect(screen.getByText('尚未完成研究')).toBeTruthy()
})

it('uses the same dossier in summary and report modes and keeps report mode read only', async () => {
  request.mockResolvedValue(payload())
  const open = vi.fn()
  const { rerender } = render(<SectorResearchPanel instrumentId="xlk-us" variant="summary" onOpenEvents={open} />)
  await load()
  expect(screen.getByTestId('canonical-dossier').getAttribute('data-variant')).toBe('summary')
  fireEvent.click(screen.getByRole('button', { name: '阅读完整研究' }))
  expect(open).toHaveBeenCalledOnce()
  rerender(<SectorResearchPanel instrumentId="xlk-us" readingMode />)
  expect(screen.getByTestId('canonical-dossier').getAttribute('data-reading')).toBe('true')
  expect(screen.queryByRole('button', { name: '更新研究' })).toBeNull()
  rerender(<SectorResearchPanel instrumentId="xlk-us" variant="status" />)
  expect(screen.queryByTestId('canonical-dossier')).toBeNull()
})

it('prevents running without a data connection while preserving saved content', async () => {
  request.mockResolvedValue({ ...payload(), available: false, message: '尚未配置数据连接。' })
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  expect(screen.getByText('尚未配置数据连接。')).toBeTruthy()
  expect(screen.getByRole('button', { name: '更新研究' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByTestId('canonical-dossier')).toBeTruthy()
})

it('starts only the selected instrument and stops polling after completion', async () => {
  let started = false, complete = false
  request.mockImplementation(async (_path: string, init?: RequestInit) => {
    if (init?.method === 'POST') { started = true; return { run_id: 'run-1', status: 'queued' } }
    return payload(started ? complete ? 'completed' : 'running' : 'completed')
  })
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '更新研究' })) })
  expect(request).toHaveBeenCalledWith('/api/sector-research/runs', { method: 'POST', body: JSON.stringify({ instrument_ids: ['xlk-us'] }) })
  complete = true
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  const calls = request.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
  expect(request.mock.calls.length).toBe(calls)
})

it('shows reflection only for a completed check, without making it a conclusion', async () => {
  const data = payload()
  request.mockResolvedValue({ ...data, sectors: [{ ...data.sectors[0], latest_review: { ...review('completed'), reflection: { status: 'insufficient_evidence', summary: '本轮未能核实费用变化', reviewed_update_ids: [] } } }] })
  const { rerender } = render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  const coverage = screen.getByText(/^检查记录与覆盖/).closest('details')!
  expect(coverage.open).toBe(false)
  expect(within(coverage).getByText(/复核证据不足/)).toBeTruthy()
  request.mockResolvedValue({ ...data, sectors: [{ ...data.sectors[0], latest_review: { ...review('failed'), reflection: { status: 'reviewed', summary: '未发布的复核', reviewed_update_ids: [] } } }] })
  rerender(<SectorResearchPanel instrumentId="another" />)
  await load()
  expect(screen.queryByText(/未发布的复核/)).toBeNull()
})

it('shows a shared risk case as withdrawn, with the incorrect body kept in read-only history', async () => {
  const original = event()
  request.mockResolvedValue({ instruments: [{ instrument_id: original.instrument_id, name: '当前标的' }], cases: [{
    case_id: original.case_id, instrument_id: original.instrument_id, signal: 'sector:withdrawn',
    title: original.title, body: original.body, severity: 'attention', status: 'handled', trigger_active: false,
    evidence_json: { direction: 'risk', withdrawn: true, withdrawal_reason: '原文未支持旧稿，已撤回。', withdrawn_at: '2026-09-06T09:00:00+08:00', sources: original.sources },
    history_json: [{ at: '2026-09-06T09:00:00+08:00', action: 'withdrawn', detail: '核证后保留撤回前记录' }],
  }] })
  render(<InstrumentRiskPanel instrumentId={original.instrument_id} />)
  await load()
  expect(screen.queryByText(original.body)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '全部记录 1' }))
  expect(screen.getByText('核证撤回')).toBeTruthy()
  expect(screen.getByText('原文未支持旧稿，已撤回。').closest('details')).toBeNull()
  expect(screen.queryByText('当前未触发')).toBeNull()
  expect(screen.queryByText('已处理')).toBeNull()
  const history = screen.getByText('撤回前记录与来源').closest('details')!
  expect(history.open).toBe(false)
  expect(within(history).getByText(original.body)).toBeTruthy()
  fireEvent.click(screen.getByText('撤回前记录与来源'))
  expect(within(history).getByRole('link', { name: '公司原始公告' }).getAttribute('href')).toBe('https://example.com/earnings')
  expect(screen.queryByRole('button', { name: '重新打开' })).toBeNull()
  expect(screen.queryByRole('button', { name: '保存跟进' })).toBeNull()
})

it('opens list risk directly without research execution controls', async () => {
  request.mockResolvedValue({ instruments: [{ instrument_id: 'xlk-us', name: 'XLK 信息技术' }], cases: [] })
  render(<InstrumentRiskPanel watchlistId="sector-list" />)
  await load()
  expect(screen.queryByLabelText('每日研究更新')).toBeNull()
  expect(screen.queryByRole('button', { name: '更新研究' })).toBeNull()
  expect(screen.getByRole('button', { name: '重点关注 0' })).toBeTruthy()
  expect(request).toHaveBeenCalledTimes(1)
  expect(request).toHaveBeenCalledWith('/api/risk?watchlist_id=sector-list', undefined)
})
