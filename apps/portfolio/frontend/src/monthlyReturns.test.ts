import { describe, expect, it } from 'vitest'

import type { PortfolioDailyPerformancePoint } from './lib/api'
import { buildMonthlyBuckets, buildMonthlyReturnMatrixRows } from './lib/monthlyReturns'

function point(
  asOfDate: string,
  dailyTwr: number | null,
  overrides: Partial<PortfolioDailyPerformancePoint> = {},
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
    market_observation_count: 1,
    return_observation_eligible: true,
    beginning_nav: 100,
    ending_nav: 100 * (1 + (dailyTwr ?? 0)),
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
    cumulative_twr: dailyTwr,
    drawdown: 0,
    ...overrides,
  }
}

function dailyPoints(startDate: string, endDate: string, returns: Record<string, number | null> = {}) {
  const points: PortfolioDailyPerformancePoint[] = []
  const current = new Date(`${startDate}T00:00:00Z`)
  const end = new Date(`${endDate}T00:00:00Z`)
  while (current <= end) {
    const dateKey = current.toISOString().slice(0, 10)
    points.push(point(dateKey, dateKey in returns ? returns[dateKey] : 0))
    current.setUTCDate(current.getUTCDate() + 1)
  }
  return points
}

describe('monthly return reliability boundaries', () => {
  it('withholds an inception-inside-month return instead of presenting it as a full month', () => {
    const buckets = buildMonthlyBuckets([
      point('2026-03-31', 0.01, { beginning_nav: 0, external_cash_in: 100 }),
      ...dailyPoints('2026-04-01', '2026-04-30', {
        '2026-04-01': 0.02,
        '2026-04-30': -0.01,
      }),
    ])

    expect(buckets[0]).toMatchObject({
      bucket_key: '2026-03',
      coverage_state: 'partial',
      cumulative_twr: null,
      unavailable_reason: 'Missing reliable month-start boundary.',
    })
    expect(buckets[1].cumulative_twr).toBeCloseTo(1.02 * 0.99 - 1)
  })

  it('withholds a month and YTD when return coverage breaks inside the period', () => {
    const buckets = buildMonthlyBuckets([
      ...dailyPoints('2026-01-01', '2026-01-31', {
        '2026-01-01': 0.01,
        '2026-01-02': null,
        '2026-01-31': 0.02,
      }).map((item) =>
        item.as_of_date === '2026-01-02'
          ? { ...item, return_coverage_state: 'unavailable' as const }
          : item,
      ),
      ...dailyPoints('2026-02-01', '2026-02-28', {
        '2026-02-01': 0.01,
        '2026-02-28': 0.01,
      }),
    ])
    const rows = buildMonthlyReturnMatrixRows(buckets)

    expect(buckets[0]).toMatchObject({
      coverage_state: 'partial',
      cumulative_twr: null,
      unavailable_reason: 'Return coverage is not continuous.',
    })
    expect(rows[0].hasYearStartAnchor).toBe(false)
    expect(rows[0].ytd).toBeNull()
  })

  it('compounds YTD only across a contiguous complete sequence starting January 1', () => {
    const buckets = buildMonthlyBuckets([
      ...dailyPoints('2026-01-01', '2026-01-31', {
        '2026-01-01': 0.01,
        '2026-01-31': 0.02,
      }),
      ...dailyPoints('2026-02-01', '2026-02-28', {
        '2026-02-01': -0.01,
        '2026-02-28': 0.03,
      }),
    ])
    const rows = buildMonthlyReturnMatrixRows(buckets)
    const january = 1.01 * 1.02 - 1
    const february = 0.99 * 1.03 - 1

    expect(rows[0].hasYearStartAnchor).toBe(true)
    expect(rows[0].ytd).toBeCloseTo((1 + january) * (1 + february) - 1)
  })
})
