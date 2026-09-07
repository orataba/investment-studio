// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import EstimateHistoryPanel from './EstimateHistoryPanel'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'

const mocks = vi.hoisted(() => ({ reference: vi.fn(), evidence: vi.fn() }))
vi.mock('../lib/api', () => ({ getInstrumentReferenceData: mocks.reference }))
vi.mock('../lib/workbenchApi', () => ({ readWorkbench: mocks.evidence }))

const emptyEvidence = {
  supported: false, status: 'unsupported', current_snapshot: null, previous_snapshot: null,
  coverage: {}, changes: [], observations: [], unmatched: [], gaps: [],
}
beforeEach(() => {
  mocks.reference.mockResolvedValue({ sections: { fund_info: { assetClass: 'Equity' } } })
  mocks.evidence.mockResolvedValue(emptyEvidence)
})
afterEach(() => { cleanup(); vi.resetAllMocks() })

it.each([
  { fund_type: '其他', invest_type: '黄金现货合约' },
  { assetClass: 'Commodities' },
])('hides company estimates when the disclosed investment category is not applicable: %j', async (fundInfo) => {
  mocks.reference.mockResolvedValue({ sections: { fund_info: fundInfo } })
  const { container } = render(<EstimateHistoryPanel instrumentId="registered-commodity" language="zh-Hans" />)
  await waitFor(() => expect(mocks.reference).toHaveBeenCalledWith('registered-commodity'))
  expect(mocks.evidence).not.toHaveBeenCalled()
  expect(container.textContent).toBe('')
})

it.each([{ assetClass: 'Equity' }, { fund_type: '其他' }, {}])('keeps unsupported or unclassified ETFs distinct from not applicable: %j', async (fundInfo) => {
  mocks.reference.mockResolvedValue({ sections: { fund_info: fundInfo } })
  render(<EstimateHistoryPanel instrumentId="registered-etf" language="zh-Hans" />)
  expect(await screen.findByText('成份公司预期快照 · 尚未覆盖')).toBeTruthy()
  expect(mocks.evidence).toHaveBeenCalledWith('/sector-research/estimates?instrument_id=registered-etf')
})

it('preserves the supported company estimate baseline', async () => {
  mocks.evidence.mockResolvedValue({ ...emptyEvidence, supported: true, status: 'baseline', coverage: { current_company_count: 73 },
    current_snapshot: { observation_id: 'collection-1', collected_at: '2026-09-07T08:00:00Z',
      collected_at_min: '2026-09-07T07:59:00Z', collected_at_max: '2026-09-07T07:59:50Z' },
    history_available_from: '2026-09-07T08:00:00Z' })
  render(<EstimateHistoryPanel instrumentId="registered-equity-etf" language="en" />)
  expect(await screen.findByText('Constituent estimate history · First baseline')).toBeTruthy()
  expect(screen.getByText('73 constituent company records retained.')).toBeTruthy()
  expect(screen.getByText(/Current collection/).textContent).toContain('Collection history begins')
  expect(screen.getByText(/Each data collection is retained/)).toBeTruthy()
})

it('translates collected estimate limitations while preserving original research text', async () => {
  document.cookie = 'investment_studio_language=en; path=/'
  const gaps = [
    'FMP预期数据未提供币种，不能用股票报价币种代替。',
    '至少一侧预期币种未确认，数值差异仅作待核实线索。',
    '两次预期币种不同，不能直接计算上修或下修。',
    '前次无有效分析师覆盖，新增覆盖不等于预测上修。',
    '缺少明确的源记录采集时间，无法确定观测先后。',
    '部分源记录采集时间尚未推进，重复读取不是新的预期观测。',
    '两次预期数据来源口径不同，变动尚未核实。',
    '部分公司缺少当前ETF权重，权重覆盖不完整。',
    '当前快照没有可用的公司预期。',
  ]
  mocks.evidence.mockResolvedValue({ ...emptyEvidence, supported: true, status: 'limited', gaps })
  render(<LanguageProvider><EstimateHistoryPanel instrumentId="xlk" language="en" /><p translate="no">{gaps[1]}</p></LanguageProvider>)
  await waitFor(() => {
    const rendered = screen.getAllByRole('listitem')
    expect(rendered).toHaveLength(gaps.length)
    expect(rendered.every((item) => !/[\u3400-\u9fff]/.test(item.textContent || ''))).toBe(true)
  })
  expect(screen.getByText('Currency is unverified in at least one observation; numerical differences are only leads to verify.')).toBeTruthy()
  expect(screen.getByText(gaps[1], { selector: 'p[translate="no"]' })).toBeTruthy()
})
