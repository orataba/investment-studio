import type { PortfolioPerformanceHistoryReliability } from './api'

export function annualizedReturnDisplayEligible(
  reliability: PortfolioPerformanceHistoryReliability | null | undefined,
) {
  return reliability?.annualized_return_eligible === true
}
