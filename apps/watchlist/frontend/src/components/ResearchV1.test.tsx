// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import InvestmentResearchState from './InvestmentResearchState'
import ResearchRecentEvents from './ResearchRecentEvents'
import ResearchEventCard from './ResearchEventCard'
import ResearchUpdateCard from './ResearchUpdateCard'
import { EvidenceFigure } from './ResearchModules'
import type { InvestmentView, ResearchEventsResponse, ResearchInsight, ResearchUpdate, SavedResearchNotebook } from '../lib/researchDossierApi'
import { announceResearchPublication } from '../lib/researchUpdates'

const api = vi.hoisted(() => ({ events: vi.fn(), note: vi.fn(), theme: vi.fn(), pin: vi.fn(), source: vi.fn(), role: 'member' }))
vi.mock('./AccountBoundary', () => ({ useStudioAccount: () => ({ team_role: api.role }) }))
vi.mock('../lib/api', async original => ({ ...await original<typeof import('../lib/api')>(), createInstrumentResearchNote: api.note }))
vi.mock('../lib/researchDossierApi', async original => ({ ...await original<typeof import('../lib/researchDossierApi')>(), getResearchEvents: api.events, getResearchTheme: api.theme, pinResearchEvent: api.pin, getSavedResearchSource: api.source }))
const insight = (index: number): ResearchInsight => ({ key: `risk-${index}`, title: `风险 ${index}`, explanation: '经营影响仍需核实。', next_watch: '核对下一次披露。', source_ids: ['original'], figure_source_ids: [], theme_ids: [], event_keys: [] })
const view = (extra: Partial<InvestmentView> = {}): InvestmentView => ({ direction: '需求改善与执行风险并存。', horizon: '', attractiveness: '', risk: '', conviction: '', assumptions: [], source_ids: ['original'], updated_at: '2026-09-19', ...extra })
const notebook = (investment_view: InvestmentView): SavedResearchNotebook => ({ investment_view, run_id: 'saved', checked_at: '2026-09-25', key_drivers: [], questions: [], important_changes: [], next_research: [], source_ids: ['original'] })
const event = (extra: Partial<ResearchUpdate> = {}): ResearchUpdate => ({ update_id: 'event:1:v1', event_key: 'stable-event', kind: 'event', title: '重要进展', body: '已确认发生的事项。', recorded_at: '2026-09-25T09:00:00Z', occurred_at: '2026-09-22', timeline_date: '2026-09-22', information_type: 'fact', author: '研究员', author_role: 'researcher', theme_ids: [], sources: [], reference: { instrument_id: 'stock', event_case_id: 'case-1', event_version_id: 'event:1:v1' }, follow_up: 'watch', next_check: '下次公司披露。', ...extra })
const response = (events: ResearchUpdate[], extra: Partial<ResearchEventsResponse> = {}): ResearchEventsResponse => ({ instrument_id: 'stock', events, late_arrivals: [], total: events.length, has_more: false, next_offset: null, ...extra })
beforeEach(() => { vi.resetAllMocks(); api.role = 'member'; api.events.mockResolvedValue(response([])); api.pin.mockResolvedValue({ event_version_id: 'event:1:v2', follow_up_pinned: true }) })
afterEach(cleanup)

it('keeps opportunity and risk together, discloses all hidden counts and binds each item to evidence', () => {
  const state = notebook(view({ opportunities: [{ ...insight(0), title: '新增机会' }], risks: [1, 2, 3, 4].map(insight), coverage_status: 'assessed' }))
  render(<InvestmentResearchState instrumentId="stock" compact notebook={state} sources={ids => <span>{ids.join(',')}</span>} />)
  expect(screen.getByText('新增机会')).toBeTruthy()
  expect(screen.getByText('风险 3')).toBeTruthy()
  expect(screen.queryByText('风险 4')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '展开其余 1 项' }))
  expect(screen.getByText('风险 4')).toBeTruthy()
  const card = screen.getByRole('heading', { name: '风险 1' }).closest('article')!
  fireEvent.click(within(card).getByText('依据与下一观察'))
  const evidence = screen.getByRole('dialog', { name: '风险 1 · 依据与下一观察' })
  expect(within(evidence).getByText('核对下一次披露。')).toBeTruthy()
  expect(within(evidence).getByText('original')).toBeTruthy()
  expect(screen.getByRole('region', { name: '当前机会与风险' }).querySelector('time')?.dateTime).toBe('2026-09-19')
})

