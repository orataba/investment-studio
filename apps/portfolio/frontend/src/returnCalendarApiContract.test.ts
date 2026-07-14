import { afterEach, describe, expect, it, vi } from 'vitest'

import { clearPortfolioApiCache, getPortfolioPerformanceReport } from './lib/api'

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

function performanceReportPayload(cumulativeTwrMethod50: unknown = '0.0123456789') {
  const metric = (method50: unknown) => ({
    method50,
    published: method50,
    rounding_adjustment_exact: '0',
  })
  return {
    portfolio_id: 'portfolio/ops',
    base_currency: 'USD',
    valuation_timezone: 'Asia/Shanghai',
    axis: 'instrument',
    selected_group_key: null,
    frequency: 'monthly',
    publication: {},
    performance: {
      status: 'ready',
      annualized_twr: null,
    },
    statistics: {},
    xirr: { rate: null },
    portfolio_bridge: null,
    rebased_wealth_series: [],
    daily_series: [],
    attribution: {},
    attribution_groups: [],
    return_calendar: [
      {
        bucket_key: '2026-01',
        frequency: 'monthly',
        calendar_start_date: '2026-01-01',
        calendar_end_date: '2026-01-31',
        coverage_state: 'complete',
        coverage_reason_codes: [],
        status: 'ready',
        effective_return_start_date: '2026-01-01',
        effective_return_end_date: '2026-01-31',
        observation_count: 21,
        cumulative_twr: metric(cumulativeTwrMethod50),
        current_drawdown: metric('-0.001'),
        max_drawdown: metric('-0.002'),
        reason_codes: [],
      },
    ],
    attribution_calendar: [],
  }
}

describe('published performance report request contract', () => {
  it('gets the monthly calendar from the same single published report request', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(performanceReportPayload()), { status: 200 }),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const response = await getPortfolioPerformanceReport('portfolio/ops', {
      start_date: '2026-01-01',
      end_date: '2026-07-13',
      axis: 'instrument',
      frequency: 'monthly',
    })

    expect(response.return_calendar[0].cumulative_twr?.method50).toBe('0.0123456789')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio%2Fops/performance/report?start_date=2026-01-01&end_date=2026-07-13&axis=instrument&frequency=monthly',
      expect.any(Object),
    )
  })

  it('rejects a JSON number where an exact decimal string is required', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify(performanceReportPayload(0.0123456789)), {
            status: 200,
          }),
        ),
      ),
    )

    await expect(
      getPortfolioPerformanceReport('portfolio/ops', {
        axis: 'instrument',
        frequency: 'monthly',
      }),
    ).rejects.toThrow(
      'performance_report.return_calendar[0].cumulative_twr.method50 is not a canonical exact decimal string',
    )
  })
})
