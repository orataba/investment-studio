import { describe, expect, it } from 'vitest'

import {
  buildBenchmarkPeriodMetrics,
  eligiblePortfolioReturnDates,
} from './pages/PerformancePage'
import type {
  PortfolioDailyPerformancePoint,
  PortfolioInstrumentPriceChartPoint,
} from './lib/api'

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
      )?.periodReturn,
    ).toBeCloseTo(0.1)
  })
})
