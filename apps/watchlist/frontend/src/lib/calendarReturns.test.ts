import { describe, expect, it } from 'vitest'

import {
  areAdjacentCalendarMonths,
  buildAnnualReturnSeries,
  buildMonthlyCloseSeries,
  buildMonthlyReturnMatrix,
  buildMonthlyReturnWindows,
  buildMonthlyReturnSeries,
} from './calendarReturns'

describe('watchlist calendar-period returns', () => {
  it('uses the prior month close and the current month close', () => {
    const points = [
      { date: '2025-12-31', value: 100 },
      { date: '2026-01-02', value: 101 },
      { date: '2026-01-30', value: 110 },
      { date: '2026-02-02', value: 111 },
      { date: '2026-02-27', value: 121 },
    ]

    expect(buildMonthlyCloseSeries(points)).toEqual([
      { date: '2025-12-31', value: 100 },
      { date: '2026-01-30', value: 110 },
      { date: '2026-02-27', value: 121 },
    ])
    const monthlyReturns = buildMonthlyReturnSeries(points)
    expect(monthlyReturns).toHaveLength(2)
    expect(monthlyReturns[0]).toMatchObject({ date: '2026-01-30' })
    expect(monthlyReturns[1]).toMatchObject({ date: '2026-02-27' })
    expect(monthlyReturns[0].value).toBeCloseTo(10)
    expect(monthlyReturns[1].value).toBeCloseTo(10)
    expect(buildMonthlyReturnWindows(points)[0]).toMatchObject({
      anchorDate: '2025-12-31',
      endDate: '2026-01-30',
    })

    const matrix = buildMonthlyReturnMatrix(points)
    expect(matrix[0].monthWindows[0]).toEqual({
      anchorDate: '2025-12-31',
      endDate: '2026-01-30',
    })
  })

  it('does not bridge a missing calendar month', () => {
    const points = [
      { date: '2025-12-31', value: 100 },
      { date: '2026-01-30', value: 110 },
      { date: '2026-03-31', value: 150 },
    ]

    const monthlyReturns = buildMonthlyReturnSeries(points)
    expect(monthlyReturns).toHaveLength(1)
    expect(monthlyReturns[0]).toMatchObject({ date: '2026-01-30' })
    expect(monthlyReturns[0].value).toBeCloseTo(10)
    expect(areAdjacentCalendarMonths('2026-01-30', '2026-03-31')).toBe(false)
  })

  it('uses adjacent calendar-year closes for annual returns', () => {
    const points = [
      { date: '2024-01-02', value: 100 },
      { date: '2025-01-02', value: 120 },
      { date: '2026-01-02', value: 150 },
    ]

    const annualReturns = buildAnnualReturnSeries(points)
    expect(annualReturns).toHaveLength(2)
    expect(annualReturns[0]).toMatchObject({
      year: '2025',
      date: '2025-01-02',
      anchorDate: '2024-01-02',
    })
    expect(annualReturns[0].value).toBeCloseTo(20)
    expect(annualReturns[1]).toMatchObject({
      year: '2026',
      date: '2026-01-02',
      anchorDate: '2025-01-02',
    })
    expect(annualReturns[1].value).toBeCloseTo(25)
  })

  it('uses the last available observation in each year, not only December 31', () => {
    const annualReturns = buildAnnualReturnSeries([
      { date: '2024-11-29', value: 100 },
      { date: '2025-01-02', value: 120 },
    ])

    expect(annualReturns).toHaveLength(1)
    expect(annualReturns[0]).toMatchObject({
      anchorDate: '2024-11-29',
      date: '2025-01-02',
    })
    expect(annualReturns[0].value).toBeCloseTo(20)
  })

  it('does not bridge a missing calendar year', () => {
    expect(
      buildAnnualReturnSeries([
        { date: '2023-12-29', value: 100 },
        { date: '2025-12-31', value: 120 },
      ]),
    ).toEqual([])
  })

  it('keeps an annual-only row when no adjacent monthly pair exists', () => {
    const rows = buildMonthlyReturnMatrix([
      { date: '2024-12-31', value: 100 },
      { date: '2025-12-31', value: 120 },
      { date: '2026-12-31', value: 132 },
    ])

    expect(rows.map((row) => row.year)).toEqual(['2026', '2025'])
    expect(rows[0].months).toEqual(Array.from({ length: 12 }, () => null))
    expect(rows[0].ytd).toBeCloseTo(10)
    expect(rows[0].ytdWindow).toEqual({
      anchorDate: '2025-12-31',
      endDate: '2026-12-31',
    })
    expect(rows[1].ytd).toBeCloseTo(20)
  })
})
