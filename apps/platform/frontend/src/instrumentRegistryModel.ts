import type {
  BrokerIdentifier,
  DataStatus,
  ExpectedFrequency,
  InstrumentCore,
  InstrumentIdentifier,
  InstrumentType,
  MetricFamily,
  PriceUnit,
  QuoteBasis,
  QuoteSelectionPolicy,
  ReturnSemantics,
  SourceSettings,
} from '../../../../packages/instrument-core/ts/src'
import { resolveQuoteBasis, resolveRoleQuote } from './quoteRoleResolution'

export type SourceMode = 'manual' | 'email' | 'api'
export type RefreshChannel = 'configured' | 'email' | 'tushare' | 'all'
export type InstrumentLifecycleStatus = 'active' | 'archived'

export type PlatformMarketDataPoint = {
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

export type PlatformLifecycleState = {
  status: InstrumentLifecycleStatus
  changed_at: string | null
  changed_by: string | null
  canonical_instrument_id?: string | null
}

type PlatformInstrumentRecordBase = {
  latest_market_data: PlatformMarketDataPoint[]
  quote_selection_policy: QuoteSelectionPolicy
  coverage_state: DataStatus
  source_settings: SourceSettings
  refresh_status: {
    status: string
    message: string
    requested_at: string | null
    requested_by: string | null
    mode: SourceMode
    last_successful_requested_at: string | null
  }
  lifecycle_state: PlatformLifecycleState
}

export type PlatformInstrumentRecord = InstrumentCore & PlatformInstrumentRecordBase

export type PlatformInstrumentDetail = PlatformInstrumentRecord & {
  market_data: PlatformMarketDataPoint[]
}

export type PlatformInstrumentsResponse = {
  registry_name: string
  instruments: PlatformInstrumentRecord[]
}

export type PlatformBulkRefreshResponse = {
  source: RefreshChannel
  refreshed_count: number
  skipped_count: number
  results: Array<{
    instrument_id: string
    instrument_name: string
    instrument_type: InstrumentType
    source_mode: SourceMode
    source_api_profile: string
    status: string
    message: string
  }>
}

export type PlatformRegistrySummary = {
  total_count: number
  active_count: number
  archived_count: number
  fund_count: number
  index_count: number
  fund_with_quote_count: number
}

export type CurrencyCode = string
export type FxRateSourceKind = 'direct' | 'inverse' | 'cross'

export type PlatformFxRateRecord = {
  base_currency: CurrencyCode
  quote_currency: CurrencyCode
  rate: string
  as_of_date: string
  source_kind: FxRateSourceKind
  instrument_id?: string | null
  source_instrument_ids: string[]
  provider?: string | null
  status: DataStatus
}

export type PlatformFxRatesResponse = {
  supported_currencies: CurrencyCode[]
  maintained_pairs: string[]
  rates: PlatformFxRateRecord[]
}

export type PlatformNavImportPreviewRow = {
  as_of_date: string
  nav?: string | null
  nav_with_dividend?: string | null
  currency: string
  instrument_code?: string | null
  instrument_name?: string | null
}

export type PlatformNavImportPreviewResponse = {
  row_count: number
  rows: PlatformNavImportPreviewRow[]
}

type CreateInstrumentPayloadBase = {
  instrument_name: string
  currency: string
  identifiers: InstrumentIdentifier[]
  broker_identifiers: BrokerIdentifier[]
}

export type CreateInstrumentPayload = CreateInstrumentPayloadBase & {
  instrument_type: InstrumentType
}

export type UpsertFxRatePayload = {
  base_currency: CurrencyCode
  quote_currency: CurrencyCode
  rate: string
  as_of_date: string
  provider?: string | null
  status: DataStatus
}

export type UpsertMarketDataPayload = {
  instrument_id: string
  metric_family: MetricFamily
  quote_basis: QuoteBasis
  as_of_date: string
  value: string
  currency: string
  provider?: string | null
  status: DataStatus
}

export type UpdateSourceSettingsPayload = {
  instrument_id: string
  source_mode: SourceMode
  source_email: string
  source_location: string
  source_api_profile: string
  expected_frequency: ExpectedFrequency
  market_calendar: string | null
  release_lag_days: number
  return_semantics: ReturnSemantics
}

export type TriggerRefreshPayload = {
  instrument_id: string
  updated_by?: string | null
  source?: RefreshChannel
}

export type TriggerChannelRefreshPayload = {
  source: RefreshChannel
  updated_by?: string | null
  full_history?: boolean
}

export type ImportNavTextPayload = {
  instrument_id: string
  raw_text: string
  provider?: string | null
  status: DataStatus
  updated_by?: string | null
}

export type ImportNavFilePayload = {
  instrument_id: string
  file_name: string
  file_content_base64: string
  provider?: string | null
  status: DataStatus
  updated_by?: string | null
}

export type LifecycleTransitionPayload = {
  instrument_id: string
  updated_by?: string | null
}

const PLATFORM_API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export function primaryIdentifier(record: PlatformInstrumentRecord) {
  return (
    record.identifiers.find((item) => item.is_primary)?.identifier_value ??
    record.identifiers[0]?.identifier_value ??
    record.instrument_id
  )
}

export function allowedFamiliesForInstrument(instrumentType: InstrumentType): MetricFamily[] {
  if (instrumentType === 'fx') return ['fx']
  if (instrumentType === 'fund') return ['price']
  return ['price']
}

export function formatBasisLabel(quoteBasis: QuoteBasis) {
  if (quoteBasis === 'official_nav') return 'Unit NAV'
  if (quoteBasis === 'total_return_nav') return 'Total return NAV'
  if (quoteBasis === 'adjusted_close') return 'Adjusted close'
  if (quoteBasis === 'last') return 'Last trade'
  if (quoteBasis === 'spot') return 'Spot'
  if (quoteBasis === 'par') return 'Par'
  return 'Close'
}

export function formatPolicyPath(policy: QuoteBasis[]) {
  return policy.map((value) => formatBasisLabel(value)).join(' → ') || '—'
}

export function latestQuoteSnapshot(record: PlatformInstrumentRecord) {
  const officialNav = resolveQuoteBasis(record, 'official_nav')
  const totalReturnNav = resolveQuoteBasis(record, 'total_return_nav')
  const selectedQuote = resolveRoleQuote(record, 'valuation')
  return {
    officialNav,
    totalReturnNav,
    selectedQuote,
    latestQuoteDate: selectedQuote?.as_of_date ?? null,
  }
}

export function formatDecimalValue(value: string) {
  if (!/^-?\d+\.\d+$/.test(value)) return value
  const [whole, fraction] = value.split('.')
  const trimmedFraction = fraction.replace(/0+$/, '')
  return trimmedFraction ? `${whole}.${trimmedFraction}` : whole
}

export function formatPointValue(point: PlatformMarketDataPoint | null) {
  return point ? `${formatDecimalValue(point.value)} ${point.currency}` : '—'
}

export function formatPrimaryQuoteDetail(
  primaryQuote: PlatformMarketDataPoint | null,
  officialNav: PlatformMarketDataPoint | null,
  totalReturnNav: PlatformMarketDataPoint | null,
) {
  if (primaryQuote && !['official_nav', 'total_return_nav'].includes(primaryQuote.quote_basis)) {
    return formatBasisLabel(primaryQuote.quote_basis)
  }
  if (officialNav && totalReturnNav) return `TR ${formatPointValue(totalReturnNav)}`
  if (officialNav) return 'Unit NAV only'
  if (totalReturnNav) return 'Total return NAV only'
  if (primaryQuote) return formatBasisLabel(primaryQuote.quote_basis)
  return 'No market data'
}

export function formatCoverageLabel(state: DataStatus) {
  if (state === 'complete') return 'Complete'
  if (state === 'partial') return 'Partial'
  return 'Unavailable'
}

export function formatSourceMode(mode: SourceMode) {
  if (mode === 'api') return 'API'
  if (mode === 'email') return 'Email'
  return 'Manual'
}

export function formatLifecycleLabel(status: InstrumentLifecycleStatus) {
  return status === 'archived' ? 'Archived' : 'Active'
}

export function upsertInstrumentRecord(
  current: PlatformInstrumentRecord[],
  updated: PlatformInstrumentRecord,
  includeInactive: boolean,
) {
  const next = current.filter((item) => item.instrument_id !== updated.instrument_id)
  if (!includeInactive && updated.lifecycle_state.status === 'archived') {
    return next.sort((left, right) => left.instrument_name.localeCompare(right.instrument_name))
  }
  return [...next, updated].sort((left, right) =>
    left.instrument_name.localeCompare(right.instrument_name),
  )
}

export function formatFxPairLabel(baseCurrency: string, quoteCurrency: string) {
  return `${baseCurrency}/${quoteCurrency}`
}

export function parseFxPairLabel(
  pairLabel: string,
  supportedCurrencies: CurrencyCode[],
): [CurrencyCode, CurrencyCode] | null {
  const [rawBase, rawQuote, ...remainder] = pairLabel.split('/')
  const baseCurrency = rawBase?.trim().toUpperCase()
  const quoteCurrency = rawQuote?.trim().toUpperCase()
  if (
    remainder.length > 0 ||
    !baseCurrency ||
    !quoteCurrency ||
    baseCurrency === quoteCurrency ||
    !supportedCurrencies.includes(baseCurrency) ||
    !supportedCurrencies.includes(quoteCurrency)
  ) {
    return null
  }
  return [baseCurrency, quoteCurrency]
}

export function formatFxSourceKind(sourceKind: FxRateSourceKind) {
  if (sourceKind === 'cross') return 'Cross'
  if (sourceKind === 'inverse') return 'Inverse'
  return 'Direct'
}

export function currentLocalDate() {
  const now = new Date()
  const offsetMs = now.getTimezoneOffset() * 60_000
  return new Date(now.getTime() - offsetMs).toISOString().slice(0, 10)
}

export function bytesToBase64(bytes: Uint8Array) {
  let binary = ''
  const chunkSize = 0x8000
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize)
    binary += String.fromCharCode(...chunk)
  }
  return btoa(binary)
}

