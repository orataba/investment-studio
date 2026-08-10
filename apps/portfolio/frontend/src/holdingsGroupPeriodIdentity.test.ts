import { describe, expect, it } from 'vitest'

import {
  groupedAnnualizedVolatility,
  groupedCurrentDrawdown,
  groupedMaxDrawdown,
  groupedReturnSeries,
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

  it('uses derivative carrying value as zero-return capital without changing return-currency compatibility', () => {
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

    expect(result).toEqual({
      dates: ['2026-07-02', '2026-07-03'],
      returns: [0.06, -0.03],
      firstReturnStartDate: '2026-07-01',
    })
  })
})
