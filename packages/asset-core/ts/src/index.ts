export type AssetType = 'fund' | 'bond' | 'equity' | 'cash' | 'fx' | 'other'
export type IdentifierType = 'ticker' | 'isin' | 'cusip' | 'sedol' | 'internal' | 'fund_name' | 'other'
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

export interface AssetIdentifier {
  identifier_type: IdentifierType
  identifier_value: string
  is_primary: boolean
}

export interface AssetCore {
  asset_id: string
  asset_name: string
  asset_type: AssetType
  currency: string
  identifiers: AssetIdentifier[]
}

export interface QuoteSelectionPolicy {
  trading: QuoteBasis[]
  valuation: QuoteBasis[]
  total_return: QuoteBasis[]
  chart: QuoteBasis[]
  reference: QuoteBasis[]
}

export interface MarketDataPoint {
  asset_id: string
  metric_family: MetricFamily
  quote_basis: QuoteBasis
  as_of_date: string
  value: string
  currency: string
  provider?: string | null
  status: DataStatus
}
