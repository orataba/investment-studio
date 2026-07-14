import { describe, expect, it } from 'vitest'

import {
  exactDecimalDivideHalfEven,
  exactDecimalMultiply,
  exactDecimalQuantizeHalfEven,
  exactDecimalSum,
} from './lib/exactDecimal'

describe('canonical decimal display arithmetic', () => {
  it('adds values without binary floating-point loss', () => {
    expect(exactDecimalSum(['0.1', '0.2'])).toBe('0.3')
  })

  it('aligns arbitrary scales and canonicalizes the result', () => {
    expect(exactDecimalSum(['100000000000000000000.00000001', '-0.000000009', '0.000000009'])).toBe(
      '100000000000000000000.00000001',
    )
  })

  it('handles cancellation and signs exactly', () => {
    expect(exactDecimalSum(['-999.999', '1000', '-0.001'])).toBe('0')
    expect(exactDecimalSum(['-1.2', '0.01'])).toBe('-1.19')
  })

  it('rejects non-canonical aliases instead of silently accepting them', () => {
    expect(() => exactDecimalSum(['1.0'])).toThrow(/Invalid canonical decimal/)
    expect(() => exactDecimalSum(['1e-3'])).toThrow(/Invalid canonical decimal/)
  })

  it('multiplies transaction drafts exactly before the amount boundary', () => {
    expect(exactDecimalMultiply('0.100000000000', '0.200000000000')).toBe('0.02')
    expect(
      exactDecimalQuantizeHalfEven(
        exactDecimalMultiply('123456789.123456789012', '9.876543210987'),
        8,
      ),
    ).toBe('1219326312.46753085')
  })

  it('uses explicit half-even rounding for price and amount suggestions', () => {
    expect(exactDecimalQuantizeHalfEven('1.1234567890125', 12)).toBe('1.123456789012')
    expect(exactDecimalQuantizeHalfEven('1.1234567890135', 12)).toBe('1.123456789014')
    expect(exactDecimalDivideHalfEven('1', '8', 12)).toBe('0.125')
    expect(exactDecimalDivideHalfEven('1', '6', 2)).toBe('0.17')
  })
})
