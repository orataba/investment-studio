import { describe, expect, it } from 'vitest'

import {
  groupedAnnualizedVolatility,
  groupedCurrentDrawdown,
  groupedMaxDrawdown,
  groupedReturnSeries,
} from './pages/PortfolioHomePage'
import { holdingFixture, holdingsWorkspaceFixture, instrumentFixture } from './test/portfolioFixtures'

function returnSeries(
  periods: Array<[startDate: string, endDate: string, value: number]>,
) {
  return {
    first_return_start_date: periods[0]?.[0] ?? null,
    points: periods.map(([start_date, date, value]) => ({
      start_date,
      date,
      value,
    })),
  }
}

describe('holdings current-basket period identity', () => {
  it('allows unequal initial histories once every member shares one complete common path', () => {
    const first = holdingFixture({
      market_value: 500,
      market_value_base: 500,
      instrument_return_series_1m: returnSeries([
        ['2026-07-01', '2026-07-02', 0.01],
        ['2026-07-02', '2026-07-03', 0.02],
        ['2026-07-03', '2026-07-04', 0.03],
      ]),
    })
    const second = holdingFixture({
      line_id: 'holding:asset-2',
      instrument_core: instrumentFixture({
        instrument_id: 'asset-2',
        instrument_name: 'Beta Fund',
      }),
      market_value: 500,
      market_value_base: 500,
      instrument_return_series_1m: returnSeries([
        ['2026-07-02', '2026-07-03', 0.04],
        ['2026-07-03', '2026-07-04', 0.05],
      ]),
    })
    const workspace = holdingsWorkspaceFixture({ rows: [first, second] })

    const result = groupedReturnSeries(
      [first, second],
      workspace,
      (row) => row.instrument_return_series_1m,
    )

    expect(result).toEqual({
      dates: ['2026-07-03', '2026-07-04'],
      returns: [0.03, 0.04],
      firstReturnStartDate: '2026-07-02',
    })
  })

  it('fails closed when a member is missing a period inside the common path', () => {
    const first = holdingFixture({
      market_value: 500,
      market_value_base: 500,
      instrument_return_series_1m: returnSeries([
        ['2026-07-01', '2026-07-02', 0.01],
        ['2026-07-02', '2026-07-03', 0.02],
        ['2026-07-03', '2026-07-04', 0.03],
        ['2026-07-04', '2026-07-05', 0.04],
      ]),
    })
    const second = holdingFixture({
      line_id: 'holding:asset-2',
      instrument_core: instrumentFixture({
        instrument_id: 'asset-2',
        instrument_name: 'Beta Fund',
      }),
      market_value: 500,
      market_value_base: 500,
      instrument_return_series_1m: returnSeries([
        ['2026-07-02', '2026-07-03', 0.05],
        ['2026-07-04', '2026-07-05', 0.06],
      ]),
    })
    const workspace = holdingsWorkspaceFixture({ rows: [first, second] })

    expect(
      groupedReturnSeries(
        [first, second],
        workspace,
        (row) => row.instrument_return_series_1m,
      ),
    ).toBeNull()
  })

  it('fails closed when every member shares the same internal period gap', () => {
    const periods: Array<[string, string, number]> = [
      ['2026-07-01', '2026-07-02', 0.01],
      ['2026-07-03', '2026-07-04', 0.02],
    ]
    const first = holdingFixture({
      market_value: 500,
      market_value_base: 500,
      instrument_return_series_1m: returnSeries(periods),
    })
    const second = holdingFixture({
      line_id: 'holding:asset-2',
      instrument_core: instrumentFixture({
        instrument_id: 'asset-2',
        instrument_name: 'Beta Fund',
      }),
      market_value: 500,
      market_value_base: 500,
      instrument_return_series_1m: returnSeries(periods),
    })
    const workspace = holdingsWorkspaceFixture({ rows: [first, second] })

    expect(
      groupedReturnSeries(
        [first, second],
        workspace,
        (row) => row.instrument_return_series_1m,
      ),
    ).toBeNull()
  })

  it('withholds grouped volatility and drawdown for a uniformly stale tail', () => {
    const start = new Date('2026-06-20T00:00:00Z')
    const periods = Array.from({ length: 30 }, (_, index) => {
      const periodStart = new Date(start)
      periodStart.setUTCDate(start.getUTCDate() + index)
      const periodEnd = new Date(start)
      periodEnd.setUTCDate(start.getUTCDate() + index + 1)
      return [
        periodStart.toISOString().slice(0, 10),
        periodEnd.toISOString().slice(0, 10),
        index % 2 === 0 ? 0.01 : -0.005,
      ] as [string, string, number]
    })
    const series = returnSeries(periods)
    const holding = holdingFixture({
      market_value: 1_000,
      market_value_base: 1_000,
      instrument_return_series_1m: series,
      instrument_return_series_all: series,
    })
    const workspace = holdingsWorkspaceFixture({
      as_of_date: '2026-07-28',
      rows: [holding],
    })

    expect(groupedAnnualizedVolatility([holding], workspace, '1m')).toBeNull()
    expect(groupedCurrentDrawdown([holding], workspace)).toBeNull()
    expect(groupedMaxDrawdown([holding], workspace)).toBeNull()
  })
})