it.each(['limited', 'not_established', undefined] as const)('does not interpret missing insight arrays as no risk (coverage %s)', coverage_status => {
  render(<InvestmentResearchState instrumentId="stock" compact notebook={notebook(view({ opportunities: [], risks: [], coverage_status }))} sources={() => null} />)
  expect(screen.queryByText(/未发现足够明确的新风险线索/)).toBeNull()
  expect(screen.getAllByText(/尚未建立|不足/).length).toBeGreaterThan(0)
})

it.each([undefined, null])('keeps old string assessments readable with %s structured arrays', arrays => {
  render(<InvestmentResearchState instrumentId="stock" compact notebook={notebook(view({ risk: '旧版流动性风险', attractiveness: '旧版需求机会', opportunities: arrays, risks: arrays }))} sources={() => null} />)
  expect(screen.getByText('旧版流动性风险')).toBeTruthy()
  expect(screen.getByText('旧版需求机会')).toBeTruthy()
  expect(screen.queryByText(/未发现足够明确/)).toBeNull()
})

it('gets a referenced older event on demand by its stable key', async () => {
  api.events.mockResolvedValue(response([event({ title: '窗口外原事件' })]))
  render(<InvestmentResearchState instrumentId="stock" compact notebook={notebook(view({ risks: [{ ...insight(1), event_keys: ['stable-event'] }] }))} sources={() => null} />)
  expect(api.events).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('依据与下一观察'))
  expect(await screen.findByRole('heading', { name: '窗口外原事件' })).toBeTruthy()
  expect(api.events).toHaveBeenCalledWith('stock', 'history', expect.any(AbortSignal), 0, 'stable-event')
})

it('loads ongoing and paged historical events on demand without a client-side seven-day filter', async () => {
  api.events.mockImplementation(async (_id, scope, _signal, offset) => scope === 'watch' ? response([event({ title: '月前仍在跟进', timeline_date: '2026-08-01' })]) : scope === 'history' ? offset ? response([event({ update_id: 'older', title: '下一页历史' })]) : response([event()], { has_more: true, next_offset: 20, total: 21 }) : response([]))
  render(<ResearchRecentEvents instrumentId="stock" />)
  await screen.findByText(/近 7 天没有已记录/)
  fireEvent.click(screen.getByRole('button', { name: '仍在跟进' }))
  expect(await screen.findByRole('heading', { name: '月前仍在跟进' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '历史' }))
  fireEvent.click(await screen.findByRole('button', { name: '加载更多事件' }))
  expect(await screen.findByRole('heading', { name: '下一页历史' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: '重要进展' })).toBeTruthy()
  expect(api.events).toHaveBeenLastCalledWith('stock', 'history', expect.any(AbortSignal), 20)
})

it('retains known dates, unknown importance and overdue follow-up without claiming risk resolution', async () => {
  render(<ResearchEventCard update={event({ importance_score: null, follow_up_review_status: 'due', follow_up_until: '2026-09-24' })} />)
  expect(screen.getByText('2026-09-22')).toBeTruthy()
  expect(screen.getByText('重要性 未评估')).toBeTruthy()
  expect(screen.getByText('到期待复核')).toBeTruthy()
  fireEvent.click(screen.getByText('查看影响、公开观点与依据'))
  expect(await screen.findByText(/未取得足够的公开观点材料/)).toBeTruthy()
  expect(screen.getByText('暂无可比事件窗口数据。')).toBeTruthy()
  expect(screen.queryByText(/风险已解除/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '固定跟进' }))
  await waitFor(() => expect(api.pin).toHaveBeenCalledWith('case-1', 'event:1:v1', true))
  fireEvent.click(await screen.findByRole('button', { name: '取消固定' }))
  await waitFor(() => expect(api.pin).toHaveBeenLastCalledWith('case-1', 'event:1:v2', false))
})

it('shows independent follow-up without granting readers pin controls', async () => {
  api.role = 'reader'
  render(<ResearchEventCard update={event({ timeline_date: null, follow_up: 'none' })} />)
  expect(screen.getByText('日期待核实')).toBeTruthy()
  expect(screen.getByText('不再主动跟进')).toBeTruthy()
  fireEvent.click(screen.getByText('查看影响、公开观点与依据'))
  await screen.findByText('发生了什么')
  expect(screen.queryByRole('button', { name: '固定跟进' })).toBeNull()
  expect(screen.queryByRole('button', { name: '写投资观点' })).toBeNull()
})

