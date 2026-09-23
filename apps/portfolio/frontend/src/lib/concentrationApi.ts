import { requestPortfolioResource } from './api'

export type ConcentrationScopeKind = 'security' | 'fcn' | 'taxonomy'
export type ConcentrationLimit = {
  scope: ConcentrationScopeKind
  taxonomy_id: string | null
  entity_id: string
  limit_weight: number | null
}
export type FcnAllocation = {
  contract_id: string
  method: 'equal' | 'custom'
  weights: Array<{ instrument_id: string; weight: number }>
}
export type ConcentrationSettingsRecord = {
  portfolio_id: string
  revision: number
  latest_revision: number
  effective_from: string | null
  enabled_taxonomy_ids: string[]
  limits: ConcentrationLimit[]
  fcn_allocations: FcnAllocation[]
}
export type ConcentrationSettingsInput = {
  expected_revision: number
  effective_from: string
  enabled_taxonomy_ids: string[]
  limits: ConcentrationLimit[]
  fcn_allocations: FcnAllocation[]
}
export type ConcentrationSource = {
  source_id: string
  title: string
  instrument_id?: string
  contract_id?: string
  amount_base: number | null
  detail_path?: string
  allocation_weight?: number
  allocation_method?: 'equal' | 'custom'
}
export type ConcentrationRow = {
  entity_id: string
  name: string
  depth: number
  parent_entity_id: string | null
  exposure_base: number | null
  known_exposure_base: number
  lower_bound_weight: number | null
  weight: number | null
  security_exposure_base: number | null
  fcn_exposure_base: number | null
  limit_weight: number | null
  status: 'within' | 'breached' | 'unconfigured' | 'unavailable'
  headroom_weight: number | null
  sources: ConcentrationSource[]
  coverage: string[]
}
export type ConcentrationScope = {
  scope: ConcentrationScopeKind
  taxonomy_id: string | null
  name: string
  enabled: boolean
  rows: ConcentrationRow[]
  status: 'complete' | 'partial' | 'unavailable'
  coverage: string[]
}
export type ConcentrationResponse = {
  portfolio_id: string
  as_of_date: string
  base_currency: string
  nav: number | null
  weight_basis: 'portfolio_nav'
  valuation_basis: 'operating_book'
  excluded_option_positions: number
  status: 'complete' | 'partial' | 'unavailable'
  settings_revision: number
  settings_effective_from?: string | null
  scopes: ConcentrationScope[]
  coverage: string[]
  sources: ConcentrationSource[]
  fcn_contracts: Array<{
    contract_id: string
    name: string
    underlyings: Array<{ instrument_id: string; name: string }>
    allocation: FcnAllocation
  }>
}

const path = (portfolioId: string) => `/api/portfolios/${encodeURIComponent(portfolioId)}/concentration`
export function getConcentration(portfolioId: string, asOfDate?: string) {
  return requestPortfolioResource<ConcentrationResponse>(`${path(portfolioId)}${asOfDate ? `?as_of_date=${encodeURIComponent(asOfDate)}` : ''}`)
}
export function getConcentrationSettings(portfolioId: string, asOfDate?: string) {
  return requestPortfolioResource<ConcentrationSettingsRecord>(`${path(portfolioId)}/settings${asOfDate ? `?as_of_date=${encodeURIComponent(asOfDate)}` : ''}`)
}
export function saveConcentrationSettings(portfolioId: string, input: ConcentrationSettingsInput) {
  return requestPortfolioResource<ConcentrationSettingsRecord>(`${path(portfolioId)}/settings`, { method: 'PUT', body: JSON.stringify(input) })
}

export function concentrationScopeKey(scope: Pick<ConcentrationScope, 'scope' | 'taxonomy_id'>) {
  return scope.scope === 'taxonomy' ? `taxonomy:${scope.taxonomy_id}` : scope.scope
}
