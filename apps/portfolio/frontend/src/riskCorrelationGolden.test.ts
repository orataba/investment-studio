import { describe, expect, it } from 'vitest'

import { buildCorrelationMatrix } from './lib/riskCorrelation'
import {
  alignReturnPointsToFrequency,
  alignReturnSeriesToFrequency,
} from './lib/riskReturnAlignment'
import { assessRiskWindowCoverage } from './lib/riskWindowCoverage'

type MatrixScope = Parameters<typeof buildCorrelationMatrix>[0]
type MatrixSeries = MatrixScope['series'][number]

const AS_OF_DATE = '2026-02-28'
const LOOKBACK_DAYS = 30

function shiftUtcDate(isoDate: string, days: number) {
  const date = new Date(`${isoDate}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

const sampleDates = Array.from({ length: LOOKBACK_DAYS }, (_, index) =>
  shiftUtcDate(AS_OF_DATE, index - (LOOKBACK_DAYS - 1)),
)

function matrixSeries({
  key,
  label,
  values,
  weight,
  dates = sampleDates,
  periodStarts,
  reverseInsertion = false,
}: {
  key: string
  label: string
  values: number[]
  weight: number
  dates?: string[]
  periodStarts?: Map<string, string | null>
  reverseInsertion?: boolean
}): MatrixSeries {
  const entries = dates.map((date, index) => [date, values[index]] as const)
  const orderedEntries = reverseInsertion ? entries.reverse() : entries
  return {
    groupKey: key,
    groupLabel: label,
    returnsByDate: new Map(orderedEntries),
    periodStartByDate:
      periodStarts ?? new Map(dates.map((date) => [date, shiftUtcDate(date, -1)] as const)),
    endingWeightByDate: new Map([[AS_OF_DATE, weight]]),
    latestWeight: weight,
    observationCount: dates.length,
  }
}

function strictScope(series: MatrixSeries[], memberCount = series.length): MatrixScope {
  return { memberCount, series, issues: [] }
}

function trendingValues(scale = 1) {
  return sampleDates.map((_, index) => ((index - 14.5) / 10_000) * scale)
}

describe('Risk correlation numeric golden contract', () => {
  it('applies the coverage ratio to the actual calendar-month span', () => {
    const dates = Array.from({ length: 23 }, (_, index) =>
      shiftUtcDate('2026-03-01', index),
    )

    expect(
      assessRiskWindowCoverage(
        dates,
        '2026-03-28',
        30,
        'daily',
        undefined,
        '2026-02-28',
      ),
    ).toEqual({
      ok: true,
      observationCount: 23,
      error: null,
    })
  })

  it('keeps weekly return compounding, period identity, weights, and window coverage semantics', () => {
    const weeklySource = matrixSeries({
      key: 'weekly',
      label: 'Weekly',
      dates: ['2026-02-23', '2026-02-24', '2026-02-28'],
      values: [0.1, 0.2, 0.3],
      weight: 0.5,
      periodStarts: new Map([
        ['2026-02-23', '2026-02-22'],
        ['2026-02-24', '2026-02-23'],
        ['2026-02-28', '2026-02-27'],
      ]),
    })
    weeklySource.endingWeightByDate = new Map([
      ['2026-02-23', 0.4],
      ['2026-02-24', 0.45],
      ['2026-02-28', 0.5],
    ])

    const alignedSeries = alignReturnSeriesToFrequency([weeklySource], 'weekly', AS_OF_DATE)[0]
    const alignedPoints = alignReturnPointsToFrequency(
      ['2026-02-23', '2026-02-24', '2026-02-28'].map((date, index) => ({
        date,
        value: [0.1, 0.2, 0.3][index],
      })),
      'weekly',
      AS_OF_DATE,
    )

    expect([...alignedSeries.returnsByDate.entries()]).toEqual([
      ['2026-02-27', 0.7160000000000002],
    ])
    expect([...alignedSeries.periodStartByDate.entries()]).toEqual([
      ['2026-02-27', '2026-02-22'],
    ])
    expect([...alignedSeries.endingWeightByDate.entries()]).toEqual([
      ['2026-02-27', 0.5],
    ])
    expect(alignedSeries.observationCount).toBe(1)
    expect(alignedPoints).toEqual([
      { date: '2026-02-27', value: 0.7160000000000002 },
    ])
    expect(assessRiskWindowCoverage(sampleDates, AS_OF_DATE, LOOKBACK_DAYS, 'daily')).toEqual({
      ok: true,
      observationCount: 30,
      error: null,
    })
    expect(assessRiskWindowCoverage(sampleDates.slice(0, 14), AS_OF_DATE, LOOKBACK_DAYS, 'daily')).toEqual({
      ok: false,
      observationCount: 14,
      error: 'Risk window requires at least 15 daily observations; got 14.',
    })
  })

  it('keeps diagonal, symmetry, perfect positive/negative correlation, date ordering, and member ordering', () => {
    const alpha = matrixSeries({
      key: 'alpha',
      label: 'Alpha',
      values: trendingValues(1),
      weight: 0.3,
      reverseInsertion: true,
    })
    const beta = matrixSeries({ key: 'beta', label: 'Beta', values: trendingValues(2), weight: 0.6 })
    const gamma = matrixSeries({ key: 'gamma', label: 'Gamma', values: trendingValues(-1), weight: 0.1 })

    const result = buildCorrelationMatrix(
      strictScope([gamma, alpha, beta]),
      AS_OF_DATE,
      LOOKBACK_DAYS,
      'daily',
    )

    expect(result.issues).toEqual([])
    expect(result.alignedObservationCount).toBe(30)
    expect(result.matrix.groups.map((group) => group.key)).toEqual(['beta', 'alpha', 'gamma'])
    expect(result.matrix.cells).toHaveLength(3)
    result.matrix.cells.forEach((row, rowIndex) => {
      expect(row[rowIndex]?.value).toBeCloseTo(1, 12)
      row.forEach((cell, columnIndex) => {
        expect(cell.observationCount).toBe(30)
        expect(cell.value).toBeCloseTo(result.matrix.cells[columnIndex]?.[rowIndex]?.value ?? 0, 12)
      })
    })
    expect(result.matrix.cells[0]?.[1]?.value).toBeCloseTo(1, 12)
    expect(result.matrix.cells[0]?.[2]?.value).toBeCloseTo(-1, 12)
    expect(result.matrix.maxAbs).toBeCloseTo(1, 12)
  })

  it('fails closed when a constant series makes correlation unavailable', () => {
    const variable = matrixSeries({ key: 'variable', label: 'Variable', values: trendingValues(), weight: 0.6 })
    const constant = matrixSeries({
      key: 'constant',
      label: 'Constant',
      values: sampleDates.map(() => 0),
      weight: 0.4,
    })

    const result = buildCorrelationMatrix(
      strictScope([variable, constant]),
      AS_OF_DATE,
      LOOKBACK_DAYS,
      'daily',
    )

    expect(result.matrix).toEqual({ groups: [], cells: [], maxAbs: 0 })
    expect(result.alignedObservationCount).toBe(30)
    expect(result.issues.length).toBeGreaterThan(0)
    expect(result.issues.every((issue) => issue.reason === 'calculation_unavailable')).toBe(true)
  })

  it('fails closed when the supplied series count does not match the scope member count', () => {
    const alpha = matrixSeries({ key: 'alpha', label: 'Alpha', values: trendingValues(), weight: 1 })

    const result = buildCorrelationMatrix(strictScope([alpha], 2), AS_OF_DATE, LOOKBACK_DAYS, 'daily')

    expect(result.matrix.groups).toEqual([])
    expect(result.scopeMemberCount).toBe(2)
    expect(result.alignedObservationCount).toBe(0)
  })

  it('fails closed and identifies a missing date regardless of map insertion order', () => {
    const missingDate = sampleDates[12]
    const reducedDates = sampleDates.filter((date) => date !== missingDate)
    const alpha = matrixSeries({
      key: 'alpha',
      label: 'Alpha',
      values: trendingValues(),
      weight: 0.6,
      reverseInsertion: true,
    })
    const beta = matrixSeries({
      key: 'beta',
      label: 'Beta',
      values: trendingValues(2).filter((_, index) => index !== 12),
      dates: reducedDates,
      weight: 0.4,
      reverseInsertion: true,
    })

    const result = buildCorrelationMatrix(strictScope([beta, alpha]), AS_OF_DATE, LOOKBACK_DAYS, 'daily')

    expect(result.matrix.groups).toEqual([])
    expect(result.alignedObservationCount).toBe(0)
    expect(result.issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          memberKey: 'beta',
          reason: 'misaligned_dates',
          missingDates: [missingDate],
          missingDateCount: 1,
        }),
      ]),
    )
  })

  it('fails closed when equal return end dates have different period starts', () => {
    const mismatchDate = sampleDates[18]
    const betaStarts = new Map(sampleDates.map((date) => [date, shiftUtcDate(date, -1)] as const))
    betaStarts.set(mismatchDate, shiftUtcDate(mismatchDate, -2))
    const alpha = matrixSeries({ key: 'alpha', label: 'Alpha', values: trendingValues(), weight: 0.6 })
    const beta = matrixSeries({
      key: 'beta',
      label: 'Beta',
      values: trendingValues(2),
      weight: 0.4,
      periodStarts: betaStarts,
    })

    const result = buildCorrelationMatrix(strictScope([alpha, beta]), AS_OF_DATE, LOOKBACK_DAYS, 'daily')

    expect(result.matrix.groups).toEqual([])
    expect(result.alignedObservationCount).toBe(0)
    expect(result.issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          memberKey: 'beta',
          reason: 'misaligned_dates',
          missingDates: [mismatchDate],
          missingDateCount: 1,
        }),
      ]),
    )
  })
})
