export type InstrumentType = 'fund' | 'etf' | 'index' | 'bond' | 'equity' | 'cash' | 'fx' | 'other'
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
export type QuoteRole = 'trading' | 'valuation' | 'total_return' | 'chart' | 'reference'
export type DataStatus = 'complete' | 'partial' | 'unavailable'
export type CorporateActionStatus = 'detected' | 'confirmed' | 'cancelled'
export type QuantityRounding = 'exact' | 'truncate' | 'round_half_up' | 'cash_in_lieu'

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
  source_ref?: string | null
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
