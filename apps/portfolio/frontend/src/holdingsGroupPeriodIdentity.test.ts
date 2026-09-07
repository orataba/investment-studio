import { describe, expect, it, vi } from 'vitest'

import {
  groupedAnnualizedVolatility,
  groupedCurrentDrawdown,
  groupedMaxDrawdown,
  groupedReturnSeries,
  buildHoldingsGroupRiskMetrics,
} from './pages/PortfolioHomePage'
import {
  holdingFixture,
  holdingsWorkspaceFixture,
  instrumentFixture,
  optionContractFixture,
} from './test/portfolioFixtures'

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
  it('builds each group risk result with the same values and missing-period rules as the existing calculations', () => {
    function seriesFor(offset: number, missingPeriod: boolean) {
      const end = Date.parse('2026-07-15T00:00:00Z')
      const periods = Array.from({ length: 380 }, (_, index): [string, string, number] => [
        new Date(end - (380 - index) * 86_400_000).toISOString().slice(0, 10),
        new Date(end - (379 - index) * 86_400_000).toISOString().slice(0, 10),
        index % 3 === 0 ? -0.02 - offset : 0.01 + offset,
      ]).filter((_period, index) => !missingPeriod || index !== 370)
      return {
        instrument_return_series_1m: returnSeries(periods.slice(-35)),
        instrument_return_series_3m: returnSeries(periods.slice(-100)),
        instrument_return_series_6m: returnSeries(periods.slice(-190)),
        instrument_return_series_1y: returnSeries(periods),
        instrument_return_series_all: returnSeries(periods),
      }
    }
    for (const missingPeriod of [false, true]) {
      const rows = [
        holdingFixture({ market_value: 600, market_value_base: 600, ...seriesFor(0, false) }),
        holdingFixture({
          line_id: 'holding:asset-2', market_value: 400, market_value_base: 400,
          ...seriesFor(0.003, missingPeriod),
        }),
      ]
      const workspace = holdingsWorkspaceFixture({ rows })
      const result = buildHoldingsGroupRiskMetrics(rows, workspace)
      for (const range of ['1m', '3m', '6m', '1y'] as const) {
        expect(result.volatility[range]).toBe(groupedAnnualizedVolatility(rows, workspace, range))
        if (missingPeriod) expect(result.volatility[range]).toBeNull()
        else expect(result.volatility[range]).toBeGreaterThan(0)
      }
      expect(result.currentDrawdown).toBe(groupedCurrentDrawdown(rows, workspace))
      expect(result.maxDrawdown).toBe(groupedMaxDrawdown(rows, workspace))
      if (missingPeriod) {
        expect(result.currentDrawdown).toBeNull()
        expect(result.maxDrawdown).toBeNull()
      } else {
        expect(result.maxDrawdown).toBeLessThan(0)
      }
    }
  })

  it('normalizes the full-history series once for both drawdown values', () => {
    const series = returnSeries([
      ['2026-07-12', '2026-07-13', 0.1],
      ['2026-07-13', '2026-07-14', -0.2],
      ['2026-07-14', '2026-07-15', 0.05],
    ])
    const readPoints = vi.fn(() => series.points)
    const row = holdingFixture({
      instrument_return_series_all: { ...series, get points() { return readPoints() } },
    })
    const result = buildHoldingsGroupRiskMetrics([row], holdingsWorkspaceFixture({ rows: [row] }))

    // normalizedReturnSeries checks then reads points: two property reads per alignment.
    expect(readPoints).toHaveBeenCalledTimes(2)
    expect(result.currentDrawdown).toBeCloseTo(-0.16)
    expect(result.maxDrawdown).toBeCloseTo(-0.2)
  })

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

  it('excludes derivatives instead of presenting zero volatility', () => {
    const derivative = holdingFixture({
      line_id: 'holding:option-1',
      holding_category: 'derivatives',
      instrument_core: null,
      derivative_contract_id: 'option-1',
      derivative_contract: optionContractFixture(),
      valuation_basis: 'carried_cost',
      coverage_status: 'event-cost',
      market_value: 500,
      market_value_base: 500,
      risk_eligible: false,
      forward_risk_status: 'excluded',
      forward_risk_share: null,
    })
    const workspace = holdingsWorkspaceFixture({ rows: [derivative] })

    expect(groupedAnnualizedVolatility([derivative], workspace, '1m')).toBeNull()
    expect(groupedCurrentDrawdown([derivative], workspace)).toBeNull()
    expect(groupedMaxDrawdown([derivative], workspace)).toBeNull()
  })

  it('withholds aggregate risk when there is no market-risk-bearing asset', () => {
    const basePending = holdingFixture({
      line_id: 'pending:USD',
      holding_category: 'cash_and_settlement',
      holding_kind: 'settlement_receivable',
      instrument_core: {
        ...instrumentFixture(),
        instrument_id: 'pending:USD',
        instrument_name: 'Pending USD',
        instrument_type: 'cash',
        currency: 'USD',
      },
      market_value: 100,
      market_value_base: 100,
      forward_risk_status: 'modeled_zero',
      forward_risk_share: 0,
      risk_eligible: false,
    })
    const foreignPending = holdingFixture({
      ...basePending,
      line_id: 'pending:HKD',
      instrument_core: {
        ...basePending.instrument_core!,
        instrument_id: 'pending:HKD',
        instrument_name: 'Pending HKD',
        currency: 'HKD',
      },
      forward_risk_status: 'pending_settlement',
      forward_risk_share: null,
    })

    expect(
      groupedAnnualizedVolatility(
        [basePending],
        holdingsWorkspaceFixture({ rows: [basePending] }),
        '1m',
      ),
    ).toBeNull()
    expect(
      groupedAnnualizedVolatility(
        [foreignPending],
        holdingsWorkspaceFixture({ rows: [foreignPending] }),
        '1m',
      ),
    ).toBeNull()
  })

  it('withholds a non-base current-basket path until a base-currency return overlay exists', () => {
    const security = holdingFixture({
      market_value: 600,
      market_value_base: 600,
      instrument_core: instrumentFixture({ currency: 'HKD' }),
      instrument_return_series_1m: returnSeries([
        ['2026-07-01', '2026-07-02', 0.1],
        ['2026-07-02', '2026-07-03', -0.05],
      ]),
    })
    const derivative = holdingFixture({
      line_id: 'holding:option-zero-return-capital',
      holding_category: 'derivatives',
      instrument_core: null,
      derivative_contract_id: 'option-zero-return-capital',
      derivative_contract: optionContractFixture(),
      market_value: 400,
      market_value_base: 400,
      valuation_basis: 'carried_cost',
      coverage_status: 'event-cost',
      risk_eligible: false,
      forward_risk_status: 'excluded',
      forward_risk_share: null,
    })
    const workspace = holdingsWorkspaceFixture({ rows: [security, derivative] })

    const result = groupedReturnSeries(
      [security, derivative],
      workspace,
      (row) => row.instrument_return_series_1m,
      true,
    )

    expect(result).toBeNull()
  })
})
