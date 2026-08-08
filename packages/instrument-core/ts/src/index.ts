export type InstrumentType =
  | 'fund'
  | 'etf'
  | 'index'
  | 'bond'
  | 'equity'
  | 'fcn'
  | 'option'
  | 'cash'
  | 'fx'
  | 'other'
export type NonOptionInstrumentType = Exclude<InstrumentType, 'option'>
export type NonDerivativeInstrumentType = Exclude<InstrumentType, 'option' | 'fcn'>
export type OptionType = 'call' | 'put'
export type OptionSettlementType = 'physical' | 'cash'
export type BrokerIdentifierType = 'contract_id' | 'symbol' | 'product_code'
export type FCNBarrierType = 'none' | 'knock_in' | 'knock_out' | 'dual'
export type DerivativeAdjustmentPolicyType =
  | 'exchange_rules'
  | 'contract_terms'
  | 'manual_review'
export type DerivativeContractReconciliationStatus =
  | 'not_applicable'
  | 'unmatched'
  | 'ready'
export type ExpectedFrequency = 'daily' | 'weekly' | 'monthly' | 'event_driven'
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

export interface OptionContractIdentity {
  underlying_instrument_id: string
  option_type: OptionType
  expiry_date: string
  strike: string
  contract_multiplier: string
  settlement_type: OptionSettlementType
  contract_currency: string
}

export interface BrokerIdentifier {
  broker: string
  identifier_type: BrokerIdentifierType
  identifier_value: string
  is_primary: boolean
}

export interface CorporateActionAdjustmentPolicy {
  policy_type: DerivativeAdjustmentPolicyType
  authority_reference: string
  quantity_rounding: QuantityRounding
  adjust_strike: boolean
  adjust_multiplier: boolean
  adjust_deliverable: boolean
}

export interface FCNContractMetadata {
  notional: string
  issue_date: string
  maturity_date: string
  contract_currency: string
  issuer: string
  counterparty: string
  underlying_instrument_ids: string[]
  deliverable_instrument_ids: string[]
  barrier_type: FCNBarrierType
  barrier_level: string | null
}

export interface DerivativeContractReconciliation {
  status: DerivativeContractReconciliationStatus
  canonical_contract_id: string | null
  broker_keys: string[]
  issues: string[]
}

export type OptionContractIdentityInput = Readonly<{
  underlying_instrument_id: string
  option_type: OptionType
  expiry_date: string
  strike: string | number
  contract_multiplier: string | number
  settlement_type: OptionSettlementType
  contract_currency: string
}>

export type FCNContractMetadataInput = Readonly<{
  notional: string | number
  issue_date: string
  maturity_date: string
  contract_currency: string
  issuer: string
  counterparty: string
  underlying_instrument_ids: string[]
  deliverable_instrument_ids: string[]
  barrier_type: FCNBarrierType
  barrier_level?: string | number | null
}>

type InstrumentCoreBase = {
  instrument_id: string
  instrument_name: string
  currency: string
  identifiers: InstrumentIdentifier[]
  broker_identifiers: BrokerIdentifier[]
  market_data_updated_at?: string | null
}

export type InstrumentCore =
  | (InstrumentCoreBase & {
      instrument_type: 'option'
      option_contract: OptionContractIdentity
      fcn_contract?: null
      corporate_action_adjustment_policy: CorporateActionAdjustmentPolicy
    })
  | (InstrumentCoreBase & {
      instrument_type: 'fcn'
      option_contract?: null
      fcn_contract: FCNContractMetadata
      corporate_action_adjustment_policy: CorporateActionAdjustmentPolicy
    })
  | (InstrumentCoreBase & {
      instrument_type: NonDerivativeInstrumentType
      option_contract?: null
      fcn_contract?: null
      corporate_action_adjustment_policy?: null
    })

function canonicalPositiveDecimal(value: string | number, fieldName: string): string {
  const normalized = String(value).trim()
  const numericValue = Number(normalized)
  if (!normalized || !Number.isFinite(numericValue) || numericValue <= 0) {
    throw new Error(`${fieldName} must be a finite positive decimal.`)
  }
  return normalized
}

