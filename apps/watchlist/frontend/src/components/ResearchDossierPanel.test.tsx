// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ResearchCatalyst, ResearchDossier, ResearchMandate, ResearchMaterial, SavedResearchNotebook } from '../lib/researchDossierApi'
import * as researchDossierApi from '../lib/researchDossierApi'
import ResearchDossierPanel from './ResearchDossierPanel'
import SectorResearchPanel from './SectorResearchPanel'
import { LanguageProvider, LanguageSelector } from '../../../../../packages/ui/src/i18n'
import { announceResearchPublication } from '../lib/researchUpdates'
import { researchMessages, researchPatterns } from '../researchMessages'

const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ fetchJson: request, API_BASE_URL: '' }))
vi.mock('./ResearchThemesPanel', () => ({ default: () => <div data-testid="themes-panel" /> }))
vi.mock('./ResearchRecentEvents', () => ({ default: () => <div data-testid="recent-events" /> }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); vi.useRealTimers() })
const question = { key: 'cloud-cash', question: '云投入能否转化为现金回报？', assessment: '收入已有改善，现金回收仍待验证。', evidence_for: ['新增合同增长。'], evidence_against: ['折旧负担仍在增加。'], next_check: '对照下一期现金流及资本开支。', status: 'open' as const, source_ids: ['original-1'] }
const notebook: SavedResearchNotebook = { run_id: 'completed-1', checked_at: '2026-09-06T08:00:00+08:00', prior_analysis: { fundamental_view: '底层需求仍在增长，需要核实回报质量。', valuation_view: '当前估值需要收入兑现。', source_ids: [], sources: [], version_id: 'prior-notebook', updated_at: '2026-09-01T08:00:00Z', note: '此前保存的综合分析，保留原日期。' }, key_drivers: ['客户现金回报'], questions: [question], important_changes: ['新合同提高了下一季收入可见度。'], next_research: ['核实新增合同转化情况。'], source_ids: ['original-1'], sources: [{ source_id: 'original-1', title: '公司原始报告', url: 'https://example.com/original', published_at: '2026-09-05', retrieved_at: '2026-09-06T07:00:00+08:00' }] }
const material: ResearchMaterial = { source_id: 'material-1', entry_id: null, title: '基金季度报告', body: '材料全文只在研究档案中显示。', source: '/api/instruments/fund-1/documents/report.pdf', metadata: { published_at: '2026-08-10', effective_date: '2026-06-30', extraction: '已读取PDF文字层。' }, recorded_at: '2026-09-05T11:00:00+08:00' }
const dossier = (): ResearchDossier => ({
  notebook, notebook_history: [{ run_id: notebook.run_id, checked_at: notebook.checked_at, important_changes: notebook.important_changes }, { run_id: 'earlier-1', checked_at: '2026-09-04T08:00:00+08:00', important_changes: ['此前仍缺少收入兑现证据。'] }],
  frameworks: [{ id: 'etf', title: 'ETF底层回报研究', body: '研究方法正文不在首屏展开。', version: '1', source: 'research-library', role: 'research_method' }],
  materials: [material], historical_case_limitations: ['旧样本不代表完整事件分布。'], historical_cases: [{ case_id: 'dotcom', source_id: 'historical:dotcom', role: 'historical_research', case_title: '互联网泡沫中的融资反馈', event: { information_window: { start: '2000-03-10', end: '2000-04-14' }, verified_new_information: '原档记录融资窗口收缩。', session_mapping: '数周信息累积后重估。' }, analysis: { causal_chain: ['估值压缩影响融资。'], market_interpretation: '需要区分单日冲击与持续重估。' }, current_use: { lesson: '核实现金流和融资约束。', similarity_requirements: ['融资依赖上升。'], important_differences: ['当前龙头有现金流。'] }, sources: [{ title: '历史原始来源', url: 'https://example.com/history', supports: '原档用于核对事件日期。' }], limitations: ['未重核历史成分。'] }],
})
beforeEach(() => { request.mockResolvedValue(dossier()) })

