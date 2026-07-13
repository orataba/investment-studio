import { describe, expect, it } from 'vitest'

import type { PortfolioPerformanceHistoryReliability } from './lib/api'
import {
  annualizedReturnDisplayEligible,
  selectPerformanceHistoryReliability,
} from './lib/performanceHistoryPresentation'

function reliability(
  eligible: boolean,
  sampleLabel: string,
): PortfolioPerformanceHistoryReliability {
  return {
    start_date: '2026-01-01',
    end_date: '2026-01-31',
    elapsed_days: 30,
    calendar_span_days: 31,
    minimum_history_days: 365,
    annualized_return_eligible: eligible,
    annualized_return_reason_codes: eligible
      ? []
      : ['annualized_return_history_below_minimum'],
    sample_label: sampleLabel,
    annualization_message: eligible ? null : 'Insufficient history.',
  }
}

describe('performance history presentation', () => {
  it('fails closed unless backend eligibility is explicitly true', () => {
    expect(annualizedReturnDisplayEligible(reliability(true, 'eligible'))).toBe(true)
    expect(annualizedReturnDisplayEligible(reliability(false, 'ineligible'))).toBe(false)
    expect(annualizedReturnDisplayEligible(null)).toBe(false)
    expect(annualizedReturnDisplayEligible(undefined)).toBe(false)
  })

  it('prefers comparison-window reliability when the backend supplies it', () => {
    const summaryReliability = reliability(true, 'summary window')
    const comparisonReliability = reliability(false, 'comparison window')

    expect(
      selectPerformanceHistoryReliability(
        summaryReliability,
        comparisonReliability,
      ),
    ).toBe(comparisonReliability)
    expect(
      selectPerformanceHistoryReliability(summaryReliability, undefined),
    ).toBe(summaryReliability)
  })
})
