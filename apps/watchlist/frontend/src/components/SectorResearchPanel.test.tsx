// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SectorResearchPanel, { type SectorResearch, type SectorEventRecord } from './SectorResearchPanel'
import InstrumentRiskPanel from './InstrumentRiskPanel'
import { LanguageProvider, LanguageSelector } from '../../../../../packages/ui/src/i18n'
import { researchMessages, researchPatterns } from '../researchMessages'

const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ fetchJson: request }))
vi.mock('./ResearchTrackingPanel', () => ({ default: () => null }))
vi.mock('../../../../../packages/ui/src/RiskOfficerPanel', () => ({ default: () => null }))
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-06T08:00:00+08:00')) })
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); window.history.replaceState(null, '', '/') })
const review = (status: string) => ({ run_id: 'run-1', status, checked_at: '2026-09-06T08:00:00+08:00', summary: '需求趋势暂未出现实质变化，继续验证盈利预期。', coverage: ['尚未接入 X 专用来源'] })
const sector = (overrides: Partial<SectorResearch> = {}): SectorResearch => ({ instrument_id: 'xlk-us', ticker: 'XLK', sector_name: '信息技术', latest_review: review('limited'), ...overrides })
const event = (overrides: Partial<SectorEventRecord> = {}): SectorEventRecord => ({
  case_id: 'cloud-demand', instrument_id: 'xlk-us', event_key: 'cloud-demand', title: '云需求新增变化', body: '分析当前指引如何改变盈利预期。',
  direction: 'opportunity', information_type: 'fact', confidence: 'confirmed', next_watch: '关注下一次云业务指引。',
  published_at: '2026-09-05T08:00:00+08:00', occurred_at: '2026-09-05', discovered_at: '2026-09-06T08:00:00+08:00',
  updated_at: '2026-09-06T08:00:00+08:00', trigger_active: true, status: 'open', recording_type: 'new',
  sources: [{ source_id: 'source-1', title: '公司原始公告', url: 'https://example.com/earnings', published_at: '2026-09-05T08:00:00+08:00' }],
  coverage: [], history: [], ...overrides,
})
const currentReview = (value: SectorResearch['latest_review'] | undefined) => value && ({ ...value,
  current_research: value.current_research === undefined ? { investment_view: {
    direction: value.summary, horizon: '', attractiveness: '', risk: '', conviction: '', assumptions: [], source_ids: [],
    updated_at: value.view_updated_at || value.checked_at,
  } } : value.current_research,
})
const payload = (events: SectorEventRecord[] = [], overrides: Partial<SectorResearch> = {}) => {
  const asset = sector(overrides)
  return { available: true, sectors: [{ ...asset, latest_review: currentReview(asset.latest_review),
    last_completed_review: currentReview(asset.last_completed_review) }], events }
}
const load = async () => { await act(async () => {}) }

it('does not invent coverage restrictions when a registered instrument has no research yet', async () => {
  request.mockResolvedValue({ available: true, sectors: [], events: [] })
  const { rerender, container } = render(<SectorResearchPanel instrumentId="other" />)
  await load()
  expect(screen.getByText('尚未完成研究。')).toBeTruthy()
  expect(screen.queryByText(/11 只美股行业 ETF/)).toBeNull()
  rerender(<SectorResearchPanel instrumentId="other" variant="summary" />)
  expect(container.textContent).toBe('')
})