it('opens one complete report with analysis and themes, fetching historical versions only on demand', async () => {
  const module = { key: 'business-fundamentals', summary: '增长开始兑现，现金回报仍需验证。', analysis: '收入增长不能替代资本回报，需要把新增投入与回款匹配。', coverage: 'partial' as const, gaps: [], next_check: '', source_ids: [], figure_source_ids: [], evidence_as_of: '2026-09-05' }
  const quant = { ...module, key: 'market-quantitative', analysis: '量化对照使用实际共同样本。' }
  const events = { ...module, key: 'events-expectations', analysis: '事件预期与实际结果需要比较。' }
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, modules: [module, quant, events] } })
  const { container } = render(<ResearchDossierPanel instrumentId="fund-1" />)
  expect(await screen.findByText(module.analysis)).toBeTruthy()
  expect(container.querySelector('#research-analysis-fund-1')?.textContent).toContain(module.analysis)
  expect(container.querySelector('#research-analysis-fund-1')?.textContent).toContain(quant.analysis)
  expect(container.querySelector('#research-analysis-fund-1')?.textContent).toContain(events.analysis)
  expect(container.querySelector('#research-changes-fund-1 [data-testid="recent-events"]')).toBeTruthy()
  expect(screen.getByText(notebook.important_changes[0]).closest('details')).toBeNull()
  expect(screen.getByTestId('themes-panel')).toBeTruthy()
  expect(screen.getByRole('navigation', { name: '研究报告目录' })).toBeTruthy()
  expect(request.mock.calls.map(([path]) => path)).toEqual(['/api/research/instruments/fund-1/dossier?include_history=false'])
  expect(screen.queryByText(material.body)).toBeNull()
  await expandArchive()
  expect(request).toHaveBeenCalledWith('/api/research/instruments/fund-1/dossier?include_history=true', expect.anything())
})
async function expandArchive() {
  if (!screen.queryByRole('dialog', { name: '研究档案' })) fireEvent.click(await screen.findByRole('button', { name: '研究档案' }))
  await screen.findByRole('region', { name: '研究材料' })
  const raw = screen.queryByText('底稿原始记录', { selector: 'summary' }); if (raw && !raw.closest('details')?.open) fireEvent.click(raw)
}

it('presents independent investment dimensions and binds forecast follow-ups to the original version', async () => {
  const oldDate = '2026-09-01T08:00:00+08:00'
  const forecast = { key: 'reserve-demand', version_id: 'forecast-original', created_at: oldDate, updated_at: oldDate, claim: '储备需求可能在下一季度增强。', variable: '储备需求', horizon: '下一季度', observation_condition: '', assumptions: ['持续披露可比口径。'], invalidation: '披露显示持续净卖出。', status: 'active' as const, source_ids: ['original-1'] }
  const investmentView = { version_id: 'view-1', updated_at: oldDate, direction: '中期偏多', horizon: '未来三个月', attractiveness: '上涨后吸引力减弱', risk: '短期波动风险上升', conviction: '', assumptions: [], source_ids: ['original-1'] }
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, version_id: 'notebook-2', investment_view: { ...investmentView, versions: [{ ...investmentView, version_id: 'view-old', direction: '此前偏多', attractiveness: '', risk: '', assumptions: ['此前假设融资约束维持。'] }] }, forecasts: [{ ...forecast, version_id: 'forecast-updated', claim: '储备需求可能更早增强。', updated_at: notebook.checked_at, versions: [forecast] }] } })
  const ask = vi.fn()
  render(<ResearchDossierPanel instrumentId="gold-etf" onAskAssistant={ask} />)
  const view = await screen.findByRole('region', { name: '当前投资判断' })
  expect(within(view).getByText('中期偏多')).toBeTruthy()
  expect(within(view).getByText('上涨后吸引力减弱')).toBeTruthy()
  expect(within(view).getByText('短期波动风险上升')).toBeTruthy()
  expect(within(view).queryByText('判断把握程度')).toBeNull()
  expect(view.querySelector('time')?.dateTime).toBe(oldDate)
  expect(within(view).queryByText('观点修订历史 · 1 次')).toBeNull()
  fireEvent.click(within(view).getByRole('button', { name: '追问当前观点' }))
  expect(ask.mock.calls[0][1]).toEqual({ instrument_id: 'gold-etf', notebook_version_id: 'notebook-2' })
  await expandArchive()
  fireEvent.click(screen.getByText('当前判断与修订历史'))
  fireEvent.click(screen.getByText('观点修订历史 · 1 次'))
  expect(screen.getByText('此前假设融资约束维持。')).toBeTruthy()
  const predictions = screen.getByRole('region', { name: '持续预测' })
  fireEvent.click(within(predictions).getByText('预测修订历史 · 1 次'))
  fireEvent.click(within(predictions).getByRole('button', { name: '追问当时的预测' }))
  expect(ask.mock.calls[1][1]).toEqual({ instrument_id: 'gold-etf', notebook_version_id: 'notebook-2', forecast_key: forecast.key, forecast_version_id: 'forecast-original' })
  expect(ask.mock.calls[1][0]).toContain(forecast.claim)
  expect(within(predictions).getAllByText(forecast.invalidation)).toHaveLength(2)
})

