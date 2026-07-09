export type InstrumentType = 'fund' | 'index' | 'bond' | 'equity' | 'cash' | 'fx' | 'other'
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
  | 'spot'
  | 'clean_price'
  | 'dirty_price'
  | 'par'
export type QuoteRole = 'trading' | 'valuation' | 'total_return' | 'chart' | 'reference'
export type DataStatus = 'complete' | 'partial' | 'unavailable'

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
  provider?: string | null
  status: DataStatus
}
