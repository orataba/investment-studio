import { describe, expect, it } from 'vitest'

import { formatDailyPercent, formatLabel, formatPercent, formatPercentInput } from './lib/format'

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

describe('formatDailyPercent', () => {
  it('keeps the direction of daily changes below the normal rounding precision', () => {
    expect(formatDailyPercent(-0.00004)).toBe('-0.004%')
    expect(formatDailyPercent(0.00004)).toBe('0.004%')
    expect(formatDailyPercent(-0.00000001)).toBe('-0.000001%')
    expect(formatDailyPercent(-0.000049999)).toBe('-0.005%')
    expect(formatDailyPercent(-0.00005)).toBe('-0.01%')
    expect(formatDailyPercent(0.00005)).toBe('0.01%')
    expect(formatDailyPercent(0)).toBe('0.00%')
    expect(formatDailyPercent(-0)).toBe('0.00%')
    expect(formatDailyPercent(-0.02)).toBe('-2.00%')
    expect(formatDailyPercent(null)).toBe('—')
    expect(formatDailyPercent(Infinity)).toBe('—')
    expect(formatPercent(-0.00004)).toBe('0.00%')
  })
})