it('refreshes the same instrument dossier after a shared research publication', async () => {
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  await expandArchive()
  await act(async () => announceResearchPublication(['other']))
  expect(request.mock.calls.filter(([path]) => path.includes('include_history=false'))).toHaveLength(1)
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, questions: [{ ...question, assessment: '新证据已修订当前判断。' }] } })
  await act(async () => announceResearchPublication(['fund-1']))
  expect(await screen.findByText('新证据已修订当前判断。')).toBeTruthy()
})

it('reloads a completed notebook when the same research run finishes', async () => {
  request.mockResolvedValue({ ...dossier(), notebook: null })
  const { rerender } = render(<ResearchDossierPanel instrumentId="fund-1" reviewRunId="run-1" reviewStatus="running" />)
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  request.mockResolvedValue(dossier())
  rerender(<ResearchDossierPanel instrumentId="fund-1" reviewRunId="run-1" reviewStatus="completed" />)
  await expandArchive()
  expect(screen.getByRole('region', { name: '当时研究问题与判断' })).toBeTruthy()
  expect(request.mock.calls.filter(([path]) => path.includes('include_history=false'))).toHaveLength(2)
})

it('opens the retained source version on demand instead of treating the public URL as an immutable original', async () => {
  const source = { ...notebook.sources![0], document_id: 'document-1', version_id: 'original-version-1' }
  request.mockImplementation(async (path: string) => path.includes('?source_id=') ? { text: '这是该次研究保存的原文版本。' } : { ...dossier(), prior_sources: [source] })
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  await expandArchive()
  await expandArchive()
  fireEvent.click(screen.getByText('已取得的公开原文 · 1'))
  expect(request.mock.calls.some(([path]) => path.includes('?source_id='))).toBe(false)
  await act(async () => { fireEvent.click(screen.getByText('查看已保存的原文')) })
  expect(await screen.findByText('这是该次研究保存的原文版本。')).toBeTruthy()
  expect(request).toHaveBeenCalledWith('/api/research/instruments/fund-1/dossier?source_id=original-1', expect.anything())
})

