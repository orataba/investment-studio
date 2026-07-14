import { describe, expect, it } from 'vitest'

import type { PortfolioDailyPublishedReturnCalendarBucket } from './lib/api'
import { buildReturnCalendarMatrixRows } from './lib/returnCalendarPresentation'

function bucket(
  key: string,
  cumulativeTwrMethod50: string | null,
): PortfolioDailyPublishedReturnCalendarBucket {
  const metric = (method50: string) => ({
    method50,
    published: method50,
    rounding_adjustment_exact: '0',
  })
  return {
    bucket_key: key,
    frequency: 'monthly',
    calendar_start_date: `${key}-01`,
    calendar_end_date: `${key}-28`,
    coverage_state: cumulativeTwrMethod50 == null ? 'unavailable' : 'complete',
    coverage_reason_codes:
      cumulativeTwrMethod50 == null ? ['return_chain_unavailable'] : [],
    status: cumulativeTwrMethod50 == null ? 'unavailable' : 'ready',
    effective_return_start_date: cumulativeTwrMethod50 == null ? null : `${key}-01`,
    effective_return_end_date: cumulativeTwrMethod50 == null ? null : `${key}-28`,
    observation_count: cumulativeTwrMethod50 == null ? 0 : 20,
    cumulative_twr: cumulativeTwrMethod50 == null ? null : metric(cumulativeTwrMethod50),
    current_drawdown: cumulativeTwrMethod50 == null ? null : metric('-0.001'),
    max_drawdown: cumulativeTwrMethod50 == null ? null : metric('-0.002'),
    reason_codes: cumulativeTwrMethod50 == null ? ['return_chain_unavailable'] : [],
  }
}

describe('return calendar presentation', () => {
  it('only places backend buckets into calendar cells and preserves unavailable values', () => {
    const january = bucket('2026-01', '0.01')
    const february = bucket('2026-02', null)
    const previousDecember = bucket('2025-12', '-0.02')

    const rows = buildReturnCalendarMatrixRows([january, previousDecember, february])

    expect(rows.map((row) => row.year)).toEqual(['2026', '2025'])
    expect(rows[0].months[0]).toBe(january)
    expect(rows[0].months[1]).toBe(february)
    expect(rows[0].months[1]?.cumulative_twr).toBeNull()
    expect(rows[0].months.slice(2)).toEqual(Array.from({ length: 10 }, () => null))
  })

  it('ignores non-monthly and malformed keys rather than inventing a bucket', () => {
    const weekly = { ...bucket('2026-01', '0.01'), frequency: 'weekly' as const }
    const malformed = { ...bucket('2026-13', '0.01'), bucket_key: '2026-13' }
    expect(buildReturnCalendarMatrixRows([weekly, malformed])).toEqual([])
  })
})
