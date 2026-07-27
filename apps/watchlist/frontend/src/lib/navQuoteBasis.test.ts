import { describe, expect, it } from 'vitest'

import {
  buildNavQuoteBasisContext,
  buildNavQuoteBasisSeries,
  filterNavQuoteRowsByCurrency,
  navQuoteValueForBasis,
  returnKindsAreComparable,
} from './navQuoteBasis'

describe('NAV quote basis presentation', () => {
  const row = {
    as_of_date: '2026-07-15',
    nav: 1.01,
    nav_with_dividend: 1.23,
    selected_basis_type: 'nav_with_dividend',
    selected_value: 1.23,
    currency: 'USD',
  }

  it('reads only the explicitly requested basis even when another basis is canonical', () => {
    expect(navQuoteValueForBasis(row, 'nav')).toBe(1.01)
    expect(navQuoteValueForBasis(row, 'nav_with_dividend')).toBe(1.23)
  })

  it('fails closed instead of substituting the other NAV basis', () => {
    const officialNavMissing = { ...row, nav: null }

    expect(navQuoteValueForBasis(officialNavMissing, 'nav')).toBeNull()
    expect(buildNavQuoteBasisSeries([officialNavMissing], 'nav')).toEqual([])
    expect(buildNavQuoteBasisSeries([officialNavMissing], 'nav_with_dividend')).toEqual([
      { date: '2026-07-15', value: 1.23 },
    ])
  })

  it('keeps the requested basis active when only the other basis is available', () => {
    const officialNavMissing = { ...row, nav: null }

    expect(buildNavQuoteBasisContext([officialNavMissing], 'nav')).toEqual({
      activeBasis: 'nav',
      availableBases: ['nav_with_dividend'],
      basisSeries: [],
    })
  })

  it('rejects missing and mismatched currencies instead of falling back across currencies', () => {
    expect(filterNavQuoteRowsByCurrency([{ ...row, currency: null }], 'USD')).toEqual([])
    expect(filterNavQuoteRowsByCurrency([{ ...row, currency: 'EUR' }], 'USD')).toEqual([])
    expect(filterNavQuoteRowsByCurrency([row], '')).toEqual([])
  })

  it('allows a benchmark only when both currency and requested basis are available', () => {
    const matchingRows = filterNavQuoteRowsByCurrency([row], 'USD')
    expect(buildNavQuoteBasisContext(matchingRows, 'nav').basisSeries).toEqual([
      { date: '2026-07-15', value: 1.01 },
    ])

    const missingBasisRows = filterNavQuoteRowsByCurrency([{ ...row, nav: null }], 'USD')
    expect(buildNavQuoteBasisContext(missingBasisRows, 'nav').basisSeries).toEqual([])
  })

  it('allows relative metrics only for matching, explicitly identified return semantics', () => {
    expect(returnKindsAreComparable('total_return', 'total_return')).toBe(true)
    expect(returnKindsAreComparable('price_return', 'price_return')).toBe(true)
    expect(returnKindsAreComparable('total_return', 'price_return')).toBe(false)
    expect(returnKindsAreComparable('total_return', null)).toBe(false)
    expect(returnKindsAreComparable(null, null)).toBe(false)
  })
})