it('shows saved numeric evidence under an investment view without inventing publication dates', async () => {
  const source = { source_id: 'computed:vol', source_type: 'computed_metric', title: '价格波动研究', as_of: '2026-09-06T08:00:00+08:00' }
  const saved = { ...source, data: { current: { date: '2026-09-04', volatility_pct: 24.5 }, previous: { date: '2026-09-03', volatility_pct: 22 }, change_pp: 2.5, limitations: ['波动变化不等同于未来下跌。'] }, methodology: { half_life_sessions: 21, annualization: 252 } }
  request.mockImplementation(async (path: string) => path.includes('?source_id=') ? saved : { ...dossier(), notebook: { ...notebook, sources: [source, { source_id: 'instrument-snapshot', source_type: 'instrument_snapshot', title: '已披露资料与指标', run_cutoff: source.as_of }], investment_view: { direction: '中期偏多', horizon: '三个月', attractiveness: '', risk: '短期波动上升', conviction: '', assumptions: [], source_ids: [source.source_id, 'instrument-snapshot'], updated_at: source.as_of } } })
  render(<ResearchDossierPanel instrumentId="gold-etf" />)
  const view = await screen.findByRole('region', { name: '当前投资判断' })
  fireEvent.click(within(view).getByText('关键假设与依据'))
  const evidence = screen.getByRole('dialog', { name: '关键假设与依据' })
  const record = within(evidence).getByText('价格波动研究').closest('li')!
  expect(within(record).getByText(/计算截至/)).toBeTruthy()
  expect(within(record).queryByText(/发布/)).toBeNull()
  const snapshot = within(evidence).getByText('已披露资料与指标').closest('li')!
  expect(within(snapshot).getByText(/研究快照截至/)).toBeTruthy()
  expect(within(snapshot).queryByText(/发布/)).toBeNull()
  await act(async () => { fireEvent.click(within(record).getByText('查看已保存的计算依据')) })
  expect(await within(record).findByText(/当前波动率 24.5%/)).toBeTruthy()
  expect(within(record).getByText('较前次 +2.5 个百分点')).toBeTruthy()
  expect(within(record).getByText('波动变化不等同于未来下跌。')).toBeTruthy()
  expect(request).toHaveBeenCalledWith('/api/research/instruments/gold-etf/dossier?source_id=computed%3Avol', expect.anything())
})

it('labels archived investment views as historical and carries their notebook version into a follow-up', async () => {
  const past = { ...notebook, run_id: 'past-run', version_id: 'past-notebook', investment_view: { direction: '当时偏多', horizon: '未来一季', attractiveness: '', risk: '', conviction: '', assumptions: ['当时需求改善'], source_ids: [], updated_at: '2026-09-01T08:00:00+08:00' } }
  request.mockResolvedValue({ ...dossier(), notebook_history: [{ run_id: past.run_id, checked_at: past.checked_at, important_changes: [], notebook: past }] })
  const ask = vi.fn()
  render(<ResearchDossierPanel instrumentId="fund-1" onAskAssistant={ask} />)
  await expandArchive()
  await expandArchive()
  fireEvent.click(screen.getByText('以往底稿变化 · 1 次'))
  const historical = screen.getByRole('region', { name: '当时投资判断' })
  expect(within(historical).getByText('当时偏多')).toBeTruthy()
  expect(within(historical).queryByText('当前投资判断')).toBeNull()
  fireEvent.click(within(historical).getByText('关键假设与依据'))
  expect(within(historical).getByText('当时需求改善')).toBeTruthy()
  fireEvent.click(within(historical).getByRole('button', { name: '追问当时的观点' }))
  expect(ask.mock.calls[0][1]).toEqual({ instrument_id: 'fund-1', notebook_version_id: 'past-notebook' })
})

it('shows saved research questions and important changes while leaving long materials and history folded away', async () => {
  const ask = vi.fn()
  render(<ResearchDossierPanel instrumentId="fund-1" reviewRunId="completed-1" onAskAssistant={ask} />)
  await expandArchive()
  const questions = screen.getByRole('region', { name: '当时研究问题与判断' })
  expect(within(questions).getByText(question.assessment)).toBeTruthy()
  expect(screen.getAllByText(notebook.important_changes[0]).length).toBeGreaterThan(0)
  expect(screen.getByText(material.body).closest('details')?.open).toBe(false)
  expect(screen.getByText('互联网泡沫中的融资反馈').closest('details')?.open).toBe(false)
  expect(screen.getByText('研究方法正文不在首屏展开。').closest('details')?.open).toBe(false)
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
  await expandArchive()
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
  await expandArchive()
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
  expect(within(screen.getByRole('region', { name: '当时研究问题与判断' })).getByText(question.assessment)).toBeTruthy()
})

