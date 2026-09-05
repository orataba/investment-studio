import {
  assessPerformanceBenchmarkBasis,
  type PerformanceBenchmarkBasisAssessment,
} from './performanceBenchmarkBasis'

export type BenchmarkComparisonMode = 'canonical' | 'exploratory' | 'unavailable'

export type BenchmarkComparisonGuardReason =
  | 'benchmark_total_return_comparable'
  | 'benchmark_price_return_comparable'
  | 'benchmark_basis_unconfirmed_comparable'
  | 'benchmark_basis_unavailable'
  | 'benchmark_currency_unavailable'
  | 'benchmark_currency_mismatch'
  | 'benchmark_start_anchor_missing'
  | 'benchmark_date_coverage_unavailable'
  | 'benchmark_date_coverage_incomplete'

export type BenchmarkComparisonGuard = {
  mode: BenchmarkComparisonMode
  reason: BenchmarkComparisonGuardReason
  canonicalComparisonEligible: boolean
  relativeComparisonEligible: boolean
  basisAssessment: PerformanceBenchmarkBasisAssessment
  benchmarkCurrency: string
  portfolioCurrency: string
  missingEligibleDates: string[]
  warning: string | null
}

type BenchmarkPoint = {
  date: string
  value: number
}

type BenchmarkComparisonGuardInput = {
  chartBasis: string | null | undefined
  returnSemantics?: string | null
  benchmarkCurrency: string | null | undefined
  portfolioCurrency: string | null | undefined
  points: BenchmarkPoint[]
  startBoundaryDate: string
  eligiblePortfolioDates: string[]
}

export function normalizeBenchmarkCurrency(value: string | null | undefined) {
  return String(value ?? '').trim().toUpperCase()
}

function unavailableAssessment(
  basisAssessment: PerformanceBenchmarkBasisAssessment,
  reason: BenchmarkComparisonGuardReason,
  benchmarkCurrency: string,
  portfolioCurrency: string,
  warning: string,
  missingEligibleDates: string[] = [],
): BenchmarkComparisonGuard {
  return {
    mode: 'unavailable',
    reason,
    canonicalComparisonEligible: false,
    relativeComparisonEligible: false,
    basisAssessment,
    benchmarkCurrency,
    portfolioCurrency,
    missingEligibleDates,
    warning,
  }
}

export function assessBenchmarkComparisonGuard({
  chartBasis,
  returnSemantics,
  benchmarkCurrency: rawBenchmarkCurrency,
  portfolioCurrency: rawPortfolioCurrency,
  points,
  startBoundaryDate,
  eligiblePortfolioDates,
}: BenchmarkComparisonGuardInput): BenchmarkComparisonGuard {
  const basisAssessment = assessPerformanceBenchmarkBasis(chartBasis, returnSemantics)
  const benchmarkCurrency = normalizeBenchmarkCurrency(rawBenchmarkCurrency)
  const portfolioCurrency = normalizeBenchmarkCurrency(rawPortfolioCurrency)

  if (!basisAssessment.basis) {
    return unavailableAssessment(
      basisAssessment,
      'benchmark_basis_unavailable',
      benchmarkCurrency,
      portfolioCurrency,
      basisAssessment.warning ?? 'Benchmark basis is unavailable.',
    )
  }
  if (!benchmarkCurrency || !portfolioCurrency) {
    return unavailableAssessment(
      basisAssessment,
      'benchmark_currency_unavailable',
      benchmarkCurrency,
      portfolioCurrency,
      'Benchmark comparison is unavailable because benchmark and portfolio currencies must both be identified.',
    )
  }
  if (benchmarkCurrency !== portfolioCurrency) {
    return unavailableAssessment(
      basisAssessment,
      'benchmark_currency_mismatch',
      benchmarkCurrency,
      portfolioCurrency,
      `Benchmark currency ${benchmarkCurrency} does not match portfolio base ${portfolioCurrency}.`,
    )
  }

  const finitePoints = points.filter((point) => Number.isFinite(point.value))
  if (!finitePoints.some((point) => point.date <= startBoundaryDate)) {
    return unavailableAssessment(
      basisAssessment,
      'benchmark_start_anchor_missing',
      benchmarkCurrency,
      portfolioCurrency,
      `Benchmark comparison is unavailable because no benchmark observation exists on or before ${startBoundaryDate}.`,
    )
  }

  const uniqueEligibleDates = [...new Set(eligiblePortfolioDates)].sort()
  if (!uniqueEligibleDates.length) {
    return unavailableAssessment(
      basisAssessment,
      'benchmark_date_coverage_unavailable',
      benchmarkCurrency,
      portfolioCurrency,
      'Benchmark comparison is unavailable because the portfolio has no eligible return observations in the selected period.',
    )
  }
  const benchmarkDates = new Set(finitePoints.map((point) => point.date))
  const missingEligibleDates = uniqueEligibleDates.filter((dateKey) => !benchmarkDates.has(dateKey))
  if (missingEligibleDates.length) {
    return unavailableAssessment(
      basisAssessment,
      'benchmark_date_coverage_incomplete',
      benchmarkCurrency,
      portfolioCurrency,
      `Benchmark comparison is unavailable because ${missingEligibleDates.length} eligible portfolio return date${
        missingEligibleDates.length === 1 ? ' is' : 's are'
      } missing from benchmark history.`,
      missingEligibleDates,
    )
  }

  const totalReturn = basisAssessment.returnSemantics === 'total_return'
  return {
    mode: totalReturn ? 'canonical' : 'exploratory',
    reason: totalReturn
      ? 'benchmark_total_return_comparable'
      : basisAssessment.returnSemantics === 'price_return'
        ? 'benchmark_price_return_comparable'
        : 'benchmark_basis_unconfirmed_comparable',
    canonicalComparisonEligible: totalReturn,
    relativeComparisonEligible: true,
    basisAssessment,
    benchmarkCurrency,
    portfolioCurrency,
    missingEligibleDates: [],
    warning: basisAssessment.warning,
  }
}
