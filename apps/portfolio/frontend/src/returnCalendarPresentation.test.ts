import { describe, expect, it } from 'vitest'

import type { PortfolioReturnCalendarBucket } from './lib/api'
import { buildReturnCalendarMatrixRows } from './lib/returnCalendarPresentation'

function bucket(key: string, cumulativeTwr: number | null): PortfolioReturnCalendarBucket {
  return {
    bucket_key: key,
    frequency: 'monthly',
    start_date: `${key}-01`,
    end_date: `${key}-28`,
    nav_coverage_state: cumulativeTwr == null ? 'unavailable' : 'complete',
    nav_coverage_reason_codes: cumulativeTwr == null ? ['nav_window_not_complete'] : [],
    twr_state: cumulativeTwr == null ? 'broken' : 'linked',
    twr_reliability_status: cumulativeTwr == null ? 'unavailable' : 'reliable',
    twr_reliability_reasons: cumulativeTwr == null ? ['crosses_broken_twr_boundary'] : [],
    observation_count: cumulativeTwr == null ? 0 : 20,
    start_nav: 100,
    end_nav: 100,
    external_cash_in: 0,
    external_cash_out: 0,
    net_external_inflow: 0,
    absolute_change: 0,
    delta: 0,
    cumulative_twr: cumulativeTwr,
  }
}

describe('return calendar presentation', () => {
  it('only places backend buckets into calendar cells and preserves unavailable values', () => {
    const january = bucket('2026-01', 0.01)
    const february = bucket('2026-02', null)
    const previousDecember = bucket('2025-12', -0.02)

    const rows = buildReturnCalendarMatrixRows([january, previousDecember, february])

    expect(rows.map((row) => row.year)).toEqual(['2026', '2025'])
    expect(rows[0].months[0]).toBe(january)
    expect(rows[0].months[1]).toBe(february)
    expect(rows[0].months[1]?.cumulative_twr).toBeNull()
    expect(rows[0].months.slice(2)).toEqual(Array.from({ length: 10 }, () => null))
  })

  it('ignores non-monthly and malformed keys rather than inventing a bucket', () => {
    const weekly = { ...bucket('2026-01', 0.01), frequency: 'weekly' as const }
    const malformed = { ...bucket('2026-13', 0.01), bucket_key: '2026-13' }
    expect(buildReturnCalendarMatrixRows([weekly, malformed])).toEqual([])
  })
})