it('retains the last event response on refresh failure and rejects a previous instrument response', async () => {
  api.events.mockResolvedValueOnce(response([event()]))
  const { rerender } = render(<ResearchRecentEvents instrumentId="stock" />)
  await screen.findByRole('heading', { name: '重要进展' })
  api.events.mockRejectedValueOnce(new Error('来源不可用'))
  await act(async () => announceResearchPublication(['stock']))
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('保留上次有效内容'))
  expect(screen.getByRole('heading', { name: '重要进展' })).toBeTruthy()
  let complete!: (value: ResearchEventsResponse) => void
  api.events.mockReturnValueOnce(new Promise<ResearchEventsResponse>(resolve => { complete = resolve }))
  await act(async () => announceResearchPublication(['stock']))
  api.events.mockResolvedValueOnce(response([event({ title: '新标的事件' })]))
  rerender(<ResearchRecentEvents instrumentId="second" />)
  await screen.findByRole('heading', { name: '新标的事件' })
  await act(async () => complete(response([event({ title: '迟到的旧标的结果' })])))
  expect(screen.queryByText('迟到的旧标的结果')).toBeNull()
  expect(screen.queryByText('重要进展')).toBeNull()
})

it.each(['watchlist_observations', 'event_market_reaction'])('renders fixed retained %s numbers while preserving missing values and limitations', analysis_kind => {
  render(<EvidenceFigure source={{ source_id: 'computed:fixed', title: '确定性观察', source_type: 'computed_metric', data: { analysis_kind, metrics: { 相对表现: null, 波动率: 18.2 }, tables: [], charts: [], limitations: ['只有部分成分行情。'] } }} />)
  expect(screen.getByText('18.2')).toBeTruthy()
  expect(screen.getByText('—')).toBeTruthy()
  expect(screen.getByText('只有部分成分行情。')).toBeTruthy()
  expect(screen.queryByText('0')).toBeNull()
})

it('opens a theme-only insight with its current analysis and original source in one click', async () => {
  api.theme.mockResolvedValue({ theme_id: 'theme-1', title: '长期主题依据', synthesis: '多次披露支持需求改善，但现金回款仍待验证。', source_version_id: 'theme:theme-1:4', sources: [{ source_id: 'theme-source', title: '主题原始披露', url: 'https://example.com/disclosure' }] })
  render(<InvestmentResearchState instrumentId="stock" compact notebook={notebook(view({ risks: [{ ...insight(1), source_ids: [], theme_ids: ['theme-1'] }] }))} sources={() => null} />)
  expect(api.theme).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '依据与下一观察' }))
  expect(await screen.findByText('多次披露支持需求改善，但现金回款仍待验证。')).toBeTruthy()
  expect(screen.getByRole('link', { name: '主题原始披露' }).getAttribute('href')).toBe('https://example.com/disclosure')
  expect(api.theme).toHaveBeenCalledWith('stock', 'theme-1', expect.any(AbortSignal))
})

it('retains structured opportunities and risks in historical views without substituting current linked themes', () => {
  render(<InvestmentResearchState instrumentId="stock" historical notebook={notebook(view({ risks: [{ ...insight(1), theme_ids: ['historic-theme'] }], opportunities: [{ ...insight(2), title: '当时的机会' }] }))} sources={ids => <span>{ids.join(',')}</span>} />)
  expect(screen.getByRole('region', { name: '当时投资判断' })).toBeTruthy()
  expect(screen.getByText('当时的机会')).toBeTruthy()
  expect(screen.getByText('风险 1')).toBeTruthy()
  expect(api.theme).not.toHaveBeenCalled()
  expect(screen.getAllByText('original').length).toBeGreaterThan(0)
})

it('opens one event history only on request and preserves historical versions and their original source identity', async () => {
  const past = event({ update_id: 'event:1:v0', title: '当时的事件判断', body: '当时观察仍不充分。', superseded: true, recorded_at: '2026-08-01T00:00:00Z', reference: { instrument_id: 'stock', event_case_id: 'case-1', event_version_id: 'event:1:v0' } })
  api.events.mockResolvedValue(response([past]))
  render(<ResearchEventCard update={event()} />)
  expect(api.events).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('查看影响、公开观点与依据'))
  fireEvent.click(await screen.findByText('研究历史与 PM 操作'))
  expect(await screen.findByRole('heading', { name: '当时的事件判断' })).toBeTruthy()
  expect(api.events).toHaveBeenCalledWith('stock', 'history', expect.any(AbortSignal), 0, 'stable-event', true)
  expect(screen.getByText('已修订 · 当时版本')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '写投资观点' })).toBeNull()
})

it('labels a PM pin audit as an administrative choice without attributing the retained research to the PM', () => {
  render(<ResearchUpdateCard update={event({ author: 'PM', author_role: 'user', change: 'follow_up_pinned' })} />)
  expect(screen.getByText('PM 跟进设置')).toBeTruthy()
  expect(screen.queryByText('人工判断')).toBeNull()
})

