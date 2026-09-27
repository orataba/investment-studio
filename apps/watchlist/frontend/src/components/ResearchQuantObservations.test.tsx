// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ResearchQuantObservations from './ResearchQuantObservations'
import type { SavedResearchNotebook } from '../lib/researchDossierApi'
vi.mock('./ResearchModules', () => ({ SavedFigure: ({ source }: { source: { title: string } }) => <figure>{source.title}</figure> }))
vi.mock('./ResearchEvidence', () => ({ dateLabel: (value: string) => value, SourceList: ({ sources }: { sources: Array<{ source_id: string; title: string }> }) => <ul>{sources.map(source => <li key={source.source_id}>{source.title}</li>)}</ul> }))
afterEach(cleanup)

it('only displays explicitly classified market observations and keeps other evidence accessible', () => {
  const notebook: SavedResearchNotebook = {
    run_id: 'research', checked_at: '2026-09-27', source_ids: [], key_drivers: [], questions: [], important_changes: [], next_research: [],
    modules: [{ key: 'market-quantitative', summary: '波动率抬升。', analysis: '价格和行业内部结构需要结合观察。', coverage: 'partial', gaps: ['基准数据尚未配置。'], next_check: '', source_ids: ['vol', 'cashflow', 'unclassified'], figure_source_ids: ['vol', 'cashflow', 'unclassified'], evidence_as_of: '2026-09-26' }],
    sources: [{ source_id: 'vol', title: '行业波动率', observation_domain: 'market' }, { source_id: 'cashflow', title: '公司现金流', observation_domain: 'fundamental' }, { source_id: 'unclassified', title: '尚未分类的计算' }],
  }
  render(<ResearchQuantObservations instrumentId="xlk" notebook={notebook} />)
  expect(screen.getByText('行业波动率')).toBeTruthy()
  expect(screen.queryByText('公司现金流')).toBeNull()
  expect(screen.queryByText('尚未分类的计算')).toBeNull()
  expect(screen.getByText('量化数据部分可用。')).toBeTruthy()
  expect(screen.queryByText('基准数据尚未配置。')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '查看数据缺口' }))
  expect(screen.getByText('基准数据尚未配置。')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '关闭查看数据缺口' }))
  fireEvent.click(screen.getByRole('button', { name: '量化解释与依据' }))
  expect(screen.getByText('公司现金流')).toBeTruthy()
  expect(screen.getByText('尚未分类的计算')).toBeTruthy()
})