it('edits the instrument research mandate through its own API while preserving research materials and judgments', async () => {
  const mandate: ResearchMandate = {
    instrument_id: 'fund-1', entry_id: 'mandate-1', role: 'research_method', updated_at: null,
    title: '该基金的持续研究框架', background: '以基金持仓与管理人披露为背景，持续核实策略的收益来源。',
    mechanisms: ['持仓风格影响净值表现。'], research_approach: ['对照定期披露与历史底稿。'],
    focus: ['重点跟踪策略容量变化。'], source_plan: ['管理人季度报告。'], gaps: ['最新持仓披露仍待补齐。'],
  }
  const saved = { ...mandate, background: '专门跟踪持仓变化与策略容量，按披露周期更新。', focus: ['核对策略容量。', '核对持仓变化。'], updated_at: '2026-09-07T08:00:00+08:00' }
  let currentMandate = mandate
  request.mockImplementation(async (_path: string, init?: RequestInit) => { if (init?.method === 'PUT') { currentMandate = saved; return saved }; return { ...dossier(), mandate: currentMandate } })
  const save = vi.spyOn(researchDossierApi, 'saveResearchMandate')
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  await act(async () => {})
  expect(screen.queryByText(mandate.background)).toBeNull()
  fireEvent.click(await screen.findByRole('button', { name: '研究设置与范围' }))
  const task = screen.getByRole('region', { name: '研究框架' })
  expect(within(task).getByText(mandate.background)).toBeTruthy()
  expect(within(task).getByText(mandate.focus[0]).closest('details')?.open).toBe(true)
  fireEvent.click(within(task).getByRole('button', { name: '编辑研究框架' }))
  fireEvent.change(within(task).getByLabelText('背景与研究边界'), { target: { value: saved.background } })
  fireEvent.change(within(task).getByLabelText('重点关注（每行一项）'), { target: { value: ' 核对策略容量。\n\n核对持仓变化。 ' } })
  fireEvent.click(within(task).getByRole('button', { name: '保存研究框架' }))
  await within(task).findByText(saved.background)
  const input = { title: mandate.title, background: saved.background, mechanisms: mandate.mechanisms, research_approach: mandate.research_approach, focus: saved.focus, user_constraints: [], source_plan: mandate.source_plan, gaps: mandate.gaps, user_methods: [] }
  expect(save).toHaveBeenCalledExactlyOnceWith('fund-1', input)
  expect(request.mock.calls.filter(([, init]) => init?.method)).toEqual([
    ['/api/research/instruments/fund-1/dossier/mandate', { method: 'PUT', body: JSON.stringify(input) }],
  ])
  expect(within(task).getByText(saved.focus[1])).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '关闭研究设置与范围' }))
  await expandArchive()
  expect(within(screen.getByRole('region', { name: '当时研究问题与判断' })).getByText(question.assessment)).toBeTruthy()
  fireEvent.click(screen.getByText(material.title, { selector: 'summary' }))
  expect(within(screen.getByRole('region', { name: '研究材料' })).getByText(material.body)).toBeTruthy()
})

