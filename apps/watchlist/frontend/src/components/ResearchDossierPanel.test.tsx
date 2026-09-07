// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ResearchCatalyst, ResearchDossier, ResearchMandate, ResearchMaterial, SavedResearchNotebook } from '../lib/researchDossierApi'
import * as researchDossierApi from '../lib/researchDossierApi'
import ResearchDossierPanel from './ResearchDossierPanel'
import SectorResearchPanel from './SectorResearchPanel'
import { LanguageProvider, LanguageSelector } from '../../../../../packages/ui/src/i18n'

const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ fetchJson: request, API_BASE_URL: '' }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); vi.useRealTimers() })
const question = { key: 'cloud-cash', question: '云投入能否转化为现金回报？', assessment: '收入已有改善，现金回收仍待验证。', evidence_for: ['新增合同增长。'], evidence_against: ['折旧负担仍在增加。'], next_check: '对照下一期现金流及资本开支。', status: 'open' as const, source_ids: ['original-1'] }
const notebook: SavedResearchNotebook = { run_id: 'completed-1', checked_at: '2026-09-06T08:00:00+08:00', fundamental_view: '底层需求仍在增长，需要核实回报质量。', key_drivers: ['客户现金回报'], valuation_view: '当前估值需要收入兑现。', questions: [question], important_changes: ['新合同提高了下一季收入可见度。'], next_research: ['核实新增合同转化情况。'], source_ids: ['original-1'], sources: [{ source_id: 'original-1', title: '公司原始报告', url: 'https://example.com/original', published_at: '2026-09-05', retrieved_at: '2026-09-06T07:00:00+08:00' }] }
const material: ResearchMaterial = { source_id: 'material-1', entry_id: null, title: '基金季度报告', body: '材料全文只在研究档案中显示。', source: '/api/instruments/fund-1/documents/report.pdf', metadata: { published_at: '2026-08-10', effective_date: '2026-06-30', extraction: '已读取PDF文字层。' }, recorded_at: '2026-09-05T11:00:00+08:00' }
const dossier = (): ResearchDossier => ({
  notebook, notebook_history: [{ run_id: notebook.run_id, checked_at: notebook.checked_at, important_changes: notebook.important_changes }, { run_id: 'earlier-1', checked_at: '2026-09-04T08:00:00+08:00', important_changes: ['此前仍缺少收入兑现证据。'] }],
  frameworks: [{ id: 'etf', title: 'ETF底层回报研究', body: '研究方法正文不在首屏展开。', version: '1', source: 'research-library', role: 'research_method' }],
  materials: [material], historical_case_limitations: ['旧样本不代表完整事件分布。'], historical_cases: [{ case_id: 'dotcom', source_id: 'historical:dotcom', role: 'historical_research', case_title: '互联网泡沫中的融资反馈', event: { information_window: { start: '2000-03-10', end: '2000-04-14' }, verified_new_information: '原档记录融资窗口收缩。', session_mapping: '数周信息累积后重估。' }, analysis: { causal_chain: ['估值压缩影响融资。'], market_interpretation: '需要区分单日冲击与持续重估。' }, current_use: { lesson: '核实现金流和融资约束。', similarity_requirements: ['融资依赖上升。'], important_differences: ['当前龙头有现金流。'] }, sources: [{ title: '历史原始来源', url: 'https://example.com/history', supports: '原档用于核对事件日期。' }], limitations: ['未重核历史成分。'] }],
})
beforeEach(() => { request.mockResolvedValue(dossier()) })
async function expandArchive() {
  await act(async () => { fireEvent.click(screen.getByText('研究档案', { selector: 'summary' })) })
  await screen.findByRole('region', { name: '研究材料' })
}

