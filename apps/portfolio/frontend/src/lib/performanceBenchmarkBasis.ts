const CONFIRMED_TOTAL_RETURN_BASES = new Set([
  'adjusted_close',
  'total_return_nav',
])

export type PerformanceBenchmarkBasisAssessment = {
  basis: string | null
  comparisonEligible: boolean
  label: string
  warning: string | null
}

function displayBasis(value: string) {
  return value.replace(/_/g, ' ')
}

export function assessPerformanceBenchmarkBasis(
  chartBasis: string | null | undefined,
): PerformanceBenchmarkBasisAssessment {
  const basis = String(chartBasis ?? '').trim().toLowerCase() || null
  if (!basis) {
    return {
      basis: null,
      comparisonEligible: false,
      label: 'Unavailable',
      warning:
        'Benchmark basis is unavailable. Portfolio-relative metrics are withheld because total-return comparability cannot be verified.',
    }
  }

  if (CONFIRMED_TOTAL_RETURN_BASES.has(basis)) {
    return {
      basis,
      comparisonEligible: true,
      label: `${displayBasis(basis)} · confirmed total-return basis`,
      warning: null,
    }
  }

  return {
    basis,
    comparisonEligible: false,
    label: `${displayBasis(basis)} · price / valuation basis`,
    warning: `Benchmark uses ${basis}, not a confirmed total-return basis. Portfolio-relative differences and relative statistics are withheld rather than comparing portfolio TWR with a price / valuation return.`,
  }
}
