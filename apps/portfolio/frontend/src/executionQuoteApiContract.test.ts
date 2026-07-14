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
      value: '1.6600000000001234',
      suggested_transaction_price: '1.66',
      suggested_transaction_price_scale: 12,
      suggested_transaction_price_rounding: 'ROUND_HALF_EVEN',
      suggested_transaction_price_was_rounded: true,
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
      ingestion_time_state: 'observed',
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

    expect(response.value).toBe('1.6600000000001234')
    expect(response.suggested_transaction_price).toBe('1.66')
    expect(response.quote_basis).toBe('close')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio%2Fops/transactions/execution-quote?instrument_id=159516-sz&as_of_date=2026-03-27',
      expect.any(Object),
    )
  })

  it('rejects a binary JSON number at the exact quote boundary', async () => {
    const payload = {
      portfolio_id: 'portfolio/ops',
      instrument_id: '159516-sz',
      requested_as_of_date: '2026-03-27',
      selection_role: 'trading',
      value: 1.66,
      suggested_transaction_price: '1.66',
      suggested_transaction_price_scale: 12,
      suggested_transaction_price_rounding: 'ROUND_HALF_EVEN',
      suggested_transaction_price_was_rounded: false,
      quote_date: '2026-03-27',
      quote_basis: 'close',
      metric_family: 'price',
      currency: 'CNY',
      source_ref: 'test',
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
      quote_selection_policy_version: null,
      quote_selection_policy_revision: null,
      quote_series_id: 'series',
      observation_id: 'observation',
      revision_id: 'revision',
      revision_number: 1,
      payload_hash: 'hash',
      source_published_at: null,
      ingested_at: null,
      ingestion_time_state: null,
      calculation_dependency: {},
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }))),
    )

    await expect(
      getPortfolioTransactionExecutionQuote('portfolio/ops', '159516-sz', '2026-03-27'),
    ).rejects.toThrow(/canonical exact decimal string/)
  })
})
