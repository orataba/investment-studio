import { describe, expect, it } from 'vitest'

import { formatNumber, formatPercent, signedValueClass } from './format'

describe('watchlist numeric presentation boundary', () => {
  it('withholds non-finite analytical values', () => {
    expect(formatNumber(Number.NaN)).toBe('—')
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe('—')
    expect(formatPercent(Number.NEGATIVE_INFINITY)).toBe('—')
    expect(signedValueClass(Number.POSITIVE_INFINITY)).toBe('')
  })
})
