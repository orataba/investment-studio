import { describe, expect, it } from 'vitest'

import { buildCurrentInstrumentReturnSeries } from './pages/RiskPage'
import { holdingFixture, holdingsWorkspaceFixture, instrumentFixture } from './test/portfolioFixtures'

const alphaPoints = [
  { start_date: '2026-07-19', date: '2026-07-20', value: 0.01 },
  { start_date: '2026-07-20', date: '2026-07-21', value: -0.005 },
  { start_date: '2026-07-21', date: '2026-07-22', value: 0.003 },
]

function secondHolding(
  points: Array<{ start_date: string; date: string; value: number }>,
) {
  return holdingFixture({
    line_id: 'holding:beta',
    instrument_core: instrumentFixture({
      instrument_id: 'beta',
      instrument_name: 'Beta',
    }),
    allocation: 0.5,
    market_value: 500,
    market_value_base: 500,
    instrument_return_series_all: {
      first_return_start_date: points[0]?.start_date ?? null,
      points,
    },
  })
}

function workspace(
  betaPoints: Array<{ start_date: string; date: string; value: number }>,
) {
  return holdingsWorkspaceFixture({
    as_of_date: '2026-07-22',
    rows: [
      holdingFixture({
        allocation: 0.5,
        market_value: 500,
        market_value_base: 500,
        instrument_return_series_all: {
          first_return_start_date: alphaPoints[0].start_date,
          points: alphaPoints,
        },
      }),
      secondHolding(betaPoints),
    ],
  })
}

describe('Current-weight rolling risk alignment', () => {
  it('fails closed instead of silently intersecting away an internal missing date', () => {
    const result = buildCurrentInstrumentReturnSeries(
      workspace([alphaPoints[0], alphaPoints[2]]),
    )

    expect(result.value).toEqual([])
    expect(result.errors.join(' ')).toContain('requires identical return dates')
    expect(result.errors.join(' ')).toContain('2026-07-21')
  })

  it('fails closed when equal end dates represent different return periods', () => {
    const betaPoints = alphaPoints.map((point) =>
      point.date === '2026-07-21'
        ? { ...point, start_date: '2026-07-19' }
        : point,
    )

    const result = buildCurrentInstrumentReturnSeries(workspace(betaPoints))

    expect(result.value).toEqual([])
    expect(result.errors.join(' ')).toContain('requires one period identity')
    expect(result.errors.join(' ')).toContain('2026-07-21')
  })

  it('allows different inception dates when all periods align after the latest inception', () => {
    const result = buildCurrentInstrumentReturnSeries(
      workspace(alphaPoints.slice(1)),
    )

    expect(result.errors).toEqual([])
    expect(result.value).toHaveLength(2)
  })
})
