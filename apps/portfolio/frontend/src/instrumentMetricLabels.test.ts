import { describe, expect, it } from 'vitest'

import {
  performanceSeriesLabel,
  valuationQuoteLabel,
} from './lib/instrumentMetricLabels'

describe('instrument metric labels', () => {
  it('keeps valuation NAV distinct from the total-return performance series', () => {
    expect(valuationQuoteLabel('official_nav')).toBe('Official NAV')
    expect(performanceSeriesLabel('total_return_nav')).toBe('Total Return NAV')
  })

  it('labels tradeable close separately from adjusted performance history', () => {
    expect(valuationQuoteLabel('close')).toBe('Market Close')
    expect(performanceSeriesLabel('adjusted_close')).toBe('Adjusted Close')
  })

  it('keeps cumulative and reinvested fund series explicit', () => {
    expect(performanceSeriesLabel('cumulative_nav')).toBe('Cumulative NAV')
    expect(performanceSeriesLabel('dividend_adjusted_nav')).toBe('Dividend-adjusted NAV')
    expect(performanceSeriesLabel('reinvested_nav')).toBe('Total Return NAV')
  })
})
