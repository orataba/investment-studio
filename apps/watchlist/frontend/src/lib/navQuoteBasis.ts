export type NavQuoteBasis = 'nav' | 'nav_with_dividend'
export type ComparableReturnKind = 'total_return' | 'price_return' | 'unit_nav_return'

export type NavQuoteRow = {
  as_of_date: string
  nav: number | null
  nav_with_dividend: number | null
  currency?: string | null
}

export type NavQuotePoint = {
  date: string
  value: number
}

export type NavQuoteBasisContext = {
  activeBasis: NavQuoteBasis
  availableBases: NavQuoteBasis[]
  basisSeries: NavQuotePoint[]
}

const NAV_QUOTE_BASES: readonly NavQuoteBasis[] = ['nav_with_dividend', 'nav']

export function normalizeNavQuoteCurrency(currency: string | null | undefined): string {
  return String(currency ?? '').trim().toUpperCase()
}

export function returnKindsAreComparable(
  primary: ComparableReturnKind | null | undefined,
  benchmark: ComparableReturnKind | null | undefined,
): boolean {
  return primary != null && benchmark != null && primary === benchmark
}

export function filterNavQuoteRowsByCurrency(
  rows: readonly NavQuoteRow[],
  currency: string | null | undefined,
): NavQuoteRow[] {
  const normalizedCurrency = normalizeNavQuoteCurrency(currency)
  if (!normalizedCurrency) {
    return []
  }
  return rows.filter(
    (row) => normalizeNavQuoteCurrency(row.currency) === normalizedCurrency,
  )
}

export function navQuoteValueForBasis(
  row: NavQuoteRow,
  basis: NavQuoteBasis,
): number | null {
  const value = basis === 'nav' ? row.nav : row.nav_with_dividend
  return value != null && Number.isFinite(value) ? value : null
}

export function buildNavQuoteBasisSeries(
  rows: readonly NavQuoteRow[],
  basis: NavQuoteBasis,
): NavQuotePoint[] {
  return [...rows]
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
    .flatMap((row) => {
      const value = navQuoteValueForBasis(row, basis)
      return value == null ? [] : [{ date: row.as_of_date, value }]
    })
}

export function buildNavQuoteBasisContext(
  rows: readonly NavQuoteRow[],
  requestedBasis: NavQuoteBasis,
): NavQuoteBasisContext {
  const seriesByBasis = new Map(
    NAV_QUOTE_BASES.map((basis) => [basis, buildNavQuoteBasisSeries(rows, basis)] as const),
  )
  return {
    activeBasis: requestedBasis,
    availableBases: NAV_QUOTE_BASES.filter((basis) => (seriesByBasis.get(basis)?.length ?? 0) > 0),
    basisSeries: seriesByBasis.get(requestedBasis) ?? [],
  }
}
