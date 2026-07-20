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
  returnSemantics: string | null | undefined = null,
): PerformanceBenchmarkBasisAssessment {
  const basis = String(chartBasis ?? '').trim().toLowerCase() || null
  const semantics = String(returnSemantics ?? '').trim().toLowerCase() || 'unknown'
  if (!basis) {
    return {
      basis: null,
      comparisonEligible: false,
      label: 'Unavailable',
      warning:
        'Benchmark basis is unavailable. Portfolio-relative metrics are withheld because total-return comparability cannot be verified.',
    }
  }

  if (CONFIRMED_TOTAL_RETURN_BASES.has(basis) || semantics === 'total_return') {
    return {
      basis,
      comparisonEligible: true,
      label: `${displayBasis(basis)} · confirmed total-return basis`,
      warning: null,
    }
  }

  if (semantics === 'price_return') {
    return {
      basis,
      comparisonEligible: true,
      label: `${displayBasis(basis)} · confirmed price-return basis`,
      warning: `Benchmark uses ${basis} with confirmed price-return semantics. The series is rebased, and benchmark and relative metrics are shown. Portfolio TWR includes income while this index may not, so excess return and relative statistics include that basis difference.`,
    }
  }

  return {
    basis,
    comparisonEligible: false,
    label: `${displayBasis(basis)} · price / valuation basis`,
    warning: `Benchmark uses ${basis}, not a confirmed total-return basis. The series is rebased and standalone benchmark metrics are shown. Portfolio-relative differences and relative statistics are withheld rather than comparing portfolio TWR with an unconfirmed price / valuation return.`,
  }
}
