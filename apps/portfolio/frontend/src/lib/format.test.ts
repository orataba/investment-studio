import { describe, expect, it } from 'vitest'

import { formatCurrency, formatNumber, formatPercent, formatSignedCurrency } from './format'

describe('portfolio numeric presentation boundary', () => {
  it('fails closed when a monetary value has no canonical currency', () => {
    expect(formatCurrency(100, '')).toBe('—')
    expect(formatCurrency(100, 'US')).toBe('—')
    expect(formatSignedCurrency(100, '')).toBe('—')
  })

  it('does not render non-finite calculation output as an investment fact', () => {
    expect(formatCurrency(Number.POSITIVE_INFINITY, 'CNY')).toBe('—')
    expect(formatSignedCurrency(Number.NEGATIVE_INFINITY, 'CNY')).toBe('—')
    expect(formatNumber(Number.NaN)).toBe('—')
    expect(formatPercent(Number.POSITIVE_INFINITY)).toBe('—')
  })
})
