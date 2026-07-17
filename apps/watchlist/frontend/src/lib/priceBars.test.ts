import { describe, expect, it } from 'vitest'

import type { InstrumentPriceBar } from './api'
import { adjustPriceBars, priceReturnStats, priceRiskStats, slicePriceBars } from './priceBars'

function bar(
  date: string,
  close: string,
  factor: string | null,
): InstrumentPriceBar {
  const value = Number(close)
  return {
    date,
    open: String(value - 0.1),
    high: String(value + 0.2),
    low: String(value - 0.2),
    close,
    previous_close: null,
    volume: '1000',
    turnover: '2000',
    adjustment_factor: factor,
    currency: 'CNY',
    volume_unit: 'lot',
    turnover_unit: 'thousand_cny',
    provider: 'test',
    status: 'complete',
  }
}

describe('price bars', () => {
  it('forward-adjusts historical OHLC while leaving the latest bar at raw price', () => {
    const adjusted = adjustPriceBars(
      [bar('2026-01-02', '10', '1'), bar('2026-01-03', '6', '2')],
      'qfq',
    )

    expect(adjusted[0].close).toBe(5)
    expect(adjusted[0].high).toBe(5.1)
    expect(adjusted[1].close).toBe(6)
    expect(adjusted[1].volume).toBe(1000)
  })

  it('keeps raw prices unchanged and slices from the latest observation', () => {
    const source = Array.from({ length: 80 }, (_, index) => {
      const date = new Date(Date.UTC(2026, 0, 1 + index)).toISOString().slice(0, 10)
      return bar(date, String(index + 10), '1')
    })
    const raw = adjustPriceBars(source, 'raw')

    expect(raw[0].close).toBe(10)
    expect(slicePriceBars(raw, '1M')[0].date).toBe('2026-02-21')
    expect(slicePriceBars(raw, 'ALL')).toHaveLength(80)
  })

  it('computes returns and drawdown from adjusted closes', () => {
    const bars = adjustPriceBars(
      [bar('2025-12-31', '100', '1'), bar('2026-01-02', '120', '1'), bar('2026-01-03', '90', '1')],
      'raw',
    )

    expect(priceReturnStats(bars).dailyChange).toBeCloseTo(-25)
    expect(priceRiskStats(bars).maximumDrawdown).toBeCloseTo(-25)
    expect(priceRiskStats(bars).observationCount).toBe(3)
  })
})
