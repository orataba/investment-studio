import type { PortfolioPerformanceHistoryReliability } from './api'

export function selectPerformanceHistoryReliability(
  summaryReliability: PortfolioPerformanceHistoryReliability | null | undefined,
  comparisonReliability: PortfolioPerformanceHistoryReliability | null | undefined,
) {
  return comparisonReliability ?? summaryReliability ?? null
}

export function annualizedReturnDisplayEligible(
  reliability: PortfolioPerformanceHistoryReliability | null | undefined,
) {
  return reliability?.annualized_return_eligible === true
}