export function findFxRate(
  rates: PlatformFxRateRecord[],
  baseCurrency: CurrencyCode,
  quoteCurrency: CurrencyCode,
) {
  return (
    rates.find(
      (item) => item.base_currency === baseCurrency && item.quote_currency === quoteCurrency,
    ) ?? null
  )
}

function csvCell(value: string | number | null | undefined) {
  const normalized = String(value ?? '')
  return /[",\n\r]/.test(normalized) ? `"${normalized.replace(/"/g, '""')}"` : normalized
}

export function marketDataToCsv(points: PlatformMarketDataPoint[]) {
  const header = [
    'as_of_date',
    'metric_family',
    'quote_basis',
    'value',
    'currency',
    'price_unit',
    'price_scale',
    'status',
    'provider',
  ]
  const rows = points.map((point) =>
    [
      point.as_of_date,
      point.metric_family,
      point.quote_basis,
      point.value,
      point.currency,
      point.price_unit,
      point.price_scale,
      point.status,
      point.provider ?? '',
    ]
      .map(csvCell)
      .join(','),
  )
  return [header.join(','), ...rows].join('\n')
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const target = PLATFORM_API_BASE ? `${PLATFORM_API_BASE}${path}` : path
  const response = await fetch(target, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    ...init,
  })

  if (!response.ok) {
    const body = await response.text()
    throw new Error(body || `Platform API returned ${response.status}`)
  }

  return (await response.json()) as T
}
