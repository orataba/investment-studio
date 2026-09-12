// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SourceList } from './ResearchEvidence'

const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ fetchJson: request, API_BASE_URL: '' }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const source = { source_id: 'financials:original', source_type: 'company_snapshot', title: 'AAA财报依据', instrument_id: 'stock', run_cutoff: '2026-09-02T12:00:00Z' }
const statement = { statement_content_sha256: 'original-report', statement_type: 'income', period_end: '2026-06-30', fiscal_year: 2026,
  fiscal_period: 'Q2', reported_currency: 'CNY', available_at: '2026-09-01T12:00:00Z', observed_at: '2026-09-01T12:00:00Z', filing_date: '2026-08-25' }

it('opens the saved financial page with its research version and does not infer units or fetch further pages', async () => {
  request.mockResolvedValue({ ...source, company: { symbol: 'AAA', page_kind: 'facts', statements: [statement],
    financials: [{ ...statement, line_item: 'eps', value: '2.500000' }, { ...statement, line_item: 'shares', value: '1500000', unit: 'shares' }],
    total_rows: 12, next_offset: 2 } })
  render(<SourceList sources={[source]} instrumentId="stock" versionId="notebook-original" />)
  fireEvent.click(screen.getByText('查看已保存的原文'))
  await screen.findByText('已保存的财报科目')
  expect(request).toHaveBeenCalledWith('/api/research/instruments/stock/dossier?source_id=financials%3Aoriginal&version_id=notebook-original', expect.anything())
  const eps = screen.getByText('eps').closest('tr')!
  expect(within(eps).getByText('2.500000')).toBeTruthy()
  expect(within(eps).getByText('单位未明确')).toBeTruthy()
  expect(within(eps).queryByText('CNY')).toBeNull()
  expect(screen.getByText(/后续页未包含在本条来源/)).toBeTruthy()
  expect(screen.getByText('income · 2026-06-30')).toBeTruthy()
  expect(request).toHaveBeenCalledTimes(1)
})

it('distinguishes a statement directory from read financial facts', async () => {
  request.mockResolvedValue({ ...source, company: { symbol: 'AAA', page_kind: 'statements', statements: [{ ...statement, matching_fact_count: 200 }], financials: [], total_rows: 1, next_offset: null } })
  render(<SourceList sources={[source]} instrumentId="stock" />)
  fireEvent.click(screen.getByText('查看已保存的原文'))
  await screen.findByText('本页是报表目录，不代表已读取各项财务数据。')
  expect(screen.queryByRole('table')).toBeNull()
  expect(screen.getByText(/本页 1 条/)).toBeTruthy()
})

it('reloads the same source ID from the selected PM revision rather than reusing the previous original', async () => {
  request.mockResolvedValueOnce({ text: '第一版引用的原文' }).mockResolvedValueOnce({ text: '第二版引用的原文' })
  const { rerender } = render(<SourceList sources={[source]} instrumentId="stock" versionId="pm:opinion:1" />)
  fireEvent.click(screen.getByText('查看已保存的原文'))
  await screen.findByText('第一版引用的原文')
  rerender(<SourceList sources={[source]} instrumentId="stock" versionId="pm:opinion:2" />)
  expect(screen.queryByText('第一版引用的原文')).toBeNull()
  fireEvent.click(screen.getByText('查看已保存的原文'))
  await screen.findByText('第二版引用的原文')
  expect(request).toHaveBeenLastCalledWith('/api/research/instruments/stock/dossier?source_id=financials%3Aoriginal&version_id=pm%3Aopinion%3A2', expect.anything())
})

it('shows retained fund performance and each comparison on its own common sample without inventing missing values', async () => {
  const metric = { source_id: 'computed:fund', source_type: 'computed_metric', title: '净值共同样本', instrument_id: 'fund', as_of: '2026-09-02T12:00:00Z' }
  request.mockResolvedValue({ ...metric, methodology: '实际观察日，不年化；各对照使用各自共同样本。', data: {
    available: true, sample_start: '2026-01-01', sample_end: '2026-08-31', observations: 35,
    sample_return_pct: 3.1, currency: 'CNY', frequency: 'weekly', return_kind: 'total_return', quote_basis: 'nav_with_dividend',
    comparisons: [{ instrument_id: 'peer', name: '对照基金', role: 'configured_peer', note: '不能合成全市场排名。', comparison: {
      sample_start: '2026-03-01', sample_end: '2026-08-28', observations: 26, currency: 'CNY',
      rows: [{ instrument_id: 'fund', return_pct: 2.5, max_drawdown_pct: -1.25, correlation_to_target: null, excess_return_pp: 0.5, return_kind: 'total_return', quote_basis: 'nav_with_dividend' }],
    } }], limitations: ['最新月并非完整月份。'],
  } })
  render(<SourceList sources={[metric]} instrumentId="fund" versionId="fund-view" />)
  fireEvent.click(screen.getByText('查看已保存的计算依据'))
  await screen.findByText(/实际样本 2026-01-01 至 2026-08-31/)
  expect(screen.getByText(/共同样本 2026-03-01 至 2026-08-28/)).toBeTruthy()
  expect(screen.getByText('实际观察日，不年化；各对照使用各自共同样本。')).toBeTruthy()
  expect(screen.getByText('指定对照')).toBeTruthy()
  const row = within(screen.getByRole('table')).getAllByRole('row')[1]
  expect(within(row).getByText('总收益 · 复权累计净值')).toBeTruthy()
  expect(within(row).getByText('2.5%')).toBeTruthy()
  expect(within(row).getByText('未取得')).toBeTruthy()
  expect(within(row).getByText('0.5 个百分点')).toBeTruthy()
  expect(screen.getByText('周度')).toBeTruthy()
  expect(screen.getByText('最新月并非完整月份。')).toBeTruthy()
  expect(screen.queryByText(/"sample_return_pct"/)).toBeNull()
  fireEvent.click(screen.getByText('完整计算记录与输入依据'))
  expect(await screen.findByText(/"sample_return_pct": 3.1/)).toBeTruthy()
  expect(request).toHaveBeenCalledTimes(1)
})

it('retains a readable complete calculation for evidence outside the existing summary shapes', async () => {
  const metric = { source_id: 'computed:other', source_type: 'computed_metric', title: '特殊计算', instrument_id: 'fund' }
  request.mockResolvedValue({ ...metric, methodology: { operation: 'observed-calculation' }, data: { exact_original_value: '1.234567890123', status: 'available' } })
  render(<SourceList sources={[metric]} instrumentId="fund" />)
  fireEvent.click(screen.getByText('查看已保存的计算依据'))
  await screen.findByText('完整计算记录与输入依据')
  fireEvent.click(screen.getByText('完整计算记录与输入依据'))
  expect(await screen.findByText(/1.234567890123/)).toBeTruthy()
  expect(screen.getByText(/observed-calculation/)).toBeTruthy()
})
