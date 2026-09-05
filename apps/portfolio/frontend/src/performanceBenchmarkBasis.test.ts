import { describe, expect, it } from 'vitest'

import { assessPerformanceBenchmarkBasis } from './lib/performanceBenchmarkBasis'
import performancePageSource from './pages/PerformancePage.tsx?raw'

describe('performance benchmark basis reliability', () => {
  it.each(['adjusted_close', 'total_return_nav'])(
    'accepts %s as a confirmed total-return basis',
    (basis) => {
      const assessment = assessPerformanceBenchmarkBasis(basis)
      expect(assessment.returnSemantics).toBe('total_return')
      expect(assessment.basis).toBe(basis)
      expect(assessment.warning).toBeNull()
      expect(assessment.label).toContain('confirmed total-return basis')
    },
  )

  it.each(['close', 'official_nav', 'last', 'spot'])(
    'discloses unconfirmed distribution treatment for %s without declaring it total return',
    (basis) => {
      const assessment = assessPerformanceBenchmarkBasis(basis)
      expect(assessment.returnSemantics).toBe('unknown')
      expect(assessment.basis).toBe(basis)
      expect(assessment.warning).toContain('Comparison uses the selected price or NAV series')
      expect(assessment.warning).toContain('unconfirmed')
    },
  )

  it('accepts close when the selected index series explicitly declares total-return semantics', () => {
    const assessment = assessPerformanceBenchmarkBasis('close', 'total_return')

    expect(assessment.returnSemantics).toBe('total_return')
    expect(assessment.basis).toBe('close')
    expect(assessment.label).toContain('confirmed total-return basis')
    expect(assessment.warning).toBeNull()
  })

  it('discloses distributions excluded from a price-return comparison', () => {
    const assessment = assessPerformanceBenchmarkBasis('close', 'price_return')

    expect(assessment.returnSemantics).toBe('price_return')
    expect(assessment.label).toContain('confirmed price-return basis')
    expect(assessment.warning).toContain('Distributions are excluded')
    expect(assessment.warning).toContain('relative results include that difference')
  })

  it('fails closed when the chart does not identify its basis', () => {
    const assessment = assessPerformanceBenchmarkBasis(null)
    expect(assessment.basis).toBeNull()
    expect(assessment.label).toBe('Unavailable')
    expect(assessment.warning).toContain('no available price or NAV basis')
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
