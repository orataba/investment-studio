export type InstrumentType =
  | 'public_fund'
  | 'private_fund'
  | 'etf'
  | 'index'
  | 'equity'
  | 'crypto'
  | 'cash'
  | 'fx'
  | 'other'
export type BrokerIdentifierType = 'symbol' | 'product_code'
export type ExpectedFrequency = 'daily' | 'event_driven'
export type SourceMode = 'manual' | 'email' | 'api'
export type ReturnSemantics = 'unknown' | 'price_return' | 'total_return'
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
  | 'provider_symbol'
  | 'other'
export type MetricFamily = 'price' | 'nav' | 'fx'
export type PriceUnit = 'per_unit' | 'rate'
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
  | 'par'
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
  par: 'price',
}

export const FUND_INSTRUMENT_TYPES: ReadonlySet<InstrumentType> = new Set([
  'public_fund',
  'private_fund',
])
export const NAV_HISTORY_INSTRUMENT_TYPES: ReadonlySet<InstrumentType> = FUND_INSTRUMENT_TYPES

export function supportsNavHistoryImport(instrumentType: InstrumentType): boolean {
  return NAV_HISTORY_INSTRUMENT_TYPES.has(instrumentType)
}

export function canonicalPriceContract(
  instrumentType: InstrumentType,
  metricFamily: MetricFamily,
): PriceContract {
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

export interface BrokerIdentifier {
  broker: string
  identifier_type: BrokerIdentifierType
  identifier_value: string
  is_primary: boolean
}

export interface InstrumentCore {
  instrument_id: string
  instrument_name: string
  instrument_type: InstrumentType
  currency: string
  exchange_code: string | null
  identifiers: InstrumentIdentifier[]
  broker_identifiers: BrokerIdentifier[]
  market_data_updated_at?: string | null
}

export function canonicalBrokerIdentifiers(
  values: BrokerIdentifier[],
): BrokerIdentifier[] {
  const normalized = values.map((value) => ({
    ...value,
    broker: value.broker.trim(),
    identifier_value: value.identifier_value.trim(),
  }))
  if (normalized.some((value) => !value.broker || !value.identifier_value)) {
    throw new Error('Broker identifiers require non-blank broker and identifier_value.')
  }
  const keys = normalized.map(
    (value) =>
      `${value.broker.toLowerCase()}\u0000${value.identifier_type}\u0000${value.identifier_value.toLowerCase()}`,
  )
  if (new Set(keys).size !== keys.length) {
    throw new Error('Broker identifiers must be unique.')
  }
  const brokers = new Set(normalized.map((value) => value.broker.toLowerCase()))
  for (const broker of brokers) {
    const primaryCount = normalized.filter(
      (value) => value.broker.toLowerCase() === broker && value.is_primary,
    ).length
    if (primaryCount !== 1) {
      throw new Error('Each represented broker requires exactly one primary identifier.')
    }
  }
  return normalized
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
  return_semantics?: ReturnSemantics
  source_provider_currency?: string
  source_price_multiplier?: string
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
