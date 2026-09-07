import { describe, expect, it } from 'vitest'

import { assessBenchmarkComparisonGuard } from './lib/benchmarkComparisonGuard'

const comparableInput = {
  chartBasis: 'adjusted_close',
  benchmarkCurrency: 'USD',
  portfolioCurrency: 'usd',
  points: [
    { date: '2026-07-05', value: 99 },
    { date: '2026-07-06', value: 100 },
    { date: '2026-07-07', value: 101 },
  ],
  startBoundaryDate: '2026-07-05',
  eligiblePortfolioDates: ['2026-07-06', '2026-07-07'],
}

describe('shared benchmark comparison guard', () => {
  it('admits a total-return benchmark only with matching currency, a start anchor, and complete dates', () => {
    const assessment = assessBenchmarkComparisonGuard(comparableInput)

    expect(assessment.mode).toBe('canonical')
    expect(assessment.reason).toBe('benchmark_total_return_comparable')
    expect(assessment.canonicalComparisonEligible).toBe(true)
    expect(assessment.warning).toBeNull()
  })

  it('allows relative metrics for a confirmed price-return benchmark without calling it canonical total return', () => {
    const assessment = assessBenchmarkComparisonGuard({
      ...comparableInput,
      chartBasis: 'close',
      returnSemantics: 'price_return',
    })

    expect(assessment.mode).toBe('exploratory')
    expect(assessment.reason).toBe('benchmark_price_return_comparable')
    expect(assessment.canonicalComparisonEligible).toBe(false)
    expect(assessment.relativeComparisonEligible).toBe(true)
    expect(assessment.warning).toContain('relative results include that difference')
  })

  it('allows price changes even when close has no distribution metadata', () => {
    const assessment = assessBenchmarkComparisonGuard({
      ...comparableInput,
      chartBasis: 'close',
    })

    expect(assessment.mode).toBe('exploratory')
    expect(assessment.reason).toBe('benchmark_basis_unconfirmed_comparable')
    expect(assessment.canonicalComparisonEligible).toBe(false)
    expect(assessment.relativeComparisonEligible).toBe(true)
    expect(assessment.warning).toContain('Distribution treatment is unconfirmed')
  })

  it('admits an index close series when its return semantics are explicitly total return', () => {
    const assessment = assessBenchmarkComparisonGuard({
      ...comparableInput,
      chartBasis: 'close',
      returnSemantics: 'total_return',
    })

    expect(assessment.mode).toBe('canonical')
    expect(assessment.reason).toBe('benchmark_total_return_comparable')
    expect(assessment.canonicalComparisonEligible).toBe(true)
    expect(assessment.relativeComparisonEligible).toBe(true)
    expect(assessment.warning).toBeNull()
  })

  it('withholds comparison when currencies differ', () => {
    const assessment = assessBenchmarkComparisonGuard({
      ...comparableInput,
      benchmarkCurrency: 'EUR',
    })

    expect(assessment.mode).toBe('unavailable')
    expect(assessment.reason).toBe('benchmark_currency_mismatch')
    expect(assessment.warning).toContain('EUR')
    expect(assessment.warning).toContain('USD')
  })

  it('withholds comparison when the start anchor is missing', () => {
    const assessment = assessBenchmarkComparisonGuard({
      ...comparableInput,
      points: comparableInput.points.slice(1),
    })

    expect(assessment.mode).toBe('unavailable')
    expect(assessment.reason).toBe('benchmark_start_anchor_missing')
    expect(assessment.warning).toContain('on or before 2026-07-05')
  })

  it('withholds comparison when an eligible portfolio return date is absent', () => {
    const assessment = assessBenchmarkComparisonGuard({
      ...comparableInput,
      points: comparableInput.points.slice(0, 2),
    })

    expect(assessment.mode).toBe('unavailable')
    expect(assessment.reason).toBe('benchmark_date_coverage_incomplete')
    expect(assessment.missingEligibleDates).toEqual(['2026-07-07'])
  })

  const cashTailInput = {
    ...comparableInput,
    startBoundaryDate: '2026-09-03', endBoundaryDate: '2026-09-08',
    eligiblePortfolioDates: ['2026-09-04'],
    marketSessionDates: ['2026-09-03', '2026-09-04', '2026-09-08'],
    points: [{ date: '2026-09-03', value: 100 }, { date: '2026-09-04', value: 110 },
      { date: '2026-09-08', value: 121 }],
  }

  it.each(['2026-09-04', '2026-09-08'])('rejects a missing official trading day %s, including the cash-only tail', (missingDate) => {
    const assessment = assessBenchmarkComparisonGuard({ ...cashTailInput,
      eligiblePortfolioDates: [],
      points: cashTailInput.points.filter((point) => point.date !== missingDate),
    })
    expect(assessment.mode).toBe('unavailable')
    expect(assessment.missingEligibleDates).toEqual([missingDate])
  })

  it('allows the official US Labor Day closure and a cash-only weekend without extrapolating a trading day', () => {
    const assessment = assessBenchmarkComparisonGuard({ ...cashTailInput,
      endBoundaryDate: '2026-09-07', eligiblePortfolioDates: [],
      points: cashTailInput.points.slice(0, 2),
    })
    expect(assessment.mode).toBe('canonical')
  })

  it('requires the last official session before a non-trading start boundary', () => {
    const assessment = assessBenchmarkComparisonGuard({ ...cashTailInput,
      startBoundaryDate: '2026-09-07',
      points: cashTailInput.points.filter((point) => point.date !== '2026-09-04'),
    })
    expect(assessment.mode).toBe('unavailable')
    expect(assessment.missingEligibleDates).toEqual(['2026-09-04'])
  })

  it('does not infer a holiday when an official calendar is unavailable', () => {
    const assessment = assessBenchmarkComparisonGuard({ ...cashTailInput,
      endBoundaryDate: '2026-09-07', marketSessionDates: null,
    })
    expect(assessment.mode).toBe('unavailable')
    expect(assessment.missingEligibleDates).toEqual(['2026-09-05', '2026-09-06', '2026-09-07'])
    expect(assessment.warning).toContain('No official market calendar')
  })
})