it('shows saved research questions and important changes while leaving long materials and history folded away', async () => {
  const ask = vi.fn()
  render(<ResearchDossierPanel instrumentId="fund-1" reviewRunId="completed-1" onAskAssistant={ask} />)
  const questions = await screen.findByRole('region', { name: '正在研究的问题' })
  expect(within(questions).getByText(question.assessment)).toBeTruthy()
  expect(screen.getByRole('region', { name: '重要变化' }).textContent).toContain(notebook.important_changes[0])
  expect(screen.queryByText(material.body)).toBeNull()
  expect(screen.queryByText('互联网泡沫中的融资反馈')).toBeNull()
  expect(screen.queryByText('研究方法正文不在首屏展开。')).toBeNull()
  fireEvent.click(within(questions).getByText('正反证据'))
  expect(within(questions).getByRole('link', { name: '公司原始报告' }).getAttribute('href')).toBe('https://example.com/original')
  expect(Array.from(questions.querySelectorAll('time')).map((node) => node.dateTime)).toEqual(['2026-09-05', '2026-09-06T07:00:00+08:00'])
  fireEvent.click(within(questions).getByRole('button', { name: '追问这个问题' }))
  expect(ask.mock.calls[0][0]).toContain(question.question)
  expect(ask.mock.calls[0][0]).toContain(question.assessment)
  expect(ask.mock.calls[0][0]).toContain(question.evidence_against[0])
  expect(request).toHaveBeenCalledWith('/api/research/instruments/fund-1/dossier?include_history=true', expect.anything())
})

it('keeps historical cases and earlier notebook changes in the archive with their original dates and limitations', async () => {
  render(<ResearchDossierPanel instrumentId="xlk" onAskAssistant={vi.fn()} />)
  await screen.findByRole('region', { name: '正在研究的问题' })
  await expandArchive()
  expect(screen.getByText('以往底稿变化 · 1 次')).toBeTruthy()
  const history = screen.getByRole('region', { name: '历史案例' })
  expect(within(history).getByRole('heading', { name: '历史案例 · 未经本轮核证' })).toBeTruthy()
  expect(within(history).getByText(/不是本日事件/)).toBeTruthy()
  fireEvent.click(within(history).getByText('互联网泡沫中的融资反馈'))
  expect(within(history).getByText('历史案例 · 未经本轮核证 · 原记录区间 2000-03-10 至 2000-04-14')).toBeTruthy()
  expect(within(history).getByText('旧样本不代表完整事件分布。')).toBeTruthy()
  expect(within(history).getByRole('link', { name: '历史原始来源' })).toBeTruthy()
  fireEvent.click(screen.getByText('基金季度报告', { selector: 'summary' }))
  expect(screen.getByRole('link', { name: '查看原件或来源' }).getAttribute('href')).toBe(material.source)
  expect(screen.getByText(/发布 2026-08-10 · 资料截至 2026-06-30/)).toBeTruthy()
  expect(screen.getByText('已读取PDF文字层。')).toBeTruthy()
})

it('saves text material with separate publication and effective dates without altering the notebook', async () => {
  request.mockImplementation(async (_path: string, init?: RequestInit) => init?.method === 'POST' ? { ...material, source_id: 'material-added', entry_id: 'added', title: '管理人访谈', body: '访谈材料' } : dossier())
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  await screen.findByRole('region', { name: '正在研究的问题' })
  await expandArchive()
  fireEvent.click(screen.getByRole('button', { name: '添加材料' }))
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: '管理人访谈' } })
  fireEvent.change(screen.getByLabelText('材料正文'), { target: { value: '访谈材料' } })
  fireEvent.change(screen.getByLabelText('来源（可选）'), { target: { value: '管理人电话访谈' } })
  fireEvent.change(screen.getByLabelText('发布日期（可选）'), { target: { value: '2026-09-05' } })
  fireEvent.change(screen.getByLabelText('资料截至日期（可选）'), { target: { value: '2026-08-31' } })
  fireEvent.click(screen.getByRole('button', { name: '保存材料' }))
  await screen.findByText('材料已保存。')
  expect(request).toHaveBeenCalledWith('/api/research/instruments/fund-1/dossier/materials', { method: 'POST', body: JSON.stringify({ title: '管理人访谈', source: '管理人电话访谈', published_at: '2026-09-05', effective_date: '2026-08-31', body: '访谈材料' }) })
  expect(screen.getByText('管理人访谈', { selector: 'summary' })).toBeTruthy()
  expect(within(screen.getByRole('region', { name: '正在研究的问题' })).getByText(question.assessment)).toBeTruthy()
})

