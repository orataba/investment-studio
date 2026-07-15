import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearPortfolioApiCache,
  deletePortfolioTransaction,
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
      price_unit: 'per_unit',
      price_scale: 1,
      unavailable_reason: null,
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
    expect(response.price_unit).toBe('per_unit')
    expect(response.price_scale).toBe(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio%2Fops/transactions/execution-quote?instrument_id=159516-sz&as_of_date=2026-03-27',
      expect.any(Object),
    )
  })

  it('carries the expected row version in a destructive delete request', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            portfolio_id: 'portfolio-3',
            deleted_count: 1,
            deleted_transaction_ids: ['txn-1'],
            transfer_group_id: null,
          }),
          { status: 200 },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await deletePortfolioTransaction('portfolio-3', 'txn-1', {
      'txn-1': 7,
      'txn-2': 4,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio-3/transactions/txn-1',
      expect.objectContaining({
        method: 'DELETE',
        body: JSON.stringify({
          expected_row_versions: {
            'txn-1': 7,
            'txn-2': 4,
          },
        }),
      }),
    )
  })
})
