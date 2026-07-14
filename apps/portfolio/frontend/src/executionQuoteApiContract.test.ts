import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearPortfolioApiCache,
  getPortfolioTransactionExecutionQuote,
} from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

describe('execution quote request contract', () => {
  it('requests one unadjusted quote on or before the trade date', async () => {
    const payload = {
      portfolio_id: 'portfolio/ops',
      instrument_id: '159516-sz',
      requested_as_of_date: '2026-03-27',
      selection_role: 'trading',
      value: 1.66,
      quote_date: '2026-03-27',
      quote_basis: 'close',
      metric_family: 'price',
      currency: 'CNY',
      provider: 'tushare:fund_daily',
      status: 'complete',
      stale: false,
    }
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(payload), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    const response = await getPortfolioTransactionExecutionQuote(
      'portfolio/ops',
      '159516-sz',
      '2026-03-27',
    )

    expect(response.value).toBe(1.66)
    expect(response.quote_basis).toBe('close')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio%2Fops/transactions/execution-quote?instrument_id=159516-sz&as_of_date=2026-03-27',
      expect.any(Object),
    )
  })
})
