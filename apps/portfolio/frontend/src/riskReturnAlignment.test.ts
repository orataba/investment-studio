import { describe, expect, it } from 'vitest'

import {
  pairWindowReturns,
  returnPointsInWindow,
  riskWindowStart,
  shiftIsoCalendarMonths,
  windowReturnPoints,
  type GroupReturnSeries,
} from './lib/riskReturnAlignment'

describe('Risk window calendar alignment', () => {
  it('maps production lookbacks to calendar-month boundaries', () => {
    expect(riskWindowStart('2026-07-24', 30)).toBe('2026-06-24')
    expect(riskWindowStart('2026-07-24', 90)).toBe('2026-04-24')
    expect(riskWindowStart('2026-07-24', 180)).toBe('2026-01-24')
    expect(riskWindowStart('2026-07-24', 366)).toBe('2025-07-24')
    expect(riskWindowStart('2026-07-24', 730)).toBe('2024-07-24')
  })

  it('clamps month-end dates without overflowing into the following month', () => {
    expect(shiftIsoCalendarMonths('2026-07-31', -1)).toBe('2026-06-30')
    expect(shiftIsoCalendarMonths('2026-05-31', -3)).toBe('2026-02-28')
    expect(shiftIsoCalendarMonths('2024-05-31', -3)).toBe('2024-02-29')
  })

  it('keeps arbitrary day lookbacks as EOD boundary dates', () => {
    expect(riskWindowStart('2026-07-24', 10)).toBe('2026-07-15')
  })

  it('uses close-to-close (start, end] return rows for a calendar-month window', () => {
    const points = [
      { start_date: '2026-06-26', date: '2026-06-27', value: 0.01 },
      { start_date: '2026-06-26', date: '2026-06-29', value: 0.02 },
      { start_date: '2026-07-24', date: '2026-07-27', value: -0.01 },
      { start_date: '2026-07-27', date: '2026-07-28', value: 0.03 },
    ]

    expect(riskWindowStart('2026-07-27', 30)).toBe('2026-06-27')
    expect(returnPointsInWindow(points, '2026-07-27', 30).map((point) => point.date)).toEqual([
      '2026-06-29',
      '2026-07-27',
    ])

    const series: GroupReturnSeries = {
      groupKey: 'alpha',
      groupLabel: 'Alpha',
      returnsByDate: new Map(points.map((point) => [point.date, point.value])),
      periodStartByDate: new Map(points.map((point) => [point.date, point.start_date])),
      endingWeightByDate: new Map(),
      latestWeight: 1,
      observationCount: points.length,
    }
    expect(windowReturnPoints(series, '2026-07-27', 30).map((point) => point.date)).toEqual([
      '2026-06-29',
      '2026-07-27',
    ])
    expect(
      pairWindowReturns(
        series.returnsByDate,
        series.returnsByDate,
        '2026-07-27',
        30,
      ).map((point) => point.date),
    ).toEqual(['2026-06-29', '2026-07-27'])
  })
})
