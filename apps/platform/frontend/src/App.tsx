import { ChangeEvent, FormEvent, Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { LanguageSelector } from '../../../../packages/ui/src/i18n'
import {
  beginRequest,
  invalidateRequests,
  isRequestCurrent,
} from '../../../../packages/ui/src/requestIdentity'
import type {
  InstrumentIdentifier as PlatformInstrumentIdentifier,
  InstrumentType,
  DataStatus,
  ExpectedFrequency,
  IdentifierType,
  MetricFamily,
  PriceUnit,
  QuoteBasis,
  QuoteRole,
  QuoteSelectionPolicy as PlatformQuoteSelectionPolicy,
  SourceSettings as SharedSourceSettings,
} from '../../../../packages/instrument-core/ts/src'
import {
  canonicalPriceContract,
  supportsNavHistoryImport,
} from '../../../../packages/instrument-core/ts/src'
import { detailForSelection, instrumentsForVisibility } from './instrumentVisibility'
import { FundNavActionReview } from './FundNavActionReview'
import { NavImportButton } from './NavImportButton'
import { PriceContractFields } from './PriceContractFields'
import { SourceScheduleFields } from './SourceScheduleFields'
import {
  defaultMarketDataSelection,
  formatPriceContract,
  formatPriceUnit,
  quoteBasisOptionsForInstrument,
} from './marketDataContract'
import { resolveQuoteBasis, resolveRoleQuote, summarizeRoleQuotes } from './quoteRoleResolution'
import DataOperationsDashboard from './DataOperationsDashboard'

type SourceMode = 'manual' | 'email' | 'api'
type RefreshChannel = 'configured' | 'email' | 'tushare' | 'all'
type InstrumentLifecycleStatus = 'active' | 'archived'

type PlatformMarketDataPoint = {
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

type PlatformLifecycleState = {
  status: InstrumentLifecycleStatus
  changed_at: string | null
  changed_by: string | null
  canonical_instrument_id?: string | null
}

type PlatformInstrumentRecord = {
  instrument_id: string
  instrument_name: string
  instrument_type: InstrumentType
  currency: string
  identifiers: PlatformInstrumentIdentifier[]
  latest_market_data: PlatformMarketDataPoint[]
  quote_selection_policy: PlatformQuoteSelectionPolicy
  coverage_state: DataStatus
  source_settings: SharedSourceSettings
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

type PlatformInstrumentDetail = PlatformInstrumentRecord & {
  market_data: PlatformMarketDataPoint[]
}

type PlatformInstrumentsResponse = {
  registry_name: string
  instruments: PlatformInstrumentRecord[]
}

type PlatformBulkRefreshResponse = {
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

type PlatformRegistrySummary = {
  total_count: number
  active_count: number
  archived_count: number
  fund_count: number
  index_count: number
  fund_with_quote_count: number
}

type CurrencyCode = string
type FxRateSourceKind = 'direct' | 'inverse' | 'cross'

type PlatformFxRateRecord = {
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

type PlatformFxRatesResponse = {
  supported_currencies: CurrencyCode[]
  maintained_pairs: string[]
  rates: PlatformFxRateRecord[]
}

type PlatformNavImportPreviewRow = {
  as_of_date: string
  nav?: string | null
  nav_with_dividend?: string | null
  currency: string
  instrument_code?: string | null
  instrument_name?: string | null
}

type PlatformNavImportPreviewResponse = {
  row_count: number
  rows: PlatformNavImportPreviewRow[]
}

const PLATFORM_API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const INSTRUMENT_REGISTRY_PATH = '/instruments'

const ROLE_LABELS: Record<QuoteRole, string> = {
  trading: 'Trading',
  valuation: 'Valuation',
  total_return: 'Total Return',
  chart: 'Chart',
  reference: 'Reference',
}

function normalizePath(pathname: string) {
  const normalized = pathname.replace(/\/+$/, '')
  return normalized || '/'
}

function primaryIdentifier(record: PlatformInstrumentRecord) {
  return (
    record.identifiers.find((item) => item.is_primary)?.identifier_value ??
    record.identifiers[0]?.identifier_value ??
    record.instrument_id
  )
}

function allowedFamiliesForInstrument(instrumentType: InstrumentType): MetricFamily[] {
  if (instrumentType === 'fx') {
    return ['fx']
  }
  if (instrumentType === 'cash' || instrumentType === 'bond' || instrumentType === 'equity' || instrumentType === 'etf' || instrumentType === 'index') {
    return ['price']
  }
  if (instrumentType === 'fund') {
    return ['price']
  }
  return ['price']
}

function formatBasisLabel(quoteBasis: QuoteBasis) {
  if (quoteBasis === 'official_nav') {
    return 'Unit NAV'
  }
  if (quoteBasis === 'total_return_nav') {
    return 'Dividend-Reinvested Total Return NAV'
  }
  if (quoteBasis === 'adjusted_close') {
    return 'Adjusted Close'
  }
  if (quoteBasis === 'clean_price') {
    return 'Clean Price'
  }
  if (quoteBasis === 'dirty_price') {
    return 'Dirty Price'
  }
  if (quoteBasis === 'accrued_interest') {
    return 'Accrued Interest'
  }
  if (quoteBasis === 'last') {
    return 'Last Trade'
  }
  if (quoteBasis === 'spot') {
    return 'Spot'
  }
  if (quoteBasis === 'par') {
    return 'Par'
  }
  return 'Close'
}

function formatPolicyPath(policy: QuoteBasis[]) {
  return policy.map((value) => formatBasisLabel(value)).join(' -> ') || '—'
}

function summaryQuoteChips(record: PlatformInstrumentRecord) {
  return summarizeRoleQuotes(record)
}

function latestQuoteSnapshot(record: PlatformInstrumentRecord) {
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

function formatPointValue(point: PlatformMarketDataPoint | null) {
  return point ? `${point.value} ${point.currency}` : '—'
}

function formatPrimaryQuoteDetail(
  primaryQuote: PlatformMarketDataPoint | null,
  officialNav: PlatformMarketDataPoint | null,
  totalReturnNav: PlatformMarketDataPoint | null,
) {
  if (primaryQuote && !['official_nav', 'total_return_nav'].includes(primaryQuote.quote_basis)) {
    return formatBasisLabel(primaryQuote.quote_basis)
  }
  if (officialNav && totalReturnNav) {
    return `TR ${formatPointValue(totalReturnNav)}`
  }
  if (officialNav) {
    return 'Unit NAV only'
  }
  if (totalReturnNav) {
    return 'Dividend-reinvested total return NAV only'
  }
  if (primaryQuote) {
    return formatBasisLabel(primaryQuote.quote_basis)
  }
  return 'No quote in shared data'
}

function formatCoverageLabel(state: DataStatus) {
  if (state === 'complete') {
    return 'Complete'
  }
  if (state === 'partial') {
    return 'Partial'
  }
  return 'Unavailable'
}

function formatSourceMode(mode: SourceMode) {
  if (mode === 'api') {
    return 'API'
  }
  if (mode === 'email') {
    return 'Email'
  }
  return 'Manual'
}

function formatLifecycleLabel(status: InstrumentLifecycleStatus) {
  return status === 'archived' ? 'Archived' : 'Active'
}

function upsertInstrumentRecord(
  current: PlatformInstrumentRecord[],
  updated: PlatformInstrumentRecord,
  includeInactive: boolean,
) {
  const next = current.filter((item) => item.instrument_id !== updated.instrument_id)
  if (!includeInactive && updated.lifecycle_state.status === 'archived') {
    return next.sort((left, right) => left.instrument_name.localeCompare(right.instrument_name))
  }
  return [...next, updated].sort((left, right) => left.instrument_name.localeCompare(right.instrument_name))
}

function formatFxPairLabel(baseCurrency: string, quoteCurrency: string) {
  return `${baseCurrency}/${quoteCurrency}`
}

function parseFxPairLabel(
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

function formatFxSourceKind(sourceKind: FxRateSourceKind) {
  if (sourceKind === 'cross') {
    return 'Cross'
  }
  if (sourceKind === 'inverse') {
    return 'Inverse'
  }
  return 'Direct'
}

function currentLocalDate() {
  const now = new Date()
  const offsetMs = now.getTimezoneOffset() * 60_000
  return new Date(now.getTime() - offsetMs).toISOString().slice(0, 10)
}

function bytesToBase64(bytes: Uint8Array) {
  let binary = ''
  const chunkSize = 0x8000
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize)
    binary += String.fromCharCode(...chunk)
  }
  return btoa(binary)
}

function findFxRate(
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

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
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

function InstrumentsPage({
  instruments,
  registrySummary,
  fxRates,
  loading,
  error,
  notice,
  showInactive,
  onToggleShowInactive,
  onCreateInstrument,
  onUpsertFxRate,
  onUpsertMarketData,
  onUpdateSourceSettings,
  onTriggerRefresh,
  onTriggerChannelRefresh,
  onImportNavText,
  onImportNavFile,
  onFundNavMutationCommitted,
  onArchiveInstrument,
  onRestoreInstrument,
}: {
  instruments: PlatformInstrumentRecord[]
  registrySummary: PlatformRegistrySummary
  fxRates: PlatformFxRatesResponse | null
  loading: boolean
  error: string | null
  notice: string | null
  showInactive: boolean
  onToggleShowInactive: () => void
  onCreateInstrument: (payload: {
    instrument_name: string
    instrument_type: InstrumentType
    currency: string
    identifiers: PlatformInstrumentIdentifier[]
  }) => Promise<void>
  onUpsertFxRate: (payload: {
    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    rate: string
    as_of_date: string
    provider?: string | null
    status: DataStatus
  }) => Promise<void>
  onUpsertMarketData: (payload: {
    instrument_id: string
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: string
    value: string
    currency: string
    provider?: string | null
    status: DataStatus
  }) => Promise<void>
  onUpdateSourceSettings: (payload: {
    instrument_id: string
    source_mode: SourceMode
    source_email: string
    source_location: string
    source_api_profile: string
    expected_frequency: ExpectedFrequency
    market_calendar: string | null
    release_lag_days: number
  }) => Promise<void>
  onTriggerRefresh: (payload: {
    instrument_id: string
    updated_by?: string | null
    source?: RefreshChannel
  }) => Promise<void>
  onTriggerChannelRefresh: (payload: {
    source: RefreshChannel
    updated_by?: string | null
    full_history?: boolean
  }) => Promise<void>
  onImportNavText: (payload: {
    instrument_id: string
    raw_text: string
    provider?: string | null
    status: DataStatus
    updated_by?: string | null
  }) => Promise<void>
  onImportNavFile: (payload: {
    instrument_id: string
    file_name: string
    file_content_base64: string
    provider?: string | null
    status: DataStatus
    updated_by?: string | null
  }) => Promise<void>
  onFundNavMutationCommitted: (instrumentId: string) => Promise<PlatformInstrumentDetail>
  onArchiveInstrument: (payload: { instrument_id: string; updated_by?: string | null }) => Promise<void>
  onRestoreInstrument: (payload: { instrument_id: string; updated_by?: string | null }) => Promise<void>
}) {
  const [instrumentName, setInstrumentName] = useState('')
  const [instrumentType, setInstrumentType] = useState<InstrumentType>('equity')
  const [currency, setCurrency] = useState('USD')
  const [identifierType, setIdentifierType] = useState<IdentifierType>('ticker')
  const [identifierValue, setIdentifierValue] = useState('')
  const [selectedInstrumentId, setSelectedInstrumentId] = useState('')
  const [metricFamily, setMetricFamily] = useState<MetricFamily>('price')
  const [quoteBasis, setQuoteBasis] = useState<QuoteBasis>('close')
  const [metricValue, setMetricValue] = useState('')
  const [metricCurrency, setMetricCurrency] = useState('USD')
  const [metricDate, setMetricDate] = useState(() => currentLocalDate())
  const [metricStatus, setMetricStatus] = useState<DataStatus>('complete')
  const [sourceMode, setSourceMode] = useState<SourceMode>('manual')
  const [sourceEmail, setSourceEmail] = useState('')
  const [sourceLocation, setSourceLocation] = useState('Database Dashboard')
  const [sourceApiProfile, setSourceApiProfile] = useState('')
  const [expectedFrequency, setExpectedFrequency] = useState<ExpectedFrequency>('event_driven')
  const [marketCalendar, setMarketCalendar] = useState('')
  const [releaseLagDays, setReleaseLagDays] = useState(0)
  const [navImportText, setNavImportText] = useState('')
  const [navImportFileName, setNavImportFileName] = useState('')
  const [navImportFileContent, setNavImportFileContent] = useState('')
  const [navImportFileInputKey, setNavImportFileInputKey] = useState(0)
  const [navPreview, setNavPreview] = useState<PlatformNavImportPreviewResponse | null>(null)
  const [navPreviewError, setNavPreviewError] = useState<string | null>(null)
  const [selectedInstrumentDetail, setSelectedInstrumentDetail] = useState<PlatformInstrumentDetail | null>(null)
  const [selectedInstrumentDetailError, setSelectedInstrumentDetailError] = useState<string | null>(null)
  const [selectedInstrumentDetailLoading, setSelectedInstrumentDetailLoading] = useState(false)
  const [editableFxPair, setEditableFxPair] = useState('')
  const [fxRateValue, setFxRateValue] = useState('')
  const [fxRateDate, setFxRateDate] = useState(() => currentLocalDate())
  const [fxRateStatus, setFxRateStatus] = useState<DataStatus>('complete')
  const [activePanel, setActivePanel] = useState<'create' | 'quote' | 'source' | 'nav' | 'fx' | null>(null)
  const [searchText, setSearchText] = useState('')
  const [instrumentTypeFilter, setInstrumentTypeFilter] = useState<'all' | InstrumentType>('all')
  const [coverageFilter, setCoverageFilter] = useState<'all' | DataStatus>('all')
  const [sourceFilter, setSourceFilter] = useState<'all' | SourceMode>('all')
  const [quoteFilter, setQuoteFilter] = useState<'all' | 'has_quote' | 'missing_quote'>('all')
  const [sortMode, setSortMode] = useState<'name_asc' | 'quote_desc' | 'identifier_asc' | 'coverage'>('name_asc')
  const [refreshChannel, setRefreshChannel] = useState<RefreshChannel>('all')
  const [registryPage, setRegistryPage] = useState(1)
  const [selectionPrimed, setSelectionPrimed] = useState(false)
  const detailRequestSequenceRef = useRef(0)
  const selectedInstrumentIdRef = useRef(selectedInstrumentId)

  selectedInstrumentIdRef.current = selectedInstrumentId

  const editableFxPairs = useMemo(
    () =>
      (fxRates?.maintained_pairs ?? [])
        .map((pairLabel) => parseFxPairLabel(pairLabel, fxRates?.supported_currencies ?? []))
        .filter((pair): pair is [CurrencyCode, CurrencyCode] => pair !== null),
    [fxRates],
  )
  const selectedEditableFxPair = useMemo(() => {
    const pair = parseFxPairLabel(editableFxPair, fxRates?.supported_currencies ?? [])
    if (!pair) {
      return null
    }
    return {
      baseCurrency: pair[0],
      quoteCurrency: pair[1],
    }
  }, [editableFxPair, fxRates?.supported_currencies])
  const selectedEditableFxRate = useMemo(
    () =>
      fxRates && selectedEditableFxPair
        ? findFxRate(
            fxRates.rates,
            selectedEditableFxPair.baseCurrency,
            selectedEditableFxPair.quoteCurrency,
          )
        : null,
    [fxRates, selectedEditableFxPair],
  )
  const fxPanelRates = useMemo(
    () => {
      const supportedCurrencies = fxRates?.supported_currencies ?? []
      return supportedCurrencies.flatMap((baseCurrency, baseIndex) =>
        supportedCurrencies.slice(baseIndex + 1).map((quoteCurrency) => ({
          pairLabel: formatFxPairLabel(baseCurrency, quoteCurrency),
          record: fxRates ? findFxRate(fxRates.rates, baseCurrency, quoteCurrency) : null,
        })),
      )
    },
    [fxRates],
  )
  const activeInstrumentCount = useMemo(
    () => instruments.filter((item) => item.lifecycle_state.status === 'active').length,
    [instruments],
  )
  const archivedInstrumentCount = useMemo(
    () => instruments.filter((item) => item.lifecycle_state.status === 'archived').length,
    [instruments],
  )

  const selectedInstrument = instruments.find((item) => item.instrument_id === selectedInstrumentId) ?? null
  const priceContract = canonicalPriceContract(
    selectedInstrument?.instrument_type ?? 'other',
    metricFamily,
  )
  const allowedMetricFamilies = useMemo(
    () =>
      selectedInstrument
        ? allowedFamiliesForInstrument(selectedInstrument.instrument_type)
        : (['price', 'nav', 'fx'] as MetricFamily[]),
    [selectedInstrument],
  )
  const availableQuoteBases = useMemo(
    () =>
      quoteBasisOptionsForInstrument(
        selectedInstrument?.instrument_type ?? 'other',
        metricFamily,
      ),
    [metricFamily, selectedInstrument?.instrument_type],
  )
  const selectedQuoteSummary = useMemo(
    () => (selectedInstrument ? summaryQuoteChips(selectedInstrument) : []),
    [selectedInstrument],
  )
  const selectedInstrumentQuoteSnapshot = useMemo(
    () => (selectedInstrument ? latestQuoteSnapshot(selectedInstrument) : null),
    [selectedInstrument],
  )
  const currentSelectedInstrumentDetail = detailForSelection(selectedInstrumentDetail, selectedInstrumentId)
  const selectedInstrumentNavHistory = useMemo(
    () => {
      const merged = new Map<
        string,
        {
          as_of_date: string
          nav: string | null
          nav_with_dividend: string | null
          currency: string
          status: DataStatus
          provider: string | null
        }
      >()
      for (const point of currentSelectedInstrumentDetail?.market_data || []) {
        if (point.metric_family !== 'nav') {
          continue
        }
        const existing =
          merged.get(point.as_of_date) ||
          {
            as_of_date: point.as_of_date,
            nav: null,
            nav_with_dividend: null,
            currency: point.currency,
            status: point.status,
            provider: point.provider || null,
          }
        if (point.quote_basis === 'official_nav') {
          existing.nav = point.value
        }
        if (point.quote_basis === 'total_return_nav') {
          existing.nav_with_dividend = point.value
        }
        existing.currency = point.currency
        existing.status = point.status
        existing.provider = point.provider || existing.provider
        merged.set(point.as_of_date, existing)
      }
      return [...merged.values()].sort((left, right) => right.as_of_date.localeCompare(left.as_of_date))
    },
    [currentSelectedInstrumentDetail],
  )
  const selectedInstrumentMarketHistory = useMemo(
    () =>
      [...(currentSelectedInstrumentDetail?.market_data || [])].sort((left, right) => {
        if (left.as_of_date === right.as_of_date) {
          return `${left.metric_family}:${left.quote_basis}`.localeCompare(
            `${right.metric_family}:${right.quote_basis}`,
          )
        }
        return right.as_of_date.localeCompare(left.as_of_date)
      }),
    [currentSelectedInstrumentDetail],
  )
  const filteredInstruments = useMemo(() => {
    const searchNeedle = searchText.trim().toLowerCase()
    const filtered = instruments.filter((item) => {
      if (instrumentTypeFilter !== 'all' && item.instrument_type !== instrumentTypeFilter) {
        return false
      }
      if (coverageFilter !== 'all' && item.coverage_state !== coverageFilter) {
        return false
      }
      if (sourceFilter !== 'all' && item.source_settings.source_mode !== sourceFilter) {
        return false
      }
      const latestQuoteDate = latestQuoteSnapshot(item).latestQuoteDate
      if (quoteFilter === 'has_quote' && !latestQuoteDate) {
        return false
      }
      if (quoteFilter === 'missing_quote' && latestQuoteDate) {
        return false
      }
      if (!searchNeedle) {
        return true
      }
      const searchableFields = [
        item.instrument_name,
        item.instrument_id,
        primaryIdentifier(item),
        ...item.identifiers.map((identifier) => identifier.identifier_value),
      ]
      return searchableFields.some((value) => value.toLowerCase().includes(searchNeedle))
    })
    return [...filtered].sort((left, right) => {
      if (sortMode === 'identifier_asc') {
        return primaryIdentifier(left).localeCompare(primaryIdentifier(right))
      }
      if (sortMode === 'quote_desc') {
        const leftQuote = latestQuoteSnapshot(left).latestQuoteDate || ''
        const rightQuote = latestQuoteSnapshot(right).latestQuoteDate || ''
        if (leftQuote !== rightQuote) {
          return rightQuote.localeCompare(leftQuote)
        }
        return left.instrument_name.localeCompare(right.instrument_name)
      }
      if (sortMode === 'coverage') {
        const rank: Record<DataStatus, number> = {
          complete: 0,
          partial: 1,
          unavailable: 2,
        }
        if (rank[left.coverage_state] !== rank[right.coverage_state]) {
          return rank[left.coverage_state] - rank[right.coverage_state]
        }
        return left.instrument_name.localeCompare(right.instrument_name)
      }
      return left.instrument_name.localeCompare(right.instrument_name)
    })
  }, [instrumentTypeFilter, coverageFilter, instruments, quoteFilter, searchText, sortMode, sourceFilter])
  const filteredFundCount = useMemo(
    () => filteredInstruments.filter((item) => item.instrument_type === 'fund').length,
    [filteredInstruments],
  )
  const filteredMissingQuoteCount = useMemo(
    () =>
      filteredInstruments.filter(
        (item) => item.instrument_type === 'fund' && !latestQuoteSnapshot(item).latestQuoteDate,
      ).length,
    [filteredInstruments],
  )
  const selectedInstrumentHiddenByFilters = useMemo(
    () =>
      !!selectedInstrument &&
      !filteredInstruments.some((item) => item.instrument_id === selectedInstrument.instrument_id),
    [filteredInstruments, selectedInstrument],
  )
  const registryPageSize = 50
  const registryPageCount = Math.max(1, Math.ceil(filteredInstruments.length / registryPageSize))
  const effectiveRegistryPage = Math.min(registryPage, registryPageCount)
  const pagedInstruments = useMemo(
    () =>
      filteredInstruments.slice(
        (effectiveRegistryPage - 1) * registryPageSize,
        effectiveRegistryPage * registryPageSize,
      ),
    [effectiveRegistryPage, filteredInstruments],
  )

  useEffect(() => {
    setRegistryPage(1)
  }, [coverageFilter, instrumentTypeFilter, quoteFilter, searchText, sortMode, sourceFilter])

  function syncSelectedInstrument(instrumentId: string) {
    const selectionChanged = instrumentId !== selectedInstrumentIdRef.current
    if (selectionChanged) {
      invalidateRequests(detailRequestSequenceRef)
      setSelectedInstrumentDetail(null)
      setSelectedInstrumentDetailError(null)
      setSelectedInstrumentDetailLoading(Boolean(instrumentId))
    }
    selectedInstrumentIdRef.current = instrumentId
    setSelectedInstrumentId(instrumentId)
    setSelectionPrimed(true)
    const selected = instruments.find((item) => item.instrument_id === instrumentId)
    if (!selected) {
      return
    }
    if (!supportsNavHistoryImport(selected.instrument_type)) {
      setActivePanel((current) => (current === 'nav' ? null : current))
    }
    const defaults = defaultMarketDataSelection(selected.instrument_type)
    setMetricFamily(defaults.metric_family)
    setQuoteBasis(defaults.quote_basis)
    setMetricCurrency(selected.currency)
  }

  async function refreshSelectedInstrumentDetail(instrumentId: string) {
    if (instrumentId !== selectedInstrumentIdRef.current) {
      return
    }
    const request = beginRequest(detailRequestSequenceRef, instrumentId)
    if (!instrumentId) {
      setSelectedInstrumentDetailLoading(false)
      setSelectedInstrumentDetail(null)
      setSelectedInstrumentDetailError(null)
      return
    }
    setSelectedInstrumentDetailLoading(true)
    setSelectedInstrumentDetailError(null)
    try {
      const detail = await fetchJson<PlatformInstrumentDetail>(
        `/api/instruments/${encodeURIComponent(instrumentId)}`,
      )
      if (
        !isRequestCurrent(
          detailRequestSequenceRef,
          request,
          selectedInstrumentIdRef.current,
          detail.instrument_id,
        )
      ) {
        return
      }
      setSelectedInstrumentDetail(detail)
    } catch (requestError) {
      if (!isRequestCurrent(detailRequestSequenceRef, request, selectedInstrumentIdRef.current)) {
        return
      }
      setSelectedInstrumentDetail(null)
      setSelectedInstrumentDetailError(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to load selected instrument detail.',
      )
    } finally {
      if (isRequestCurrent(detailRequestSequenceRef, request, selectedInstrumentIdRef.current)) {
        setSelectedInstrumentDetailLoading(false)
      }
    }
  }

  useEffect(() => {
    if (!instruments.length) {
      if (selectedInstrumentId) {
        clearSelectedInstrument()
      }
      setSelectionPrimed(false)
      return
    }
    if (selectedInstrumentId && instruments.some((item) => item.instrument_id === selectedInstrumentId)) {
      return
    }
    if (selectedInstrumentId || !selectionPrimed) {
      syncSelectedInstrument(instruments[0].instrument_id)
    }
  }, [instruments, selectedInstrumentId, selectionPrimed])

  useEffect(() => {
    void refreshSelectedInstrumentDetail(selectedInstrumentId)
  }, [selectedInstrumentId])

  useEffect(
    () => () => {
      invalidateRequests(detailRequestSequenceRef)
    },
    [],
  )

  useEffect(() => {
    if (!selectedInstrument) {
      return
    }
    setSourceMode(selectedInstrument.source_settings.source_mode)
    setSourceEmail(selectedInstrument.source_settings.source_email)
    setSourceLocation(selectedInstrument.source_settings.source_location)
    setSourceApiProfile(selectedInstrument.source_settings.source_api_profile)
    setExpectedFrequency(selectedInstrument.source_settings.expected_frequency)
    setMarketCalendar(selectedInstrument.source_settings.market_calendar || '')
    setReleaseLagDays(selectedInstrument.source_settings.release_lag_days)
  }, [selectedInstrument])

  useEffect(() => {
    if (!allowedMetricFamilies.includes(metricFamily)) {
      const nextFamily = allowedMetricFamilies[0]
      setMetricFamily(nextFamily)
      setQuoteBasis(
        quoteBasisOptionsForInstrument(
          selectedInstrument?.instrument_type ?? 'other',
          nextFamily,
        )[0].value,
      )
      return
    }
    if (!availableQuoteBases.some((item) => item.value === quoteBasis)) {
      setQuoteBasis(availableQuoteBases[0].value)
    }
  }, [
    allowedMetricFamilies,
    availableQuoteBases,
    metricFamily,
    quoteBasis,
    selectedInstrument?.instrument_type,
  ])

  useEffect(() => {
    const maintainedPairLabels = editableFxPairs.map(([baseCurrency, quoteCurrency]) =>
      formatFxPairLabel(baseCurrency, quoteCurrency),
    )
    if (!maintainedPairLabels.length) {
      if (editableFxPair) {
        setEditableFxPair('')
      }
      return
    }
    if (!maintainedPairLabels.includes(editableFxPair)) {
      setEditableFxPair(maintainedPairLabels[0])
    }
  }, [editableFxPair, editableFxPairs])

  useEffect(() => {
    if (!selectedEditableFxRate) {
      setFxRateValue('')
      setFxRateDate(currentLocalDate())
      setFxRateStatus('complete')
      return
    }
    setFxRateValue(selectedEditableFxRate.rate)
    setFxRateDate(selectedEditableFxRate.as_of_date)
    setFxRateStatus(selectedEditableFxRate.status)
  }, [selectedEditableFxRate])

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await onCreateInstrument({
      instrument_name: instrumentName.trim(),
      instrument_type: instrumentType,
      currency: currency.trim().toUpperCase(),
      identifiers: [
        {
          identifier_type: identifierType,
          identifier_value: identifierValue.trim(),
          is_primary: true,
        },
      ],
    })
    setInstrumentName('')
    setIdentifierValue('')
  }

  async function handleFxSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedEditableFxPair) {
      return
    }
    await onUpsertFxRate({
      base_currency: selectedEditableFxPair.baseCurrency,
      quote_currency: selectedEditableFxPair.quoteCurrency,
      rate: fxRateValue.trim(),
      as_of_date: fxRateDate,
      provider: 'platform_fx_manual',
      status: fxRateStatus,
    })
  }

  async function handleMetricSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await onUpsertMarketData({
      instrument_id: selectedInstrumentId,
      metric_family: metricFamily,
      quote_basis: quoteBasis,
      as_of_date: metricDate,
      value: metricValue.trim(),
      currency: metricCurrency.trim().toUpperCase(),
      status: metricStatus,
      provider: 'platform_manual',
    })
    setMetricValue('')
    await refreshSelectedInstrumentDetail(selectedInstrumentId)
  }

  async function handleSourceSettingsSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedInstrumentId) {
      return
    }
    await onUpdateSourceSettings({
      instrument_id: selectedInstrumentId,
      source_mode: sourceMode,
      source_email: sourceEmail.trim(),
      source_location: sourceLocation.trim(),
      source_api_profile: sourceApiProfile.trim(),
      expected_frequency: expectedFrequency,
      market_calendar: marketCalendar.trim() || null,
      release_lag_days: releaseLagDays,
    })
    await refreshSelectedInstrumentDetail(selectedInstrumentId)
  }

  async function handleRefreshClick() {
    if (!selectedInstrumentId) {
      return
    }
    await onTriggerRefresh({
      instrument_id: selectedInstrumentId,
      updated_by: 'platform_ui',
      source: refreshChannel === 'all' ? 'configured' : refreshChannel,
    })
    await refreshSelectedInstrumentDetail(selectedInstrumentId)
  }

  async function handleRefreshChannelClick() {
    await onTriggerChannelRefresh({
      source: refreshChannel,
      updated_by: 'platform_ui',
    })
    if (selectedInstrumentId) {
      await refreshSelectedInstrumentDetail(selectedInstrumentId)
    }
  }

  async function handleNavFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) {
      setNavImportFileName('')
      setNavImportFileContent('')
      setNavPreview(null)
      setNavPreviewError(null)
      return
    }
    const buffer = await file.arrayBuffer()
    setNavImportFileName(file.name)
    setNavImportFileContent(bytesToBase64(new Uint8Array(buffer)))
    setNavImportText('')
    setNavPreview(null)
    setNavPreviewError(null)
  }

  async function handlePreviewNavImport() {
    if (!selectedInstrumentId) {
      return
    }
    const payload =
      navImportFileName && navImportFileContent
        ? {
            file_name: navImportFileName,
            file_content_base64: navImportFileContent,
          }
        : {
            raw_text: navImportText.trim(),
          }
    if (!('raw_text' in payload ? payload.raw_text : payload.file_name)) {
      return
    }
    try {
      const preview = await fetchJson<PlatformNavImportPreviewResponse>(
        `/api/instruments/${encodeURIComponent(selectedInstrumentId)}/nav-import/preview`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setNavPreview(preview)
      setNavPreviewError(null)
    } catch (requestError) {
      setNavPreview(null)
      setNavPreviewError(
        requestError instanceof Error ? requestError.message : 'Failed to preview NAV import.',
      )
    }
  }

  async function handleNavImportSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedInstrumentId) {
      return
    }
    if (navImportFileName && navImportFileContent) {
      await onImportNavFile({
        instrument_id: selectedInstrumentId,
        file_name: navImportFileName,
        file_content_base64: navImportFileContent,
        provider: 'platform_file_import',
        status: 'complete',
        updated_by: 'platform_ui',
      })
    } else if (navImportText.trim()) {
      await onImportNavText({
        instrument_id: selectedInstrumentId,
        raw_text: navImportText.trim(),
        provider: 'platform_paste_import',
        status: 'complete',
        updated_by: 'platform_ui',
      })
    } else {
      return
    }
    setNavImportText('')
    setNavImportFileName('')
    setNavImportFileContent('')
    setNavImportFileInputKey((current) => current + 1)
    setNavPreview(null)
    setNavPreviewError(null)
    await refreshSelectedInstrumentDetail(selectedInstrumentId)
  }

  async function handleArchiveClick(instrumentId: string) {
    await onArchiveInstrument({
      instrument_id: instrumentId,
      updated_by: 'platform_ui',
    })
    if (instrumentId === selectedInstrumentId) {
      await refreshSelectedInstrumentDetail(instrumentId)
    }
  }

  async function handleRestoreClick(instrumentId: string) {
    await onRestoreInstrument({
      instrument_id: instrumentId,
      updated_by: 'platform_ui',
    })
    if (instrumentId === selectedInstrumentId) {
      await refreshSelectedInstrumentDetail(instrumentId)
    }
  }

  function clearSelectedInstrument() {
    invalidateRequests(detailRequestSequenceRef)
    selectedInstrumentIdRef.current = ''
    setSelectedInstrumentId('')
    setSelectedInstrumentDetail(null)
    setSelectedInstrumentDetailError(null)
    setSelectedInstrumentDetailLoading(false)
  }

  function toggleSelectedInstrument(instrumentId: string) {
    if (selectedInstrumentId === instrumentId) {
      clearSelectedInstrument()
      return
    }
    syncSelectedInstrument(instrumentId)
  }

  function openActionPanel(panel: 'create' | 'quote' | 'source' | 'nav' | 'fx', instrumentId?: string) {
    const targetInstrument = instrumentId
      ? instruments.find((item) => item.instrument_id === instrumentId)
      : selectedInstrument
    if (
      panel === 'nav' &&
      (!targetInstrument || !supportsNavHistoryImport(targetInstrument.instrument_type))
    ) {
      return
    }
    if (panel === 'quote' && targetInstrument?.instrument_type === 'fund') {
      return
    }
    if (instrumentId) {
      syncSelectedInstrument(instrumentId)
    }
    setActivePanel(panel)
  }

  function panelButtonClass(panel: 'create' | 'quote' | 'source' | 'nav' | 'fx') {
    return `registry-submit secondary${activePanel === panel ? ' registry-submit-active' : ''}`
  }

  function clearTableFilters() {
    setSearchText('')
    setInstrumentTypeFilter('all')
    setCoverageFilter('all')
    setSourceFilter('all')
    setQuoteFilter('all')
    setSortMode('name_asc')
  }

  return (
    <main className="platform-shell">
      <header className="platform-masthead">
        <a className="data-ops-brand" href="/">
          <span>Portfolio Operations</span>
          <strong>Data Operations</strong>
        </a>
        <nav className="data-ops-nav" aria-label="Data operations navigation">
          <a className="platform-nav-link" href="/">Overview</a>
          <a className="platform-nav-link platform-nav-link-active" href={INSTRUMENT_REGISTRY_PATH}>Instrument Registry</a>
        </nav>
        <LanguageSelector />
      </header>

      <section className="registry-pagehead">
        <div className="registry-breadcrumbs">
          <a href="/">Data Operations</a>
          <span>/</span>
          <span>Instrument Registry</span>
        </div>
        <div className="registry-kicker">Shared data workspace</div>
        <h1>Instrument Registry</h1>
        <p className="hero-copy">
          Search and maintain canonical identifiers, quote policies, source rules, FX, NAV,
          and typed market data. Watchlist and Portfolio consume this registry; they do not
          own duplicate copies of these records.
        </p>
        <div className="registry-pagehead-actions">
          <div className="registry-table-meta">
            {showInactive
              ? `${registrySummary.active_count} active · ${registrySummary.archived_count} archived in registry`
              : `${activeInstrumentCount} active visible · ${registrySummary.total_count} total instruments in registry`}
          </div>
        </div>
      </section>

      {notice ? <div className="registry-notice">{notice}</div> : null}
      {error ? <div className="registry-error">{error}</div> : null}

      <section className="registry-overview-strip">
        <div className="registry-form-title">Registry Overview</div>
        <div className="registry-summary-inline" role="list" aria-label="Registry overview">
          <span role="listitem">Total {registrySummary.total_count}</span>
          <span role="listitem">Active {registrySummary.active_count}</span>
          <span role="listitem">Archived {registrySummary.archived_count}</span>
          <span role="listitem">Funds {registrySummary.fund_count}</span>
          <span role="listitem">Indexes {registrySummary.index_count}</span>
          <span role="listitem">Funds With Quote {registrySummary.fund_with_quote_count}</span>
        </div>
      </section>

      <section className="registry-control-shell">
        <div className="registry-control-grid">
          <div className="registry-control-block">
            <div className="registry-form-title">Find Instruments</div>
            <div className="registry-table-meta">
              Search by code, instrument name, instrument id, or identifier, then narrow the table before
              opening detail.
            </div>
            <div className="registry-filter-grid">
              <label className="registry-field-search">
                <span>Search</span>
                <input
                  type="search"
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                  placeholder="Code / name / instrument id / identifier"
                />
              </label>
              <label>
                <span>Type</span>
                <select value={instrumentTypeFilter} onChange={(event) => setInstrumentTypeFilter(event.target.value as 'all' | InstrumentType)}>
                  <option value="all">All Types</option>
                  <option value="equity">Equity</option>
                  <option value="index">Index</option>
                  <option value="fund">Fund</option>
                  <option value="etf">ETF</option>
                  <option value="bond">Bond</option>
                  <option value="cash">Cash</option>
                  <option value="fx">FX</option>
                  <option value="other">Other</option>
                </select>
              </label>
              <label>
                <span>Coverage</span>
                <select value={coverageFilter} onChange={(event) => setCoverageFilter(event.target.value as 'all' | DataStatus)}>
                  <option value="all">All Coverage</option>
                  <option value="complete">Complete</option>
                  <option value="partial">Partial</option>
                  <option value="unavailable">Unavailable</option>
                </select>
              </label>
              <label>
                <span>Source</span>
                <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value as 'all' | SourceMode)}>
                  <option value="all">All Sources</option>
                  <option value="manual">Manual</option>
                  <option value="email">Email</option>
                  <option value="api">API</option>
                </select>
              </label>
              <label>
                <span>Quote</span>
                <select value={quoteFilter} onChange={(event) => setQuoteFilter(event.target.value as 'all' | 'has_quote' | 'missing_quote')}>
                  <option value="all">All Instruments</option>
                  <option value="has_quote">Has Quote</option>
                  <option value="missing_quote">Missing Quote</option>
                </select>
              </label>
              <label>
                <span>Sort</span>
                <select value={sortMode} onChange={(event) => setSortMode(event.target.value as 'name_asc' | 'quote_desc' | 'identifier_asc' | 'coverage')}>
                  <option value="name_asc">Name</option>
                  <option value="quote_desc">Latest Quote Date</option>
                  <option value="identifier_asc">Identifier</option>
                  <option value="coverage">Coverage</option>
                </select>
              </label>
            </div>
            <div className="registry-filter-footer">
              <div className="registry-summary-inline registry-summary-inline-compact">
                <span>Visible {filteredInstruments.length}</span>
                <span>Funds {filteredFundCount}</span>
                <span>Funds Missing Quote {filteredMissingQuoteCount}</span>
              </div>
              <div className="registry-filter-actions">
                <label className="registry-inline-field">
                  <span>Refresh</span>
                  <select value={refreshChannel} onChange={(event) => setRefreshChannel(event.target.value as RefreshChannel)}>
                    <option value="all">Email + Tushare</option>
                    <option value="email">Email</option>
                    <option value="tushare">Tushare</option>
                  </select>
                </label>
                <button type="button" className="registry-submit" onClick={() => void handleRefreshChannelClick()}>
                  Refresh Channel
                </button>
                <button
                  type="button"
                  className={`registry-submit secondary${instrumentTypeFilter === 'fund' ? ' registry-submit-active' : ''}`}
                  onClick={() => setInstrumentTypeFilter('fund')}
                >
                  Funds Only
                </button>
                <button
                  type="button"
                  className={`registry-submit secondary${instrumentTypeFilter === 'fund' && quoteFilter === 'missing_quote' ? ' registry-submit-active' : ''}`}
                  onClick={() => {
                    setInstrumentTypeFilter('fund')
                    setQuoteFilter('missing_quote')
                  }}
                >
                  Missing Quote
                </button>
                <button type="button" className="registry-submit secondary" onClick={clearTableFilters}>
                  Reset Filters
                </button>
              </div>
            </div>
            {selectedInstrumentHiddenByFilters ? (
              <div className="registry-form-note">
                The selected instrument is hidden by current filters. Reset filters to bring its row
                back into view.
              </div>
            ) : null}
          </div>

          <div className="registry-control-block registry-control-block-accent">
            <div className="registry-form-title">Selected Instrument</div>
            {selectedInstrument ? (
              <div className="registry-selected-instrument">
                <div className="registry-selected-heading">
                  <strong>{primaryIdentifier(selectedInstrument)}</strong>
                  <span>{selectedInstrument.instrument_name}</span>
                </div>
                <div className="registry-selected-subtitle">
                  {selectedInstrument.instrument_type.toUpperCase()} · {selectedInstrument.currency} ·{' '}
                  {selectedInstrumentQuoteSnapshot?.latestQuoteDate
                    ? `Latest Quote ${selectedInstrumentQuoteSnapshot.latestQuoteDate}`
                    : 'No quote loaded yet'}
                </div>
                <div className="registry-selected-meta">
                  <span className={`coverage-badge coverage-badge-${selectedInstrument.coverage_state}`}>
                    {formatCoverageLabel(selectedInstrument.coverage_state)}
                  </span>
                  <span
                    className={`lifecycle-badge lifecycle-badge-${selectedInstrument.lifecycle_state.status}`}
                  >
                    {formatLifecycleLabel(selectedInstrument.lifecycle_state.status)}
                  </span>
                  <span className="metric-chip">
                    {formatSourceMode(selectedInstrument.source_settings.source_mode)}
                  </span>
                  <span className="metric-chip">
                    {selectedQuoteSummary.length
                      ? `${selectedQuoteSummary.length} selected quote${selectedQuoteSummary.length > 1 ? 's' : ''}`
                      : 'No selected quotes'}
                  </span>
                </div>
                <div className="registry-form-actions">
                  {selectedInstrument.instrument_type !== 'fund' ? (
                    <button type="button" className="registry-submit secondary" onClick={() => openActionPanel('quote', selectedInstrument.instrument_id)}>
                      Add Quote
                    </button>
                  ) : null}
                  <NavImportButton
                    instrumentType={selectedInstrument.instrument_type}
                    type="button"
                    className="registry-submit secondary"
                    onClick={() => openActionPanel('nav', selectedInstrument.instrument_id)}
                  />
                  <button type="button" className="registry-submit secondary" onClick={() => void handleRefreshClick()}>
                    Refresh
                  </button>
                  <button type="button" className="registry-submit secondary" onClick={clearSelectedInstrument}>
                    Close Detail
                  </button>
                </div>
              </div>
            ) : (
              <div className="registry-selected-empty">
                Select a row in the table to inspect NAV history, quote snapshot, and source
                settings here.
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="registry-table-shell">
        <div className="registry-table-header registry-toolbar-header">
          <div>
            <div className="registry-form-title">Operations</div>
            <div className="registry-table-meta">
              {selectedInstrument
                ? `Selected instrument: ${primaryIdentifier(selectedInstrument)} · ${selectedInstrument.instrument_name}`
                : 'Select an instrument from the table to manage quote, NAV, and source settings.'}
            </div>
          </div>
          <div className="registry-toolbar-actions">
            <button type="button" className={panelButtonClass('create')} onClick={() => openActionPanel('create')}>
              Add Instrument
            </button>
            {!selectedInstrument || selectedInstrument.instrument_type !== 'fund' ? (
              <button
                type="button"
                className={panelButtonClass('quote')}
                disabled={!selectedInstrument}
                onClick={() => openActionPanel('quote')}
              >
                Add Quote
              </button>
            ) : null}
            <button
              type="button"
              className={panelButtonClass('source')}
              disabled={!selectedInstrument}
              onClick={() => openActionPanel('source')}
            >
              Source Settings
            </button>
            {selectedInstrument ? (
              <NavImportButton
                instrumentType={selectedInstrument.instrument_type}
                type="button"
                className={panelButtonClass('nav')}
                onClick={() => openActionPanel('nav')}
              />
            ) : null}
            <button type="button" className={panelButtonClass('fx')} onClick={() => openActionPanel('fx')}>
              Update FX
            </button>
            <button
              type="button"
              className="registry-submit secondary"
              disabled={!selectedInstrument}
              onClick={() => void handleRefreshClick()}
            >
              Refresh Selected
            </button>
            <button type="button" className="registry-submit secondary" onClick={onToggleShowInactive}>
              {showInactive ? 'Hide Archived' : 'Show Archived'}
            </button>
          </div>
        </div>
        {activePanel ? (
          <div className="registry-action-panel">
            <div className="registry-action-panel-header">
              <div>
                <div className="registry-form-title">
                  {activePanel === 'create'
                    ? 'Add Instrument'
                    : activePanel === 'quote'
                      ? 'Add Quote'
                      : activePanel === 'source'
                        ? 'Source Settings'
                        : activePanel === 'nav'
                          ? 'Import NAV'
                          : 'Update FX'}
                </div>
                <div className="registry-table-meta">
                  {activePanel === 'create'
                    ? 'Create shared registry instruments here. Downstream Watchlist and Portfolio refresh from this layer.'
                    : activePanel === 'fx'
                      ? 'Maintain direct FX spot pairs in one place.'
                      : selectedInstrument
                        ? `${primaryIdentifier(selectedInstrument)} · ${selectedInstrument.instrument_name}`
                        : 'Select an instrument from the table first.'}
                </div>
              </div>
              <button type="button" className="app-link secondary" onClick={() => setActivePanel(null)}>
                Close Panel
              </button>
            </div>

            {activePanel === 'create' ? (
              <form className="registry-form registry-action-form" onSubmit={(event) => void handleCreate(event)}>
                <div className="registry-action-form-grid">
                  <label>
                    <span>Name</span>
                    <input value={instrumentName} onChange={(event) => setInstrumentName(event.target.value)} required />
                  </label>
                  <label>
                    <span>Type</span>
                    <select value={instrumentType} onChange={(event) => setInstrumentType(event.target.value as InstrumentType)}>
                      <option value="equity">Equity</option>
                      <option value="index">Index</option>
                      <option value="fund">Fund</option>
                      <option value="etf">ETF</option>
                      <option value="bond">Bond</option>
                      <option value="cash">Cash</option>
                      <option value="fx">FX</option>
                      <option value="other">Other</option>
                    </select>
                  </label>
                  <label>
                    <span>Currency</span>
                    <input value={currency} onChange={(event) => setCurrency(event.target.value)} required />
                  </label>
                  <label>
                    <span>Primary Identifier Type</span>
                    <select
                      value={identifierType}
                      onChange={(event) => setIdentifierType(event.target.value as IdentifierType)}
                    >
                      <option value="ticker">Ticker</option>
                      <option value="isin">ISIN</option>
                      <option value="cusip">CUSIP</option>
                      <option value="sedol">SEDOL</option>
                      <option value="internal">Internal</option>
                      <option value="other">Other</option>
                    </select>
                  </label>
                  <label className="registry-field-wide">
                    <span>Primary Identifier</span>
                    <input
                      value={identifierValue}
                      onChange={(event) => setIdentifierValue(event.target.value)}
                      required
                    />
                  </label>
                </div>
                <div className="registry-form-actions">
                  <button type="submit" className="registry-submit">
                    Create Instrument
                  </button>
                </div>
              </form>
            ) : null}

            {activePanel === 'quote' ? (
              selectedInstrument ? (
                <form className="registry-form registry-action-form" onSubmit={(event) => void handleMetricSubmit(event)}>
                  <div className="registry-action-form-grid">
                    <label className="registry-field-wide">
                      <span>Selected Instrument</span>
                      <input value={selectedInstrument.instrument_name} readOnly />
                    </label>
                    <label>
                      <span>Family</span>
                      <select
                        value={metricFamily}
                        onChange={(event) => {
                          const nextFamily = event.target.value as MetricFamily
                          setMetricFamily(nextFamily)
                          setQuoteBasis(
                            quoteBasisOptionsForInstrument(
                              selectedInstrument.instrument_type,
                              nextFamily,
                            )[0].value,
                          )
                        }}
                      >
                        {allowedMetricFamilies.map((family) => (
                          <option key={family} value={family}>
                            {family.toUpperCase()}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>Basis</span>
                      <select value={quoteBasis} onChange={(event) => setQuoteBasis(event.target.value as QuoteBasis)}>
                        {availableQuoteBases.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <PriceContractFields
                      instrumentType={selectedInstrument.instrument_type}
                      metricFamily={metricFamily}
                    />
                    <label>
                      <span>Value</span>
                      <input value={metricValue} onChange={(event) => setMetricValue(event.target.value)} required />
                    </label>
                    <label>
                      <span>Currency</span>
                      <input
                        value={metricCurrency}
                        onChange={(event) => setMetricCurrency(event.target.value)}
                        readOnly={selectedInstrument.instrument_type === 'fx'}
                        required
                      />
                    </label>
                    <label>
                      <span>As Of</span>
                      <input
                        type="date"
                        value={metricDate}
                        onChange={(event) => setMetricDate(event.target.value)}
                        required
                      />
                    </label>
                    <label>
                      <span>Status</span>
                      <select value={metricStatus} onChange={(event) => setMetricStatus(event.target.value as DataStatus)}>
                        <option value="complete">Complete</option>
                        <option value="partial">Partial</option>
                        <option value="unavailable">Unavailable</option>
                      </select>
                    </label>
                  </div>
                  <div className="registry-form-actions">
                    <button type="submit" className="registry-submit">
                      Save Quote
                    </button>
                  </div>
                  <div className="registry-form-note">
                    Price contract: {formatPriceContract(priceContract.price_unit, priceContract.price_scale)}
                    {' · '}
                    Valuation path: {formatPolicyPath(selectedInstrument.quote_selection_policy.valuation)}
                    {' · '}
                    Total return path: {formatPolicyPath(selectedInstrument.quote_selection_policy.total_return)}
                  </div>
                </form>
              ) : (
                <div className="registry-form-note">Select an instrument from the table first.</div>
              )
            ) : null}

            {activePanel === 'source' ? (
              selectedInstrument ? (
                <form className="registry-form registry-action-form" onSubmit={(event) => void handleSourceSettingsSubmit(event)}>
                  <div className="registry-action-form-grid">
                    <label className="registry-field-wide">
                      <span>Selected Instrument</span>
                      <input value={selectedInstrument.instrument_name} readOnly />
                    </label>
                    <label>
                      <span>Source Mode</span>
                      <select
                        value={sourceMode}
                        onChange={(event) => {
                          const nextMode = event.target.value as SourceMode
                          setSourceMode(nextMode)
                          if (nextMode === 'api' && !sourceApiProfile.trim()) {
                            setSourceApiProfile('tushare')
                          }
                        }}
                      >
                        <option value="manual">Manual</option>
                        <option value="email">Email</option>
                        <option value="api">API</option>
                      </select>
                    </label>
                    <SourceScheduleFields
                      expectedFrequency={expectedFrequency}
                      marketCalendar={marketCalendar}
                      releaseLagDays={releaseLagDays}
                      onExpectedFrequencyChange={setExpectedFrequency}
                      onMarketCalendarChange={setMarketCalendar}
                      onReleaseLagDaysChange={setReleaseLagDays}
                    />
                    <label>
                      <span>Email Source</span>
                      <input
                        value={sourceEmail}
                        onChange={(event) => setSourceEmail(event.target.value)}
                        disabled={sourceMode !== 'email'}
                        placeholder="pricing@internal.com"
                      />
                    </label>
                    <label>
                      <span>API Profile</span>
                      <input
                        value={sourceApiProfile}
                        onChange={(event) => setSourceApiProfile(event.target.value)}
                        disabled={sourceMode !== 'api'}
                        placeholder="tushare"
                      />
                    </label>
                    <label className="registry-field-wide">
                      <span>Folder / Rule</span>
                      <input
                        value={sourceLocation}
                        onChange={(event) => setSourceLocation(event.target.value)}
                        placeholder="Database Dashboard queue / mailbox folder / endpoint rule"
                      />
                    </label>
                  </div>
                  <div className="registry-form-actions">
                    <button type="submit" className="registry-submit">
                      Save Source Settings
                    </button>
                    <button type="button" className="registry-submit secondary" onClick={() => void handleRefreshClick()}>
                      Refresh Now
                    </button>
                  </div>
                </form>
              ) : (
                <div className="registry-form-note">Select an instrument from the table first.</div>
              )
            ) : null}

            {activePanel === 'nav' ? (
              selectedInstrument && supportsNavHistoryImport(selectedInstrument.instrument_type) ? (
                <form className="registry-form registry-action-form" onSubmit={(event) => void handleNavImportSubmit(event)}>
                  <div className="registry-action-form-grid">
                    <label className="registry-field-wide">
                      <span>Selected Instrument</span>
                      <input value={selectedInstrument.instrument_name} readOnly />
                    </label>
                    <label className="registry-field-wide">
                      <span>Upload Excel / CSV</span>
                      <input
                        key={navImportFileInputKey}
                        type="file"
                        accept=".csv,.tsv,.txt,.xlsx,.xls"
                        onChange={(event) => void handleNavFileChange(event)}
                      />
                    </label>
                    <label className="registry-field-wide">
                      <span>Pasted Rows</span>
                      <textarea
                        value={navImportText}
                        onChange={(event) => {
                          if (navImportFileName || navImportFileContent) {
                            setNavImportFileName('')
                            setNavImportFileContent('')
                            setNavImportFileInputKey((current) => current + 1)
                          }
                          setNavImportText(event.target.value)
                        }}
                        placeholder={`date,nav,nav_with_dividend,currency\nYYYY-MM-DD,12.84,18.46,USD`}
                      />
                    </label>
                  </div>
                  <div className="registry-form-note">
                    Only two fund NAV series are accepted: Unit NAV and Dividend-Reinvested Total Return NAV.
                  </div>
                  {navImportFileName ? (
                    <div className="registry-form-note">
                      File ready: <strong>{navImportFileName}</strong>
                    </div>
                  ) : null}
                  <div className="registry-form-actions">
                    <button
                      type="button"
                      className="registry-submit secondary"
                      disabled={!selectedInstrumentId || (!navImportText.trim() && !navImportFileContent)}
                      onClick={() => void handlePreviewNavImport()}
                    >
                      Preview Parsed Rows
                    </button>
                    <button
                      type="submit"
                      className="registry-submit"
                      disabled={!selectedInstrumentId || (!navImportText.trim() && !navImportFileContent)}
                    >
                      Import NAV Rows
                    </button>
                    <button type="button" className="registry-submit secondary" onClick={() => void handleRefreshClick()}>
                      Refresh From Source
                    </button>
                  </div>
                  {navPreviewError ? <div className="registry-error">{navPreviewError}</div> : null}
                  {navPreview ? (
                    <div className="registry-preview">
                      <div className="registry-preview-header">
                        <strong>{navPreview.row_count} rows ready to import</strong>
                        <span>{navImportFileName ? `Parsed from ${navImportFileName}` : 'Parsed from pasted text'}</span>
                      </div>
                      <div className="registry-table-wrap">
                        <table className="registry-table registry-table-compact">
                          <thead>
                            <tr>
                              <th>Date</th>
                              <th>Unit NAV</th>
                              <th>Dividend-Reinvested Total Return NAV</th>
                              <th>Currency</th>
                              <th>Code</th>
                              <th>Name</th>
                            </tr>
                          </thead>
                          <tbody>
                            {navPreview.rows.slice(0, 12).map((row) => (
                              <tr key={`${row.as_of_date}-${row.nav || ''}-${row.nav_with_dividend || ''}`}>
                                <td>{row.as_of_date}</td>
                                <td>{row.nav || '—'}</td>
                                <td>{row.nav_with_dividend || '—'}</td>
                                <td>{row.currency}</td>
                                <td>{row.instrument_code || '—'}</td>
                                <td>{row.instrument_name || '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : null}
                </form>
              ) : (
                <div className="registry-form-note">Select an instrument from the table first.</div>
              )
            ) : null}

            {activePanel === 'fx' ? (
              <div className="registry-action-layout">
                <div className="registry-table-wrap">
                  <table className="registry-table registry-table-compact">
                    <thead>
                      <tr>
                        <th>Pair</th>
                        <th>Rate</th>
                        <th>As Of</th>
                        <th>Source</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fxPanelRates.map(({ pairLabel, record }) => (
                        <tr key={pairLabel}>
                          <td>{pairLabel}</td>
                          <td>{record ? record.rate : '—'}</td>
                          <td>{record ? record.as_of_date : '—'}</td>
                          <td>{record ? formatFxSourceKind(record.source_kind) : 'Missing'}</td>
                          <td>{record ? record.status : 'unavailable'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <form className="registry-form registry-action-form" onSubmit={(event) => void handleFxSubmit(event)}>
                  <div className="registry-action-form-grid">
                    <label>
                      <span>Pair</span>
                      <select value={editableFxPair} onChange={(event) => setEditableFxPair(event.target.value)}>
                        {editableFxPairs.map(([baseCurrency, quoteCurrency]) => {
                          const value = formatFxPairLabel(baseCurrency, quoteCurrency)
                          return (
                            <option key={value} value={value}>
                              {value}
                            </option>
                          )
                        })}
                      </select>
                    </label>
                    <label>
                      <span>Spot Rate</span>
                      <input value={fxRateValue} onChange={(event) => setFxRateValue(event.target.value)} required />
                    </label>
                    <label>
                      <span>As Of</span>
                      <input type="date" value={fxRateDate} onChange={(event) => setFxRateDate(event.target.value)} required />
                    </label>
                    <label>
                      <span>Status</span>
                      <select value={fxRateStatus} onChange={(event) => setFxRateStatus(event.target.value as DataStatus)}>
                        <option value="complete">Complete</option>
                        <option value="partial">Partial</option>
                        <option value="unavailable">Unavailable</option>
                      </select>
                    </label>
                  </div>
                  <div className="registry-form-actions">
                    <button type="submit" className="registry-submit" disabled={!selectedEditableFxPair}>
                      Save FX Rate
                    </button>
                  </div>
                  <div className="registry-form-note">
                    Supported settlement currencies:{' '}
                    {fxRates?.supported_currencies.length
                      ? fxRates.supported_currencies.join(' / ')
                      : 'Unavailable'}.
                  </div>
                </form>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="registry-table-shell">
        <div className="registry-table-header">
          <div>
            <div className="registry-form-title">Instrument Registry</div>
            <div className="registry-table-meta">
              {loading
                ? 'Loading instruments...'
                : showInactive
                  ? `Showing ${filteredInstruments.length} filtered instruments out of ${instruments.length} in registry view`
                  : `Showing ${filteredInstruments.length} filtered active instruments out of ${instruments.length} visible`}
            </div>
          </div>
          <button type="button" className="app-link secondary" onClick={onToggleShowInactive}>
            {showInactive ? 'Show Active Only' : 'Include Archived'}
          </button>
        </div>
        <div className="registry-table-wrap">
          <table className="registry-table registry-table-main">
            <thead>
              <tr>
                <th>Open</th>
                <th>Identifier</th>
                <th>Name</th>
                <th>Type</th>
                <th>Currency</th>
                <th>Latest Quote</th>
                <th>Quote Date</th>
                <th>Selected Quotes</th>
                <th>Source</th>
                <th>Coverage</th>
                <th>Lifecycle</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {!filteredInstruments.length ? (
                <tr className="registry-empty-row">
                  <td colSpan={12}>
                    No instruments match the current filters. Reset filters or broaden the search.
                  </td>
                </tr>
              ) : null}
              {pagedInstruments.map((item) => {
                const isSelected = item.instrument_id === selectedInstrumentId
                const detailRowId = `registry-detail-${encodeURIComponent(item.instrument_id)}`
                const quoteSummary = summaryQuoteChips(item)
                const { officialNav, totalReturnNav, selectedQuote, latestQuoteDate } = latestQuoteSnapshot(item)
                const primaryQuote = selectedQuote
                return (
                  <Fragment key={item.instrument_id}>
                    <tr
                      className={`registry-row-summary${isSelected ? ' registry-row-selected' : ''}${
                        item.lifecycle_state.status === 'archived' ? ' registry-row-archived' : ''
                      }`}
                    >
                      <td>
                        <button
                          type="button"
                          className={`registry-row-toggle${isSelected ? ' registry-row-toggle-active' : ''}`}
                          aria-expanded={isSelected}
                          aria-controls={detailRowId}
                          aria-label={`${isSelected ? 'Close' : 'Open'} details for ${item.instrument_name}`}
                          onClick={() => toggleSelectedInstrument(item.instrument_id)}
                        >
                          {isSelected ? 'Close' : 'Open'}
                        </button>
                      </td>
                      <td>
                        <div className="instrument-id-stack">
                          <strong>{primaryIdentifier(item)}</strong>
                          <span>{item.instrument_id}</span>
                        </div>
                      </td>
                      <td>{item.instrument_name}</td>
                      <td>{item.instrument_type}</td>
                      <td>{item.currency}</td>
                      <td>
                        <div className="instrument-id-stack">
                          <strong>{formatPointValue(primaryQuote)}</strong>
                          <span>{formatPrimaryQuoteDetail(primaryQuote, officialNav, totalReturnNav)}</span>
                        </div>
                      </td>
                      <td>
                        <div className="instrument-id-stack">
                          <strong>{latestQuoteDate || '—'}</strong>
                          <span>
                            {latestQuoteDate
                              ? primaryQuote?.provider || 'Shared market data'
                              : '—'}
                          </span>
                        </div>
                      </td>
                      <td>
                        <div className="instrument-metric-stack">
                          {quoteSummary.length ? (
                            quoteSummary.map(({ role, point }) => (
                              <span
                                key={`${item.instrument_id}-${role}-${point.quote_basis}`}
                                className="metric-chip"
                              >
                                {ROLE_LABELS[role]} {point.value} {point.currency}
                              </span>
                            ))
                          ) : (
                            <span className="metric-chip metric-chip-muted">No market data</span>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="instrument-id-stack">
                          <strong>{formatSourceMode(item.source_settings.source_mode)}</strong>
                          <span>{item.refresh_status.status}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`coverage-badge coverage-badge-${item.coverage_state}`}>
                          {formatCoverageLabel(item.coverage_state)}
                        </span>
                      </td>
                      <td>
                        <div className="instrument-id-stack">
                          <span
                            className={`lifecycle-badge lifecycle-badge-${item.lifecycle_state.status}`}
                          >
                            {formatLifecycleLabel(item.lifecycle_state.status)}
                          </span>
                          <span>{item.lifecycle_state.changed_at || 'Platform search default'}</span>
                        </div>
                      </td>
                      <td>
                        <div className="registry-row-actions">
                          {item.instrument_type !== 'fund' ? (
                            <button
                              type="button"
                              className="registry-submit secondary"
                              onClick={() => openActionPanel('quote', item.instrument_id)}
                            >
                              Quote
                            </button>
                          ) : null}
                          {item.lifecycle_state.status === 'archived' ? (
                            <button
                              type="button"
                              className="registry-submit secondary"
                              onClick={() => void handleRestoreClick(item.instrument_id)}
                            >
                              Restore
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="registry-submit danger"
                              onClick={() => void handleArchiveClick(item.instrument_id)}
                            >
                              Archive
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {isSelected ? (
                      <tr className="registry-row-detail" id={detailRowId}>
                        <td colSpan={12}>
                          <div className="registry-detail-panel">
                            <div className="registry-detail-toolbar">
                              <div>
                                <div className="registry-form-title">Instrument Detail</div>
                                <div className="registry-table-meta">
                                  {selectedInstrumentDetailLoading
                                    ? 'Loading NAV sequence and shared market data...'
                                    : `${selectedInstrumentNavHistory.length} NAV rows · ${selectedInstrumentMarketHistory.length} market-data points`}
                                </div>
                              </div>
                              <div className="registry-toolbar-actions">
                                {item.instrument_type !== 'fund' ? (
                                  <button type="button" className="registry-submit secondary" onClick={() => openActionPanel('quote', item.instrument_id)}>
                                    Add Quote
                                  </button>
                                ) : null}
                                <NavImportButton
                                  instrumentType={item.instrument_type}
                                  type="button"
                                  className="registry-submit secondary"
                                  onClick={() => openActionPanel('nav', item.instrument_id)}
                                />
                                <button type="button" className="registry-submit secondary" onClick={() => openActionPanel('source', item.instrument_id)}>
                                  Source Settings
                                </button>
                                <button type="button" className="registry-submit secondary" onClick={() => void handleRefreshClick()}>
                                  Refresh
                                </button>
                              </div>
                            </div>

                            {selectedInstrumentDetailError ? <div className="registry-error">{selectedInstrumentDetailError}</div> : null}

                            <div className="registry-detail-grid">
                              <div className="registry-detail-block">
                                <div className="registry-form-title">Instrument Summary</div>
                                <div className="registry-table-wrap">
                                  <table className="registry-table registry-table-compact">
                                    <thead>
                                      <tr>
                                        <th>Field</th>
                                        <th>Value</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      <tr>
                                        <td>Instrument ID</td>
                                        <td>{item.instrument_id}</td>
                                      </tr>
                                      <tr>
                                        <td>Primary Identifier</td>
                                        <td>{primaryIdentifier(item)}</td>
                                      </tr>
                                      <tr>
                                        <td>Source Mode</td>
                                        <td>{formatSourceMode(item.source_settings.source_mode)}</td>
                                      </tr>
                                      <tr>
                                        <td>Expected Frequency</td>
                                        <td>{item.source_settings.expected_frequency.replace('_', ' ')}</td>
                                      </tr>
                                      <tr>
                                        <td>Market Calendar</td>
                                        <td>{item.source_settings.market_calendar || 'Not configured'}</td>
                                      </tr>
                                      <tr>
                                        <td>Release Lag</td>
                                        <td>{item.source_settings.release_lag_days} day(s)</td>
                                      </tr>
                                      <tr>
                                        <td>Coverage</td>
                                        <td>{formatCoverageLabel(item.coverage_state)}</td>
                                      </tr>
                                      <tr>
                                        <td>Latest Quote Date</td>
                                        <td>{latestQuoteDate || '—'}</td>
                                      </tr>
                                      <tr>
                                        <td>Refresh Status</td>
                                        <td>{item.refresh_status.status || '—'}</td>
                                      </tr>
                                      <tr>
                                        <td>Valuation Path</td>
                                        <td>{formatPolicyPath(item.quote_selection_policy.valuation)}</td>
                                      </tr>
                                      <tr>
                                        <td>Total Return Path</td>
                                        <td>{formatPolicyPath(item.quote_selection_policy.total_return)}</td>
                                      </tr>
                                    </tbody>
                                  </table>
                                </div>
                              </div>

                              <div className="registry-detail-block">
                                <div className="registry-form-title">Quote Snapshot</div>
                                <div className="registry-table-wrap">
                                  <table className="registry-table registry-table-compact">
                                    <thead>
                                      <tr>
                                        <th>Role</th>
                                        <th>Basis</th>
                                        <th>Value</th>
                                        <th>Unit</th>
                                        <th>Scale</th>
                                        <th>Date</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {selectedQuoteSummary.length ? (
                                        selectedQuoteSummary.map(({ role, point }) => (
                                          <tr key={`${item.instrument_id}-${role}-${point.quote_basis}`}>
                                            <td>{ROLE_LABELS[role]}</td>
                                            <td>{formatBasisLabel(point.quote_basis)}</td>
                                            <td>{point.value} {point.currency}</td>
                                            <td>{formatPriceUnit(point.price_unit)}</td>
                                            <td>{point.price_scale}</td>
                                            <td>{point.as_of_date}</td>
                                          </tr>
                                        ))
                                      ) : (
                                        <tr>
                                          <td colSpan={6}>No selected quotes for this instrument.</td>
                                        </tr>
                                      )}
                                    </tbody>
                                  </table>
                                </div>
                              </div>
                            </div>

                            <div className="registry-detail-stack">
                              <div className="registry-detail-section">
                                <div className="registry-detail-header">
                                  <div>
                                    <div className="registry-form-title">NAV Sequence</div>
                                    <div className="registry-table-meta">
                                      {selectedInstrumentNavHistory.length
                                        ? `Showing ${Math.min(selectedInstrumentNavHistory.length, 16)} recent rows`
                                        : 'No NAV history loaded for this instrument'}
                                    </div>
                                  </div>
                                </div>
                                <div className="registry-table-wrap">
                                  <table className="registry-table registry-table-compact">
                                    <thead>
                                      <tr>
                                        <th>Date</th>
                                        <th>Unit NAV</th>
                                        <th>Dividend-Reinvested Total Return NAV</th>
                                        <th>Currency</th>
                                        <th>Status</th>
                                        <th>Provider</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {selectedInstrumentNavHistory.length ? (
                                        selectedInstrumentNavHistory.slice(0, 16).map((point) => (
                                          <tr key={`${point.as_of_date}-${point.currency}`}>
                                            <td>{point.as_of_date}</td>
                                            <td>{point.nav || '—'}</td>
                                            <td>{point.nav_with_dividend || '—'}</td>
                                            <td>{point.currency}</td>
                                            <td>{point.status}</td>
                                            <td>{point.provider || '—'}</td>
                                          </tr>
                                        ))
                                      ) : (
                                        <tr>
                                          <td colSpan={6}>No NAV history loaded for this instrument.</td>
                                        </tr>
                                      )}
                                    </tbody>
                                  </table>
                                </div>
                              </div>

                              {item.instrument_type === 'fund' ? (
                                <FundNavActionReview
                                  instrumentId={item.instrument_id}
                                  request={fetchJson}
                                  onMutationCommitted={async (instrumentId) => {
                                    const detail = await onFundNavMutationCommitted(instrumentId)
                                    if (selectedInstrumentIdRef.current === instrumentId) {
                                      setSelectedInstrumentDetail(detail)
                                      setSelectedInstrumentDetailError(null)
                                    }
                                  }}
                                />
                              ) : null}

                              <div className="registry-detail-section">
                                <div className="registry-detail-header">
                                  <div>
                                    <div className="registry-form-title">All Shared Market Data</div>
                                    <div className="registry-table-meta">
                                      {selectedInstrumentMarketHistory.length
                                        ? `Showing ${Math.min(selectedInstrumentMarketHistory.length, 24)} recent points`
                                        : 'No shared market data loaded for this instrument'}
                                    </div>
                                  </div>
                                </div>
                                <div className="registry-table-wrap">
                                  <table className="registry-table registry-table-compact">
                                    <thead>
                                      <tr>
                                        <th>Date</th>
                                        <th>Family</th>
                                        <th>Basis</th>
                                        <th>Value</th>
                                        <th>Currency</th>
                                        <th>Unit</th>
                                        <th>Scale</th>
                                        <th>Status</th>
                                        <th>Provider</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {selectedInstrumentMarketHistory.length ? (
                                        selectedInstrumentMarketHistory.slice(0, 24).map((point) => (
                                          <tr key={`${point.metric_family}-${point.quote_basis}-${point.as_of_date}-${point.currency}`}>
                                            <td>{point.as_of_date}</td>
                                            <td>{point.metric_family}</td>
                                            <td>{formatBasisLabel(point.quote_basis)}</td>
                                            <td>{point.value}</td>
                                            <td>{point.currency}</td>
                                            <td>{formatPriceUnit(point.price_unit)}</td>
                                            <td>{point.price_scale}</td>
                                            <td>{point.status}</td>
                                            <td>{point.provider || '—'}</td>
                                          </tr>
                                        ))
                                      ) : (
                                        <tr>
                                          <td colSpan={9}>No shared market data loaded for this instrument.</td>
                                        </tr>
                                      )}
                                    </tbody>
                                  </table>
                                </div>
                              </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
        {filteredInstruments.length > registryPageSize ? (
          <div className="registry-pagination">
            <span>
              Page {effectiveRegistryPage} of {registryPageCount} · {filteredInstruments.length} instruments
            </span>
            <div>
              <button
                type="button"
                className="registry-submit secondary"
                disabled={effectiveRegistryPage <= 1}
                onClick={() => setRegistryPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </button>
              <button
                type="button"
                className="registry-submit secondary"
                disabled={effectiveRegistryPage >= registryPageCount}
                onClick={() => setRegistryPage((current) => Math.min(registryPageCount, current + 1))}
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </section>
    </main>
  )
}

export default function App() {
  const currentPath = normalizePath(window.location.pathname)
  const [instruments, setInstruments] = useState<PlatformInstrumentRecord[]>([])
  const [allInstruments, setAllInstruments] = useState<PlatformInstrumentRecord[]>([])
  const [fxRates, setFxRates] = useState<PlatformFxRatesResponse | null>(null)
  const [loadingInstruments, setLoadingInstruments] = useState(false)
  const [registryError, setRegistryError] = useState<string | null>(null)
  const [registryNotice, setRegistryNotice] = useState<string | null>(null)
  const [showInactive, setShowInactive] = useState(false)
  const fxWriteQueueRef = useRef<Promise<void>>(Promise.resolve())
  const registryReadSequenceRef = useRef(0)
  const showInactiveRef = useRef(showInactive)

  showInactiveRef.current = showInactive

  useEffect(() => {
    if (currentPath !== INSTRUMENT_REGISTRY_PATH) {
      return undefined
    }
    let cancelled = false
    const requestSequence = ++registryReadSequenceRef.current
    setLoadingInstruments(true)

    Promise.all([
      fetchJson<PlatformInstrumentsResponse>('/api/instruments?include_inactive=true'),
      fetchJson<PlatformFxRatesResponse>('/api/fx-rates'),
    ])
      .then(([allInstrumentPayload, fxPayload]) => {
        if (!cancelled && requestSequence === registryReadSequenceRef.current) {
          setInstruments(
            instrumentsForVisibility(allInstrumentPayload.instruments, showInactiveRef.current),
          )
          setAllInstruments(allInstrumentPayload.instruments)
          setFxRates(fxPayload)
          setRegistryError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled && requestSequence === registryReadSequenceRef.current) {
          setRegistryError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load instruments.',
          )
        }
      })
      .finally(() => {
        if (!cancelled && requestSequence === registryReadSequenceRef.current) {
          setLoadingInstruments(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [currentPath])

  const registrySummary = useMemo<PlatformRegistrySummary>(() => {
    const base = allInstruments.length ? allInstruments : instruments
    const activeCount = base.filter((item) => item.lifecycle_state.status === 'active').length
    const fundInstruments = base.filter((item) => item.instrument_type === 'fund')
    const indexInstruments = base.filter((item) => item.instrument_type === 'index')
    const fundsWithQuoteCount = fundInstruments.filter((item) => latestQuoteSnapshot(item).latestQuoteDate).length
    return {
      total_count: base.length,
      active_count: activeCount,
      archived_count: base.length - activeCount,
      fund_count: fundInstruments.length,
      index_count: indexInstruments.length,
      fund_with_quote_count: fundsWithQuoteCount,
    }
  }, [allInstruments, instruments])

  async function handleCreateInstrument(payload: {
    instrument_name: string
    instrument_type: InstrumentType
    currency: string
    identifiers: PlatformInstrumentIdentifier[]
  }) {
    try {
      const created = await fetchJson<PlatformInstrumentRecord>('/api/instruments', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setInstruments((current) => upsertInstrumentRecord(current, created, showInactive))
      setAllInstruments((current) => upsertInstrumentRecord(current, created, true))
      setRegistryNotice(`Created instrument "${created.instrument_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to create instrument.',
      )
    }
  }

  async function handleUpsertFxRate(payload: {
    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    rate: string
    as_of_date: string
    provider?: string | null
    status: DataStatus
  }) {
    const previousWrite = fxWriteQueueRef.current
    let finishWrite: () => void = () => undefined
    fxWriteQueueRef.current = new Promise<void>((resolve) => {
      finishWrite = resolve
    })
    await previousWrite.catch(() => undefined)
    try {
      const updated = await fetchJson<PlatformFxRateRecord>('/api/fx-rates', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      const refreshSequence = ++registryReadSequenceRef.current
      const [refreshedAllInstruments, refreshedFxRates] = await Promise.all([
        fetchJson<PlatformInstrumentsResponse>('/api/instruments?include_inactive=true'),
        fetchJson<PlatformFxRatesResponse>('/api/fx-rates'),
      ])
      if (refreshSequence !== registryReadSequenceRef.current) {
        return
      }
      setInstruments(
        instrumentsForVisibility(refreshedAllInstruments.instruments, showInactiveRef.current),
      )
      setAllInstruments(refreshedAllInstruments.instruments)
      setFxRates(refreshedFxRates)
      setRegistryNotice(
        `Updated ${formatFxPairLabel(updated.base_currency, updated.quote_currency)} to ${updated.rate}.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to update FX rate.',
      )
    } finally {
      finishWrite()
    }
  }

  async function handleUpsertMarketData(payload: {
    instrument_id: string
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: string
    value: string
    currency: string
    provider?: string | null
    status: DataStatus
  }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/market-data`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(`Updated ${formatBasisLabel(payload.quote_basis)} for "${updated.instrument_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to save market data.',
      )
    }
  }

  async function handleUpdateSourceSettings(payload: {
    instrument_id: string
    source_mode: SourceMode
    source_email: string
    source_location: string
    source_api_profile: string
    expected_frequency: ExpectedFrequency
    market_calendar: string | null
    release_lag_days: number
  }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/source-settings`,
        {
          method: 'PUT',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(`Saved shared source settings for "${updated.instrument_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to save source settings.',
      )
    }
  }

  async function handleTriggerRefresh(payload: {
    instrument_id: string
    updated_by?: string | null
    source?: RefreshChannel
  }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/refresh`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(updated.refresh_status.message || `Triggered refresh for "${updated.instrument_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to trigger refresh.',
      )
    }
  }

  async function handleTriggerChannelRefresh(payload: {
    source: RefreshChannel
    updated_by?: string | null
    full_history?: boolean
  }) {
    try {
      const response = await fetchJson<PlatformBulkRefreshResponse>('/api/instruments/refresh', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      const refreshSequence = ++registryReadSequenceRef.current
      const [refreshedAllInstruments, refreshedFxRates] = await Promise.all([
        fetchJson<PlatformInstrumentsResponse>('/api/instruments?include_inactive=true'),
        fetchJson<PlatformFxRatesResponse>('/api/fx-rates'),
      ])
      if (refreshSequence !== registryReadSequenceRef.current) {
        return
      }
      setInstruments(
        instrumentsForVisibility(refreshedAllInstruments.instruments, showInactiveRef.current),
      )
      setAllInstruments(refreshedAllInstruments.instruments)
      setFxRates(refreshedFxRates)
      setRegistryNotice(
        `Refresh ${response.source}: ${response.refreshed_count} updated, ${response.results.length - response.refreshed_count} checked, ${response.skipped_count} skipped.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to refresh source channel.',
      )
    }
  }

  async function handleImportNavText(payload: {
    instrument_id: string
    raw_text: string
    provider?: string | null
    status: DataStatus
    updated_by?: string | null
  }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/nav-import`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(updated.refresh_status.message || `Imported NAV history for "${updated.instrument_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to import NAV history.',
      )
    }
  }

  async function handleImportNavFile(payload: {
    instrument_id: string
    file_name: string
    file_content_base64: string
    provider?: string | null
    status: DataStatus
    updated_by?: string | null
  }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/nav-import/file`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(updated.refresh_status.message || `Imported NAV history for "${updated.instrument_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to import NAV file.',
      )
    }
  }

  async function handleArchiveInstrument(payload: { instrument_id: string; updated_by?: string | null }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/archive`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(`Archived "${updated.instrument_name}". Downstream search now hides it by default.`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to archive instrument.',
      )
    }
  }

  async function handleRestoreInstrument(payload: { instrument_id: string; updated_by?: string | null }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/restore`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(`Restored "${updated.instrument_name}" to downstream search.`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to restore instrument.',
      )
    }
  }

  async function handleFundNavMutationCommitted(instrumentId: string) {
    try {
      const detail = await fetchJson<PlatformInstrumentDetail>(
        `/api/instruments/${encodeURIComponent(instrumentId)}`,
      )
      setInstruments((current) => upsertInstrumentRecord(current, detail, showInactive))
      setAllInstruments((current) => upsertInstrumentRecord(current, detail, true))
      setRegistryError(null)
      return detail
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error
          ? requestError.message
          : 'The NAV event was confirmed, but the refreshed instrument failed to load.',
      )
      throw requestError
    }
  }

  if (currentPath === INSTRUMENT_REGISTRY_PATH) {
    return (
      <InstrumentsPage
        instruments={instruments}
        registrySummary={registrySummary}
        fxRates={fxRates}
        loading={loadingInstruments}
        error={registryError}
        notice={registryNotice}
        showInactive={showInactive}
        onToggleShowInactive={() =>
          setShowInactive((current) => {
            const next = !current
            setInstruments(instrumentsForVisibility(allInstruments, next))
            return next
          })
        }
        onCreateInstrument={handleCreateInstrument}
        onUpsertFxRate={handleUpsertFxRate}
        onUpsertMarketData={handleUpsertMarketData}
        onUpdateSourceSettings={handleUpdateSourceSettings}
        onTriggerRefresh={handleTriggerRefresh}
        onTriggerChannelRefresh={handleTriggerChannelRefresh}
        onImportNavText={handleImportNavText}
        onImportNavFile={handleImportNavFile}
        onFundNavMutationCommitted={handleFundNavMutationCommitted}
        onArchiveInstrument={handleArchiveInstrument}
        onRestoreInstrument={handleRestoreInstrument}
      />
    )
  }

  return <DataOperationsDashboard />
}