it('keeps user instructions distinct from the analyst focus when editing another mandate field', async () => {
  const mandate: ResearchMandate = {
    instrument_id: 'fund-1', entry_id: 'mandate-1', role: 'research_method', updated_at: notebook.checked_at,
    title: '持续研究', background: '原背景', mechanisms: [], research_approach: [],
    focus: ['研究员新增的盈利问题'], user_focus: ['用户指定的现金回报问题'], author: { origin: 'research' }, source_plan: [], gaps: [],
  }
  request.mockImplementation(async (_path: string, init?: RequestInit) => init?.method === 'PUT' ? { ...mandate, background: '修订背景' } : { ...dossier(), mandate })
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  fireEvent.click(await screen.findByRole('button', { name: '研究设置与范围' }))
  const task = screen.getByRole('region', { name: '研究框架' })
  expect(within(task).getByRole('heading', { name: '用户指定重点' })).toBeTruthy()
  expect(within(task).getByText('研究员新增的盈利问题')).toBeTruthy()
  expect(within(task).getByText('更新来源：研究员')).toBeTruthy()
  fireEvent.click(within(task).getByRole('button', { name: '编辑研究框架' }))
  expect((within(task).getByLabelText('重点关注（每行一项）') as HTMLTextAreaElement).value).toBe('用户指定的现金回报问题')
  fireEvent.change(within(task).getByLabelText('背景与研究边界'), { target: { value: '修订背景' } })
  fireEvent.click(within(task).getByRole('button', { name: '保存研究框架' }))
  await within(task).findByText('修订背景')
  const [, options] = request.mock.calls.find(([, init]) => init?.method === 'PUT')!
  expect(JSON.parse(options.body).focus).toEqual(['用户指定的现金回报问题'])
})

it('uploads the actual file and retains an explicit unread-body state and original link', async () => {
  request.mockImplementation(async (_path: string, init?: RequestInit) => init?.method === 'POST' ? { ...material, source_id: 'scan', title: '扫描报告.pdf', body: '', source: '/api/research/entries/scan/file', metadata: { source: '基金管理人', extraction: '扫描件需补充文字' } } : dossier())
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  await expandArchive()
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
  await expandArchive()
  expect(screen.getByRole('region', { name: '当时研究问题与判断' })).toBeTruthy()
  expect(request.mock.calls.filter(([path]) => path.includes('include_history=false'))).toHaveLength(2)
})

it('keeps the overview summary short and retains the saved notebook when the latest review failed', async () => {
  const savedReview = { run_id: notebook.run_id, checked_at: notebook.checked_at, status: 'completed', coverage: [], current_research: { investment_view: { direction: '已完成的当前判断。', updated_at: notebook.checked_at } } }
  request.mockImplementation(async (path: string) => path.includes('/dossier') ? { ...dossier(), notebook: { ...notebook, investment_view: { direction: '已完成的当前判断。', horizon: '', attractiveness: '', risk: '', conviction: '', assumptions: [], source_ids: [], updated_at: notebook.checked_at } } } : { available: true, sectors: [{ instrument_id: 'fund-1', ticker: 'FUND', sector_name: '测试基金', latest_review: { ...savedReview, run_id: 'failed-2', status: 'failed', summary: '新一轮未完成。' }, last_completed_review: savedReview }], events: [] })
  const { rerender } = render(<SectorResearchPanel instrumentId="fund-1" variant="summary" />)
  await screen.findByText('已完成的当前判断。')
  expect(screen.queryByRole('button', { name: '研究档案' })).toBeNull()
  expect(request.mock.calls.some(([path]) => path.endsWith('/dossier?include_history=false'))).toBe(true)
  rerender(<SectorResearchPanel instrumentId="fund-1" />)
  await expandArchive()
  const questions = screen.getByRole('region', { name: '当时研究问题与判断' })
  expect(within(questions).getByText(question.assessment)).toBeTruthy()
  expect(within(screen.getAllByRole('region', { name: '当前投资判断' })[0]).getByText('已完成的当前判断。')).toBeTruthy()
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
  await expandArchive()
  fireEvent.click(screen.getByText('研究日程与结果'))
  const upcoming = screen.getByText('研究日程与结果').closest('details')!
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
  await expandArchive()
  fireEvent.click(screen.getByText('研究日程与结果'))
  const upcoming = screen.getByText('研究日程与结果').closest('details')!
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
  await expandArchive()
  fireEvent.click(screen.getByText('研究日程与结果'))
  const upcoming = screen.getByText('研究日程与结果').closest('details')!
  expect(within(upcoming).getAllByRole('heading', { level: 4 })).toHaveLength(3)
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
  render(<LanguageProvider messages={researchMessages} patterns={researchPatterns}><LanguageSelector /><ResearchDossierPanel instrumentId="fund-1" onAskAssistant={vi.fn()} /></LanguageProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'Research archive' }))
  fireEvent.click(await screen.findByText('Original notebook records', { selector: 'summary' }))
  const questions = await screen.findByRole('region', { name: 'Historical questions and judgments' })
  expect(within(questions).getByRole('heading', { name: 'Risk' })).toBeTruthy()
  expect(within(questions).getByText('Research')).toBeTruthy()
  expect(within(questions).getByRole('button', { name: 'Ask about this question' })).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await screen.findByRole('region', { name: '当时研究问题与判断' })
  expect(within(questions).getByRole('heading', { name: 'Risk' })).toBeTruthy()
  expect(within(questions).getByText('Research')).toBeTruthy()
  expect(within(questions).getByRole('button', { name: '追问这个问题' })).toBeTruthy()
  window.history.replaceState(null, '', '/')
})


