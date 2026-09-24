// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ResearchModules, { EvidenceFigure, fundamentalSectionTitle } from './ResearchModules'
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

it('names the fundamental chapter by selected exposure methods and preserves unrecognized saved analysis', () => {
  const withMethod = (id: string) => ({ ...plan, modules: [{ ...plan.modules[0], id }] })
  expect(fundamentalSectionTitle(withMethod('commodity-supply-demand'))).toBe('供需与定价')
  expect(fundamentalSectionTitle(withMethod('fund-strategy'))).toBe('策略与回报来源')
  expect(fundamentalSectionTitle(withMethod('equity-aggregation'))).toBe('成份与定价')
  const saved = { ...notebook, modules: [{ ...module, key: 'custom-existing-method', figure_source_ids: [] }, { ...module, figure_source_ids: [] }] }
  render(<ResearchModules instrumentId="fund" notebook={saved} section="fundamentals" />)
  expect(screen.getByRole('heading', { name: 'custom-existing-method' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: '市场与量化' })).toBeNull()
})

it('uses method order, exposes evidence gaps and never claims an old result follows an updated method', async () => {
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
  fireEvent.click(screen.getByRole('button', { name: '追问这一领域' }))
  expect(ask.mock.calls[0][1]).toEqual({ instrument_id: 'fund', notebook_version_id: 'notebook-original' })
  await waitFor(() => expect(request).toHaveBeenCalledWith('/api/research/instruments/fund/dossier?source_id=computed%3Acompare&version_id=notebook-original', expect.anything()))
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
  expect(request).toHaveBeenCalledWith('/api/research/instruments/fund/dossier?source_id=computed%3Acompare&version_id=notebook-original', expect.anything())
})

it('shows retained holdings weights without renormalizing or treating the collection date as the holdings date', () => {
  render(<EvidenceFigure source={{ source_id: 'holdings', source_type: 'sector_snapshot', snapshot: { holdings_as_of: '2026-06-30', holdings_observed_on: '2026-07-18', top_holdings: [{ symbol: 'AAA', weight_percent: 20 }, { symbol: 'BBB', weight_percent: 10 }] } }} />)
  expect(screen.getByText('20.00%')).toBeTruthy()
  expect(screen.getByText('10.00%')).toBeTruthy()
  expect(screen.getByText(/持仓日期 2026-06-30 · 采集观察日 2026-07-18/)).toBeTruthy()
  expect(screen.getByText(/权重未重新归一化/)).toBeTruthy()
})

it('adds and removes explicit method priorities while preserving user constraints and unrelated source references', async () => {
  const mandate: ResearchMandate = { instrument_id: 'fund', entry_id: null, role: 'research_method', updated_at: null, title: '研究方法', background: '', mechanisms: [], research_approach: [], focus: [], user_focus: ['现金回报'], source_plan: [], gaps: [], user_constraints: ['仅使用可核实披露'], module_focus: [{ module_id: 'market-quantitative', reason: '保留风险比较', source_ids: ['original-method'], selected_by: 'user' }] }
  request.mockImplementation(async (_path: string, init?: RequestInit) => ({ ...mandate, ...JSON.parse(String(init?.body)) }))
  render(<ResearchMandateRecord instrumentId="fund" mandate={mandate} availableModules={plan.modules} onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '编辑研究框架' }))
  fireEvent.click(screen.getByRole('button', { name: '添加研究领域' }))
  fireEvent.change(screen.getByLabelText('选择原因 2'), { target: { value: '额外检验债务风险补偿' } })
  fireEvent.click(screen.getByRole('button', { name: '保存研究框架' }))
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  const input = JSON.parse(request.mock.calls[0][1].body)
  expect(input.user_constraints).toEqual(mandate.user_constraints)
  expect(input.focus).toEqual(['现金回报'])
  expect(input.module_focus[0]).toEqual(mandate.module_focus![0])
  expect(input.module_focus[1]).toMatchObject({ module_id: 'pricing-compensation', reason: '额外检验债务风险补偿', source_ids: [] })
  fireEvent.click(screen.getByRole('button', { name: '编辑研究框架' }))
  fireEvent.click(screen.getAllByRole('button', { name: '移除重点' })[0])
  fireEvent.click(screen.getByRole('button', { name: '保存研究框架' }))
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  expect(JSON.parse(request.mock.calls[1][1].body).module_focus).toEqual([])
})

