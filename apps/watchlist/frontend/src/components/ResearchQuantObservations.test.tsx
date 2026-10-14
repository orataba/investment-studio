// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
vi.mock('./ResearchCurrentNumbers', () => ({ default: () => <div data-testid="current-numbers" /> }))
import ResearchQuantObservations from './ResearchQuantObservations'
import type { SavedResearchNotebook } from '../lib/researchDossierApi'
vi.mock('./ResearchModules', () => ({ SavedFigure: ({ source }: { source: { title: string } }) => <figure>{source.title}</figure> }))
vi.mock('./ResearchEvidence', () => ({ dateLabel: (value: string) => value, SourceList: ({ sources }: { sources: Array<{ source_id: string; title: string }> }) => <ul>{sources.map(source => <li key={source.source_id}>{source.title}</li>)}</ul> }))
afterEach(cleanup)

it('displays classified market and fundamental metrics and keeps unclassified evidence on demand', () => {
  const notebook: SavedResearchNotebook = {
    run_id: 'research', checked_at: '2026-09-27', source_ids: [], key_drivers: [], questions: [], important_changes: [], next_research: [],
    modules: [{ key: 'market-quantitative', summary: '波动率抬升。', analysis: '价格和行业内部结构需要结合观察。', coverage: 'partial', gaps: ['基准数据尚未配置。', '只有一段市场样本。'], next_check: '', source_ids: ['vol', 'cashflow', 'unclassified'], figure_source_ids: ['vol', 'cashflow', 'unclassified'], evidence_as_of: '2026-09-26' }],
    sources: [{ source_id: 'vol', title: '行业波动率', observation_domain: 'market' }, { source_id: 'cashflow', title: '公司现金流', observation_domain: 'fundamental' }, { source_id: 'unclassified', title: '尚未分类的计算' }],
  }
  render(<ResearchQuantObservations instrumentId="xlk" notebook={notebook} />)
  expect(screen.getByText('行业波动率')).toBeTruthy()
  expect(screen.getByText('公司现金流')).toBeTruthy()
  expect(screen.queryByText('尚未分类的计算')).toBeNull()
  expect(screen.getByText('部分分析受数据范围限制')).toBeTruthy()
  expect(screen.queryByText('基准数据尚未配置。')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '数据覆盖与缺口' }))
  expect(screen.getByText('基准数据尚未配置。')).toBeTruthy()
  expect(screen.getByText('只有一段市场样本。')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '关闭数据覆盖与缺口' }))
  fireEvent.click(screen.getByRole('button', { name: '量化解释与依据' }))
  expect(screen.getAllByText('公司现金流')).toHaveLength(2)
  expect(screen.getByText('尚未分类的计算')).toBeTruthy()
})


it('keeps canonical quantitative data available before model analysis exists', () => {
  const { container } = render(<ResearchQuantObservations instrumentId="private-fund" notebook={null} />)
  expect(container.textContent).toContain('尚未形成量化分析')
  expect(screen.getByTestId('current-numbers')).toBeTruthy()
})

it('shows published analysis under free module keys by its retained numeric sources, not its title', () => {
  const module = { summary: '', analysis: '', coverage: 'partial' as const, gaps: [], next_check: '', source_ids: [], figure_source_ids: [], evidence_as_of: '2026-10-12' }
  const notebook: SavedResearchNotebook = {
    run_id: 'published', checked_at: '2026-10-12', source_ids: [], key_drivers: [], questions: [], important_changes: [], next_research: [],
    modules: [
      { ...module, key: 'return-path-and-volatility', title: '收益路径与波动结构', summary: '8月至10月月收益为负，7月为正。', analysis: '10月尚未结束，不能与完整月份直接等同。', figure_source_ids: ['returns'] },
      { ...module, key: 'exposure-and-valuation-evidence', title: '敞口与估值证据', summary: '持仓资料缺失，不等于没有持仓。', source_ids: ['holdings'] },
      { ...module, key: 'unrelated', title: '量化标题不构成分类依据', summary: '没有数值来源的另项分析。', source_ids: ['unclassified'] },
      { ...module, key: 'market-quantitative', summary: '已确认尚缺估值数据。' },
    ],
    sources: [{ source_id: 'returns', title: '留存收益路径', observation_domain: 'market' }, { source_id: 'holdings', title: '留存持仓资料', observation_domain: 'fundamental' }, { source_id: 'unclassified', title: '未分类资料' }],
  }
  render(<ResearchQuantObservations instrumentId="513130-sh" notebook={notebook} />)
  expect(screen.queryByText(/尚未形成量化分析/)).toBeNull()
  expect(screen.getByText('8月至10月月收益为负，7月为正。')).toBeTruthy()
  expect(screen.getByText('持仓资料缺失，不等于没有持仓。')).toBeTruthy()
  expect(screen.getByText('已确认尚缺估值数据。')).toBeTruthy()
  expect(screen.queryByText('没有数值来源的另项分析。')).toBeNull()
  const analysis = screen.getByRole('heading', { name: '收益路径与波动结构' }).closest('article')!
  fireEvent.click(within(analysis).getByRole('button', { name: '量化解释与依据' }))
  expect(screen.getByText('10月尚未结束，不能与完整月份直接等同。')).toBeTruthy()
})