function canonicalIsoDate(value: string, fieldName: string): string {
  const normalized = value.trim()
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(normalized)
  if (!match) throw new Error(`${fieldName} must be an ISO date.`)
  const [, yearText, monthText, dayText] = match
  const year = Number(yearText)
  const month = Number(monthText)
  const day = Number(dayText)
  const candidate = new Date(Date.UTC(year, month - 1, day))
  if (
    candidate.getUTCFullYear() !== year ||
    candidate.getUTCMonth() !== month - 1 ||
    candidate.getUTCDate() !== day
  ) {
    throw new Error(`${fieldName} must be a valid ISO date.`)
  }
  return normalized
}

export function canonicalOptionContractIdentity(
  input: OptionContractIdentityInput,
): OptionContractIdentity {
  const underlyingInstrumentId = input.underlying_instrument_id.trim()
  if (!underlyingInstrumentId) {
    throw new Error('underlying_instrument_id must not be blank.')
  }
  const contractCurrency = input.contract_currency.trim().toUpperCase()
  if (!contractCurrency || contractCurrency.length > 8) {
    throw new Error('contract_currency must be a non-blank ISO currency code.')
  }
  return {
    underlying_instrument_id: underlyingInstrumentId,
    option_type: input.option_type,
    expiry_date: canonicalIsoDate(input.expiry_date, 'expiry_date'),
    strike: canonicalPositiveDecimal(input.strike, 'strike'),
    contract_multiplier: canonicalPositiveDecimal(
      input.contract_multiplier,
      'contract_multiplier',
    ),
    settlement_type: input.settlement_type,
    contract_currency: contractCurrency,
  }
}

function canonicalInstrumentIdList(values: string[], fieldName: string): string[] {
  const normalized = values.map((value) => value.trim())
  if (!normalized.length || normalized.some((value) => !value)) {
    throw new Error(`${fieldName} must contain non-blank instrument ids.`)
  }
  if (new Set(normalized).size !== normalized.length) {
    throw new Error(`${fieldName} must not contain duplicate instrument ids.`)
  }
  return normalized
}

export function canonicalFCNContractMetadata(
  input: FCNContractMetadataInput,
): FCNContractMetadata {
  const issueDate = canonicalIsoDate(input.issue_date, 'issue_date')
  const maturityDate = canonicalIsoDate(input.maturity_date, 'maturity_date')
  if (maturityDate < issueDate) {
    throw new Error('maturity_date must not precede issue_date.')
  }
  const issuer = input.issuer.trim()
  const counterparty = input.counterparty.trim()
  if (!issuer || !counterparty) {
    throw new Error('issuer and counterparty must not be blank.')
  }
  const contractCurrency = input.contract_currency.trim().toUpperCase()
  if (!contractCurrency || contractCurrency.length > 8) {
    throw new Error('contract_currency must be a non-blank ISO currency code.')
  }
  const barrierLevel =
    input.barrier_level === null || input.barrier_level === undefined || input.barrier_level === ''
      ? null
      : canonicalPositiveDecimal(input.barrier_level, 'barrier_level')
  if (input.barrier_type === 'none' && barrierLevel !== null) {
    throw new Error('barrier_level must be null when barrier_type is none.')
  }
  if (input.barrier_type !== 'none' && barrierLevel === null) {
    throw new Error('barrier_level is required for an active barrier.')
  }
  return {
    notional: canonicalPositiveDecimal(input.notional, 'notional'),
    issue_date: issueDate,
    maturity_date: maturityDate,
    contract_currency: contractCurrency,
    issuer,
    counterparty,
    underlying_instrument_ids: canonicalInstrumentIdList(
      input.underlying_instrument_ids,
      'underlying_instrument_ids',
    ),
    deliverable_instrument_ids: canonicalInstrumentIdList(
      input.deliverable_instrument_ids,
      'deliverable_instrument_ids',
    ),
    barrier_type: input.barrier_type,
    barrier_level: barrierLevel,
  }
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

export function canonicalCorporateActionAdjustmentPolicy(
  value: CorporateActionAdjustmentPolicy,
): CorporateActionAdjustmentPolicy {
  const authorityReference = value.authority_reference.trim()
  if (!authorityReference) {
    throw new Error('authority_reference must not be blank.')
  }
  return {
    ...value,
    authority_reference: authorityReference,
  }
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