it('keeps method changes unavailable in report mode', () => {
  const mandate: ResearchMandate = { instrument_id: 'fund', entry_id: null, role: 'research_method', updated_at: null, title: '研究方法', background: '', mechanisms: [], research_approach: [], focus: [], source_plan: [], gaps: [] }
  const { rerender } = render(<ResearchMandateRecord instrumentId="fund" mandate={mandate} availableModules={plan.modules} onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '编辑研究框架' }))
  expect(screen.getByRole('button', { name: '保存研究框架' })).toBeTruthy()
  rerender(<ResearchMandateRecord instrumentId="fund" mandate={mandate} readOnly availableModules={plan.modules} onSaved={vi.fn()} />)
  expect(screen.queryByRole('button', { name: '保存研究框架' })).toBeNull()
  expect(screen.queryByRole('button', { name: '添加研究领域' })).toBeNull()
})


it('scales disclosed signed or leveraged weights without clipping their values', () => {
  render(<EvidenceFigure source={{ source_id: 'holdings', source_type: 'sector_snapshot', snapshot: { top_holdings: [{ symbol: 'Long', weight_percent: 120 }, { symbol: 'Short', weight_percent: -10 }] } }} />)
  expect(screen.getByText('120.00%')).toBeTruthy()
  expect(screen.getByText('-10.00%')).toBeTruthy()
  expect(screen.getByText(/权重标尺 -10% 至 120%/)).toBeTruthy()
})

it('applies reading order and supplementary preferences without hiding risk or event analysis', () => {
  const saved = { ...notebook, modules: [
    { ...module, figure_source_ids: [] },
    { ...module, key: 'business-fundamentals', figure_source_ids: [] },
    { ...module, key: 'pricing-compensation', figure_source_ids: [] },
    { ...module, key: 'events-expectations', figure_source_ids: [] },
  ] }
  const preferences = { priority_modules: ['pricing-compensation'], hidden_modules: ['business-fundamentals', 'market-quantitative', 'events-expectations'], summary_focus: [], detail_level: 'standard' as const }
  const { container, rerender } = render(<ResearchModules instrumentId="fund" notebook={saved} preferences={preferences} />)
  expect([...container.querySelectorAll('.research-domain-module h3')].map(node => node.textContent)).toEqual(['定价与风险补偿', '市场与量化', '事件、预期与争议'])
  rerender(<ResearchModules instrumentId="fund" notebook={saved} preferences={preferences} supplementary />)
  expect([...container.querySelectorAll('.research-domain-module h3')].map(node => node.textContent)).toEqual(['经营与现金流'])
})

it('saves human research methods and report preferences without replacing existing source references', async () => {
  const mandate: ResearchMandate = { instrument_id: 'fund', entry_id: null, role: 'research_method', updated_at: null, title: '研究方法', background: '', mechanisms: [], research_approach: [], focus: [], user_focus: ['现金回报'], source_plan: ['管理人披露'], gaps: [], user_methods: ['复核盈利与现金转化'], report_preferences: { priority_modules: ['market-quantitative'], hidden_modules: [], summary_focus: ['盈利兑现'], detail_level: 'standard' } }
  request.mockImplementation(async (_path: string, init?: RequestInit) => ({ ...mandate, ...JSON.parse(String(init?.body)) }))
  render(<ResearchMandateRecord instrumentId="fund" mandate={mandate} availableModules={plan.modules} onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '编辑研究框架' }))
  fireEvent.change(screen.getByLabelText('人工指定的研究方法（每行一项）'), { target: { value: ' 对照现金流与资本开支\n\n检验债务偿付来源 ' } })
  fireEvent.change(screen.getByLabelText('摘要重点（每行一项）'), { target: { value: ' 风险变化\n\n回报兑现 ' } })
  fireEvent.change(screen.getByLabelText('研究篇幅'), { target: { value: 'detailed' } })
  fireEvent.click(screen.getByRole('button', { name: '保存研究框架' }))
  await waitFor(() => expect(request).toHaveBeenCalledOnce())
  const input = JSON.parse(request.mock.calls[0][1].body)
  expect(input.user_methods).toEqual(['对照现金流与资本开支', '检验债务偿付来源'])
  expect(input.report_preferences).toEqual({ priority_modules: ['market-quantitative'], hidden_modules: [], summary_focus: ['风险变化', '回报兑现'], detail_level: 'detailed' })
  expect(input.source_plan).toEqual(['管理人披露'])
})
