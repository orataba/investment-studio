import { requestPortfolioResource } from './api'

export type PortfolioTailRiskRow = {
  holding_id: string
  instrument_id: string | null
  name: string
  market_value_base: number | null
  weight: number | null
  status: 'modeled' | 'excluded' | 'base_currency_cash' | 'no_exposure'
  reason: string | null
  observation_count: number
  rejected_period_count: number
  calendar_basis: string | null
  fx_instrument_ids: string[]
  fx_rejected_period_count: number
  unmatched_fx_period_count: number
  first_scenario_start_date: string | null
  last_scenario_end_date: string | null
}

export type PortfolioTailRisk = {
  portfolio_id: string
  as_of_date: string
  base_currency: string
  portfolio_nav: number | null
  status: 'available' | 'unavailable'
  coverage_status: 'partial' | 'complete'
  method: string
  horizon: string
  confidence: number
  lookback_days: number
  window_start_date: string
  window_end_date: string
  history_coverage_status: 'complete' | 'partial' | 'unavailable' | 'unverified'
  expected_common_observation_count: number | null
  uncovered_observation_count: number | null
  uncovered_leading_observation_count: number | null
  missing_internal_observation_count: number | null
  uncovered_trailing_observation_count: number | null
  actual_history_days: number | null
  history_span_fraction: number | null
  first_scenario_start_date: string | null
  last_scenario_end_date: string | null
  observation_count: number
  tail_effective_observations: number
  tail_observation_count: number
  tail_max_observation_weight: number | null
  var_amount: number | null
  var_nav_fraction: number | null
  expected_shortfall_amount: number | null
  expected_shortfall_nav_fraction: number | null
  modeled_gross_exposure: number
  excluded_gross_exposure: number | null
  modeled_gross_nav_fraction: number | null
  excluded_gross_nav_fraction: number | null
  modeled_fraction_of_known_gross: number | null
  rows: PortfolioTailRiskRow[]
  limitations: string[]
  interpretation: string
  precision_note: string
  scope_note: string
  sources?: Array<Record<string, unknown>>
}

export function getPortfolioTailRisk(portfolioId: string, filters: {
  asOfDate?: string
  confidence?: number
  lookbackDays?: number
} = {}) {
  const query = new URLSearchParams()
  if (filters.asOfDate) query.set('as_of_date', filters.asOfDate)
  query.set('confidence', String(filters.confidence ?? 0.95))
  query.set('lookback_days', String(filters.lookbackDays ?? 1095))
  return requestPortfolioResource<PortfolioTailRisk>(
    `/api/portfolios/${encodeURIComponent(portfolioId)}/tail-risk?${query.toString()}`,
  )
}
