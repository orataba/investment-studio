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
  it('blocks unknown return semantics', () => {
    expect(
      benchmarkRiskBasisAssessment(chart({ return_semantics: 'unknown' }), 'CNY'),
    ).toEqual({
      blocking: true,
      message: 'Benchmark risk comparison requires confirmed price-return or total-return semantics.',
    })
  })

  it('blocks raw-currency comparisons', () => {
    expect(benchmarkRiskBasisAssessment(chart({ currency: 'USD' }), 'CNY')).toEqual({
      blocking: true,
      message: 'Benchmark risk comparison requires a base-currency return series; got USD versus CNY.',
    })
  })

  it('allows a confirmed price-return series with a comparability warning', () => {
    expect(
      benchmarkRiskBasisAssessment(chart({ return_semantics: 'price_return' }), 'CNY'),
    ).toEqual({
      blocking: false,
      message:
        'Benchmark uses price returns; volatility and Sharpe exclude distributions and are not fully comparable with portfolio total returns.',
    })
  })
})
