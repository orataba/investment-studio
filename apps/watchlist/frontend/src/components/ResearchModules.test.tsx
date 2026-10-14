// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ResearchModules, { EvidenceFigure, SavedFigure } from './ResearchModules'
import ResearchMandateRecord from './ResearchMandateRecord'
import type { ResearchMandate, ResearchModule, ResearchPlan, SavedResearchNotebook } from '../lib/researchDossierApi'
const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ fetchJson: request, API_BASE_URL: '' }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const module: ResearchModule = { key: 'market-quantitative', summary: '回报高于基准，但样本短。', analysis: '共同样本比较尚不能确认长期优势。\n仍需区分风险暴露与超额收益。', coverage: 'partial', gaps: ['尚缺长期样本'], next_check: '观察回撤与超额收益能否持续。', source_ids: [], figure_source_ids: ['computed:compare'], evidence_as_of: '2026-09-18', method_version: '1', updated_at: '2026-09-19T08:00:00Z' }
const plan: ResearchPlan = { scope: '登记ETF及已披露成份', basis: [], gaps: [], modules: [
  { id: 'pricing-compensation', title: '定价与风险补偿', version: '2', body: '', questions: [], evidence_requirements: [], applicability: 'unconfirmed', reason: '债务期限尚未披露' },
  { id: module.key, title: '市场与量化', version: '2', body: '', questions: [], evidence_requirements: [], applicability: 'applicable', reason: '具备可比行情' },
] }
const notebook: SavedResearchNotebook = { version_id: 'notebook-original', run_id: 'run', checked_at: '2026-09-20T08:00:00Z', modules: [module], key_drivers: [], questions: [], important_changes: [], next_research: [], source_ids: ['computed:compare'], sources: [{ source_id: 'computed:compare', source_type: 'computed_metric', title: '共同样本结果' }] }

it('loads only the version-bound figure and leaves complete originals behind an explicit read', async () => {
  const source = { source_id: 'computed:compare', source_type: 'computed_metric' as const, title: '共同样本结果' }
  request.mockImplementation(async (url: string) => url.endsWith('&projection=figure')
    ? { ...source, display_projection: 'figure', data: { sample_start: '2026-09-01', sample_end: '2026-09-18', observations: 14,
      rows: [{ instrument_id: 'fund', return_pct: 2, max_drawdown_pct: -3 }] } }
    : { ...source, data: { rows: [] }, input_series: { raw_original: [1, 2, 3] } })
  render(<SavedFigure instrumentId="fund" notebookVersionId="notebook-original" source={source} />)
  await screen.findByRole('img', { name: '共同样本区间收益对比' })
  expect(request).toHaveBeenCalledTimes(1)
  expect(request).toHaveBeenLastCalledWith('/api/research/instruments/fund/dossier?source_id=computed%3Acompare&version_id=notebook-original&projection=figure', expect.anything())
  fireEvent.click(screen.getByRole('button', { name: '来源与完整依据' }))
  expect(request).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByText('查看已保存的计算依据'))
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  expect(request).toHaveBeenLastCalledWith('/api/research/instruments/fund/dossier?source_id=computed%3Acompare&version_id=notebook-original', expect.anything())
})

it('uses the research question as the analysis title without requiring a catalog method', () => {
  const saved = { ...notebook, modules: [{ ...module, key: 'cash-conversion', title: '投入能否转化为现金回报？', figure_source_ids: [] }, { ...module, figure_source_ids: [] }] }
  render(<ResearchModules instrumentId="fund" notebook={saved} section="fundamentals" />)
  expect(screen.getByRole('heading', { name: '投入能否转化为现金回报？' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: '市场与量化' })).toBeNull()
})

it('exposes evidence gaps and never claims an old result follows an updated method', async () => {
  request.mockResolvedValue({ data: { rows: [] } })
  const ask = vi.fn()
  const { container } = render(<ResearchModules instrumentId="fund" plan={plan} notebook={notebook} onAskAssistant={ask} />)
  expect([...container.querySelectorAll('.research-domain-module h3')].map(node => node.textContent)).toEqual(['市场与量化'])
  expect(screen.queryByText('适用范围待核实')).toBeNull()
  expect(screen.queryByText('部分覆盖')).toBeNull()
  expect(screen.getByText('尚缺长期样本')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '依据与原文 · 1' }))
  expect(screen.getByText('部分覆盖')).toBeTruthy()
  expect(screen.getByText(/方法版本 1/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '关闭市场与量化 · 研究依据' }))
  expect(screen.getByText('方法已更新，待复核；以下保留原方法下的研究判断。')).toBeTruthy()
  expect(screen.getByText(module.analysis, { normalizer: text => text }).closest('details')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '追问这项分析' }))
  expect(ask.mock.calls[0][1]).toEqual({ instrument_id: 'fund', notebook_version_id: 'notebook-original', source_ids: ['computed:compare'] })
  await waitFor(() => expect(request).toHaveBeenCalledWith('/api/research/instruments/fund/dossier?source_id=computed%3Acompare&version_id=notebook-original&projection=figure', expect.anything()))
})

