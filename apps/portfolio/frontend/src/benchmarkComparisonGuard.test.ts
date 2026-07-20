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
    expect(assessment.warning).toContain('benchmark and relative metrics are shown')
  })

  it('withholds relative metrics when close has no verified return semantics', () => {
    const assessment = assessBenchmarkComparisonGuard({
      ...comparableInput,
      chartBasis: 'close',
    })

    expect(assessment.mode).toBe('exploratory')
    expect(assessment.reason).toBe('benchmark_price_only_exploratory')
    expect(assessment.canonicalComparisonEligible).toBe(false)
    expect(assessment.relativeComparisonEligible).toBe(false)
    expect(assessment.warning).toContain('Portfolio-relative differences and relative statistics are withheld')
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
})
