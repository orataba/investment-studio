import type { PortfolioHoldingRow } from './api'

const NON_MARKET_VALUATION_BASES = new Set(['carried_cost', 'premium_liability'])
const EVENT_COVERAGE_STATES = new Set(['event-cost', 'event-liability'])

type HoldingValuationIdentity = {
  valuation_basis?: string | null
  coverage_status: string
}

export function isOptionObligationHolding(
  row: Pick<PortfolioHoldingRow, 'holding_kind'>,
) {
  return row.holding_kind === 'option_obligation'
}

export function holdingUsesEventValuation(row: HoldingValuationIdentity) {
  return (
    NON_MARKET_VALUATION_BASES.has(row.valuation_basis ?? '') ||
    EVENT_COVERAGE_STATES.has(row.coverage_status)
  )
}

export function holdingDayChangeUnavailable(row: PortfolioHoldingRow) {
  return holdingUsesEventValuation(row)
}

export function holdingDayChangeExportValue(
  row: PortfolioHoldingRow,
  value: number | null | undefined,
) {
  return holdingDayChangeUnavailable(row) ? 'N/A' : value ?? null
}

export function holdingForwardRiskShare(row: PortfolioHoldingRow): number | null {
  if (row.forward_risk_status === 'modeled_zero' || row.forward_risk_status === 'no_exposure') return 0
  return row.forward_risk_status === 'ok' && typeof row.forward_risk_share === 'number' && Number.isFinite(row.forward_risk_share)
    ? row.forward_risk_share : null
}
