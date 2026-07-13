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
      source_ref: 'tushare:fund_daily',
      source_status: 'complete',
      status: 'complete',
      resolution_status: 'resolved',
      freshness_status: 'current',
      ingestion_status: 'current',
      reliability_status: 'reliable',
      reason_codes: [],
      stale: false,
      carry_forward: false,
      age_days: 0,
      quote_selection_policy_version: 'quote_selection_policy.v1',
      quote_selection_policy_revision: 'policy-hash',
      quote_series_id: 'series-id',
      observation_id: 'observation-id',
      revision_id: 'revision-id',
      revision_number: 1,
      payload_hash: 'payload-hash',
      source_published_at: null,
      ingested_at: '2026-03-27T10:00:00Z',
      calculation_dependency: { fingerprint: 'dependency-hash' },
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