it('keeps decoded prior analysis folded and binds its evidence to the original notebook version', async () => {
  const priorSource = { source_id: 'old-source', title: '旧底稿的原始来源', document_id: 'old-document', version_id: 'old-source-version' }
  const prior = { ...notebook.prior_analysis!, source_ids: ['old-source'], sources: [priorSource], version_id: 'old-notebook-version' }
  request.mockImplementation(async (path: string) => path.includes('?source_id=') ? { text: '当时留存的证据正文' } : { ...dossier(), notebook: { ...notebook, version_id: 'new-notebook-version', prior_analysis: prior } })
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  await expandArchive()
  const label = await screen.findByText(/^既有综合分析/)
  expect(label.closest('details')?.open).toBe(false)
  fireEvent.click(label)
  const record = label.closest('details')!
  expect(within(record).getByText(prior.fundamental_view)).toBeTruthy()
  fireEvent.click(within(record).getByText('查看已保存的原文'))
  expect(await within(record).findByText('当时留存的证据正文')).toBeTruthy()
  expect(request).toHaveBeenCalledWith('/api/research/instruments/fund-1/dossier?source_id=old-source&version_id=old-notebook-version', expect.anything())
})

it('does not present a quiet recheck of the same revision as a historical change', async () => {
  const first = { ...notebook, run_id: 'first-run', version_id: 'original-version' }
  request.mockResolvedValue({ ...dossier(), notebook: { ...first, run_id: 'quiet-run', checked_at: '2026-09-23T00:00:00Z' },
    notebook_history: [{ version_id: first.version_id, run_id: first.run_id, checked_at: first.checked_at, important_changes: [], notebook: first }] })
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  await expandArchive()
  expect(screen.queryByText(/^以往底稿变化/)).toBeNull()
})

it('keeps saved reviews and lessons readable in the report archive', async () => {
  const review = { key: 'financing-review', forecast_key: 'financing', forecast_version_id: 'forecast-original',
    outcome: '回款变化尚不能确定', mechanism_assessment: '融资完成不证明经营改善', alternative_explanations: [], source_ids: ['original-1'] }
  const lesson = { key: 'financing-lesson', lesson: '区分资金取得与使用效果', applicability: '类似融资情景', limitations: '单次结果不证明因果',
    forecast_key: 'financing', forecast_version_id: 'forecast-original', source_ids: ['original-1'], version_id: 'lesson-original' }
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, forecast_reviews: [review], lessons: [lesson] } })
  render(<ResearchDossierPanel instrumentId="fund-1" onAskAssistant={vi.fn()} />)
  await expandArchive()
  fireEvent.click(screen.getByText('预测复盘与研究经验', { selector: 'summary' }))
  expect(screen.getByText(review.outcome)).toBeTruthy()
  expect(screen.getByText(review.mechanism_assessment)).toBeTruthy()
  expect(screen.getByText(lesson.lesson)).toBeTruthy()
  expect(screen.getByText(lesson.limitations)).toBeTruthy()
  expect(screen.getAllByRole('link', { name: '公司原始报告' }).length).toBeGreaterThan(0)
  expect(Boolean(screen.queryByRole('button', { name: '复核这条经验' }))).toBe(true)
})

