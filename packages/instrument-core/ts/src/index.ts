export type InstrumentType = 'fund' | 'etf' | 'index' | 'bond' | 'equity' | 'cash' | 'fx' | 'other'
export type ExpectedFrequency = 'daily' | 'weekly' | 'monthly' | 'event_driven'
export type SourceMode = 'manual' | 'email' | 'api'
export type IdentifierType =
  | 'ticker'
  | 'exchange_ticker'
  | 'ts_code'
  | 'isin'
  | 'cusip'
  | 'sedol'
  | 'internal'
  | 'fund_name'
  | 'cash_currency'
  | 'other'
export type MetricFamily = 'price' | 'nav' | 'fx'
export type PriceUnit = 'per_unit' | 'percent_of_par' | 'rate'
export type QuoteBasis =
  | 'last'
  | 'close'
  | 'adjusted_close'
  | 'official_nav'
  | 'total_return_nav'
  | 'cumulative_nav'
  | 'accumulated_nav'
  | 'cum_nav'
  | 'dividend_adjusted_nav'
  | 'reinvested_nav'
  | 'spot'
  | 'clean_price'
  | 'dirty_price'
  | 'par'
  | 'accrued_interest'
export type QuoteRole = 'trading' | 'valuation' | 'total_return' | 'chart' | 'reference'
export type DataStatus = 'complete' | 'partial' | 'unavailable'
export type CorporateActionStatus = 'detected' | 'confirmed' | 'cancelled'
export type QuantityRounding = 'exact' | 'truncate' | 'round_half_up' | 'cash_in_lieu'

export type PriceContract = Readonly<{
  price_unit: PriceUnit
  price_scale: string
}>

export const QUOTE_BASIS_METRIC_FAMILY: Readonly<Record<QuoteBasis, MetricFamily>> = {
  last: 'price',
  close: 'price',
  adjusted_close: 'price',
  official_nav: 'nav',
  total_return_nav: 'nav',
  cumulative_nav: 'nav',
  accumulated_nav: 'nav',
  cum_nav: 'nav',
  dividend_adjusted_nav: 'nav',
  reinvested_nav: 'nav',
  spot: 'fx',
  clean_price: 'price',
  dirty_price: 'price',
  par: 'price',
  accrued_interest: 'price',
}

export const NAV_HISTORY_INSTRUMENT_TYPES: ReadonlySet<InstrumentType> = new Set(['fund'])

export function supportsNavHistoryImport(instrumentType: InstrumentType): boolean {
  return NAV_HISTORY_INSTRUMENT_TYPES.has(instrumentType)
}

export function canonicalPriceContract(
  instrumentType: InstrumentType,
  metricFamily: MetricFamily,
): PriceContract {
  if (instrumentType === 'bond' && metricFamily === 'price') {
    return { price_unit: 'percent_of_par', price_scale: '0.01' }
  }
  if (instrumentType === 'fx' || metricFamily === 'fx') {
    return { price_unit: 'rate', price_scale: '1' }
  }
  return { price_unit: 'per_unit', price_scale: '1' }
}

export interface InstrumentIdentifier {
  identifier_type: IdentifierType
  identifier_value: string
  is_primary: boolean
}

export interface InstrumentCore {
  instrument_id: string
  instrument_name: string
  instrument_type: InstrumentType
  currency: string
  identifiers: InstrumentIdentifier[]
  market_data_updated_at?: string | null
}

export interface SourceSettings {
  source_mode: SourceMode
  source_email: string
  source_location: string
  source_api_profile: string
  source_email_rules: Array<Record<string, unknown>>
  expected_frequency: ExpectedFrequency
  market_calendar: string | null
  release_lag_days: number
}

export interface QuoteSelectionPolicy {
  trading: QuoteBasis[]
  valuation: QuoteBasis[]
  total_return: QuoteBasis[]
  chart: QuoteBasis[]
  reference: QuoteBasis[]
}

export interface MarketDataPoint {
  instrument_id: string
  metric_family: MetricFamily
  quote_basis: QuoteBasis
  as_of_date: string
  value: string
  currency: string
  price_unit: PriceUnit
  price_scale: string
  provider?: string | null
  status: DataStatus
}

export interface CorporateActionEvent {
  corporate_action_event_id: string
  instrument_id: string
  action_type: 'share_split'
  announcement_date?: string | null
  record_date?: string | null
  effective_date: string
  payable_date?: string | null
  new_units: string
  old_units: string
  quantity_rounding: QuantityRounding
  quantity_precision: number
  cost_basis_treatment: 'carry'
  source: string
  external_event_id?: string | null
  status: CorporateActionStatus
  provenance: Record<string, unknown>
  created_at: string
  updated_at: string
}
