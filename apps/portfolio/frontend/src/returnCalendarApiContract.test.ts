import { afterEach, describe, expect, it, vi } from 'vitest'

import { clearPortfolioApiCache, getPortfolioReturnCalendar } from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

describe('return calendar request contract', () => {
  it('requests authoritative monthly buckets for the exact reported window', async () => {
    const payload = {
      portfolio_id: 'portfolio/ops',
      base_currency: 'USD',
      valuation_timezone: 'Asia/Shanghai',
      valuation_cutoff_policy: 'market_close',
      summary: {
        frequency: 'monthly',
        twr_state: 'linked',
        twr_reliability_status: 'reliable',
        twr_reliability_reasons: [],
        bucket_count: 0,
        complete_bucket_count: 0,
        partial_bucket_count: 0,
        unavailable_bucket_count: 0,
        start_date: null,
        end_date: null,
      },
      buckets: [],
    }
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(payload), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    const response = await getPortfolioReturnCalendar('portfolio/ops', {
      start_date: '2026-01-01',
      end_date: '2026-07-13',
      frequency: 'monthly',
    })

    expect(response.summary.frequency).toBe('monthly')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio%2Fops/performance/calendar?start_date=2026-01-01&end_date=2026-07-13&frequency=monthly',
      expect.any(Object),
    )
  })
})
