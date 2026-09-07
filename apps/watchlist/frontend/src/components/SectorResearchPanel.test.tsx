// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SectorResearchPanel, { type SectorResearch, type SectorEventRecord, type EventSnapshot } from './SectorResearchPanel'
import InstrumentRiskPanel from './InstrumentRiskPanel'

const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ fetchJson: request }))
vi.mock('../../../../../packages/ui/src/RiskOfficerPanel', () => ({ default: () => null }))
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-06T08:00:00+08:00')) })
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers() })
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
const payload = (events: SectorEventRecord[] = [], overrides: Partial<SectorResearch> = {}) => ({ available: true, sectors: [sector(overrides)], events })
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
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(screen.queryByText(/只美股行业 ETF/)).toBeNull()
  expect(screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)).toEqual(['当前研究结论', '风险与机会', '研究进展'])
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

it('loads only the saved conclusion’s fetched originals on expansion and shares one source list for a multi-instrument run', async () => {
  const saved = { ...review('completed'), run_id: 'saved-run', checked_at: '2026-09-05T08:00:00+08:00' }
  const latest = { ...review('failed'), run_id: 'failed-run', summary: '最新更新未完成。' }
  const published = '2026-09-04T17:00:00+08:00'
  const retrieved = '2026-09-05T07:30:00+08:00'
  request.mockImplementation(async (path: string) => path === '/api/research/runs/saved-run/context' ? { web_evidence: [
    { operation: 'search', sources: [{ title: '只有搜索结果', url: 'https://example.com/search-only', text: '搜索摘要不是已取得原文。' }] },
    { operation: 'fetch', sources: [
      { title: '已查阅的公司报告', url: 'https://example.com/report', text: '原文正文不应显示在研究主页面。', published_at: published, retrieved_at: retrieved },
      { title: '时间尚未核实的原文', url: 'https://example.com/undated', text: '取得正文但没有可靠日期。', published_at: null, retrieved_at: null },
      { title: '未取得正文', url: 'https://example.com/empty', text: '  ' },
    ] },
  ] } : { available: true, sectors: Array.from({ length: 11 }, (_, index) => sector({ instrument_id: `fund-${index}`, ticker: `FUND${index}`, latest_review: latest, last_completed_review: saved })), events: [] })
  render(<SectorResearchPanel watchlistId="all-funds" />)
  await load()
  expect(request.mock.calls.map(([path]) => path)).toEqual(['/api/sector-research?watchlist_id=all-funds'])
  await act(async () => {
    fireEvent.click(screen.getByText(/^研究来源与覆盖/))
    await vi.advanceTimersByTimeAsync(0)
  })
  const originals = screen.getByRole('region', { name: '本轮查阅原文' })
  expect(within(originals).getByRole('link', { name: '已查阅的公司报告' }).getAttribute('href')).toBe('https://example.com/report')
  expect(within(originals).getByText('这些标的共享本轮查阅资料，不代表每篇原文均支持每个标的的结论。')).toBeTruthy()
  expect(Array.from(originals.querySelectorAll('time')).map((node) => node.dateTime)).toEqual([published, retrieved, '', ''])
  expect(within(originals).getAllByText('时间未知')).toHaveLength(2)
  expect(screen.queryByText('只有搜索结果')).toBeNull()
  expect(screen.queryByText('未取得正文')).toBeNull()
  expect(screen.queryByText('原文正文不应显示在研究主页面。')).toBeNull()
  expect(request.mock.calls.filter(([path]) => path.includes('/context')).map(([path]) => path)).toEqual(['/api/research/runs/saved-run/context'])
})

it('keeps a saved conclusion visible during an update and offers a contextual event follow-up', async () => {
  const saved = { ...review('completed'), run_id: 'completed-1', summary: '目前仍需关注资本开支向收入的转化。' }
  request.mockResolvedValue(payload([event()], { latest_review: review('running'), last_completed_review: saved }))
  const ask = vi.fn()
  render(<SectorResearchPanel instrumentId="xlk-us" onAskAssistant={ask} />)
  await load()
  expect(within(screen.getByRole('region', { name: '当前研究结论' })).getByText(saved.summary)).toBeTruthy()
  expect(screen.getByRole('button', { name: '研究更新中…' }).hasAttribute('disabled')).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '追问这项判断' }))
  expect(ask).toHaveBeenCalledWith(expect.stringContaining('云需求新增变化'))
  expect(ask.mock.calls[0][0]).toContain('分析当前指引如何改变盈利预期。')
})