it('shows completed fundamental research for an arbitrary instrument before its risks and progress', async () => {
  const completed = { ...review('completed'), summary: '策略收益主要来自信用利差，当前流动性条款与持仓期限仍需一并评估。' }
  request.mockResolvedValue(payload([], { instrument_id: 'private-fund-1', ticker: 'FUND', sector_name: '私募信用基金', latest_review: completed, last_completed_review: completed }))
  render(<SectorResearchPanel instrumentId="private-fund-1" />)
  await load()
  expect(request).toHaveBeenCalledWith('/api/sector-research?instrument_id=private-fund-1', expect.anything())
  const conclusion = screen.getByRole('region', { name: '当前研究结论' })
  expect(within(conclusion).getByText(completed.summary)).toBeTruthy()
  expect(within(conclusion).getByText(completed.summary).closest('details')).toBeNull()
  expect(conclusion.querySelector('time')?.dateTime).toBe(completed.checked_at)
  expect(screen.getByRole('heading', { name: '研究追踪' })).toBeTruthy()
  expect(screen.getByText('私募信用基金 · 研究已更新')).toBeTruthy()
  const hint = screen.getByRole('button', { name: /研究更新口径:/ })
  expect(hint.closest('.sector-research-heading')).not.toBeNull()
  expect(screen.queryByText('持续检查新信息，只有重要观点、预测或风险变化才形成研究更新。')).toBeNull()
  fireEvent.click(hint)
  expect(screen.getByRole('tooltip').textContent).toContain('持续检查新信息')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('tooltip')).toBeNull()
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(screen.queryByText(/只美股行业 ETF/)).toBeNull()
  expect(screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)).toEqual(['当前研究结论'])
})

it('keeps the last completed conclusion when the latest research update fails', async () => {
  const saved = { ...review('limited'), run_id: 'completed-1', checked_at: '2026-09-05T08:00:00+08:00', summary: '现金流仍支持当前投入，但需要观察新增订单的兑现。' }
  const failed = { ...review('failed'), run_id: 'failed-2', summary: '本次来源获取失败，更新未完成。' }
  request.mockResolvedValue(payload([], { latest_review: failed, last_completed_review: saved }))
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  const conclusion = screen.getByRole('region', { name: '当前研究结论' })
  expect(within(conclusion).getByText(saved.summary)).toBeTruthy()
  expect(within(conclusion).queryByText(failed.summary)).toBeNull()
  expect(conclusion.querySelector('time')?.dateTime).toBe(saved.checked_at)
  expect(within(conclusion).getByRole('status').textContent).toContain('研究更新未完成')
  expect(screen.getByText(`本轮未完成原因：${failed.summary}`).closest('details')?.open).toBe(false)
  expect(screen.getByRole('button', { name: '更新研究' }).hasAttribute('disabled')).toBe(false)
})

it('separates a quiet check from the original view date without creating empty risk or event sections', async () => {
  const originalDate = '2026-09-01T08:00:00+08:00'
  const checked = { ...review('completed'), view_updated_at: originalDate, view_run_id: 'original-view-run', change_kind: 'none' as const, coverage: [] }
  request.mockResolvedValue(payload([], { latest_review: checked, last_completed_review: checked }))
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  const conclusion = screen.getByRole('region', { name: '当前研究结论' })
  expect(Array.from(conclusion.querySelectorAll('time')).map(node => node.dateTime)).toEqual([originalDate, checked.checked_at])
  expect(within(conclusion).getByText(/未发现新增投资变化/)).toBeTruthy()
  expect(screen.queryByRole('region', { name: '当前风险与机会' })).toBeNull()
  expect(screen.queryByRole('region', { name: '研究进展' })).toBeNull()
  await act(async () => { fireEvent.click(screen.getByText('研究来源与覆盖')); await vi.advanceTimersByTimeAsync(0) })
  expect(request.mock.calls.some(([path]) => path === '/api/research/runs/original-view-run/context?section=sources')).toBe(true)
})

it('qualifies a quiet result when the research had material coverage gaps', async () => {
  const checked = { ...review('limited'), change_kind: 'none' as const, coverage: ['未取得最新持仓数据'] }
  request.mockResolvedValue(payload([], { latest_review: checked, last_completed_review: checked }))
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  const conclusion = screen.getByRole('region', { name: '当前研究结论' })
  expect(within(conclusion).getByText(/在已覆盖的信息中未发现新增投资变化/)).toBeTruthy()
  expect(within(conclusion).queryByText('未发现新增投资变化', { exact: true })).toBeNull()
  expect(screen.getByText('未取得最新持仓数据')).toBeTruthy()
})

