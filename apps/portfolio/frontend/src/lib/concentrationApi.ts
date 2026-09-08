import { requestPortfolioResource } from './api'

export type ConcentrationScopeKind = 'security' | 'fcn' | 'taxonomy'
export type ConcentrationRule = {
  rule_id: string
  scope: ConcentrationScopeKind
  taxonomy_id: string | null
  entity_id: string | null
  watch_weight: number | null
  limit_weight: number | null
  enabled: boolean
}
export type FcnAllocation = {
  contract_id: string
  method: 'equal' | 'custom'
  weights: Array<{ instrument_id: string; weight: number }>
}
export type ConcentrationSettingsRecord = {
  portfolio_id: string
  revision: number
  effective_from: string | null
  rules: ConcentrationRule[]
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
  watch_weight: number | null
  limit_weight: number | null
  status: 'within' | 'watch' | 'breached' | 'unconfigured' | 'unavailable'
  headroom_weight: number | null
  rule_id: string | null
  sources: ConcentrationSource[]
  coverage: string[]
}
export type ConcentrationScope = {
  scope: ConcentrationScopeKind
  taxonomy_id: string | null
  name: string
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
export function getConcentrationSettings(portfolioId: string) {
  return requestPortfolioResource<ConcentrationSettingsRecord>(`${path(portfolioId)}/settings`)
}
export function saveConcentrationSettings(portfolioId: string, input: {
  expected_revision: number
  effective_from: string
  rules: ConcentrationRule[]
  fcn_allocations: FcnAllocation[]
}) {
  return requestPortfolioResource<ConcentrationSettingsRecord>(`${path(portfolioId)}/settings`, { method: 'PUT', body: JSON.stringify(input) })
}

export function concentrationScopeKey(scope: Pick<ConcentrationScope, 'scope' | 'taxonomy_id'>) {
  return scope.scope === 'taxonomy' ? `taxonomy:${scope.taxonomy_id}` : scope.scope
}