it('uses occurrence before publication and never puts newly collected old news in the recent timeline', async () => {
  const backfill = event({ case_id: 'old', title: '补充发现的旧事件', occurred_at: '2026-08-20', recording_type: 'backfill', information_type: 'rumor', confidence: 'unverified' })
  const recent = event({ occurred_at: null, title: '新发布的行业线索', information_type: 'opinion' })
  const upcoming = event({ case_id: 'upcoming', title: '预定监管审议', occurred_at: '2026-09-10' })
  request.mockResolvedValue(payload([backfill, recent, upcoming]))
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  const timeline = screen.getByLabelText('所选日期内研究进展')
  expect(within(timeline).getByRole('heading', { name: '新发布的行业线索' })).toBeTruthy()
  expect(within(timeline).queryByText('补充发现的旧事件')).toBeNull()
  expect(screen.getByText('补录与时间待核实')).toBeTruthy()
  expect(screen.getByText('发生 2026-08-20')).toBeTruthy()
  expect(screen.getByText('传闻')).toBeTruthy()
  expect(screen.getByText('补录')).toBeTruthy()
  expect(screen.getByText('发生时间未知，按发布日期归入时间线。')).toBeTruthy()
  expect(screen.getAllByText(/^收录.*2026/)).toHaveLength(3)
  expect(screen.getByText('待发生事项')).toBeTruthy()
  expect(screen.getByText('预计发生 2026-09-10')).toBeTruthy()
  fireEvent.change(screen.getByRole('combobox', { name: '浏览范围' }), { target: { value: '30' } })
  expect(within(timeline).getByRole('heading', { name: '补充发现的旧事件' })).toBeTruthy()
})

it('uses the displayed local calendar day for a timestamp at the seven-day boundary', async () => {
  const localMidnight = new Date(2026, 7, 31, 0, 30).toISOString()
  request.mockResolvedValue(payload([event({ title: '边界时刻的进展', occurred_at: localMidnight })]))
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  expect(within(screen.getByLabelText('所选日期内研究进展')).getByRole('heading', { name: '边界时刻的进展' })).toBeTruthy()
})

it('groups progress under one stable event and keeps evidence and long analysis in details', async () => {
  const first: EventSnapshot = event({ occurred_at: '2026-09-02', discovered_at: '2026-09-02T08:00:00+08:00' })
  const updated = event({ history: [
    { at: '2026-09-01T08:00:00+08:00', action: 'new', detail: '仅保留早期人工记录', snapshot: null },
    { at: '2026-09-02T08:00:00+08:00', action: 'new', detail: '第一条进展', snapshot: first },
    { at: '2026-09-06T08:00:00+08:00', action: 'updated', detail: '第二条进展', snapshot: event({ sources: [{ title: '非法链接', url: 'javascript:alert(1)' }] }) },
  ] })
  request.mockResolvedValue(payload([updated]))
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  expect(within(screen.getByLabelText('所选日期内研究进展')).getAllByRole('heading', { name: '云需求新增变化' })).toHaveLength(1)
  expect(screen.getByText('进展更新')).toBeTruthy()
  expect(screen.getAllByText('分析当前指引如何改变盈利预期。').filter((node) => node.classList.contains('sector-event-impact'))).toHaveLength(2)
  expect(screen.getAllByText('分析当前指引如何改变盈利预期。').filter((node) => node.closest('details')).every((node) => !node.closest('details')?.open)).toBe(true)
  expect(screen.getByText(/记录于.*仅保留早期人工记录/)).toBeTruthy()
  fireEvent.click(screen.getAllByText('影响、证据与下一步')[0])
  expect(screen.getByRole('link', { name: '公司原始公告' }).getAttribute('href')).toBe('https://example.com/earnings')
  expect(screen.queryByRole('link', { name: '非法链接' })).toBeNull()
  expect(within(screen.getByLabelText('所选日期内研究进展')).getAllByText('关注下一次云业务指引。')).toHaveLength(2)
})