it('does not present unavailable research as an empty assessment while the dossier is loading', async () => {
  let resolve!: (value: ResearchDossier) => void
  request.mockReturnValue(new Promise<ResearchDossier>(done => { resolve = done }))
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  expect(screen.getByRole('status').textContent).toBe('Loading')
  expect(screen.queryByText(/尚未形成/)).toBeNull()
  expect(screen.queryByTestId('themes-panel')).toBeNull()
  await act(async () => resolve({ ...dossier(), notebook: null }))
  expect(screen.getByText(/尚未形成当前投资判断/)).toBeTruthy()
})

it('reads a decision and its changes without disclosures and preserves each change date and exact source', async () => {
  const brief = { recommendation: '保持观察，等待现金回报改善。', rationale: '收入兑现仍不足以覆盖资本投入。', conditions: ['资本开支回落且回款改善'], horizon: '未来一季', next_decision: '下季财报', source_ids: ['original-1'], updated_at: '2026-09-10T08:00:00Z' }
  const change = { key: 'cash', title: '现金回报下降', before: '投入与回款基本匹配。', after: '投入快于回款。', baseline_as_of: '2026-06-30', mechanism: '资本开支先于收入兑现。', decision_implication: '当前不增加此类敞口。', condition: '核对下一季回款。', source_ids: ['original-1'], updated_at: '2026-09-09T08:00:00Z' }
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, version_id: 'decision-notebook', checked_at: '2026-09-24T08:00:00Z', decision_brief: brief, changes: [change] } })
  const { container } = render(<ResearchDossierPanel instrumentId="fund-1" />)
  expect((await screen.findByText(brief.recommendation)).closest('details')).toBeNull()
  expect(screen.getByText(brief.conditions[0]).closest('details')).toBeNull()
  expect(screen.getByText(change.decision_implication).closest('details')).toBeNull()
  expect(screen.queryByText(notebook.important_changes[0])).toBeNull()
  expect(container.querySelector('.research-change time')?.getAttribute('datetime')).toBe(change.updated_at)
  expect(container.querySelector('.research-report-changes')?.compareDocumentPosition(container.querySelector('.research-report-body')!)! & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '查看变化依据' }))
  expect(screen.getByRole('dialog', { name: change.title })).toBeTruthy()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('never promotes an unreconciled earlier recommendation after the investment assessment changes', async () => {
  const brief = { recommendation: '原建议增加风险。', rationale: '原理由。', conditions: [], horizon: '原期限', next_decision: '', source_ids: [], updated_at: '2026-09-01T08:00:00Z', needs_review: true, basis_view_version_id: 'old-view' }
  request.mockResolvedValue({ ...dossier(), notebook: { ...notebook, decision_brief: brief, investment_view: { direction: '当前转为审慎。', horizon: '本月', attractiveness: '', risk: '现金流恶化', conviction: '', assumptions: [], source_ids: [], updated_at: '2026-09-24T08:00:00Z', version_id: 'new-view' } } })
  render(<ResearchDossierPanel instrumentId="fund-1" />)
  expect(await screen.findByText('当前转为审慎。')).toBeTruthy()
  expect(screen.queryByText(brief.recommendation)).toBeNull()
  expect(screen.getByText('投资判断已变化，原建议待复核。')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '查看原建议' }))
  expect(within(screen.getByRole('dialog', { name: '查看原建议' })).getByText(brief.recommendation)).toBeTruthy()
})
