import { describe, expect, it } from 'vitest'

import type { PortfolioEntryRecord } from './lib/api'
import { groupPortfolioTotalsByBaseCurrency } from './lib/portfolioTotals'

function portfolio(
  id: string,
  baseCurrency: string,
  nav: number,
  dayChangeValue: number | null,
): PortfolioEntryRecord {
  return {
    portfolio_id: id,
    portfolio_name: id,
    base_currency: baseCurrency,
    as_of_date: '2026-07-13',
    nav,
    day_change_value: dayChangeValue,
    day_change_pct: null,
    securities_count: 0,
    sort_order: 0,
  }
}

describe('portfolio currency totals', () => {
  it('never adds different base currencies together', () => {
    expect(
      groupPortfolioTotalsByBaseCurrency([
        portfolio('usd-1', 'USD', 100, 2),
        portfolio('cny-1', 'CNY', 700, -7),
        portfolio('usd-2', 'usd', 50, 1),
      ]),
    ).toEqual([
      { baseCurrency: 'CNY', portfolioCount: 1, nav: 700, dayChangeValue: -7 },
      { baseCurrency: 'USD', portfolioCount: 2, nav: 150, dayChangeValue: 3 },
    ])
  })

  it('does not convert a missing day change into zero', () => {
    expect(
      groupPortfolioTotalsByBaseCurrency([
        portfolio('usd-1', 'USD', 100, 2),
        portfolio('usd-2', 'USD', 50, null),
      ])[0],
    ).toEqual({ baseCurrency: 'USD', portfolioCount: 2, nav: 150, dayChangeValue: null })
  })
})