it.each([true, false])('uses the current structured judgment independently of report prose (summary present: %s)', async hasSummary => {
  const viewTime = '2026-09-02T08:00:00+08:00'
  const summaryTime = '2026-09-03T08:00:00+08:00'
  const investmentView = { direction: '估值有吸引力，但需核实净息差的稳定性。', horizon: '中期', attractiveness: '估值折价',
    risk: '信用成本与净息差', conviction: '中等', assumptions: [], source_ids: ['retained-snapshot'], updated_at: viewTime }
  const checked = { ...review('completed'), change_kind: 'knowledge' as const, summary: hasSummary ? review('completed').summary : '',
    view_updated_at: hasSummary ? summaryTime : null, current_research: { investment_view: investmentView } }
  request.mockResolvedValue(payload([], { instrument_id: '600036-sh', latest_review: checked, last_completed_review: checked }))
  render(<SectorResearchPanel instrumentId="600036-sh" />)
  await load()
  const conclusion = screen.getByRole('region', { name: '当前研究结论' })
  expect(within(conclusion).getByText(investmentView.direction).getAttribute('translate')).toBe('no')
  if (hasSummary) expect(within(conclusion).queryByText(checked.summary)).toBeNull()
  expect(conclusion.querySelector('time')?.dateTime).toBe(viewTime)
  expect(within(conclusion).getByText(/研究资料已更新/)).toBeTruthy()
  expect(conclusion.textContent).not.toContain('沿用')
  expect(screen.queryByText('尚无已发布的投资判断。')).toBeNull()
  expect(screen.queryByText('尚未形成研究结论。')).toBeNull()
})

it('never resurrects old report prose after the current judgment is withdrawn', async () => {
  const checked = { ...review('completed'), change_kind: 'knowledge' as const, summary: '曾经看好但已撤回的报告', current_research: { investment_view: null } }
  request.mockResolvedValue(payload([], { latest_review: checked, last_completed_review: checked }))
  render(<SectorResearchPanel instrumentId="600036-sh" />)
  await load()
  expect(screen.getByText('尚未形成研究结论。')).toBeTruthy()
  expect(within(screen.getByRole('region', { name: '当前研究结论' })).queryByText(checked.summary)).toBeNull()
  expect(screen.getByText('信息技术 · 研究资料已更新')).toBeTruthy()
  expect(screen.queryByText(/投资判断沿用/)).toBeNull()
})

it('translates the updated knowledge and empty-conclusion labels in both directions', async () => {
  window.history.replaceState(null, '', '/?lang=en')
  const checked = { ...review('completed'), change_kind: 'knowledge' as const, summary: '' }
  request.mockResolvedValue(payload([], { latest_review: checked, last_completed_review: checked }))
  render(<LanguageProvider messages={researchMessages} patterns={researchPatterns}><LanguageSelector /><SectorResearchPanel instrumentId="600036-sh" /></LanguageProvider>)
  await load()
  expect(screen.getByText('No research conclusion has been formed yet.')).toBeTruthy()
  expect(screen.getAllByText(/Research materials updated/).length).toBeGreaterThan(0)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await load()
  expect(screen.getByText('尚未形成研究结论。')).toBeTruthy()
  expect(screen.getByText('信息技术 · 研究资料已更新')).toBeTruthy()
})

it('enables fund research updates while preserving saved research', async () => {
  request.mockResolvedValue({ ...payload(), research_enabled: true })
  render(<SectorResearchPanel instrumentId="public-fund" />)
  await load()
  expect(screen.getByRole('button', { name: '更新研究' }).hasAttribute('disabled')).toBe(false)
  expect(screen.getByText(review('limited').summary)).toBeTruthy()
})

