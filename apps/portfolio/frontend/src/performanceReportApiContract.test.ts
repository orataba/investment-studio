import { afterEach, describe, expect, it, vi } from 'vitest'

import { clearPortfolioApiCache, getPortfolioPerformanceReport } from './lib/api'

function reportPayload() {
  const metric = (method50: string, published = method50) => ({
    method50,
    published,
    rounding_adjustment_exact: '0',
  })
  return {
    portfolio_id: 'portfolio/ops',
    publication: {
      publication_id: 'publication-1',
      run_id: 'run-1',
      manifest_id: 'manifest-1',
      published_fencing_token: 7,
      published_at: '2026-07-14T09:00:00Z',
      calculated_at: '2026-07-14T08:59:00Z',
      requested_as_of: '2026-07-13',
      effective_as_of: '2026-07-13',
      output_range_start: '2026-01-01',
      output_range_end: '2026-07-13',
      methodology_version: 'portfolio-daily.v1',
      output_schema_version: 'portfolio-daily-output.v1',
      canonical_output_hash: 'hash',
      captured_generation: 3,
      current_generation: 3,
      stale: false,
      pending: false,
      pending_generation: null,
      pending_run_id: null,
      pending_run_status: null,
      pending_intent_id: null,
      pending_intent_status: null,
      coverage_state: 'complete',
      reason_codes: [],
    },
    base_currency: 'CNY',
    valuation_timezone: 'Asia/Shanghai',
    axis: 'instrument',
    selected_group_key: null,
    frequency: 'monthly',
    performance: {
      status: 'ready',
      start_date: '2026-01-01',
      end_date: '2026-07-13',
      effective_return_start_date: '2025-12-31',
      effective_return_end_date: '2026-07-13',
      elapsed_days: 194,
      snapshot_count: 1,
      measured_nav_count: 1,
      measured_return_count: 1,
      cumulative_twr: metric('0.1'),
      annualized_twr: null,
      current_drawdown: metric('0'),
      max_drawdown: metric('-0.02'),
      return_chain_status: 'linked',
      coverage_state: 'complete',
      reason_codes: ['annualized_twr_requires_at_least_365_elapsed_days'],
    },
    statistics: {
      status: 'unavailable',
      method_version: 'calendar-period.v1.monthly.ppy-12.vol-sample-n-minus-1.downside-target-zero-n.decimal50',
      reason_codes: ['risk_statistics_minimum_two_observations'],
      frequency: 'monthly',
      observation_count: 1,
      excluded_partial_bucket_count: 0,
      excluded_unavailable_bucket_count: 0,
      periods_per_year: null,
      mean_period_return: null,
      annualized_arithmetic_mean: null,
      annualized_volatility: null,
      annualized_downside_deviation: null,
    },
    xirr: {
      status: 'ready',
      method_version: 'xirr.v1.actual-365.decimal50.unique-sign-change',
      reason_codes: ['xirr_annualized_headline_requires_at_least_365_elapsed_days'],
      cash_flow_count: 2,
      annualized_headline_eligible: false,
      rate: {
        method50: '0.123456789012345678901',
        published: '0.123456789012345679',
        rounding_adjustment_exact: '0.000000000000000000099',
      },
      xnpv_residual_exact: '0',
    },
    portfolio_bridge: {
      axis: 'portfolio',
      group_key: '__portfolio__',
      group_label: 'Portfolio',
      opening_nav_exact: '100',
      closing_nav_exact: '110',
      external_flow_in_exact: '0',
      external_flow_out_exact: '0',
      internal_flow_in_exact: '0',
      internal_flow_out_exact: '0',
      economic_pnl_exact: '10',
      linked_contribution_method50: '0.1',
      linking_adjustment_exact: '0',
      linked_contribution_effective: '0.1',
      closure_residual_exact: '0',
    },
    rebased_wealth_series: [],
    daily_series: [],
    attribution: {
      status: 'ready',
      method_version: 'frongello-forward.v2.decimal50-deterministic-group-balance',
      axis: 'instrument',
      effective_start_date: '2025-12-31',
      effective_end_date: '2026-07-13',
      observation_count: 1,
      cumulative_twr_method50: '0.1',
      total_linked_contribution_effective: '0.1',
      total_linking_adjustment_exact: '0',
      closure_residual_exact: '0',
      reason_codes: [],
    },
    attribution_groups: [],
    return_calendar: [],
    attribution_calendar: [],
  }
}

afterEach(() => {
  clearPortfolioApiCache()
  vi.unstubAllGlobals()
})

describe('published performance report contract', () => {
  it('loads one coherent report and preserves canonical exact decimals as strings', async () => {
    const payload = reportPayload()
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(payload), { status: 200 })),
    )
    vi.stubGlobal('fetch', fetchMock)

    const response = await getPortfolioPerformanceReport('portfolio/ops', {
      start_date: '2026-01-01',
      end_date: '2026-07-13',
      axis: 'instrument',
      frequency: 'monthly',
    })

    expect(response.performance.cumulative_twr?.method50).toBe('0.1')
    expect(response.xirr.rate?.method50).toBe('0.123456789012345678901')
    expect(response.portfolio_bridge?.economic_pnl_exact).toBe('10')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/portfolios/portfolio%2Fops/performance/report?start_date=2026-01-01&end_date=2026-07-13&axis=instrument&frequency=monthly',
      expect.any(Object),
    )
  })

  it('rejects a JSON number at the exact-decimal wire boundary', async () => {
    const payload = reportPayload()
    payload.performance.cumulative_twr.method50 = 0.1 as unknown as string
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }))),
    )

    await expect(getPortfolioPerformanceReport('portfolio-ops')).rejects.toThrow(
      'performance_report.performance.cumulative_twr.method50 is not a canonical exact decimal string',
    )
  })
})
