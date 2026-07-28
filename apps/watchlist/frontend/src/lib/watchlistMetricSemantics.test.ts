import { describe, expect, it } from 'vitest'

import {
  isMetricAsOfSensitiveField,
  summarizeMetricAsOfDates,
} from './watchlistMetricSemantics'

describe('watchlist row metric as-of semantics', () => {
  it('treats return and risk metrics as endpoint-sensitive', () => {
    expect(isMetricAsOfSensitiveField('return_1m')).toBe(true)
    expect(isMetricAsOfSensitiveField('volatility')).toBe(true)
    expect(isMetricAsOfSensitiveField('sharpe_ratio')).toBe(true)
    expect(isMetricAsOfSensitiveField('duration')).toBe(false)
  })

  it('allows a group metric only when all populated rows share one endpoint', () => {
    const aligned = summarizeMetricAsOfDates('return_1m', [
      { return_1m: 1, metric_as_of_date: '2026-07-24' },
      { return_1m: 2, metric_as_of_date: '2026-07-24' },
    ])
    const mixed = summarizeMetricAsOfDates('return_1m', [
      { return_1m: 1, metric_as_of_date: '2026-07-24' },
      { return_1m: 2, metric_as_of_date: '2026-07-27' },
    ])

    expect(aligned).toMatchObject({ comparable: true, asOfDate: '2026-07-24' })
    expect(mixed).toMatchObject({
      comparable: false,
      asOfDate: null,
      dates: ['2026-07-24', '2026-07-27'],
    })
  })

  it('fails closed when a populated metric has no endpoint metadata', () => {
    expect(
      summarizeMetricAsOfDates('volatility', [
        { volatility: 8.5, metric_as_of_date: null },
      ]),
    ).toMatchObject({ comparable: false, missingDateCount: 1 })
  })
})
