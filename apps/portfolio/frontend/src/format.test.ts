import { describe, expect, it } from 'vitest'

import { formatLabel } from './lib/format'

describe('formatLabel', () => {
  it('preserves common portfolio acronyms', () => {
    expect(formatLabel('etf')).toBe('ETF')
    expect(formatLabel('fx_conversion')).toBe('FX Conversion')
    expect(formatLabel('total_return_nav')).toBe('Total Return NAV')
  })
})
