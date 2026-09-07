import { describe, expect, it } from 'vitest'

import {
  buildBenchmarkPeriodMetrics,
  buildPerformanceMetricRows,
  buildRelativePerformanceMetrics,
  eligiblePortfolioReturnDates,
} from './pages/PerformancePage'
import type {
  PortfolioDailyPerformancePoint,
  PortfolioInstrumentPriceChartPoint,
} from './lib/api'
import { performanceFixture } from './test/portfolioFixtures'

function dailyPoint(
  asOfDate: string,
  dailyTwr: number | null,
): PortfolioDailyPerformancePoint {
  return {
    as_of_date: asOfDate,
    coverage_state: 'complete',
    valuation_coverage_state: 'complete',
    return_coverage_state: 'complete',
    book_pnl_coverage_state: 'complete',
    attribution_coverage_state: 'complete',
    return_chain_continuous: true,
    stale_price_flag: false,
    stale_fx_flag: false,
    market_observation_count: dailyTwr == null ? 0 : 1,
    return_observation_eligible: dailyTwr != null,
    return_observation_exclusion_reason: dailyTwr == null ? 'missing_return' : null,
    modeled_market_exposure_present: true,
    market_risk_observation_count: dailyTwr == null ? 0 : 1,
    market_risk_return_coverage_state: dailyTwr == null ? 'unavailable' : 'complete',
    market_risk_return_chain_continuous: dailyTwr != null,
    market_risk_return_observation_eligible: dailyTwr != null,
    market_risk_return_observation_exclusion_reason: dailyTwr == null ? 'missing_return' : null,
    market_risk_basis: 'zero_return_cash_and_derivatives',
    market_risk_label: 'Market Risk Return',
    performance_basis: 'market_value',
    performance_label: 'Total Portfolio Return',
    beginning_nav: 100,
    ending_nav: dailyTwr == null ? 100 : 100 * (1 + dailyTwr),
    pending_settlement: 0,
    realized_pnl: 0,
    derivative_lifecycle_realized_pnl: 0,
    unrealized_pnl: 0,
    income_cash_amount: 0,
    expense_cash_amount: 0,
    cash_currency_gains: 0,
    pending_settlement_currency_gains: 0,
    instrument_currency_gains: 0,
    return_of_capital_amount: 0,
    total_pnl: 0,
    risk_scope_excluded_pnl: 0,
    market_risk_pnl: 0,
    external_cash_in: 0,
    external_cash_out: 0,
    net_external_inflow: 0,
    absolute_change: 0,
    delta: 0,
    daily_twr: dailyTwr,
    cumulative_twr: dailyTwr,
    drawdown: 0,
    market_risk_daily_return: dailyTwr,
    market_risk_cumulative_return: dailyTwr,
    market_risk_drawdown: dailyTwr == null ? null : 0,
  }
}

function benchmarkPoint(
  date: string,
  value: number,
): PortfolioInstrumentPriceChartPoint {
  return {
    date,
    value,
  }
}

