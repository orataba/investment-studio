import { describe, expect, it } from 'vitest'

import {
  performanceSeriesLabel,
  valuationQuoteLabel,
} from './lib/instrumentMetricLabels'

describe('instrument metric labels', () => {
  it('keeps valuation NAV distinct from the total-return performance series', () => {
    expect(valuationQuoteLabel('official_nav')).toBe('Unit NAV')
    expect(performanceSeriesLabel('total_return_nav')).toBe(
      'Dividend-Reinvested Total Return NAV',
    )
  })

  it('labels tradeable close separately from adjusted performance history', () => {
    expect(valuationQuoteLabel('close')).toBe('Market Close')
    expect(performanceSeriesLabel('adjusted_close')).toBe('Adjusted Close')
  })

})
