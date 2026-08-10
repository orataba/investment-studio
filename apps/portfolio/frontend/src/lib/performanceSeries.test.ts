import { describe, expect, it } from 'vitest'

import type { PortfolioDailyPerformancePoint } from './api'
import {
  buildPortfolioValueChartPoints,
  buildTwrIndexPoints,
  rebasePerformanceSeriesTo100,
} from './performanceSeries'

function dailyPoint({
  date,
  endingNav,
  dailyTwr,
  cumulativeTwr,
  eligible,
}: {
  date: string
  endingNav: number
  dailyTwr: number
  cumulativeTwr: number
  eligible: boolean
}): PortfolioDailyPerformancePoint {
  return {
    as_of_date: date,
    coverage_state: 'complete',
    valuation_coverage_state: 'complete',
    return_coverage_state: 'complete',
    book_pnl_coverage_state: 'complete',
    attribution_coverage_state: 'complete',
    return_chain_continuous: true,
    stale_price_flag: !eligible,
    stale_fx_flag: false,
    market_observation_count: eligible ? 1 : 0,
    return_observation_eligible: eligible,
    return_observation_exclusion_reason: eligible ? null : 'missing_market_observation',
    modeled_market_exposure_present: true,
    market_risk_observation_count: eligible ? 1 : 0,
    market_risk_return_coverage_state: eligible ? 'complete' : 'unavailable',
    market_risk_return_chain_continuous: eligible,
    market_risk_return_observation_eligible: eligible,
    market_risk_return_observation_exclusion_reason: eligible ? null : 'missing_market_observation',
    market_risk_basis: 'zero_return_cash_and_derivatives',
    market_risk_label: 'Market Risk Return',
    performance_basis: 'market_value',
    performance_label: 'Total Portfolio Return',
    beginning_nav: endingNav,
    ending_nav: endingNav,
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
    cumulative_twr: cumulativeTwr,
    drawdown: 0,
    market_risk_daily_return: eligible ? dailyTwr : null,
    market_risk_cumulative_return: eligible ? cumulativeTwr : null,
    market_risk_drawdown: eligible ? 0 : null,
  }
}

describe('performance series display normalization', () => {
  it('rebases the first visible observation to exactly 100', () => {
    const result = rebasePerformanceSeriesTo100([
      { date: '2026-03-31', value: 100.55687825085025 },
      { date: '2026-04-01', value: 102.56801581586725 },
    ])

    expect(result[0]?.value).toBe(100)
    expect(result[1]?.value).toBeCloseTo((102.56801581586725 / 100.55687825085025) * 100)
  })

  it('does not fabricate a display series from an invalid base', () => {
    expect(rebasePerformanceSeriesTo100([{ date: '2026-03-31', value: 0 }])).toEqual([])
  })
})

describe('performance chart observation filtering', () => {
  const points = [
    dailyPoint({
      date: '2026-07-17',
      endingNav: 100,
      dailyTwr: 0.01,
      cumulativeTwr: 0.01,
      eligible: true,
    }),
    dailyPoint({
      date: '2026-07-18',
      endingNav: 100,
      dailyTwr: 0,
      cumulativeTwr: 0.01,
      eligible: false,
    }),
    dailyPoint({
      date: '2026-07-19',
      endingNav: 100,
      dailyTwr: 0,
      cumulativeTwr: 0.01,
      eligible: false,
    }),
    dailyPoint({
      date: '2026-07-20',
      endingNav: 102,
      dailyTwr: 0.0198019802,
      cumulativeTwr: 0.03,
      eligible: true,
    }),
  ]

  it('omits carried-forward weekend valuations from the value chart', () => {
    expect(buildPortfolioValueChartPoints(points)).toEqual([
      { date: '2026-07-17', value: 100 },
      { date: '2026-07-20', value: 102 },
    ])
  })

  it('omits carried-forward weekend returns from the TWR chart', () => {
    expect(buildTwrIndexPoints(points)).toEqual([
      { date: '2026-07-17', value: 101 },
      { date: '2026-07-20', value: 103 },
    ])
  })

  it('uses observation eligibility rather than treating every zero return as stale', () => {
    const unchangedTradingDay = dailyPoint({
      date: '2026-07-21',
      endingNav: 102,
      dailyTwr: 0,
      cumulativeTwr: 0.03,
      eligible: true,
    })

    expect(buildPortfolioValueChartPoints([...points, unchangedTradingDay])).toContainEqual({
      date: '2026-07-21',
      value: 102,
    })
    expect(buildTwrIndexPoints([...points, unchangedTradingDay])).toContainEqual({
      date: '2026-07-21',
      value: 103,
    })
  })

  it('also omits a weekday holiday or stale valuation marked ineligible', () => {
    const holiday = dailyPoint({
      date: '2026-02-17',
      endingNav: 99,
      dailyTwr: 0,
      cumulativeTwr: -0.01,
      eligible: false,
    })

    expect(buildPortfolioValueChartPoints([holiday])).toEqual([])
    expect(buildTwrIndexPoints([holiday])).toEqual([])
  })
})