it.each(['removed', 'unconfirmed'] as const)('keeps existing analysis when optional method selection is %s', (state) => {
  const saved = { ...notebook, modules: [{ ...module, key: 'equity-aggregation', figure_source_ids: [] }] }
  const current: ResearchPlan = { ...plan, mode: 'question_driven', modules: state === 'removed' ? [] : [{
    ...plan.modules[0], id: 'equity-aggregation', version: '1', applicability: 'unconfirmed',
  }] }
  const { container } = render(<ResearchModules instrumentId="fund" plan={current} notebook={saved} />)
  expect(screen.getByText(module.summary)).toBeTruthy()
  expect(container.querySelector('.sector-research-limitation')).toBeNull()
})

it('renders figures referenced only through figure_source_ids from the exact saved notebook, including negative returns', async () => {
  request.mockResolvedValue({ source_id: 'computed:compare', source_type: 'computed_metric', title: '共同样本结果', data: { sample_start: '2026-06-01', sample_end: '2026-09-18', observations: 80, currency: 'USD', rows: [
    { instrument_id: 'fund', name: 'Target', return_pct: 12.34, max_drawdown_pct: -8 },
    { instrument_id: 'benchmark', name: 'Benchmark', return_pct: -2.5, max_drawdown_pct: -10 },
  ], limitations: ['短样本不能说明长期优势'] } })
  render(<ResearchModules instrumentId="fund" plan={plan} notebook={notebook} />)
  expect(await screen.findByRole('img', { name: '共同样本区间收益对比' })).toBeTruthy()
  expect(screen.getByText('+12.34%')).toBeTruthy()
  expect(screen.getByText('-2.50%')).toBeTruthy()
  expect(screen.getByText(/2026-06-01 至 2026-09-18/)).toBeTruthy()
  expect(request).toHaveBeenCalledWith('/api/research/instruments/fund/dossier?source_id=computed%3Acompare&version_id=notebook-original&projection=figure', expect.anything())
})

it('shows retained holdings weights without renormalizing or treating the collection date as the holdings date', () => {
  render(<EvidenceFigure source={{ source_id: 'holdings', source_type: 'sector_snapshot', snapshot: { holdings_as_of: '2026-06-30', holdings_observed_on: '2026-07-18', top_holdings: [{ symbol: 'AAA', weight_percent: 20 }, { symbol: 'BBB', weight_percent: 10 }] } }} />)
  expect(screen.getByText('20.00%')).toBeTruthy()
  expect(screen.getByText('10.00%')).toBeTruthy()
  expect(screen.getByText(/持仓日期 2026-06-30 · 采集观察日 2026-07-18/)).toBeTruthy()
  expect(screen.getByText(/权重未重新归一化/)).toBeTruthy()
})

const mandate: ResearchMandate = { instrument_id: 'fund', entry_id: null, role: 'research_method', updated_at: null, guidance: '核实现金回报，仅使用可核实披露。', revision: 3, title: '研究方法', background: '', mechanisms: [], research_approach: [], focus: ['研究员新增的问题'], user_focus: ['现金回报'], source_plan: [], gaps: [], user_constraints: ['仅使用可核实披露'] }

