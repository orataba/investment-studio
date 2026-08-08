import { describe, expect, it } from 'vitest'

import {
  holdingDayChangeExportValue,
  holdingDayChangeUnavailable,
  holdingUsesEventValuation,
  isOptionObligationHolding,
} from './lib/holdingPresentation'
import { holdingFixture } from './test/portfolioFixtures'

describe('holding presentation', () => {
  it.each([
    { valuationBasis: 'carried_cost', coverageStatus: 'event-cost' },
    { valuationBasis: 'premium_liability', coverageStatus: 'event-liability' },
  ])(
    'exports N/A instead of a manufactured zero for $valuationBasis',
    ({ valuationBasis, coverageStatus }) => {
      const row = holdingFixture({
        valuation_basis: valuationBasis,
        coverage_status: coverageStatus,
        day_change_value: 0,
        day_change_pct: 0,
      })

      expect(holdingUsesEventValuation(row)).toBe(true)
      expect(holdingDayChangeUnavailable(row)).toBe(true)
      expect(holdingDayChangeExportValue(row, row.day_change_value)).toBe('N/A')
      expect(holdingDayChangeExportValue(row, row.day_change_pct)).toBe('N/A')
    },
  )

  it('preserves an observed market zero as numeric zero', () => {
    const row = holdingFixture({
      valuation_basis: 'market_quote',
      coverage_status: 'price-nav-fx',
      day_change_value: 0,
      day_change_pct: 0,
    })

    expect(holdingDayChangeExportValue(row, row.day_change_value)).toBe(0)
    expect(holdingDayChangeExportValue(row, row.day_change_pct)).toBe(0)
  })

  it('identifies written option obligation rows by holding kind', () => {
    expect(
      isOptionObligationHolding(
        holdingFixture({ holding_kind: 'option_obligation' }),
      ),
    ).toBe(true)
  })
})
