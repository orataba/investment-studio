const CONFIRMED_TOTAL_RETURN_BASES = new Set([
  'adjusted_close',
  'total_return_nav',
])

export type PerformanceBenchmarkBasisAssessment = {
  basis: string | null
  returnSemantics: 'total_return' | 'price_return' | 'unknown'
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
      returnSemantics: 'unknown',
      label: 'Unavailable',
      warning:
        'Benchmark has no available price or NAV basis.',
    }
  }

  if (CONFIRMED_TOTAL_RETURN_BASES.has(basis) || semantics === 'total_return') {
    return {
      basis,
      returnSemantics: 'total_return',
      label: `${displayBasis(basis)} · confirmed total-return basis`,
      warning: null,
    }
  }

  if (semantics === 'price_return') {
    return {
      basis,
      returnSemantics: 'price_return',
      label: `${displayBasis(basis)} · confirmed price-return basis`,
      warning: 'Comparison uses the selected price-return series. Distributions are excluded from this benchmark, so relative results include that difference.',
    }
  }

  return {
    basis,
    returnSemantics: 'unknown',
    label: `${displayBasis(basis)} · price / valuation basis`,
    warning: 'Comparison uses the selected price or NAV series. Distribution treatment is unconfirmed and may affect relative results.',
  }
}