it('edits the instrument research mandate through its own API while preserving research materials and judgments', async () => {
  const mandate: ResearchMandate = {
    instrument_id: 'fund-1', entry_id: 'mandate-1', role: 'research_method', updated_at: null,
    title: '该基金的持续研究任务', background: '以基金持仓与管理人披露为背景，持续核实策略的收益来源。',
    mechanisms: ['持仓风格影响净值表现。'], research_approach: ['对照定期披露与历史底稿。'],
    focus: ['重点跟踪策略容量变化。'], source_plan: ['管理人季度报告。'], gaps: ['最新持仓披露仍待补齐。'],
  }
  const saved = { ...mandate, background: '专门跟踪持仓变化与策略容量，按披露周期更新。', focus: ['核对策略容量。', '核对持仓变化。'], updated_at: '2026-09-07T08:00:00+08:00' }
  request.mockImplementation(async (_path: string, init?: RequestInit) => init?.method === 'PUT' ? saved : { ...dossier(), mandate })
  const save = vi.spyOn(researchDossierApi, 'saveResearchMandate')
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  await screen.findByRole('region', { name: '正在研究的问题' })
  expect(screen.queryByText(mandate.background)).toBeNull()
  await expandArchive()
  const task = screen.getByRole('region', { name: '标的研究任务' })
  expect(within(task).getByText(mandate.background)).toBeTruthy()
  expect(within(task).getByText(mandate.focus[0]).closest('details')?.open).toBe(true)
  fireEvent.click(within(task).getByRole('button', { name: '编辑研究任务' }))
  fireEvent.change(within(task).getByLabelText('背景与研究边界'), { target: { value: saved.background } })
  fireEvent.change(within(task).getByLabelText('重点关注（每行一项）'), { target: { value: ' 核对策略容量。\n\n核对持仓变化。 ' } })
  fireEvent.click(within(task).getByRole('button', { name: '保存研究任务' }))
  await within(task).findByText(saved.background)
  const input = { title: mandate.title, background: saved.background, mechanisms: mandate.mechanisms, research_approach: mandate.research_approach, focus: saved.focus, source_plan: mandate.source_plan, gaps: mandate.gaps }
  expect(save).toHaveBeenCalledExactlyOnceWith('fund-1', input)
  expect(request.mock.calls.filter(([, init]) => init?.method)).toEqual([
    ['/api/research/instruments/fund-1/dossier/mandate', { method: 'PUT', body: JSON.stringify(input) }],
  ])
  expect(within(task).getByText(saved.focus[1])).toBeTruthy()
  expect(within(screen.getByRole('region', { name: '正在研究的问题' })).getByText(question.assessment)).toBeTruthy()
  fireEvent.click(screen.getByText(material.title, { selector: 'summary' }))
  expect(within(screen.getByRole('region', { name: '研究材料' })).getByText(material.body)).toBeTruthy()
})

it('uploads the actual file and retains an explicit unread-body state and original link', async () => {
  request.mockImplementation(async (_path: string, init?: RequestInit) => init?.method === 'POST' ? { ...material, source_id: 'scan', title: '扫描报告.pdf', body: '', source: '/api/research/entries/scan/file', metadata: { source: '基金管理人', extraction: '扫描件需补充文字' } } : dossier())
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  await screen.findByRole('region', { name: '正在研究的问题' })
  await expandArchive()
  fireEvent.click(screen.getByRole('button', { name: '添加材料' }))
  fireEvent.change(screen.getByLabelText('材料类型'), { target: { value: 'file' } })
  const file = new File(['scanned-pdf'], '扫描报告.pdf', { type: 'application/pdf' })
  fireEvent.change(screen.getByLabelText('文件'), { target: { files: [file] } })
  fireEvent.change(screen.getByLabelText('来源（可选）'), { target: { value: '基金管理人' } })
  expect(screen.getByRole('button', { name: '保存材料' }).hasAttribute('disabled')).toBe(false)
  fireEvent.submit(screen.getByRole('form', { name: '添加研究材料' }))
  await screen.findByText('材料已保存。')
  const [path, options] = request.mock.calls.find(([, init]) => init?.method === 'POST')!
  expect(path).toBe('/api/research/instruments/fund-1/dossier/files')
  expect(options.headers).toEqual({})
  expect(options.body.get('file')).toBe(file)
  expect(options.body.get('source')).toBe('基金管理人')
  expect(options.body.has('published_at')).toBe(false)
  fireEvent.click(screen.getByText('扫描报告.pdf · 正文未读取', { selector: 'summary' }))
  expect(screen.getByText('扫描件需补充文字')).toBeTruthy()
  expect(screen.getByText('资料出处：基金管理人')).toBeTruthy()
  expect(screen.getByText('已保存资料记录，尚无可供研究使用的正文。')).toBeTruthy()
})