it('shows actual guidance changes without attributing analyst work-plan updates to the user', () => {
  render(<ResearchMandateRecord instrumentId="fund" mandate={{ ...mandate, versions: [
    { ...mandate, version_id: 'v1', guidance: '此前指导', guidance_updated_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-02T00:00:00Z' },
    { ...mandate, version_id: 'v2', guidance: '此前指导', guidance_updated_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-03T00:00:00Z' },
    { ...mandate, version_id: 'v3', updated_at: '2026-09-01T00:00:00Z', author: { origin: 'research', display_name: '研究员' } },
  ] }} onSaved={vi.fn()} />)
  expect(screen.getAllByText('此前指导')).toHaveLength(1)
  expect(screen.getAllByText(mandate.guidance)).toHaveLength(1)
  expect(screen.queryByText(/更新来源.*研究员/)).toBeNull()
  expect(screen.getAllByText('主研究员 · 总览 · 研究 Skill')).toHaveLength(2)
  expect(screen.getByRole('heading', { name: /指导更新.*2026-08-01/ })).toBeTruthy()
})

it('retains role-only guidance changes and exposes the saved role skills without duplicating work-plan revisions', () => {
  const previousSkills = { overview: '旧总览范围', recent_updates: '旧动态证据要求', themes: '旧主题研究框架', quantitative: '旧量化样本约束' }
  const currentSkills = { ...previousSkills, themes: '新主题研究框架' }
  render(<ResearchMandateRecord instrumentId="fund" mandate={{ ...mandate, section_skills: currentSkills, versions: [
    { ...mandate, version_id: 'v1', section_skills: previousSkills, updated_at: '2026-08-01T00:00:00Z' },
    { ...mandate, version_id: 'v2', section_skills: previousSkills, focus: ['仅更新研究员计划'], updated_at: '2026-08-02T00:00:00Z' },
    { ...mandate, version_id: 'v3', section_skills: currentSkills, updated_at: '2026-08-03T00:00:00Z' },
  ] }} onSaved={vi.fn()} />)
  const summary = screen.getByText('指导修订历史', { selector: 'summary' })
  fireEvent.click(summary)
  const history = summary.closest('details')!
  expect(history.querySelectorAll('article')).toHaveLength(1)
  expect(within(history).getByText(mandate.guidance)).toBeTruthy()
  for (const skill of Object.values(previousSkills)) expect(within(history).getByText(skill)).toBeTruthy()
  expect(within(history).queryByText('新主题研究框架')).toBeNull()
  expect(within(history).queryByText('仅更新研究员计划')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '编辑研究指导' }))
  expect(screen.getByText(/本标的补充用于主研究员及动态、主题、量化三位专员/)).toBeTruthy()
})

it('saves one guidance paragraph at its original revision without resubmitting generated analyst fields', async () => {
  const onSaved = vi.fn()
  const saved = { ...mandate, guidance: '未来一年盈利增长能否转化为现金回报？', revision: 4 }
  request.mockResolvedValue(saved)
  render(<ResearchMandateRecord instrumentId="fund" mandate={mandate} onSaved={onSaved} />)
  fireEvent.click(screen.getByRole('button', { name: '编辑研究指导' }))
  expect(screen.getAllByRole('textbox')).toHaveLength(6)
  fireEvent.change(screen.getByLabelText('本标的研究范围与约束（可选）'), { target: { value: ` ${saved.guidance} ` } })
  fireEvent.click(screen.getByRole('button', { name: '保存研究指导' }))
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved))
  expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({ section_skills: { overview: '', recent_updates: '', themes: '', quantitative: '' }, guidance: saved.guidance, focus_company_symbols: [], expected_revision: 3 })
})

it('allows an empty guidance paragraph without requiring a title or a research method', async () => {
  request.mockResolvedValue({ ...mandate, guidance: '', revision: 4 })
  render(<ResearchMandateRecord instrumentId="fund" mandate={mandate} onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '编辑研究指导' }))
  fireEvent.change(screen.getByLabelText('本标的研究范围与约束（可选）'), { target: { value: '  ' } })
  fireEvent.click(screen.getByRole('button', { name: '保存研究指导' }))
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({ section_skills: { overview: '', recent_updates: '', themes: '', quantitative: '' }, guidance: '', focus_company_symbols: [], expected_revision: 3 })
})

it('preserves the local draft on conflict and requires review of the latest guidance before saving against it', async () => {
  const onSaved = vi.fn()
  request.mockRejectedValueOnce(new Error('研究指导已被其他人更新，请核对最新版本。'))
  const { rerender } = render(<ResearchMandateRecord instrumentId="fund" mandate={mandate} onSaved={onSaved} />)
  fireEvent.click(screen.getByRole('button', { name: '编辑研究指导' }))
  fireEvent.change(screen.getByLabelText('本标的研究范围与约束（可选）'), { target: { value: '我的新研究问题' } })
  fireEvent.click(screen.getByRole('button', { name: '保存研究指导' }))
  await screen.findByRole('alert')
  const latest = { ...mandate, guidance: '另一位用户刚修改的研究期限', revision: 4 }
  rerender(<ResearchMandateRecord instrumentId="fund" mandate={latest} onSaved={onSaved} />)
  expect(screen.getByLabelText('本标的研究范围与约束（可选）')).toHaveProperty('value', '我的新研究问题')
  request.mockResolvedValueOnce({ mandate: latest })
  fireEvent.click(screen.getByRole('button', { name: '读取最新指导并核对' }))
  await screen.findByText(latest.guidance)
  expect(screen.getByLabelText('本标的研究范围与约束（可选）')).toHaveProperty('value', '我的新研究问题')
  expect(request.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1)
  request.mockResolvedValueOnce({ ...latest, guidance: '我的新研究问题', revision: 5 })
  fireEvent.click(screen.getByRole('button', { name: '保存研究指导' }))
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce())
  expect(JSON.parse(request.mock.calls[2][1].body)).toEqual({ section_skills: { overview: '', recent_updates: '', themes: '', quantitative: '' }, guidance: '我的新研究问题', focus_company_symbols: [], expected_revision: 4 })
})

