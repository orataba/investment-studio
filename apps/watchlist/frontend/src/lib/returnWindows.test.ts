import { describe, expect, it } from 'vitest'

import {
  annualizedReturnPercent,
  commonObservationDateWindow,
  cumulativeReturnPercentToGrowthIndex100,
  namedReturnWindowSpec,
  normalizeCumulativeReturn,
  periodReturnPercent,
  resolveAlignedReturnWindows,
  resolveReturnWindow,
} from './returnWindows'

describe('canonical return window policy', () => {
  const points = [
    { date: '2025-12-31', value: 100 },
    { date: '2026-01-02', value: 101 },
    { date: '2026-06-30', value: 110 },
    { date: '2026-07-01', value: 111 },
    { date: '2026-07-02', value: 112 },
    { date: '2026-07-08', value: 120 },
  ]

  it('uses the selected start close for a custom 2-to-8 range', () => {
    const window = resolveReturnWindow(points, '2026-07-02', '2026-07-08')

    expect(window?.anchorDate).toBe('2026-07-02')
    expect(window?.endDate).toBe('2026-07-08')
    expect(window && periodReturnPercent(window)).toBeCloseTo((120 / 112 - 1) * 100)
    expect(window && normalizeCumulativeReturn(window)[0].value).toBe(0)
  })

  it('plots normalized returns as a growth index with an exact 100 start', () => {
    const window = resolveReturnWindow(points, '2026-07-02', '2026-07-08')
    const normalized = window ? normalizeCumulativeReturn(window) : []
    const growthIndex = cumulativeReturnPercentToGrowthIndex100(normalized)

    expect(growthIndex[0]?.value).toBe(100)
    expect(growthIndex[growthIndex.length - 1]?.value).toBeCloseTo((120 / 112) * 100)
  })

  it('uses the previous month-end close for MTD', () => {
    const spec = namedReturnWindowSpec('MTD', '2026-07-08')
    const window = resolveReturnWindow(points, spec.start, spec.end, spec.anchorMode)

    expect(window?.anchorDate).toBe('2026-06-30')
  })

  it('uses the previous year-end close for YTD', () => {
    const spec = namedReturnWindowSpec('YTD', '2026-07-08')
    const window = resolveReturnWindow(points, spec.start, spec.end, spec.anchorMode)

    expect(window?.anchorDate).toBe('2025-12-31')
  })

  it('exposes the actual sparse-series anchor and endpoint', () => {
    const sparse = [
      { date: '2026-07-03', value: 100 },
      { date: '2026-07-10', value: 105 },
    ]
    const spec = namedReturnWindowSpec('1D', '2026-07-10')
    const window = resolveReturnWindow(sparse, spec.start, spec.end, spec.anchorMode)

    expect(window?.anchorDate).toBe('2026-07-03')
    expect(window?.endDate).toBe('2026-07-10')
  })

  it('clamps calendar-month lookbacks instead of overflowing month end', () => {
    expect(namedReturnWindowSpec('1M', '2026-03-31')).toMatchObject({
      start: '2026-02-28',
      end: '2026-03-31',
      anchorMode: 'on_or_before',
    })
  })

  it('withholds annualized returns before the first calendar anniversary', () => {
    const shortWindow = resolveReturnWindow(
      [
        { date: '2024-02-29', value: 100 },
        { date: '2025-02-27', value: 110 },
      ],
      '2024-02-29',
      '2025-02-27',
    )
    const anniversaryWindow = resolveReturnWindow(
      [
        { date: '2024-02-29', value: 100 },
        { date: '2025-02-28', value: 110 },
      ],
      '2024-02-29',
      '2025-02-28',
    )

    expect(shortWindow && annualizedReturnPercent(shortWindow)).toBeNull()
    expect(anniversaryWindow && annualizedReturnPercent(anniversaryWindow)).toBeCloseTo(10)
  })

  it('uses exact common closes for benchmark comparison windows', () => {
    const aligned = resolveAlignedReturnWindows(
      [
        { date: '2026-06-27', value: 100 },
        { date: '2026-06-30', value: 102 },
        { date: '2026-07-27', value: 110 },
      ],
      [
        { date: '2026-06-26', value: 200 },
        { date: '2026-06-27', value: 201 },
        { date: '2026-07-24', value: 208 },
        { date: '2026-07-27', value: 210 },
      ],
      '2026-06-27',
      '2026-07-28',
    )

    expect(aligned?.left.points.map((point) => point.date)).toEqual([
      '2026-06-27',
      '2026-07-27',
    ])
    expect(aligned?.right.points.map((point) => point.date)).toEqual([
      '2026-06-27',
      '2026-07-27',
    ])
    expect(aligned?.left.anchorDate).toBe(aligned?.right.anchorDate)
    expect(aligned?.left.endDate).toBe(aligned?.right.endDate)
  })

  it('withholds a comparison when no complete exact common period exists', () => {
    expect(
      resolveAlignedReturnWindows(
        [
          { date: '2026-06-27', value: 100 },
          { date: '2026-07-27', value: 110 },
        ],
        [
          { date: '2026-06-26', value: 200 },
          { date: '2026-07-24', value: 210 },
        ],
        '2026-06-27',
        '2026-07-28',
      ),
    ).toBeNull()
  })

  it('exposes the exact common-observation bounds separately from overlap bounds', () => {
    expect(
      commonObservationDateWindow(
        [
          { date: '2026-06-27', value: 100 },
          { date: '2026-07-27', value: 110 },
        ],
        [
          { date: '2026-06-26', value: 200 },
          { date: '2026-06-27', value: 201 },
          { date: '2026-07-27', value: 210 },
        ],
      ),
    ).toEqual({ start: '2026-06-27', end: '2026-07-27' })
  })
})