it('does not invent questions before a completed notebook exists and refreshes after the completed run changes', async () => {
  request.mockResolvedValue({ ...dossier(), notebook: null, notebook_history: [] })
  const { rerender } = render(<ResearchDossierPanel instrumentId="fund-1" />)
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  expect(screen.queryByRole('region', { name: '正在研究的问题' })).toBeNull()
  expect(screen.queryByRole('region', { name: '重要变化' })).toBeNull()
  await expandArchive()
  expect(screen.getByText('尚未完成研究底稿。已保存的资料会留在档案中。')).toBeTruthy()
  request.mockResolvedValue(dossier())
  rerender(<ResearchDossierPanel instrumentId="fund-1" reviewRunId="completed-1" />)
  expect(await screen.findByRole('region', { name: '正在研究的问题' })).toBeTruthy()
  expect(request).toHaveBeenCalledTimes(2)
})

it('keeps the overview summary short and retains the saved notebook when the latest review failed', async () => {
  const savedReview = { run_id: notebook.run_id, checked_at: notebook.checked_at, status: 'completed', summary: '已完成的当前判断。', coverage: [] }
  request.mockImplementation(async (path: string) => path.includes('/dossier') ? dossier() : { available: true, sectors: [{ instrument_id: 'fund-1', ticker: 'FUND', sector_name: '测试基金', latest_review: { ...savedReview, run_id: 'failed-2', status: 'failed', summary: '新一轮未完成。' }, last_completed_review: savedReview }], events: [] })
  const { rerender } = render(<SectorResearchPanel instrumentId="fund-1" variant="summary" />)
  await screen.findByText('已完成的当前判断。')
  expect(screen.queryByText('研究档案', { selector: 'summary' })).toBeNull()
  expect(request.mock.calls.some(([path]) => path.includes('/dossier'))).toBe(false)
  rerender(<SectorResearchPanel instrumentId="fund-1" />)
  const questions = await screen.findByRole('region', { name: '正在研究的问题' })
  expect(within(questions).getByText(question.assessment)).toBeTruthy()
  expect(within(screen.getByRole('region', { name: '当前研究结论' })).getByText('已完成的当前判断。')).toBeTruthy()
  expect(within(questions).queryByText('新一轮未完成。')).toBeNull()
})

const catalyst = (overrides: Partial<ResearchCatalyst> = {}): ResearchCatalyst => ({
  key: 'release', title: '就业数据公布', scheduled_at: '2026-09-07T09:00:00-04:00', status: 'scheduled',
  relevance: '工资和就业变化影响利率预期。', scenarios: ['工资降温可能缓解估值压力。'], next_check: '核对实际值、预期与前值修订。', outcome: '', source_ids: ['original-1'], ...overrides,
})

it('compares scheduled instants across offsets, shows their timezone and asks with the matching source and scenarios', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-09-07T12:00:00Z'))
  const next = catalyst()
  const past = catalyst({ key: 'past', title: '此前预定公布', scheduled_at: '2026-09-07T07:00:00-04:00' })
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, catalysts: [next, past] } })
  const ask = vi.fn()
  render(<ResearchDossierPanel instrumentId="fund-1" onAskAssistant={ask} />)
  const upcoming = await screen.findByRole('region', { name: '接下来关注' })
  const nextArticle = within(upcoming).getByRole('heading', { name: next.title }).closest('article')!
  const pastArticle = within(upcoming).getByRole('heading', { name: past.title }).closest('article')!
  expect(within(nextArticle).getByText('预定事件')).toBeTruthy()
  expect(within(pastArticle).getByText('结果待核实')).toBeTruthy()
  expect(within(upcoming).queryByText('已发布')).toBeNull()
  expect(nextArticle.querySelector('time')?.dateTime).toBe(next.scheduled_at)
  expect(nextArticle.querySelector('time')?.textContent).toMatch(/GMT|UTC/)
  fireEvent.click(within(nextArticle).getByText('情景与依据'))
  expect(within(nextArticle).getByRole('link', { name: '公司原始报告' }).getAttribute('href')).toBe('https://example.com/original')
  expect(Array.from(nextArticle.querySelectorAll('time')).map((node) => node.dateTime)).toEqual([next.scheduled_at, '2026-09-05', '2026-09-06T07:00:00+08:00'])
  fireEvent.click(within(nextArticle).getByRole('button', { name: '追问这项事件' }))
  const prompt = ask.mock.calls[0][0]
  for (const text of [next.title, next.scheduled_at, next.relevance, next.scenarios[0], next.next_check, 'original-1', '预定事件']) expect(prompt).toContain(text)
})