it('loads only the saved conclusion’s fetched originals on expansion and shares one source list for a multi-instrument run', async () => {
  const saved = { ...review('completed'), run_id: 'saved-run', checked_at: '2026-09-05T08:00:00+08:00' }
  const latest = { ...review('failed'), run_id: 'failed-run', summary: '最新更新未完成。' }
  const published = '2026-09-04T17:00:00+08:00'
  const retrieved = '2026-09-05T07:30:00+08:00'
  request.mockImplementation(async (path: string) => path === '/api/research/runs/saved-run/context?section=sources' ? { sources: [
      { source_id: 'report', version_id: 'first', title: '已查阅的公司报告', url: 'https://example.com/report', body_available: true, published_at: published, retrieved_at: retrieved },
      { source_id: 'report', version_id: 'revised', title: '同网址的另一原文版本', url: 'https://example.com/report', body_available: true, published_at: null, retrieved_at: null },
      { source_id: 'empty', title: '未取得正文', url: 'https://example.com/empty', body_available: false },
  ] } : { available: true, sectors: Array.from({ length: 11 }, (_, index) => sector({ instrument_id: `fund-${index}`, ticker: `FUND${index}`, latest_review: latest, last_completed_review: saved })), events: [] })
  render(<SectorResearchPanel watchlistId="all-funds" />)
  await load()
  expect(request.mock.calls.map(([path]) => path)).toEqual(['/api/sector-research?watchlist_id=all-funds'])
  await act(async () => {
    fireEvent.click(screen.getByText(/^研究来源与覆盖/))
    await vi.advanceTimersByTimeAsync(0)
  })
  const originals = screen.getByRole('region', { name: '对应研究查阅原文' })
  expect(within(originals).getByRole('link', { name: '已查阅的公司报告' }).getAttribute('href')).toBe('https://example.com/report')
  expect(within(originals).getByRole('link', { name: '同网址的另一原文版本' }).getAttribute('href')).toBe('https://example.com/report')
  expect(within(originals).getByText('这些标的共享本轮查阅资料，不代表每篇原文均支持每个标的的结论。')).toBeTruthy()
  expect(Array.from(originals.querySelectorAll('time')).map((node) => node.dateTime)).toEqual([published, retrieved, '', ''])
  expect(within(originals).getAllByText('时间未知')).toHaveLength(2)
  expect(screen.queryByText('只有搜索结果')).toBeNull()
  expect(screen.queryByText('未取得正文')).toBeNull()
  expect(screen.queryByText('原文正文不应显示在研究主页面。')).toBeNull()
  expect(request.mock.calls.filter(([path]) => path.includes('/context')).map(([path]) => path)).toEqual(['/api/research/runs/saved-run/context?section=sources'])
})

it('keeps a saved conclusion visible during an update', async () => {
  const saved = { ...review('completed'), run_id: 'completed-1', summary: '目前仍需关注资本开支向收入的转化。' }
  request.mockResolvedValue(payload([event()], { latest_review: review('running'), last_completed_review: saved }))
  const ask = vi.fn()
  render(<SectorResearchPanel instrumentId="xlk-us" onAskAssistant={ask} />)
  await load()
  expect(within(screen.getByRole('region', { name: '当前研究结论' })).getByText(saved.summary)).toBeTruthy()
  expect(screen.getByRole('button', { name: '研究更新中…' }).hasAttribute('disabled')).toBe(true)

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

it('keeps the full-list drawer compact and shows the saved research conclusion in the overview', async () => {
  request.mockResolvedValue({ available: true, sectors: Array.from({ length: 11 }, (_, index) =>
    sector({ instrument_id: `sector-${index}`, latest_review: currentReview(review('limited')) })), events: [event()] })
  const { rerender } = render(<SectorResearchPanel watchlistId="sector-list" variant="status" />)
  await load()
  expect(screen.getByText('11 个标的 · 已更新，覆盖受限')).toBeTruthy()
  expect(screen.queryByLabelText('所选日期内研究进展')).toBeNull()
  expect(screen.queryByText('云需求新增变化')).toBeNull()
  expect(screen.queryByText('持仓截至')).toBeNull()
  const open = vi.fn()
  rerender(<SectorResearchPanel watchlistId="sector-list" variant="summary" onOpenEvents={open} />)
  expect(screen.getByText('云需求新增变化')).toBeTruthy()
  expect(screen.getAllByText('需求趋势暂未出现实质变化，继续验证盈利预期。').every((node) => node.closest('details') === null)).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '查看研究追踪' }))
  expect(open).toHaveBeenCalledOnce()
})