describe('Performance benchmark boundary alignment', () => {
  it('uses the selected start close for an existing portfolio', () => {
    const dailySeries = [
      dailyPoint('2026-01-01', null),
      dailyPoint('2026-01-02', 0.1),
    ]
    const benchmarkPoints = [
      benchmarkPoint('2026-01-01', 100),
      benchmarkPoint('2026-01-02', 110),
    ]

    expect(
      eligiblePortfolioReturnDates(
        dailySeries,
        '2026-01-01',
        '2026-01-02',
      ),
    ).toEqual(['2026-01-02'])
    expect(
      buildBenchmarkPeriodMetrics(
        benchmarkPoints,
        '2026-01-01',
        '2026-01-02',
        dailySeries,
      )?.periodReturn,
    ).toBeCloseTo(0.1)
  })

  it('uses the prior close when a funded segment includes the start-day return', () => {
    const dailySeries = [
      dailyPoint('2026-01-01', 0.1),
      dailyPoint('2026-01-02', 0),
    ]
    const benchmarkPoints = [
      benchmarkPoint('2025-12-31', 100),
      benchmarkPoint('2026-01-01', 110),
      benchmarkPoint('2026-01-02', 110),
    ]

    expect(
      eligiblePortfolioReturnDates(
        dailySeries,
        '2026-01-01',
        '2026-01-02',
      ),
    ).toEqual(['2026-01-01', '2026-01-02'])
    expect(
      buildBenchmarkPeriodMetrics(
        benchmarkPoints,
        '2026-01-01',
        '2026-01-02',
        dailySeries,
        true,
      )?.periodReturn,
    ).toBeCloseTo(0.1)
  })

  it('keeps the full benchmark period after a stock is sold and the portfolio holds cash', () => {
    const metrics = buildBenchmarkPeriodMetrics(
      [benchmarkPoint('2026-01-01', 100), benchmarkPoint('2026-01-02', 110), benchmarkPoint('2026-01-03', 121)],
      '2026-01-01', '2026-01-03',
      [dailyPoint('2026-01-01', null), dailyPoint('2026-01-02', 0), dailyPoint('2026-01-03', null)],
    )
    expect(metrics?.periodReturn).toBeCloseTo(0.21)
    expect(metrics?.dailyReturns).toHaveLength(1)
  })

  it('compares an all-cash portfolio over the complete selected interval', () => {
    const metrics = buildBenchmarkPeriodMetrics(
      [benchmarkPoint('2026-01-01', 100), benchmarkPoint('2026-01-03', 121)],
      '2026-01-01', '2026-01-03',
      [dailyPoint('2026-01-01', null), dailyPoint('2026-01-03', null)],
    )
    expect(metrics?.periodReturn).toBeCloseTo(0.21)
    expect(metrics?.annualizedVolatility).toBeNull()
  })

  it('aligns a foreign-market portfolio observation with zero benchmark return during an official closure', () => {
    const metrics = buildBenchmarkPeriodMetrics([
      benchmarkPoint('2026-09-03', 100), benchmarkPoint('2026-09-04', 110),
      benchmarkPoint('2026-09-08', 121),
    ], '2026-09-03', '2026-09-08', [
      dailyPoint('2026-09-04', .1), dailyPoint('2026-09-07', .02), dailyPoint('2026-09-08', .1),
    ], false, ['2026-09-03', '2026-09-04', '2026-09-08'])
    expect(metrics?.periodReturn).toBeCloseTo(.21)
    expect(metrics?.dailyReturns.map((point) => point.value)).toEqual([
      expect.closeTo(.1), 0, expect.closeTo(.1),
    ])
  })

  it('uses the preceding close for each daily return without bridging excluded dates', () => {
    const dailySeries = [dailyPoint('2026-01-02', 0.01), dailyPoint('2026-01-03', null),
      dailyPoint('2026-01-04', null), dailyPoint('2026-01-05', 0.01)]
    const metrics = buildBenchmarkPeriodMetrics([
      benchmarkPoint('2026-01-01', 100), benchmarkPoint('2026-01-02', 101),
      benchmarkPoint('2026-01-03', 121.2), benchmarkPoint('2026-01-04', 121.2),
      benchmarkPoint('2026-01-05', 122.412),
    ], '2026-01-01', '2026-01-05', dailySeries)
    expect(metrics?.dailyReturns.map((point) => point.value)).toEqual([
      expect.closeTo(0.01), expect.closeTo(0.01),
    ])
    const rows = buildPerformanceMetricRows({ ...performanceFixture().summary,
      risk_result_status: 'unavailable', risk_unavailable_reason: 'return_coverage_incomplete',
      annualized_volatility: .25, sharpe_ratio: 1.3, sortino_ratio: 2.7,
    }, dailySeries, 'USD', { instrument_id: 'bm' } as never, false, metrics, true)
    for (const name of ['Market Risk Volatility', 'Market Risk Sharpe Ratio', 'Market Risk Sortino Ratio',
      'Tracking Error', 'Information Ratio', 'Beta']) {
      expect(rows.find((row) => row.metric === name)?.value).toBe('—')
    }
  })

  it('uses the same explicit Thursday-to-Monday boundary for tracking error and information ratio', () => {
    const dailySeries = [dailyPoint('2026-01-02', .02), dailyPoint('2026-01-05', 0)]
    const benchmark = buildBenchmarkPeriodMetrics([
      benchmarkPoint('2026-01-01',100), benchmarkPoint('2026-01-02',100), benchmarkPoint('2026-01-05',100),
    ],'2026-01-01','2026-01-05',dailySeries)
    const metrics = buildRelativePerformanceMetrics(dailySeries,benchmark,'2026-01-01','2026-01-05')
    expect(metrics?.trackingError).toBeCloseTo(0.19111514853616393)
    expect(metrics?.informationRatio).toBeCloseTo(9.555757426808196)
  })

  it('uses geometric wealth growth for Calmar and withholds sub-year estimates', () => {
    const summary = { ...performanceFixture().summary,
      start_date:'2025-01-01',end_date:'2026-01-01',
      risk_result_status: 'available' as const, market_risk_cumulative_return:0,
      annualized_return_from_daily_mean:.03335616438356166,max_drawdown:-1/6,
    }
    const rows = buildPerformanceMetricRows(summary, [], 'USD', null, false, null, false)
    expect(rows.find((row) => row.metric === 'Market Risk Calmar Ratio')?.value).toBe('0.00')
    const shortRows = buildPerformanceMetricRows({ ...summary, start_date:'2025-12-30' }, [], 'USD', null, false, null, false)
    expect(shortRows.find((row) => row.metric === 'Market Risk Calmar Ratio')?.value).toBe('N/A')
  })

  it.each([
    ['2025-01-01', '2026-01-01'],
    ['2023-03-01', '2024-03-01'],
    ['2024-02-29', '2025-02-28'],
  ])('annualizes an exact anniversary from %s to %s as one year', (startDate, endDate) => {
    // Wealth 100 -> 99.9 -> 121: a 21% full-year return and 0.1% drawdown.
    const benchmark = buildBenchmarkPeriodMetrics([
      benchmarkPoint(startDate, 100), benchmarkPoint(endDate, 121),
    ], startDate, endDate)
    expect(benchmark?.annualizedReturn).toBeCloseTo(0.21, 12)
    const rows = buildPerformanceMetricRows({ ...performanceFixture().summary,
      start_date: startDate, end_date: endDate,
      risk_result_status: 'available', market_risk_cumulative_return: .21, max_drawdown: -.001,
    }, [], 'USD', null, false, null, false)
    expect(rows.find((row) => row.metric === 'Market Risk Calmar Ratio')?.value).toBe('210.00')
  })

  it('uses the actual anniversary year for a leap-day start and remaining stub', () => {
    // Feb 29 -> next Feb 28 is one year; Feb 28 -> Aug 28 is 181 of 365 days.
    const expectedAnnualReturn = 1.21 ** (1 / (1 + 181 / 365)) - 1
    const benchmark = buildBenchmarkPeriodMetrics([
      benchmarkPoint('2024-02-29', 100), benchmarkPoint('2025-08-28', 121),
    ], '2024-02-29', '2025-08-28')
    expect(benchmark?.annualizedReturn).toBeCloseTo(expectedAnnualReturn, 12)
    const rows = buildPerformanceMetricRows({ ...performanceFixture().summary,
      start_date: '2024-02-29', end_date: '2025-08-28',
      risk_result_status: 'available', market_risk_cumulative_return: .21, max_drawdown: -.001,
    }, [], 'USD', null, false, null, false)
    expect(rows.find((row) => row.metric === 'Market Risk Calmar Ratio')?.value).toBe((expectedAnnualReturn / .001).toFixed(2))
  })

  it('distinguishes the operational valuation restriction from insufficient history', () => {
    const rows = buildPerformanceMetricRows({ ...performanceFixture().summary,
      start_date:'2024-01-01',end_date:'2026-01-01',
      performance_basis:'operational_carrying_basis',annualization_eligible:false,
      annualization_unavailable_reason:'operational_carrying_basis_not_annualized',
    }, [], 'USD', null, false, null, false)
    expect(rows.find((row) => row.metric === 'Annualized TWR')?.reliabilityNote).toBe('Requires complete fair-value valuations')
    expect(rows.find((row) => row.metric === 'Market Risk Max DD')?.section).toBe('Risk')
    expect(rows.some((row) => row.section === 'Relative')).toBe(false)
  })

  it('does not display a negative sign when a return rounds to zero', () => {
    const rows = buildPerformanceMetricRows({ ...performanceFixture().summary, cumulative_twr: -0.00000001 },
      [], 'USD', null, false, null, false)
    expect(rows.find((row) => row.metric === 'Total Portfolio Return')?.value).toBe('0.00%')
  })
})
