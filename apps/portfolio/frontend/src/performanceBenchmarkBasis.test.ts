import { describe, expect, it } from 'vitest'

import { assessPerformanceBenchmarkBasis } from './lib/performanceBenchmarkBasis'
import performancePageSource from './pages/PerformancePage.tsx?raw'

describe('performance benchmark basis reliability', () => {
  it.each(['adjusted_close', 'total_return_nav'])(
    'accepts %s as a confirmed total-return basis',
    (basis) => {
      const assessment = assessPerformanceBenchmarkBasis(basis)
      expect(assessment.comparisonEligible).toBe(true)
      expect(assessment.basis).toBe(basis)
      expect(assessment.warning).toBeNull()
      expect(assessment.label).toContain('confirmed total-return basis')
    },
  )

  it.each(['close', 'official_nav', 'last', 'spot'])(
    'treats %s as a price or valuation fallback and withholds relative comparison',
    (basis) => {
      const assessment = assessPerformanceBenchmarkBasis(basis)
      expect(assessment.comparisonEligible).toBe(false)
      expect(assessment.basis).toBe(basis)
      expect(assessment.warning).toContain('Portfolio-relative differences and relative statistics are withheld')
      expect(assessment.warning).toContain(basis)
    },
  )

  it('accepts close when the selected index series explicitly declares total-return semantics', () => {
    const assessment = assessPerformanceBenchmarkBasis('close', 'total_return')

    expect(assessment.comparisonEligible).toBe(true)
    expect(assessment.basis).toBe('close')
    expect(assessment.label).toContain('confirmed total-return basis')
    expect(assessment.warning).toBeNull()
  })

  it('keeps explicitly price-return close exploratory while disclosing standalone metrics', () => {
    const assessment = assessPerformanceBenchmarkBasis('close', 'price_return')

    expect(assessment.comparisonEligible).toBe(true)
    expect(assessment.label).toContain('confirmed price-return basis')
    expect(assessment.warning).toContain('series is rebased')
    expect(assessment.warning).toContain('benchmark and relative metrics are shown')
    expect(assessment.warning).toContain('include that basis difference')
  })

  it('fails closed when the chart does not identify its basis', () => {
    const assessment = assessPerformanceBenchmarkBasis(null)
    expect(assessment.comparisonEligible).toBe(false)
    expect(assessment.label).toBe('Unavailable')
    expect(assessment.warning).toContain('total-return comparability cannot be verified')
  })

  it('wires basis disclosure and fail-closed comparison into Performance', () => {
    expect(performancePageSource).toContain('benchmarkChart.chart_basis')
    expect(performancePageSource).toContain('benchmarkChart.return_semantics')
    expect(performancePageSource).toContain('assessBenchmarkComparisonGuard')
    expect(performancePageSource).toContain("benchmarkGuard.mode === 'unavailable'")
    expect(performancePageSource).toContain(
      'comparableBenchmarkMetrics = relativeComparisonEligible ? benchmarkMetrics : null',
    )
    expect(performancePageSource).toContain('benchmarkGuard?.relativeComparisonEligible ?? false')
    expect(performancePageSource).toContain('Canonical comparator')
    expect(performancePageSource).toContain('label="Benchmark comparison"')
    expect(performancePageSource).toContain('benchmarkGuard.warning ?')
    expect(performancePageSource).toContain('setBenchmarkChart(null)')
  })
})
