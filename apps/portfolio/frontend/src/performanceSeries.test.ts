import { describe, expect, it } from 'vitest'

import type { PortfolioDailyPerformancePoint } from './lib/api'
import { buildTwrIndexPoints } from './lib/performanceSeries'

function point(
  asOfDate: string,
  cumulativeTwr: number | null,
  dailyTwr: number | null,
): PortfolioDailyPerformancePoint {
  return {
    as_of_date: asOfDate,
    nav_coverage_state: 'complete',
    nav_coverage_reason_codes: [],
    book_pnl_coverage_state: 'complete',
    book_pnl_coverage_reason_codes: [],
    stale_price_flag: false,
    stale_fx_flag: false,
    market_observation_count: 1,
    return_observation_eligible: dailyTwr != null,
    twr_state: cumulativeTwr == null ? 'broken' : 'linked',
    twr_reliability_status: cumulativeTwr == null ? 'unavailable' : 'reliable',
    twr_reliability_reasons: cumulativeTwr == null ? ['crosses_broken_twr_boundary'] : [],
    beginning_nav: 100,
    ending_nav: 100,
    pending_settlement: 0,
    realized_pnl: 0,
    unrealized_pnl: 0,
    income_cash_amount: 0,
    expense_cash_amount: 0,
    cash_currency_gains: 0,
    instrument_currency_gains: 0,
    return_of_capital_amount: 0,
    total_pnl: 0,
    external_cash_in: 0,
    external_cash_out: 0,
    net_external_inflow: 0,
    absolute_change: 0,
    delta: 0,
    daily_twr: dailyTwr,
    cumulative_twr: cumulativeTwr,
    drawdown: null,
  }
}

describe('authoritative TWR index presentation', () => {
  it('preserves a gap when cumulative TWR is unavailable even if daily TWR exists', () => {
    expect(
      buildTwrIndexPoints([
        point('2026-07-03', 0.15, 0.02),
        point('2026-07-01', 0.1, 0.1),
        point('2026-07-02', null, 0.25),
      ]),
    ).toEqual([
      { date: '2026-07-01', value: 110.00000000000001 },
      { date: '2026-07-02', value: null },
      { date: '2026-07-03', value: 114.99999999999999 },
    ])
  })
})
