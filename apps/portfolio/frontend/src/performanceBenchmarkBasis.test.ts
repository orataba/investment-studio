import { describe, expect, it } from 'vitest'

import { assessPerformanceBenchmarkBasis } from './lib/performanceBenchmarkBasis'
import performancePageSource from './pages/PerformancePage.tsx?raw'

describe('performance benchmark basis reliability', () => {
  it.each(['adjusted_close', 'total_return_nav', 'dividend_adjusted_nav', 'reinvested_nav'])(
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

  it('fails closed when the chart does not identify its basis', () => {
    const assessment = assessPerformanceBenchmarkBasis(null)
    expect(assessment.comparisonEligible).toBe(false)
    expect(assessment.label).toBe('Unavailable')
    expect(assessment.warning).toContain('total-return comparability cannot be verified')
  })

  it('wires basis disclosure and fail-closed comparison into Performance', () => {
    expect(performancePageSource).toContain('benchmarkChart.chart_basis')
    expect(performancePageSource).toContain('!benchmarkBasisAssessment?.comparisonEligible')
    expect(performancePageSource).toContain('Benchmark basis')
    expect(performancePageSource).toContain('performance-benchmark-basis-warning')
    expect(performancePageSource).toContain('setBenchmarkChart(null)')
  })
})
