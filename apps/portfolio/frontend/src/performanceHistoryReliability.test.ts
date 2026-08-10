import { describe, expect, it } from 'vitest'

import { buildPerformanceHistoryReliability } from './lib/performanceHistoryReliability'
import performancePageSource from './pages/PerformancePage.tsx?raw'

describe('performance short-history reliability policy', () => {
  it('marks a sub-year observed period as ineligible for annualized return headlines', () => {
    const profile = buildPerformanceHistoryReliability({
      start_date: '2026-03-31',
      end_date: '2026-07-09',
      snapshot_count: 101,
      return_observation_count: 101,
      risk_return_observation_count: 68,
      annualization_eligible: false,
      annualization_unavailable_reason: 'measurement_period_shorter_than_one_year',
    })

    expect(profile.elapsedDays).toBe(100)
    expect(profile.calendarSpanDays).toBe(101)
    expect(profile.annualizedReturnEligible).toBe(false)
    expect(profile.sampleLabel).toContain('2026-03-31 to 2026-07-09')
    expect(profile.sampleLabel).toContain('101 snapshots')
    expect(profile.sampleLabel).toContain('68 risk observations')
    expect(profile.annualizationMessage).toContain('Period TWR remains the primary return')
  })

  it('uses the canonical backend gate after a full calendar-anniversary year has elapsed', () => {
    const profile = buildPerformanceHistoryReliability({
      start_date: '2025-07-08',
      end_date: '2026-07-09',
      snapshot_count: 252,
      return_observation_count: 252,
      risk_return_observation_count: 251,
      annualization_eligible: true,
      annualization_unavailable_reason: null,
    })

    expect(profile.elapsedDays).toBe(366)
    expect(profile.annualizedReturnEligible).toBe(true)
    expect(profile.annualizationMessage).toBeNull()
  })

  it('wires period return, N/A annualization, and the observed-sample banner into Performance', () => {
    expect(performancePageSource).toContain('metric: summary.performance_label')
    expect(performancePageSource).toContain('const showReturnComparison = showRiskComparison && !operationalReturn')
    expect(performancePageSource).toContain("metric: 'Market Risk Volatility'")
    expect(performancePageSource).toContain('summary.market_risk_cumulative_return')
    expect(performancePageSource).not.toContain('Annualized Operational Return')
    expect(performancePageSource).not.toContain('Mean Daily Operational Return')
    expect(performancePageSource).toContain("metric: 'Derivative Lifecycle Realized P&L'")
    expect(performancePageSource).toContain("metric: 'IRR / MWRR'")
    expect(performancePageSource).toContain("annualizedReturnEligible ? signedPercent(summary.annualized_twr) : 'N/A'")
    expect(performancePageSource).toContain('performance-history-reliability-warning')
    expect(performancePageSource).toContain('performanceMetricsMeta')
  })
})