it('shows an honest empty state with coverage limits and prevents running without a data connection', async () => {
  request.mockResolvedValue({ ...payload([], { latest_review: null }), available: false, message: '尚未配置 FMP 数据库连接。' })
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  expect(screen.getByText('尚未完成研究。')).toBeTruthy()
  expect(screen.queryByRole('region', { name: '当前风险与机会' })).toBeNull()
  expect(screen.queryByRole('region', { name: '研究进展' })).toBeNull()
  expect(screen.getByText('尚未配置 FMP 数据库连接。')).toBeTruthy()
  expect((screen.getByRole('button', { name: '更新研究' }) as HTMLButtonElement).disabled).toBe(true)
})

it('starts the selected scope and stops polling after completion', async () => {
  let started = false, complete = false
  request.mockImplementation(async (_path: string, init?: RequestInit) => {
    if (init?.method === 'POST') { started = true; return { run_id: 'run-1', status: 'queued' } }
    return payload([], { latest_review: started ? review(complete ? 'completed' : 'running') : null })
  })
  render(<SectorResearchPanel watchlistId="sector-list" />)
  await load()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '更新研究' })) })
  expect(request).toHaveBeenCalledWith('/api/sector-research/runs', { method: 'POST', body: JSON.stringify({ instrument_ids: ['xlk-us'] }) })
  complete = true
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  const calls = request.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
  expect(request.mock.calls.length).toBe(calls)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '刷新状态' })) })
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


it.each([
  ['reviewed', '已复核既有判断', '已对照此前判断，本轮没有需要修订的结论。'],
  ['insufficient_evidence', '复核证据不足', '尚未取得整合费用的后续披露，暂无法检验原判断。'],
] as const)('keeps automatic reflection (%s) in run coverage without generating a timeline entry', async (status, label, summary) => {
  const latest = { ...review(status === 'reviewed' ? 'completed' : 'limited'), change_kind: 'none' as const, reflection: { status, summary, reviewed_update_ids: ['event:earlier:1'] } }
  request.mockResolvedValue(payload([], { latest_review: latest, last_completed_review: latest }))
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  const coverage = screen.getByText(/^研究来源与覆盖/).closest('details')!
  expect(coverage.open).toBe(false)
  expect(within(coverage).getByText(label)).toBeTruthy()
  expect(within(coverage).getByText(summary).getAttribute('translate')).toBe('no')
  expect(within(screen.getByRole('region', { name: '当前研究结论' })).queryByText(summary)).toBeNull()
  fireEvent.click(screen.getByText(/^研究来源与覆盖/))
  expect(within(coverage).getByText('本轮复核')).toBeTruthy()
})

it('does not claim a reflection for older records or incomplete updates', async () => {
  request.mockResolvedValue(payload())
  const { rerender } = render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  expect(screen.queryByText('本轮复核')).toBeNull()
  request.mockResolvedValue(payload([], { latest_review: { ...review('failed'), reflection: { status: 'reviewed', summary: '未发布的复核说明', reviewed_update_ids: [] } } }))
  rerender(<SectorResearchPanel instrumentId="another" />)
  await load()
  expect(screen.queryByText('已复核既有判断')).toBeNull()
  expect(screen.queryByText('未发布的复核说明')).toBeNull()
})
