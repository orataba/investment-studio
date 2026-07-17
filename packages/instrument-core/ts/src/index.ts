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
export type NavLineageKind = 'provider_explicit' | 'derived_dividend_reinvestment'
export type FundNavProjectionKind =
  | 'provider_explicit'
  | 'event_derived'
  | 'hybrid_reanchored'
export type FundNavProjectionStatus = 'complete' | 'partial' | 'unavailable'
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
  | 'accrued_interest'
export type QuoteRole = 'trading' | 'valuation' | 'total_return' | 'chart' | 'reference'
export type DataStatus = 'complete' | 'partial' | 'unavailable'
export type CorporateActionStatus = 'detected' | 'confirmed' | 'cancelled'
export type QuantityRounding = 'exact' | 'truncate' | 'round_half_up' | 'cash_in_lieu'
export type FundNavEventType = 'cash_distribution' | 'unit_split'
export type FundNavEventRevisionKind = 'original' | 'correction' | 'cancellation'
export type FundNavEventEvidenceKind =
  | 'provider_notice'
  | 'manual_verified'
export type FundNavAdjustmentFactorKind = 'provider_implied' | 'event_derived'
export type FundNavFactorEvidenceKind =
  | 'provider_total_return'
  | 'fund_nav_event'
  | 'zero_cash_anchor'
  | 'window_normalized_anchor'

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
  nav_lineage?: NavLineage | null
}

export interface NavLineage {
  kind: NavLineageKind
  evidence: Record<string, unknown>
  method_version?: string | null
  anchor_date?: string | null
}

export interface FundNavEvent {
  fund_nav_event_id: string
  fund_nav_action_id: string
  revision_number: number
  revision_kind: FundNavEventRevisionKind
  supersedes_fund_nav_event_id?: string | null
  instrument_id: string
  event_type: FundNavEventType
  announcement_date?: string | null
  record_date?: string | null
  effective_date: string
  payable_date?: string | null
  sequence_order?: number | null
  cash_per_unit?: string | null
  unit_ratio?: string | null
  evidence_kind: FundNavEventEvidenceKind
  source: string
  external_event_id?: string | null
  provenance: Record<string, unknown>
  recorded_by: string
  revision_reason: string
  created_at: string
  updated_at: string
}

export interface FundNavReinvestmentEvidence {
  fund_nav_reinvestment_evidence_id: string
  instrument_id: string
  fund_nav_event_id: string
  revision_number: number
  revision_kind: FundNavEventRevisionKind
  supersedes_fund_nav_reinvestment_evidence_id?: string | null
  reinvestment_nav: string
  evidence_kind: FundNavEventEvidenceKind
  source: string
  external_evidence_id?: string | null
  provenance: Record<string, unknown>
  recorded_by: string
  revision_reason: string
  created_at: string
  updated_at: string
}

export interface FundNavProjectionRun {
  fund_nav_projection_run_id: string
  instrument_id: string
  input_fingerprint: string
  source_observation_fingerprint: string
  projection_kind: FundNavProjectionKind
  projection_status: FundNavProjectionStatus
  method_version: string
  anchor_date?: string | null
  source_provider: string
  evidence: Record<string, unknown>
  created_by: string
  fund_nav_event_ids: string[]
  fund_nav_reinvestment_evidence_ids: string[]
  created_at: string
}

export interface FundNavAdjustmentFactor {
  fund_nav_adjustment_factor_id: string
  factor_logical_key: string
  instrument_id: string
  fund_nav_projection_run_id: string
  as_of_date: string
  factor_level: string
  factor_kind: FundNavAdjustmentFactorKind
  fund_nav_event_id?: string | null
  fund_nav_reinvestment_evidence_id?: string | null
  previous_fund_nav_adjustment_factor_id?: string | null
  evidence_kind: FundNavFactorEvidenceKind
  method_version: string
  anchor_date: string
  source_provider: string
  evidence: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type FundNavAdjustmentFactorInput = Omit<
  FundNavAdjustmentFactor,
  | 'fund_nav_adjustment_factor_id'
  | 'instrument_id'
  | 'fund_nav_projection_run_id'
  | 'previous_fund_nav_adjustment_factor_id'
  | 'created_at'
  | 'updated_at'
> & {
  fund_nav_adjustment_factor_id?: string
  instrument_id?: string
  fund_nav_projection_run_id?: string
  previous_factor_logical_key?: string | null
  previous_fund_nav_adjustment_factor_id?: string | null
  created_at?: string
  updated_at?: string
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