it('keeps guidance writes unavailable after access becomes read-only', () => {
  const { rerender } = render(<ResearchMandateRecord instrumentId="fund" mandate={mandate} onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '编辑研究指导' }))
  expect(screen.getByRole('button', { name: '保存研究指导' })).toBeTruthy()
  rerender(<ResearchMandateRecord instrumentId="fund" mandate={mandate} readOnly onSaved={vi.fn()} />)
  expect(screen.queryByRole('button', { name: '保存研究指导' })).toBeNull()
  expect(screen.queryByRole('textbox')).toBeNull()
})

it('edits explicit focus company symbols with guidance and retains symbol-only history', async () => {
  const saved = { ...mandate, focus_company_symbols: ['AAPL', 'MSFT', 'BRK-B'], revision: 4 }
  request.mockResolvedValue(saved)
  render(<ResearchMandateRecord instrumentId="fund" mandate={{ ...mandate, focus_company_symbols: ['AAPL'],
    versions: [{ ...mandate, version_id: 'old-focus', focus_company_symbols: ['NVDA'] }] }} onSaved={vi.fn()} />)
  expect(screen.getByText('NVDA')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '编辑研究指导' }))
  const companies = screen.getByLabelText('额外重点公司代码（可选）', { exact: false })
  expect(companies).toHaveProperty('value', 'AAPL')
  fireEvent.change(companies, { target: { value: ' aapl，msft  AAPL; BRK-B ' } })
  fireEvent.click(screen.getByRole('button', { name: '保存研究指导' }))
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  expect(JSON.parse(request.mock.calls[0][1].body)).toMatchObject({ focus_company_symbols: saved.focus_company_symbols, expected_revision: 3 })
})

it('keeps the focus-company draft on conflict and replaces it only on explicit selection', async () => {
  request.mockRejectedValueOnce(new Error('研究指导已被其他人更新，请核对最新版本。'))
  render(<ResearchMandateRecord instrumentId="fund" mandate={{ ...mandate, focus_company_symbols: ['AAPL'] }} onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '编辑研究指导' }))
  const field = () => screen.getByLabelText('额外重点公司代码（可选）', { exact: false })
  fireEvent.change(field(), { target: { value: 'NVDA' } })
  fireEvent.click(screen.getByRole('button', { name: '保存研究指导' }))
  await screen.findByRole('alert')
  request.mockResolvedValueOnce({ mandate: { ...mandate, focus_company_symbols: ['MSFT'], revision: 4 } })
  fireEvent.click(screen.getByRole('button', { name: '读取最新指导并核对' }))
  await screen.findByText('MSFT')
  expect(field()).toHaveProperty('value', 'NVDA')
  fireEvent.click(screen.getByRole('button', { name: '用最新指导替换草稿' }))
  expect(field()).toHaveProperty('value', 'MSFT')
})

it('scales disclosed signed or leveraged weights without clipping their values', () => {
  render(<EvidenceFigure source={{ source_id: 'holdings', source_type: 'sector_snapshot', snapshot: { top_holdings: [{ symbol: 'Long', weight_percent: 120 }, { symbol: 'Short', weight_percent: -10 }] } }} />)
  expect(screen.getByText('120.00%')).toBeTruthy()
  expect(screen.getByText('-10.00%')).toBeTruthy()
  expect(screen.getByText(/权重标尺 -10% 至 120%/)).toBeTruthy()
})

it('preserves published research order regardless of optional method order', () => {
  const saved = { ...notebook, modules: [
    { ...module, key: 'cash-conversion', title: '现金回报是否兑现？', figure_source_ids: [] },
    { ...module, key: 'business-fundamentals', figure_source_ids: [] },
    { ...module, key: 'pricing-compensation', figure_source_ids: [] },
  ] }
  const { container } = render(<ResearchModules instrumentId="fund" notebook={saved} plan={plan} />)
  expect([...container.querySelectorAll('.research-domain-module h3')].map(node => node.textContent)).toEqual(['现金回报是否兑现？', '经营与现金流', '定价与风险补偿'])
})
