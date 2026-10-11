import { describe, expect, it } from 'vitest'

import type { InstrumentPriceBar } from './api'
import {
  adjustPriceBars,
  slicePriceBars,
} from './priceBars'

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
      'front',
      { front: { date: '2026-01-03', factor: 2, start_date: '2026-01-02', end_date: '2026-01-03' } },
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

  it('backward adjustment uses the full-history anchor, even for a sliced response', () => {
    const adjusted = adjustPriceBars([bar('2026-06-01', '6', '2'), bar('2026-06-02', '7', '2')], 'back', {
      back: { date: '2020-01-01', factor: 1, start_date: '2020-01-01', end_date: '2026-06-02' },
    })
    expect(adjusted.map(point => point.close)).toEqual([12, 14])
    expect(adjusted[1].previousClose).toBe(12)
  })

  it('does not mix factors across independently rebased segments', () => {
    const adjusted = adjustPriceBars([bar('2026-01-02', '100', '1'), bar('2026-01-03', '50', '1')], 'front', {
      front: { date: '2026-01-03', factor: 1, start_date: '2026-01-03', end_date: '2026-01-03' },
    })
    expect(adjusted.map(point => point.date)).toEqual(['2026-01-03'])
    expect(adjusted[0].previousClose).toBeNull()
  })

  it('withholds QFQ when any bar lacks a valid adjustment factor', () => {
    const incomplete = [
      bar('2026-01-02', '10', '1'),
      bar('2026-01-03', '11', null),
    ]

    expect(adjustPriceBars(incomplete, 'front', { front: { date: '2026-01-03', factor: 1, start_date: '2026-01-02', end_date: '2026-01-03' } })).toEqual([])
    expect(adjustPriceBars([bar('2026-01-02', '10', '1')], 'front')).toEqual([])
  })
})
