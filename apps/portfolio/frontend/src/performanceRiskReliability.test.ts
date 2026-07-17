import { describe, expect, it } from 'vitest'

import {
  realizedRiskEstimateIsLowSample,
  realizedRiskContributionResidual,
  realizedRiskMetricsAvailable,
} from './lib/performanceRiskReliability'

describe('performance realized-risk reliability', () => {
  it('withholds portfolio risk totals and residuals when no risk sample exists', () => {
    const available = realizedRiskMetricsAvailable(0, null)

    expect(available).toBe(false)
    expect(realizedRiskContributionResidual([null], available)).toBeNull()
  })

  it('reconciles risk contribution only when the realized risk estimate exists', () => {
    const available = realizedRiskMetricsAvailable(21, 0.0575)

    expect(available).toBe(true)
    expect(realizedRiskContributionResidual([0.6, 0.25, 0.15], available)).toBeCloseTo(0, 12)
  })

  it('flags a computed realized-risk estimate with too few aligned periods', () => {
    expect(realizedRiskEstimateIsLowSample(1)).toBe(false)
    expect(realizedRiskEstimateIsLowSample(4)).toBe(true)
    expect(realizedRiskEstimateIsLowSample(12)).toBe(false)
  })
})
