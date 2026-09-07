// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import EstimateHistoryPanel from './EstimateHistoryPanel'

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
  mocks.evidence.mockResolvedValue({ ...emptyEvidence, supported: true, status: 'baseline', coverage: { current_company_count: 73 } })
  render(<EstimateHistoryPanel instrumentId="registered-equity-etf" language="en" />)
  expect(await screen.findByText('Constituent estimate history · First baseline')).toBeTruthy()
  expect(screen.getByText('73 constituent company records retained.')).toBeTruthy()
})
