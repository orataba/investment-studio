import { describe, expect, it } from 'vitest'

import { formatLabel, formatPercentInput } from './lib/format'

describe('formatLabel', () => {
  it('preserves common portfolio acronyms', () => {
    expect(formatLabel('etf')).toBe('ETF')
    expect(formatLabel('fcn')).toBe('FCN')
    expect(formatLabel('fx_conversion')).toBe('FX Conversion')
    expect(formatLabel('official_nav')).toBe('Unit NAV')
    expect(formatLabel('total_return_nav')).toBe('Dividend-Reinvested Total Return NAV')
  })
})

describe('formatPercentInput', () => {
  it('removes floating-point noise from editable percentage values', () => {
    expect(formatPercentInput(0.07)).toBe('7')
    expect(formatPercentInput(0.123456)).toBe('12.3456')
    expect(formatPercentInput(null)).toBe('')
  })
})
