import { buildAssetMixSummary } from './components/OverviewAssetMix'
import { describe, expect, it } from 'vitest'
import { buildCurrentInstrumentReturnSeries } from './pages/RiskPage'
import { returnWindowInputIssues } from './lib/riskWindowData'
import { holdingFixture, holdingsWorkspaceFixture, instrumentFixture } from './test/portfolioFixtures'
import type { RiskReturnSeries } from './lib/api'

const points = [
  { start_date: '2026-07-12', date: '2026-07-13', value: 0.02 },
  { start_date: '2026-07-13', date: '2026-07-14', value: 0.03 },
  { start_date: '2026-07-14', date: '2026-07-15', value: -0.01 },
]
const converted: RiskReturnSeries = { currency: 'USD', source_currency: 'HKD', source_instrument_ids: ['fx:HKDUSD'], points }
const security = () => holdingFixture({
  instrument_core: instrumentFixture({ currency: 'HKD' }),
  instrument_return_series_all: { points: points.map((p) => ({ ...p, value: 0.9 })) },
  risk_return_series: converted,
})

describe('base-currency risk payload boundary', () => {
  it('uses historical converted returns and leaves native asset trends untouched', () => {
    const row = security()
    const result = buildCurrentInstrumentReturnSeries(holdingsWorkspaceFixture({ rows: [row] }))
    expect(result.errors).toEqual([])
    expect([...result.value[0].returnsByDate.values()]).toEqual([0.02, 0.03, -0.01])
    expect(row.instrument_return_series_all!.points.map((p) => p.value)).toEqual([0.9, 0.9, 0.9])
  })

  it.each(['USD', 'HKD'])('does not fall back to native returns when the risk payload is missing (%s)', (currency) => {
    const row = security(); row.instrument_core!.currency = currency; row.risk_return_series = null
    const result = buildCurrentInstrumentReturnSeries(holdingsWorkspaceFixture({ rows: [row] }))
    expect(result.value).toEqual([])
    expect(result.errors.join(' ')).toContain('base-currency risk return series')
  })

  it('rejects an explicitly wrong risk currency', () => {
    const row = security(); row.risk_return_series = { ...converted, currency: 'HKD' }
    expect(buildCurrentInstrumentReturnSeries(holdingsWorkspaceFixture({ rows: [row] })).errors.join(' ')).toContain('HKD while the portfolio base currency is USD')
  })

  it('preserves missing FX periods as unavailable, including an adjacent null return', () => {
    const row = security(); row.risk_return_series = { ...converted, points: points.map((p, i) => ({ ...p, value: i > 0 ? null : p.value })) }
    const result = buildCurrentInstrumentReturnSeries(holdingsWorkspaceFixture({ rows: [row] }))
    expect(result.errors).toEqual([])
    expect([...result.value[0].returnsByDate.values()]).toEqual([0.02])
    expect(result.value[0].inputPoints).toHaveLength(3)
    expect(returnWindowInputIssues(result.value[0], '2026-07-15', 30)).toEqual(expect.arrayContaining([
      expect.objectContaining({ reason: 'missing_series', missingDates: ['2026-07-14', '2026-07-15'] }),
    ]))
  })

  it('retains foreign cash and a signed payable as distinct row identities despite accounting risk flags', () => {
    const monetary = (line_id: string, allocation: number) => holdingFixture({
      line_id, holding_category: 'cash_and_settlement', holding_kind: allocation > 0 ? 'settled_cash' : 'settlement_payable',
      instrument_core: instrumentFixture({ instrument_id: 'cash:HKD', instrument_type: 'cash', currency: 'HKD' }),
      risk_eligible: false, allocation, market_value_base: allocation * 1000, risk_return_series: converted,
    })
    const rows = [monetary('cash:account-a', 0.3), monetary('pending:account-a', -0.1)]
    const result = buildCurrentInstrumentReturnSeries(holdingsWorkspaceFixture({ rows }))
    expect(result.errors).toEqual([])
    expect(result.value.map((item) => [item.groupKey, item.latestWeight, item.isCashExposure])).toEqual([
      ['cash:account-a', 0.3, true], ['pending:account-a', -0.1, true],
    ])
    rows[1].risk_return_series = null
    expect(buildCurrentInstrumentReturnSeries(holdingsWorkspaceFixture({ rows })).value).toEqual([])
  })

  it('requires complete source-gap metadata for a contributing FX source', () => {
    const workspace = holdingsWorkspaceFixture({ rows: [security()] })
    workspace.risk_basis = { ...workspace.risk_basis!, gap_instrument_ids: ['fx:HKDUSD'] }
    expect(buildCurrentInstrumentReturnSeries(workspace).errors.join(' ')).toContain('Complete source-gap dates are missing')
  })
  it('includes signed foreign monetary contributions in the overview cash group and portfolio total', () => {
    const cash = (line_id: string, market_value_base: number, forward_risk_share: number) => holdingFixture({
      line_id, market_value_base, allocation: market_value_base / 1000,
      holding_category: 'cash_and_settlement', risk_eligible: false,
      instrument_core: instrumentFixture({ instrument_id: 'cash:HKD', instrument_type: 'cash', currency: 'HKD' }),
      forward_risk_status: 'ok', forward_risk_share,
    })
    const workspace = holdingsWorkspaceFixture({ rows: [
      holdingFixture({ market_value_base: 800, forward_risk_share: 0.7 }),
      cash('cash:account-a', 300, 0.4), cash('pending:account-a', -100, -0.1),
    ] })
    const summary = buildAssetMixSummary(workspace)
    expect(summary.categories.find((row) => row.key === 'cash_and_settlement')?.forwardRiskShare).toBeCloseTo(0.3)
    expect(summary.total.forwardRiskShare).toBeCloseTo(1)
    workspace.rows[2].forward_risk_status = 'unavailable'
    workspace.rows[2].forward_risk_share = null
    expect(buildAssetMixSummary(workspace).categories.find((row) => row.key === 'cash_and_settlement')?.forwardRiskShare).toBeNull()
  })

})