it('discloses the full backfill count and retains undated records when another event page is loaded', async () => {
  api.events.mockImplementation(async (_instrument, scope, _signal, offset) => scope === 'history' ? response([event({ title: '其余历史记录' })]) : offset ? response([event({ update_id: 'next-page', title: '下一页事件' })], { late_arrival_count: 25 }) : response([event()], {
    late_arrivals: [event({ update_id: 'unknown-date', title: '待核实日期的旧记录', timeline_date: null })], late_arrival_count: 25, has_more: true, next_offset: 20,
  }))
  render(<ResearchRecentEvents instrumentId="stock" />)
  const backfill = await screen.findByText('补录与日期待核实 · 25')
  expect(screen.queryByText(/本轮补录/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '加载更多事件' }))
  await screen.findByRole('heading', { name: '下一页事件' })
  fireEvent.click(backfill)
  expect(screen.getByRole('heading', { name: '待核实日期的旧记录' })).toBeTruthy()
  expect(screen.getByText(/已显示 1 \/ 25 项/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '查看历史记录' }))
  expect(await screen.findByRole('heading', { name: '其余历史记录' })).toBeTruthy()
  expect(api.events).toHaveBeenLastCalledWith('stock', 'history', expect.any(AbortSignal), 0)
})

it('records a PM opinion with the structured judgment and all cited sources at the exact original view version', async () => {
  api.note.mockResolvedValue({})
  const original = view({ version_id: 'view-original', source_ids: ['view-source'], coverage_status: 'limited', coverage_note: '尚缺下季度回款资料。',
    opportunities: [{ ...insight(1), title: '需求扩张', explanation: '新增订单支持收入可见度。', next_watch: '检查订单转化。', source_ids: ['opportunity-source'], figure_source_ids: ['quant-source'] }],
    risks: [{ ...insight(2), title: '回款延迟', explanation: '资金回收仍慢于投入。', next_watch: '检查应收账款账龄。', source_ids: ['risk-source', 'view-source'], figure_source_ids: ['quant-source'] }],
  })
  const { rerender } = render(<InvestmentResearchState instrumentId="stock" compact notebook={{ ...notebook(original), version_id: 'notebook-original' }} sources={() => null} />)
  fireEvent.click(screen.getByRole('button', { name: '记录投资观点' }))
  const background = (screen.getByLabelText('当时背景与依据') as HTMLTextAreaElement).value
  for (const text of ['需求扩张：新增订单支持收入可见度。', '下一观察：检查订单转化。', '回款延迟：资金回收仍慢于投入。', '下一观察：检查应收账款账龄。', '覆盖状态：部分覆盖', '尚缺下季度回款资料。']) expect(background).toContain(text)
  fireEvent.change(screen.getByLabelText('我的投资观点'), { target: { value: '保留独立判断，等待回款验证。' } })
  rerender(<InvestmentResearchState instrumentId="stock" compact notebook={notebook(view({ version_id: 'view-later', risks: [], opportunities: [] }))} sources={() => null} />)
  fireEvent.click(screen.getByRole('button', { name: '保存投资观点' }))
  await waitFor(() => expect(api.note).toHaveBeenCalledWith('stock', expect.objectContaining({ note: expect.objectContaining({ body: '保留独立判断，等待回款验证。', research_context: {
    notebook_version_id: 'notebook-original', investment_view_version_id: 'view-original', source_ids: ['view-source', 'opportunity-source', 'quant-source', 'risk-source'], background,
  } }) })))
})

it.each([null, []])('keeps null legacy values distinct from explicit empty insight arrays in the PM background', arrays => {
  render(<InvestmentResearchState instrumentId="stock" compact notebook={notebook(view({ opportunities: arrays, risks: arrays, attractiveness: '旧机会认识', risk: '旧风险认识', coverage_status: 'limited', coverage_note: '本轮公开信息覆盖受限。' }))} sources={() => null} />)
  fireEvent.click(screen.getByRole('button', { name: '记录投资观点' }))
  const background = (screen.getByLabelText('当时背景与依据') as HTMLTextAreaElement).value
  if (arrays === null) {
    expect(background).toContain('机会：旧机会认识')
    expect(background).toContain('风险：旧风险认识')
  } else {
    expect(background).not.toContain('旧机会认识')
    expect(background).not.toContain('旧风险认识')
    expect(background).toContain('暂无已确认机会条目')
    expect(background).toContain('暂无已确认风险条目')
    expect(background).toContain('不表示不存在风险')
  }
  expect(background).toContain('本轮公开信息覆盖受限。')
})