it('keeps verification withdrawals outside normal progress while retaining the original draft and sources', async () => {
  const old = event({ direction: 'risk' })
  const withdrawn = event({
    ...old, trigger_active: false, status: 'handled', withdrawn: true,
    withdrawal_reason: '原文日期与事件描述不符，核证后撤回。', withdrawn_at: '2026-09-06T09:00:00+08:00',
    history: [{ at: '2026-09-06T09:00:00+08:00', action: 'withdrawn', detail: '已保留撤回前版本', snapshot: old }],
  })
  request.mockResolvedValue(payload([withdrawn]))
  render(<SectorResearchPanel instrumentId="xlk-us" onAskAssistant={vi.fn()} />)
  await load()
  expect(within(screen.getByLabelText('所选日期内研究进展')).queryByText(old.body)).toBeNull()
  expect(within(screen.getByRole('region', { name: '当前风险与机会' })).queryByText(old.title)).toBeNull()
  expect(screen.queryByText('已结束')).toBeNull()
  const archive = screen.getByText('核证撤回记录 · 1').closest('details')!
  expect(archive.open).toBe(false)
  fireEvent.click(within(archive).getByText('核证撤回记录 · 1'))
  expect(within(archive).getByText(withdrawn.withdrawal_reason!)).toBeTruthy()
  expect(within(archive).getByText('核证撤回')).toBeTruthy()
  expect(within(archive).getByText('撤回前旧稿')).toBeTruthy()
  expect(within(archive).getAllByText(old.body).length).toBeGreaterThan(0)
  fireEvent.click(within(archive).getByText('影响、证据与下一步'))
  expect(within(archive).getByRole('link', { name: '公司原始公告' }).getAttribute('href')).toBe('https://example.com/earnings')
  expect(within(archive).queryByRole('button', { name: '追问这条进展' })).toBeNull()
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

it('shows FMP estimate comparisons with matching fiscal periods and collection intervals instead of publication dates', async () => {
  const previousCollected = '2026-09-04T08:00:00+08:00'
  const currentCollected = '2026-09-05T08:00:00+08:00'
  request.mockResolvedValue(payload([event({ sources: [{
    source_id: 'estimates:run-1:xlk-us', source_type: 'analyst_estimate_changes',
    previous_snapshot: { run_id: 'run-0', read_at: '2026-09-05T09:00:00+08:00', cutoff: '2026-09-05T08:00:00+08:00' },
    current_snapshot: { run_id: 'run-1', read_at: '2026-09-06T09:00:00+08:00', cutoff: '2026-09-06T08:00:00+08:00' },
    changes: [{ symbol: 'MSFT', name: 'Microsoft', frequency: 'annual', target_period_end: '2027-06-30', metric: 'eps_avg', currency: 'USD',
      previous_value: 12, current_value: 13.2, delta_pct: 10, previous_collected_at: previousCollected,
      current_collected_at: currentCollected, analyst_count_changed: true }],
  }] })]))
  render(<SectorResearchPanel instrumentId="xlk-us" />)
  await load()
  fireEvent.click(screen.getByText('影响、证据与下一步'))
  const source = screen.getByText('FMP预期快照比较').closest('li')!
  expect(within(source).getByText(/^快照读取：前次/)).toBeTruthy()
  fireEvent.click(within(source).getByText('同财期预期变动 · 1 项'))
  expect(within(source).getByText('MSFT · Microsoft · 年度财期截至 2027-06-30')).toBeTruthy()
  expect(within(source).getByText('平均每股收益预期：12 → 13.2 USD/股 · +10.00%')).toBeTruthy()
  expect(within(source).getByText(/^源数据采集区间：/)).toBeTruthy()
  expect(Array.from(source.querySelectorAll('time')).map((node) => node.dateTime)).toEqual([previousCollected, currentCollected])
  expect(within(source).getByText('分析师样本数量发生变化，共识变化不等于每位分析师均调整预测。')).toBeTruthy()
  expect(within(source).queryByText(/^原文发布/)).toBeNull()
  expect(within(source).queryByRole('link')).toBeNull()
})

it('keeps the full-list drawer compact and shows the saved research conclusion in the overview', async () => {
  request.mockResolvedValue({ available: true, sectors: Array.from({ length: 11 }, (_, index) => sector({ instrument_id: `sector-${index}` })), events: [event()] })
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
