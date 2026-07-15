import type {
  DataStatus,
  InstrumentType,
  MetricFamily,
  PriceUnit,
  QuoteBasis,
  QuoteRole,
  QuoteSelectionPolicy,
} from '../../../../packages/instrument-core/ts/src'
import {
  QUOTE_BASIS_METRIC_FAMILY,
  canonicalPriceContract,
} from '../../../../packages/instrument-core/ts/src'

export type RoleQuotePoint = {
  metric_family: MetricFamily
  quote_basis: QuoteBasis
  as_of_date: string
  currency: string
  price_unit: PriceUnit
  price_scale: string
  status: DataStatus
}

export type RoleQuoteRecord<Point extends RoleQuotePoint> = {
  instrument_type: InstrumentType
  currency: string
  latest_market_data: readonly Point[]
  quote_selection_policy: QuoteSelectionPolicy
}

export const SUMMARY_QUOTE_ROLES = ['valuation', 'trading', 'total_return'] as const

export type SummaryQuoteRole = (typeof SUMMARY_QUOTE_ROLES)[number]

type BasisResolution<Point extends RoleQuotePoint> =
  | { status: 'missing' }
  | { status: 'invalid' }
  | { status: 'resolved'; point: Point }

function normalizedCurrency(value: string): string {
  return value.trim().toUpperCase()
}

function normalizedScale(value: string): number | null {
  const normalized = value.trim()
  if (!normalized) {
    return null
  }
  const scale = Number(normalized)
  return Number.isFinite(scale) && scale > 0 ? scale : null
}

function quoteIdentityKey(point: RoleQuotePoint): string {
  const scale = normalizedScale(point.price_scale)
  return JSON.stringify([
    point.metric_family,
    normalizedCurrency(point.currency),
    point.price_unit,
    scale ?? point.price_scale.trim(),
  ])
}

function resolveBasis<Point extends RoleQuotePoint>(
  record: RoleQuoteRecord<Point>,
  basis: QuoteBasis,
): BasisResolution<Point> {
  const candidates = record.latest_market_data.filter(
    (point) => point.quote_basis === basis && point.status === 'complete',
  )
  if (!candidates.length) {
    return { status: 'missing' }
  }

  if (new Set(candidates.map(quoteIdentityKey)).size !== 1) {
    return { status: 'invalid' }
  }

  const expectedMetricFamily = QUOTE_BASIS_METRIC_FAMILY[basis]
  const expectedContract = canonicalPriceContract(record.instrument_type, expectedMetricFamily)
  const candidate = candidates.reduce((latest, point) =>
    point.as_of_date >= latest.as_of_date ? point : latest,
  )
  if (
    candidate.metric_family !== expectedMetricFamily
    || normalizedCurrency(candidate.currency) !== normalizedCurrency(record.currency)
    || candidate.price_unit !== expectedContract.price_unit
    || normalizedScale(candidate.price_scale) !== Number(expectedContract.price_scale)
  ) {
    return { status: 'invalid' }
  }

  return { status: 'resolved', point: candidate }
}

export function resolveQuoteBasis<Point extends RoleQuotePoint>(
  record: RoleQuoteRecord<Point>,
  basis: QuoteBasis,
): Point | null {
  const resolution = resolveBasis(record, basis)
  return resolution.status === 'resolved' ? resolution.point : null
}

export function resolveRoleQuote<Point extends RoleQuotePoint>(
  record: RoleQuoteRecord<Point>,
  role: QuoteRole,
): Point | null {
  const policyBases = [
    ...(record.quote_selection_policy[role] || []),
    ...(record.quote_selection_policy.reference || []),
  ]
  for (const basis of new Set(policyBases)) {
    const resolution = resolveBasis(record, basis)
    if (resolution.status === 'resolved') {
      return resolution.point
    }
    if (resolution.status === 'invalid') {
      return null
    }
  }

  return null
}

export function summarizeRoleQuotes<Point extends RoleQuotePoint>(
  record: RoleQuoteRecord<Point>,
): Array<{ role: SummaryQuoteRole; point: Point }> {
  const seenBases = new Set<QuoteBasis>()
  const summary: Array<{ role: SummaryQuoteRole; point: Point }> = []

  for (const role of SUMMARY_QUOTE_ROLES) {
    const point = resolveRoleQuote(record, role)
    if (!point || seenBases.has(point.quote_basis)) {
      continue
    }
    seenBases.add(point.quote_basis)
    summary.push({ role, point })
  }

  return summary
}
