import { describe, expect, it } from 'vitest'

import type { PortfolioInstrumentPriceChartResponse } from './lib/api'
import { benchmarkRiskBasisAssessment } from './pages/RiskPage'
import { instrumentFixture } from './test/portfolioFixtures'

function chart(
  overrides: Partial<PortfolioInstrumentPriceChartResponse> = {},
): PortfolioInstrumentPriceChartResponse {
  return {
    portfolio_id: '3',
    instrument_core: instrumentFixture(),
    as_of_date: '2026-07-24',
    range_key: 'all',
    chart_basis: 'adjusted_close',
    return_semantics: 'total_return',
    metric_family: 'price',
    currency: 'CNY',
    coverage_state: 'complete',
    points: [],
    summary: {
      point_count: 0,
      change_value: null,
      change_pct: null,
      high: null,
      low: null,
    },
    ...overrides,
  }
}

describe('Risk benchmark basis guard', () => {
  it('uses adjusted close even without a separate return-semantics label', () => {
    expect(
      benchmarkRiskBasisAssessment(chart({ return_semantics: 'unknown' }), 'CNY'),
    ).toEqual({
      blocking: false,
      message: null,
    })
  })

  it('allows split-adjusted price comparisons and discloses unconfirmed distribution treatment', () => {
    const assessment = benchmarkRiskBasisAssessment(
      chart({ chart_basis: 'close', return_semantics: 'unknown', split_adjusted: true }), 'CNY',
    )
    expect(assessment.blocking).toBe(false)
    expect(assessment.message).toContain('Distribution treatment is unconfirmed')
  })

  it('blocks raw-currency comparisons', () => {
    expect(benchmarkRiskBasisAssessment(chart({ currency: 'USD' }), 'CNY')).toEqual({
      blocking: true,
      message: 'Benchmark risk comparison requires a base-currency return series; got USD versus CNY.',
    })
  })

  it('allows a confirmed price-return series with a comparability warning', () => {
    expect(
      benchmarkRiskBasisAssessment(chart({ chart_basis: 'close', return_semantics: 'price_return' }), 'CNY'),
    ).toEqual({
      blocking: false,
      message:
        'Comparison uses the selected price-return series. Distributions are excluded from this benchmark, so relative results include that difference.',
    })
  })
})