it('does not treat today’s date-only event or a timezone-free time as an already elapsed release', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-09-07T12:00:00Z'))
  const localDay = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  const today = localDay(new Date())
  const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1)
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, catalysts: [
    catalyst({ key: 'date-only', title: '今日未给具体时间', scheduled_at: today, scenarios: [], source_ids: [], next_check: '' }),
    catalyst({ key: 'previous-date', title: '旧日程尚待核实', scheduled_at: localDay(yesterday) }),
    catalyst({ key: 'naive', title: '缺少时区的日程', scheduled_at: `${today}T09:00:00` }),
  ] } })
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  const upcoming = await screen.findByRole('region', { name: '接下来关注' })
  const todayArticle = within(upcoming).getByRole('heading', { name: '今日未给具体时间' }).closest('article')!
  expect(within(todayArticle).getByText('预定事件')).toBeTruthy()
  expect(within(todayArticle).queryByText('结果待核实')).toBeNull()
  expect(todayArticle.querySelector('details')).toBeNull()
  expect(within(todayArticle).queryByText('下一步核实')).toBeNull()
  const oldArticle = within(upcoming).getByRole('heading', { name: '旧日程尚待核实' }).closest('article')!
  expect(within(oldArticle).getByText('结果待核实')).toBeTruthy()
  const naiveArticle = within(upcoming).getByRole('heading', { name: '缺少时区的日程' }).closest('article')!
  expect(within(naiveArticle).getByText('时间待核实')).toBeTruthy()
  expect(naiveArticle.querySelector('time')?.textContent).toBe(`${today}T09:00:00（时区未披露）`)
})

it('keeps released and cancelled outcomes in the archive without duplicating scheduled items in the working paper', async () => {
  const ask = vi.fn()
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, catalysts: [
    catalyst({ title: '未来预定事项' }),
    catalyst({ key: 'released', title: '已经公布的事项', status: 'released', outcome: '实际工资增速低于此前预期。' }),
    catalyst({ key: 'cancelled', title: '已取消的事项', status: 'cancelled', outcome: '主办方已取消说明会。' }),
  ] } })
  render(<ResearchDossierPanel instrumentId="fund-1" onAskAssistant={ask} />)
  const upcoming = await screen.findByRole('region', { name: '接下来关注' })
  expect(within(upcoming).getAllByRole('heading', { level: 4 })).toHaveLength(1)
  expect(screen.queryByText('已经公布的事项')).toBeNull()
  expect(screen.queryByText('已取消的事项')).toBeNull()
  await expandArchive()
  fireEvent.click(screen.getByText('已跟踪的日程与结果'))
  expect(screen.getAllByText('未来预定事项')).toHaveLength(1)
  const released = screen.getByRole('heading', { name: '已经公布的事项' }).closest('article')!
  const cancelled = screen.getByRole('heading', { name: '已取消的事项' }).closest('article')!
  expect(within(released).getByText('已发布')).toBeTruthy()
  expect(within(released).getByText('实际工资增速低于此前预期。')).toBeTruthy()
  expect(within(cancelled).getByText('已取消')).toBeTruthy()
  fireEvent.click(within(cancelled).getByRole('button', { name: '追问这项事件' }))
  expect(ask.mock.calls[0][0]).toContain('当前记录状态：已取消')
  expect(ask.mock.calls[0][0]).toContain('主办方已取消说明会。')
})


it('translates research controls in both languages while preserving saved research wording', async () => {
  window.history.replaceState(null, '', '/?lang=en')
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, questions: [{ ...question, question: 'Risk', assessment: 'Research' }] } })
  render(<LanguageProvider><LanguageSelector /><ResearchDossierPanel instrumentId="fund-1" onAskAssistant={vi.fn()} /></LanguageProvider>)
  const questions = await screen.findByRole('region', { name: 'Questions under research' })
  expect(within(questions).getByRole('heading', { name: 'Risk' })).toBeTruthy()
  expect(within(questions).getByText('Research')).toBeTruthy()
  expect(within(questions).getByRole('button', { name: 'Ask about this question' })).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await screen.findByRole('region', { name: '正在研究的问题' })
  expect(within(questions).getByRole('heading', { name: 'Risk' })).toBeTruthy()
  expect(within(questions).getByText('Research')).toBeTruthy()
  expect(within(questions).getByRole('button', { name: '追问这个问题' })).toBeTruthy()
  window.history.replaceState(null, '', '/')
})
