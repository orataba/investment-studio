import {
  ChangeEvent,
  FormEvent,
  KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { LanguageSelector } from '../../../../packages/ui/src/i18n'
import { useModalDialog } from '../../../../packages/ui/src/useModalDialog'
import {
  beginRequest,
  invalidateRequests,
  isRequestCurrent,
} from '../../../../packages/ui/src/requestIdentity'
import type {
  DataStatus,
  ExpectedFrequency,
  IdentifierType,
  InstrumentType,
  MetricFamily,
  QuoteBasis,
  QuoteRole,
  ReturnSemantics,
} from '../../../../packages/instrument-core/ts/src'
import {
  canonicalPriceContract,
  supportsNavHistoryImport,
} from '../../../../packages/instrument-core/ts/src'
import { FundNavActionReview } from './FundNavActionReview'
import { NavImportButton } from './NavImportButton'
import { PriceContractFields } from './PriceContractFields'
import { SourceScheduleFields } from './SourceScheduleFields'
import {
  defaultMarketDataSelection,
  formatPriceContract,
  formatPriceUnit,
  quoteBasisOptions,
} from './marketDataContract'
import { summarizeRoleQuotes } from './quoteRoleResolution'
import {
  allowedFamiliesForInstrument,
  bytesToBase64,
  type CreateInstrumentPayload,
  type CurrencyCode,
  currentLocalDate,
  fetchJson,
  findFxRate,
  formatBasisLabel,
  formatCoverageLabel,
  formatDecimalValue,
  formatFxPairLabel,
  formatFxSourceKind,
  formatLifecycleLabel,
  formatPointValue,
  formatPolicyPath,
  formatPrimaryQuoteDetail,
  formatSourceMode,
  type ImportNavFilePayload,
  type ImportNavTextPayload,
  latestQuoteSnapshot,
  type LifecycleTransitionPayload,
  marketDataToCsv,
  type PlatformFxRatesResponse,
  type PlatformInstrumentDetail,
  type PlatformInstrumentRecord,
  type PlatformMarketDataPoint,
  type PlatformNavImportPreviewResponse,
  type PlatformRegistrySummary,
  primaryIdentifier,
  type RefreshChannel,
  type SourceMode,
  type TriggerChannelRefreshPayload,
  type TriggerRefreshPayload,
  type UpdateSourceSettingsPayload,
  type UpsertFxRatePayload,
  type UpsertMarketDataPayload,
  parseFxPairLabel,
} from './instrumentRegistryModel'
import { detailForSelection } from './instrumentVisibility'

const ROLE_LABELS: Record<QuoteRole, string> = {
  trading: 'Trading',
  valuation: 'Valuation',
  total_return: 'Total return',
  chart: 'Chart',
  reference: 'Reference',
}

type ActionPanel = 'create' | 'quote' | 'source' | 'nav' | 'fx'
type InspectorTab = 'overview' | 'series' | 'source' | 'governance'
type SortMode = 'name_asc' | 'quote_desc' | 'identifier_asc' | 'coverage'

type InstrumentRegistryPageProps = {
  instruments: PlatformInstrumentRecord[]
  registrySummary: PlatformRegistrySummary
  fxRates: PlatformFxRatesResponse | null
  loading: boolean
  error: string | null
  notice: string | null
  showInactive: boolean
  onToggleShowInactive: () => void
  onCreateInstrument: (payload: CreateInstrumentPayload) => Promise<void>
  onUpsertFxRate: (payload: UpsertFxRatePayload) => Promise<void>
  onUpsertMarketData: (payload: UpsertMarketDataPayload) => Promise<void>
  onUpdateSourceSettings: (payload: UpdateSourceSettingsPayload) => Promise<void>
  onTriggerRefresh: (payload: TriggerRefreshPayload) => Promise<void>
  onTriggerChannelRefresh: (payload: TriggerChannelRefreshPayload) => Promise<void>
  onImportNavText: (payload: ImportNavTextPayload) => Promise<void>
  onImportNavFile: (payload: ImportNavFilePayload) => Promise<void>
  onFundNavMutationCommitted: (instrumentId: string) => Promise<PlatformInstrumentDetail>
  onArchiveInstrument: (payload: LifecycleTransitionPayload) => Promise<void>
  onRestoreInstrument: (payload: LifecycleTransitionPayload) => Promise<void>
}

function initialInstrumentSelection() {
  if (typeof window === 'undefined') return ''
  return new URLSearchParams(window.location.search).get('instrument')?.trim() ?? ''
}

function updateInstrumentQuery(instrumentId: string) {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  if (instrumentId) {
    url.searchParams.set('instrument', instrumentId)
  } else {
    url.searchParams.delete('instrument')
  }
  window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
}

function formatFrequency(value: ExpectedFrequency) {
  return value.replace(/_/g, ' ')
}

function formatDateTime(value: string | null) {
  if (!value) return 'Never'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function fileSafeIdentifier(value: string) {
  return value.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'instrument'
}

export default function InstrumentRegistryPage({
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
}: InstrumentRegistryPageProps) {
  const [instrumentName, setInstrumentName] = useState('')
  const [instrumentType, setInstrumentType] = useState<InstrumentType>('equity')
  const [currency, setCurrency] = useState('CNY')
  const [identifierType, setIdentifierType] = useState<IdentifierType>('ticker')
  const [identifierValue, setIdentifierValue] = useState('')
  const [selectedInstrumentId, setSelectedInstrumentId] = useState(initialInstrumentSelection)
  const [metricFamily, setMetricFamily] = useState<MetricFamily>('price')
  const [quoteBasis, setQuoteBasis] = useState<QuoteBasis>('close')
  const [metricValue, setMetricValue] = useState('')
  const [metricCurrency, setMetricCurrency] = useState('CNY')
  const [metricDate, setMetricDate] = useState(currentLocalDate)
  const [metricStatus, setMetricStatus] = useState<DataStatus>('complete')
  const [sourceMode, setSourceMode] = useState<SourceMode>('manual')
  const [sourceEmail, setSourceEmail] = useState('')
  const [sourceLocation, setSourceLocation] = useState('Database Dashboard')
  const [sourceApiProfile, setSourceApiProfile] = useState('')
  const [expectedFrequency, setExpectedFrequency] =
    useState<ExpectedFrequency>('event_driven')
  const [marketCalendar, setMarketCalendar] = useState('')
  const [releaseLagDays, setReleaseLagDays] = useState(0)
  const [returnSemantics, setReturnSemantics] = useState<ReturnSemantics>('unknown')
  const [navImportText, setNavImportText] = useState('')
  const [navImportFileName, setNavImportFileName] = useState('')
  const [navImportFileContent, setNavImportFileContent] = useState('')
  const [navImportFileInputKey, setNavImportFileInputKey] = useState(0)
  const [navPreview, setNavPreview] = useState<PlatformNavImportPreviewResponse | null>(null)
  const [navPreviewError, setNavPreviewError] = useState<string | null>(null)
  const [selectedInstrumentDetail, setSelectedInstrumentDetail] =
    useState<PlatformInstrumentDetail | null>(null)
  const [selectedInstrumentDetailError, setSelectedInstrumentDetailError] =
    useState<string | null>(null)
  const [selectedInstrumentDetailLoading, setSelectedInstrumentDetailLoading] = useState(false)
  const [editableFxPair, setEditableFxPair] = useState('')
  const [fxRateValue, setFxRateValue] = useState('')
  const [fxRateDate, setFxRateDate] = useState(currentLocalDate)
  const [fxRateStatus, setFxRateStatus] = useState<DataStatus>('complete')
  const [activePanel, setActivePanel] = useState<ActionPanel | null>(null)
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('overview')
  const [searchText, setSearchText] = useState('')
  const [instrumentTypeFilter, setInstrumentTypeFilter] =
    useState<'all' | InstrumentType>('all')
  const [coverageFilter, setCoverageFilter] = useState<'all' | DataStatus>('all')
  const [sourceFilter, setSourceFilter] = useState<'all' | SourceMode>('all')
  const [quoteFilter, setQuoteFilter] =
    useState<'all' | 'has_quote' | 'missing_quote'>('all')
  const [sortMode, setSortMode] = useState<SortMode>('name_asc')
  const [refreshChannel, setRefreshChannel] = useState<RefreshChannel>('all')
  const [registryPage, setRegistryPage] = useState(1)
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false)
  const [seriesFamilyFilter, setSeriesFamilyFilter] =
    useState<'all' | MetricFamily>('all')
  const [seriesBasisFilter, setSeriesBasisFilter] = useState<'all' | QuoteBasis>('all')
  const [seriesStartDate, setSeriesStartDate] = useState('')
  const [seriesEndDate, setSeriesEndDate] = useState('')
  const [seriesPage, setSeriesPage] = useState(1)
  const [archiveConfirmation, setArchiveConfirmation] = useState(false)
  const detailRequestSequenceRef = useRef(0)
  const selectedInstrumentIdRef = useRef(selectedInstrumentId)
  const actionDrawerRef = useModalDialog(Boolean(activePanel), () => setActivePanel(null))

  selectedInstrumentIdRef.current = selectedInstrumentId

  const selectedInstrument =
    instruments.find((item) => item.instrument_id === selectedInstrumentId) ?? null
  const currentSelectedInstrumentDetail = detailForSelection(
    selectedInstrumentDetail,
    selectedInstrumentId,
  )
  const selectedInstrumentMarketHistory = useMemo(
    () =>
      [...(currentSelectedInstrumentDetail?.market_data ?? [])].sort((left, right) => {
        if (left.as_of_date === right.as_of_date) {
          return `${left.metric_family}:${left.quote_basis}`.localeCompare(
            `${right.metric_family}:${right.quote_basis}`,
          )
        }
        return right.as_of_date.localeCompare(left.as_of_date)
      }),
    [currentSelectedInstrumentDetail],
  )
  const selectedQuoteSummary = useMemo(
    () => (selectedInstrument ? summarizeRoleQuotes(selectedInstrument) : []),
    [selectedInstrument],
  )
  const selectedQuoteSnapshot = useMemo(
    () => (selectedInstrument ? latestQuoteSnapshot(selectedInstrument) : null),
    [selectedInstrument],
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
      quoteBasisOptions(metricFamily),
    [metricFamily],
  )
  const priceContract = canonicalPriceContract(
    selectedInstrument?.instrument_type ?? 'other',
    metricFamily,
  )

  const editableFxPairs = useMemo(
    () =>
      (fxRates?.maintained_pairs ?? [])
        .map((pairLabel) =>
          parseFxPairLabel(pairLabel, fxRates?.supported_currencies ?? []),
        )
        .filter((pair): pair is [CurrencyCode, CurrencyCode] => pair !== null),
    [fxRates],
  )
  const selectedEditableFxPair = useMemo(() => {
    const pair = parseFxPairLabel(editableFxPair, fxRates?.supported_currencies ?? [])
    return pair ? { baseCurrency: pair[0], quoteCurrency: pair[1] } : null
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
  const fxPanelRates = useMemo(() => {
    const supportedCurrencies = fxRates?.supported_currencies ?? []
    return supportedCurrencies.flatMap((baseCurrency, baseIndex) =>
      supportedCurrencies.slice(baseIndex + 1).map((quoteCurrency) => ({
        pairLabel: formatFxPairLabel(baseCurrency, quoteCurrency),
        record: fxRates ? findFxRate(fxRates.rates, baseCurrency, quoteCurrency) : null,
      })),
    )
  }, [fxRates])

  const filteredInstruments = useMemo(() => {
    const searchNeedle = searchText.trim().toLowerCase()
    const filtered = instruments.filter((item) => {
      if (instrumentTypeFilter !== 'all' && item.instrument_type !== instrumentTypeFilter) {
        return false
      }
      if (coverageFilter !== 'all' && item.coverage_state !== coverageFilter) return false
      if (sourceFilter !== 'all' && item.source_settings.source_mode !== sourceFilter) {
        return false
      }
      const latestQuoteDate = latestQuoteSnapshot(item).latestQuoteDate
      if (quoteFilter === 'has_quote' && !latestQuoteDate) return false
      if (quoteFilter === 'missing_quote' && latestQuoteDate) return false
      if (!searchNeedle) return true
      return [
        item.instrument_name,
        item.instrument_id,
        primaryIdentifier(item),
        ...item.identifiers.map((identifier) => identifier.identifier_value),
      ].some((value) => value.toLowerCase().includes(searchNeedle))
    })
    return [...filtered].sort((left, right) => {
      if (sortMode === 'identifier_asc') {
        return primaryIdentifier(left).localeCompare(primaryIdentifier(right))
      }
      if (sortMode === 'quote_desc') {
        const leftQuote = latestQuoteSnapshot(left).latestQuoteDate ?? ''
        const rightQuote = latestQuoteSnapshot(right).latestQuoteDate ?? ''
        return rightQuote === leftQuote
          ? left.instrument_name.localeCompare(right.instrument_name)
          : rightQuote.localeCompare(leftQuote)
      }
      if (sortMode === 'coverage') {
        const rank: Record<DataStatus, number> = {
          complete: 0,
          partial: 1,
          unavailable: 2,
        }
        return rank[left.coverage_state] === rank[right.coverage_state]
          ? left.instrument_name.localeCompare(right.instrument_name)
          : rank[left.coverage_state] - rank[right.coverage_state]
      }
      return left.instrument_name.localeCompare(right.instrument_name)
    })
  }, [
    coverageFilter,
    instrumentTypeFilter,
    instruments,
    quoteFilter,
    searchText,
    sortMode,
    sourceFilter,
  ])

  const activeRecords = useMemo(
    () => instruments.filter((item) => item.lifecycle_state.status === 'active'),
    [instruments],
  )
  const recordsWithQuotes = useMemo(
    () => activeRecords.filter((item) => latestQuoteSnapshot(item).latestQuoteDate),
    [activeRecords],
  )
  const refreshIssueCount = useMemo(
    () =>
      activeRecords.filter((item) =>
        ['failed', 'blocked'].includes(item.refresh_status.status.toLowerCase()),
      ).length,
    [activeRecords],
  )
  const missingSelectedQuoteCount = activeRecords.length - recordsWithQuotes.length
  const operationalExceptionCount = useMemo(
    () =>
      activeRecords.filter(
        (item) =>
          ['failed', 'blocked'].includes(item.refresh_status.status.toLowerCase()) ||
          !latestQuoteSnapshot(item).latestQuoteDate,
      ).length,
    [activeRecords],
  )
  const latestRegistryMarketDate = useMemo(
    () => {
      const marketDates = activeRecords
        .map((item) => latestQuoteSnapshot(item).latestQuoteDate)
        .filter((value): value is string => Boolean(value))
        .sort()
      return marketDates[marketDates.length - 1] ?? null
    },
    [activeRecords],
  )
  const coveragePercent = activeRecords.length
    ? Math.round((recordsWithQuotes.length / activeRecords.length) * 100)
    : 0
  const selectedInstrumentHiddenByFilters =
    Boolean(selectedInstrument) &&
    !filteredInstruments.some(
      (item) => item.instrument_id === selectedInstrument?.instrument_id,
    )

  const registryPageSize = 30
  const registryPageCount = Math.max(
    1,
    Math.ceil(filteredInstruments.length / registryPageSize),
  )
  const effectiveRegistryPage = Math.min(registryPage, registryPageCount)
  const pagedInstruments = filteredInstruments.slice(
    (effectiveRegistryPage - 1) * registryPageSize,
    effectiveRegistryPage * registryPageSize,
  )

  const seriesFamilyOptions = useMemo(
    () =>
      [...new Set(selectedInstrumentMarketHistory.map((point) => point.metric_family))].sort(),
    [selectedInstrumentMarketHistory],
  )
  const seriesBasisOptions = useMemo(
    () =>
      [...new Set(selectedInstrumentMarketHistory.map((point) => point.quote_basis))].sort(),
    [selectedInstrumentMarketHistory],
  )
  const filteredSeries = useMemo(
    () =>
      selectedInstrumentMarketHistory.filter((point) => {
        if (seriesFamilyFilter !== 'all' && point.metric_family !== seriesFamilyFilter) {
          return false
        }
        if (seriesBasisFilter !== 'all' && point.quote_basis !== seriesBasisFilter) {
          return false
        }
        if (seriesStartDate && point.as_of_date < seriesStartDate) return false
        if (seriesEndDate && point.as_of_date > seriesEndDate) return false
        return true
      }),
    [
      selectedInstrumentMarketHistory,
      seriesBasisFilter,
      seriesEndDate,
      seriesFamilyFilter,
      seriesStartDate,
    ],
  )
  const seriesPageSize = 25
  const seriesPageCount = Math.max(1, Math.ceil(filteredSeries.length / seriesPageSize))
  const effectiveSeriesPage = Math.min(seriesPage, seriesPageCount)
  const pagedSeries = filteredSeries.slice(
    (effectiveSeriesPage - 1) * seriesPageSize,
    effectiveSeriesPage * seriesPageSize,
  )

  useEffect(() => {
    setRegistryPage(1)
  }, [
    coverageFilter,
    instrumentTypeFilter,
    quoteFilter,
    searchText,
    sortMode,
    sourceFilter,
  ])

  useEffect(() => {
    setSeriesPage(1)
  }, [seriesBasisFilter, seriesEndDate, seriesFamilyFilter, seriesStartDate])

  useEffect(() => {
    if (
      !loading &&
      selectedInstrumentId &&
      instruments.length &&
      !instruments.some((item) => item.instrument_id === selectedInstrumentId)
    ) {
      clearSelectedInstrument()
    }
  }, [instruments, loading, selectedInstrumentId])

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
    if (!selectedInstrument) return
    setSourceMode(selectedInstrument.source_settings.source_mode)
    setSourceEmail(selectedInstrument.source_settings.source_email)
    setSourceLocation(selectedInstrument.source_settings.source_location)
    setSourceApiProfile(selectedInstrument.source_settings.source_api_profile)
    setExpectedFrequency(selectedInstrument.source_settings.expected_frequency)
    setMarketCalendar(selectedInstrument.source_settings.market_calendar ?? '')
    setReleaseLagDays(selectedInstrument.source_settings.release_lag_days)
    setReturnSemantics(selectedInstrument.source_settings.return_semantics ?? 'unknown')
  }, [selectedInstrument])

  useEffect(() => {
    if (!allowedMetricFamilies.includes(metricFamily)) {
      const nextFamily = allowedMetricFamilies[0]
      setMetricFamily(nextFamily)
      setQuoteBasis(
        quoteBasisOptions(nextFamily)[0].value,
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
    const labels = editableFxPairs.map(([baseCurrency, quoteCurrency]) =>
      formatFxPairLabel(baseCurrency, quoteCurrency),
    )
    if (!labels.length) {
      setEditableFxPair('')
    } else if (!labels.includes(editableFxPair)) {
      setEditableFxPair(labels[0])
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

  async function refreshSelectedInstrumentDetail(instrumentId: string) {
    if (instrumentId !== selectedInstrumentIdRef.current) return
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
          : 'Failed to load the instrument record.',
      )
    } finally {
      if (isRequestCurrent(detailRequestSequenceRef, request, selectedInstrumentIdRef.current)) {
        setSelectedInstrumentDetailLoading(false)
      }
    }
  }

  function syncSelectedInstrument(instrumentId: string) {
    const selectionChanged = instrumentId !== selectedInstrumentIdRef.current
    if (selectionChanged) {
      invalidateRequests(detailRequestSequenceRef)
      setSelectedInstrumentDetail(null)
      setSelectedInstrumentDetailError(null)
      setSelectedInstrumentDetailLoading(Boolean(instrumentId))
      setInspectorTab('overview')
      setSeriesFamilyFilter('all')
      setSeriesBasisFilter('all')
      setSeriesStartDate('')
      setSeriesEndDate('')
      setArchiveConfirmation(false)
    }
    selectedInstrumentIdRef.current = instrumentId
    setSelectedInstrumentId(instrumentId)
    updateInstrumentQuery(instrumentId)
    const selected = instruments.find((item) => item.instrument_id === instrumentId)
    if (!selected) return
    if (!supportsNavHistoryImport(selected.instrument_type)) {
      setActivePanel((current) => (current === 'nav' ? null : current))
    }
    const defaults = defaultMarketDataSelection(selected.instrument_type)
    setMetricFamily(defaults.metric_family)
    setQuoteBasis(defaults.quote_basis)
    setMetricCurrency(selected.currency)
  }

  function clearSelectedInstrument() {
    invalidateRequests(detailRequestSequenceRef)
    selectedInstrumentIdRef.current = ''
    setSelectedInstrumentId('')
    setSelectedInstrumentDetail(null)
    setSelectedInstrumentDetailError(null)
    setSelectedInstrumentDetailLoading(false)
    setArchiveConfirmation(false)
    updateInstrumentQuery('')
  }

  function openActionPanel(panel: ActionPanel, instrumentId?: string) {
    const targetInstrument = instrumentId
      ? instruments.find((item) => item.instrument_id === instrumentId)
      : selectedInstrument
    if (
      panel === 'nav' &&
      (!targetInstrument || !supportsNavHistoryImport(targetInstrument.instrument_type))
    ) {
      return
    }
    if (panel === 'quote' && targetInstrument?.instrument_type === 'fund') return
    if (instrumentId) syncSelectedInstrument(instrumentId)
    setActivePanel(panel)
  }

  function clearTableFilters() {
    setSearchText('')
    setInstrumentTypeFilter('all')
    setCoverageFilter('all')
    setSourceFilter('all')
    setQuoteFilter('all')
    setSortMode('name_asc')
  }

  async function panelActionSucceeded(action: () => Promise<void>) {
    try {
      await action()
      return true
    } catch {
      return false
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const saved = await panelActionSucceeded(() => onCreateInstrument({
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
      broker_identifiers: [],
    }))
    if (!saved) return
    setInstrumentName('')
    setIdentifierValue('')
  }

  async function handleMetricSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const saved = await panelActionSucceeded(() => onUpsertMarketData({
      instrument_id: selectedInstrumentId,
      metric_family: metricFamily,
      quote_basis: quoteBasis,
      as_of_date: metricDate,
      value: metricValue.trim(),
      currency: metricCurrency.trim().toUpperCase(),
      status: metricStatus,
      provider: 'platform_manual',
    }))
    if (!saved) return
    setMetricValue('')
    await refreshSelectedInstrumentDetail(selectedInstrumentId)
  }

  async function handleSourceSettingsSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedInstrumentId || !selectedInstrument) return
    const saved = await panelActionSucceeded(() => onUpdateSourceSettings({
      instrument_id: selectedInstrumentId,
      source_mode: sourceMode,
      source_email: sourceEmail.trim(),
      source_location: sourceLocation.trim(),
      source_api_profile: sourceApiProfile.trim(),
      expected_frequency: expectedFrequency,
      market_calendar: marketCalendar.trim() || null,
      release_lag_days: releaseLagDays,
      return_semantics:
        selectedInstrument.instrument_type === 'index' ? returnSemantics : 'unknown',
    }))
    if (!saved) return
    await refreshSelectedInstrumentDetail(selectedInstrumentId)
  }

  async function handleRefreshClick() {
    if (!selectedInstrumentId) return
    const refreshed = await panelActionSucceeded(() => onTriggerRefresh({
      instrument_id: selectedInstrumentId,
      updated_by: 'platform_ui',
      source: refreshChannel === 'all' ? 'configured' : refreshChannel,
    }))
    if (!refreshed) return
    await refreshSelectedInstrumentDetail(selectedInstrumentId)
  }

  async function handleRefreshChannelClick() {
    const refreshed = await panelActionSucceeded(() => onTriggerChannelRefresh({
      source: refreshChannel,
      updated_by: 'platform_ui',
    }))
    if (!refreshed) return
    if (selectedInstrumentId) await refreshSelectedInstrumentDetail(selectedInstrumentId)
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
    if (!selectedInstrumentId) return
    const payload =
      navImportFileName && navImportFileContent
        ? { file_name: navImportFileName, file_content_base64: navImportFileContent }
        : { raw_text: navImportText.trim() }
    if (!('raw_text' in payload ? payload.raw_text : payload.file_name)) return
    try {
      const preview = await fetchJson<PlatformNavImportPreviewResponse>(
        `/api/instruments/${encodeURIComponent(selectedInstrumentId)}/nav-import/preview`,
        { method: 'POST', body: JSON.stringify(payload) },
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
    if (!selectedInstrumentId) return
    if (navImportFileName && navImportFileContent) {
      const saved = await panelActionSucceeded(() => onImportNavFile({
        instrument_id: selectedInstrumentId,
        file_name: navImportFileName,
        file_content_base64: navImportFileContent,
        provider: 'platform_file_import',
        status: 'complete',
        updated_by: 'platform_ui',
      }))
      if (!saved) return
    } else if (navImportText.trim()) {
      const saved = await panelActionSucceeded(() => onImportNavText({
        instrument_id: selectedInstrumentId,
        raw_text: navImportText.trim(),
        provider: 'platform_paste_import',
        status: 'complete',
        updated_by: 'platform_ui',
      }))
      if (!saved) return
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

  async function handleFxSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedEditableFxPair) return
    await onUpsertFxRate({
      base_currency: selectedEditableFxPair.baseCurrency,
      quote_currency: selectedEditableFxPair.quoteCurrency,
      rate: fxRateValue.trim(),
      as_of_date: fxRateDate,
      provider: 'platform_fx_manual',
      status: fxRateStatus,
    })
  }

  async function handleRestoreSelectedInstrument() {
    if (!selectedInstrument) return
    await panelActionSucceeded(() => onRestoreInstrument({
      instrument_id: selectedInstrument.instrument_id,
      updated_by: 'platform_ui',
    }))
  }

  async function handleArchiveSelectedInstrument() {
    if (!selectedInstrument) return
    const archived = await panelActionSucceeded(() => onArchiveInstrument({
      instrument_id: selectedInstrument.instrument_id,
      updated_by: 'platform_ui',
    }))
    if (archived) setArchiveConfirmation(false)
  }

  function handleEditPoint(point: PlatformMarketDataPoint) {
    if (!selectedInstrument || selectedInstrument.instrument_type === 'fund') {
      if (selectedInstrument?.instrument_type === 'fund') openActionPanel('nav')
      return
    }
    setMetricFamily(point.metric_family)
    setQuoteBasis(point.quote_basis)
    setMetricValue(point.value)
    setMetricCurrency(point.currency)
    setMetricDate(point.as_of_date)
    setMetricStatus(point.status)
    openActionPanel('quote')
  }

  function downloadFilteredSeries() {
    if (!selectedInstrument || !filteredSeries.length) return
    const csv = marketDataToCsv(filteredSeries)
    const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${fileSafeIdentifier(primaryIdentifier(selectedInstrument))}-series.csv`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  }

  function handleRowKeyDown(
    event: KeyboardEvent<HTMLTableRowElement>,
    instrumentId: string,
  ) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      syncSelectedInstrument(instrumentId)
    }
  }

  return (
    <main className="platform-shell ir-shell">
      <header className="platform-masthead data-ops-masthead ir-masthead">
        <a className="data-ops-brand" href="/">
          <span>Portfolio Operations</span>
          <strong>Data Operations</strong>
        </a>
        <nav className="data-ops-nav" aria-label="Data operations navigation">
          <a className="platform-nav-link" href="/">Overview</a>
          <a
            className="platform-nav-link platform-nav-link-active"
            href="/instruments"
            aria-current="page"
          >
            Instrument Registry
          </a>
        </nav>
        <LanguageSelector />
      </header>

      <section className="ir-pagehead">
        <div>
          <div className="registry-breadcrumbs">
            <a href="/">Data Operations</a>
            <span>/</span>
            <span>Instrument Registry</span>
          </div>
          <div className="registry-kicker">Canonical data administration</div>
          <h1>Instrument Registry</h1>
          <p>
            Find a canonical instrument, assess whether its data is usable, then make a
            controlled repair. Portfolio and Watchlist consume this layer without owning
            duplicate records.
          </p>
        </div>
        <div className="ir-page-actions">
          <div className="ir-refresh-control">
            <label>
              <span>Refresh source</span>
              <select
                value={refreshChannel}
                onChange={(event) =>
                  setRefreshChannel(event.target.value as RefreshChannel)
                }
              >
                <option value="all">Email + Tushare</option>
                <option value="email">Email</option>
                <option value="tushare">Tushare</option>
              </select>
            </label>
            <button
              type="button"
              className="registry-submit secondary"
              onClick={() => void handleRefreshChannelClick()}
            >
              Refresh data
            </button>
          </div>
          <button
            type="button"
            className="registry-submit secondary"
            onClick={() => openActionPanel('fx')}
          >
            FX rates
          </button>
          <button
            type="button"
            className="registry-submit ir-primary-action"
            onClick={() => openActionPanel('create')}
          >
            <span aria-hidden="true">＋</span>
            Register instrument
          </button>
        </div>
      </section>

      {notice ? (
        <div className="ir-message ir-message-success" role="status">
          <span className="ir-message-dot" aria-hidden="true" />
          <span>{notice}</span>
        </div>
      ) : null}
      {error ? (
        <div className="ir-message ir-message-error" role="alert">
          <span className="ir-message-dot" aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : null}

      <section className="ir-health-grid" aria-label="Registry operating status">
        <article>
          <div className={`ir-health-icon ${error ? 'is-warning' : 'is-ready'}`}>
            <span aria-hidden="true" />
          </div>
          <div>
            <span>Registry connection</span>
            <strong>{loading ? 'Checking…' : error ? 'Attention' : 'Online'}</strong>
            <small>{registrySummary.total_count} canonical records reachable</small>
          </div>
        </article>
        <article>
          <div className="ir-health-icon is-neutral">%</div>
          <div>
            <span>Active data coverage</span>
            <strong>{coveragePercent}%</strong>
            <small>{recordsWithQuotes.length} of {activeRecords.length} have a selected quote</small>
          </div>
        </article>
        <article>
          <div className="ir-health-icon is-neutral">↗</div>
          <div>
            <span>Latest observation</span>
            <strong>{latestRegistryMarketDate ?? '—'}</strong>
            <small>Latest selected quote across active records</small>
          </div>
        </article>
        <article className={operationalExceptionCount ? 'is-warning' : ''}>
          <div className={`ir-health-icon ${operationalExceptionCount ? 'is-warning' : 'is-ready'}`}>
            !
          </div>
          <div>
            <span>Pipeline exceptions</span>
            <strong>{operationalExceptionCount}</strong>
            <small>
              {refreshIssueCount} refresh failures · {missingSelectedQuoteCount} missing selected data
            </small>
          </div>
        </article>
      </section>

      <section className="ir-toolbar" aria-label="Registry filters">
        <div className="ir-search">
          <span className="ir-search-icon" aria-hidden="true">⌕</span>
          <input
            type="search"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            placeholder="Search name, code, identifier, or registry ID"
            aria-label="Search instruments"
          />
          {searchText ? (
            <button type="button" onClick={() => setSearchText('')} aria-label="Clear search">
              ×
            </button>
          ) : null}
        </div>
        <div className="ir-quick-filters" aria-label="Quick filters">
          <button
            type="button"
            className={
              instrumentTypeFilter === 'all' && quoteFilter === 'all' ? 'is-active' : ''
            }
            onClick={() => {
              setInstrumentTypeFilter('all')
              setQuoteFilter('all')
            }}
          >
            All
          </button>
          <button
            type="button"
            className={instrumentTypeFilter === 'fund' ? 'is-active' : ''}
            onClick={() => {
              setInstrumentTypeFilter('fund')
              setQuoteFilter('all')
            }}
          >
            Funds
          </button>
          <button
            type="button"
            className={quoteFilter === 'missing_quote' ? 'is-active' : ''}
            onClick={() => setQuoteFilter('missing_quote')}
          >
            Missing data
          </button>
        </div>
        <button
          type="button"
          className={`ir-filter-toggle${showAdvancedFilters ? ' is-active' : ''}`}
          onClick={() => setShowAdvancedFilters((current) => !current)}
          aria-expanded={showAdvancedFilters}
        >
          Filters
          <span aria-hidden="true">{showAdvancedFilters ? '−' : '+'}</span>
        </button>
        <label className="ir-archive-toggle">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={onToggleShowInactive}
          />
          <span>Include archived</span>
        </label>

        {showAdvancedFilters ? (
          <div className="ir-advanced-filters">
            <label>
              <span>Instrument type</span>
              <select
                value={instrumentTypeFilter}
                onChange={(event) =>
                  setInstrumentTypeFilter(event.target.value as 'all' | InstrumentType)
                }
              >
                <option value="all">All types</option>
                <option value="equity">Equity</option>
                <option value="index">Index</option>
                <option value="fund">Fund</option>
                <option value="etf">ETF</option>
                <option value="cash">Cash</option>
                <option value="fx">FX</option>
                <option value="other">Other</option>
              </select>
            </label>
            <label>
              <span>Coverage</span>
              <select
                value={coverageFilter}
                onChange={(event) =>
                  setCoverageFilter(event.target.value as 'all' | DataStatus)
                }
              >
                <option value="all">All coverage</option>
                <option value="complete">Complete</option>
                <option value="partial">Partial</option>
                <option value="unavailable">Unavailable</option>
              </select>
            </label>
            <label>
              <span>Source</span>
              <select
                value={sourceFilter}
                onChange={(event) =>
                  setSourceFilter(event.target.value as 'all' | SourceMode)
                }
              >
                <option value="all">All sources</option>
                <option value="api">API</option>
                <option value="email">Email</option>
                <option value="manual">Manual</option>
              </select>
            </label>
            <label>
              <span>Market data</span>
              <select
                value={quoteFilter}
                onChange={(event) =>
                  setQuoteFilter(
                    event.target.value as 'all' | 'has_quote' | 'missing_quote',
                  )
                }
              >
                <option value="all">All records</option>
                <option value="has_quote">Has selected quote</option>
                <option value="missing_quote">Missing selected quote</option>
              </select>
            </label>
            <label>
              <span>Sort by</span>
              <select
                value={sortMode}
                onChange={(event) => setSortMode(event.target.value as SortMode)}
              >
                <option value="name_asc">Name</option>
                <option value="quote_desc">Latest quote</option>
                <option value="identifier_asc">Identifier</option>
                <option value="coverage">Coverage</option>
              </select>
            </label>
            <button type="button" onClick={clearTableFilters}>Reset all filters</button>
          </div>
        ) : null}
      </section>

      {selectedInstrumentHiddenByFilters ? (
        <div className="ir-filter-warning">
          The selected instrument is outside the current result set.
          <button type="button" onClick={clearTableFilters}>Show it</button>
        </div>
      ) : null}

      <section className="ir-workspace">
        <div className="ir-registry-panel">
          <div className="ir-section-head">
            <div>
              <span>Canonical instruments</span>
              <h2>Registry records</h2>
            </div>
            <div className="ir-section-count">
              {loading ? 'Loading…' : `${filteredInstruments.length} results`}
              <small>
                {showInactive
                  ? `${registrySummary.active_count} active · ${registrySummary.archived_count} archived`
                  : `${registrySummary.active_count} active`}
              </small>
            </div>
          </div>
          <div className="ir-table-scroll">
            <table className="ir-registry-table">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Type</th>
                  <th>Selected quote</th>
                  <th>Source</th>
                  <th>Coverage</th>
                  <th aria-label="Open record" />
                </tr>
              </thead>
              <tbody>
                {!filteredInstruments.length ? (
                  <tr>
                    <td colSpan={6}>
                      <div className="ir-empty-table">
                        <strong>No matching instruments</strong>
                        <span>Change the search or reset the active filters.</span>
                        <button type="button" onClick={clearTableFilters}>Reset filters</button>
                      </div>
                    </td>
                  </tr>
                ) : null}
                {pagedInstruments.map((item) => {
                  const isSelected = item.instrument_id === selectedInstrumentId
                  const { officialNav, totalReturnNav, selectedQuote, latestQuoteDate } =
                    latestQuoteSnapshot(item)
                  return (
                    <tr
                      key={item.instrument_id}
                      className={`${isSelected ? 'is-selected' : ''}${
                        item.lifecycle_state.status === 'archived' ? ' is-archived' : ''
                      }`}
                      onClick={() => syncSelectedInstrument(item.instrument_id)}
                      onKeyDown={(event) => handleRowKeyDown(event, item.instrument_id)}
                      tabIndex={0}
                      aria-selected={isSelected}
                    >
                      <td>
                        <div className="ir-instrument-cell">
                          <span className="ir-type-avatar">
                            {item.instrument_type.slice(0, 2).toUpperCase()}
                          </span>
                          <div>
                            <strong>{item.instrument_name}</strong>
                            <span>
                              {primaryIdentifier(item)}
                              {primaryIdentifier(item) !== item.instrument_id
                                ? ` · ${item.instrument_id}`
                                : ''}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="ir-type-cell">
                          <strong>{item.instrument_type.toUpperCase()}</strong>
                          <span>{item.currency}</span>
                        </div>
                      </td>
                      <td>
                        <div className="ir-value-cell">
                          <strong>{formatPointValue(selectedQuote)}</strong>
                          <span>
                            {latestQuoteDate ?? 'No selected quote'}
                            {latestQuoteDate
                              ? ` · ${formatPrimaryQuoteDetail(
                                  selectedQuote,
                                  officialNav,
                                  totalReturnNav,
                                )}`
                              : ''}
                          </span>
                        </div>
                      </td>
                      <td>
                        <div className="ir-source-cell">
                          <strong>{formatSourceMode(item.source_settings.source_mode)}</strong>
                          <span>{item.refresh_status.status || 'idle'}</span>
                        </div>
                      </td>
                      <td>
                        <div className="ir-health-cell">
                          <span
                            className={`coverage-badge coverage-badge-${item.coverage_state}`}
                          >
                            {formatCoverageLabel(item.coverage_state)}
                          </span>
                          {item.lifecycle_state.status === 'archived' ? (
                            <span className="lifecycle-badge lifecycle-badge-archived">
                              Archived
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td><span className="ir-row-arrow" aria-hidden="true">›</span></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="ir-pagination">
            <span>
              Page {effectiveRegistryPage} of {registryPageCount}
            </span>
            <span>
              {filteredInstruments.length
                ? `${(effectiveRegistryPage - 1) * registryPageSize + 1}–${Math.min(
                    effectiveRegistryPage * registryPageSize,
                    filteredInstruments.length,
                  )} of ${filteredInstruments.length}`
                : '0 results'}
            </span>
            <div>
              <button
                type="button"
                disabled={effectiveRegistryPage <= 1}
                onClick={() => setRegistryPage((current) => Math.max(1, current - 1))}
                aria-label="Previous registry page"
              >
                ←
              </button>
              <button
                type="button"
                disabled={effectiveRegistryPage >= registryPageCount}
                onClick={() =>
                  setRegistryPage((current) => Math.min(registryPageCount, current + 1))
                }
                aria-label="Next registry page"
              >
                →
              </button>
            </div>
          </div>
        </div>

        <aside className="ir-inspector" aria-label="Selected instrument inspector">
          {selectedInstrument ? (
            <>
              <header className="ir-inspector-head">
                <div className="ir-inspector-identity">
                  <span className="ir-type-avatar is-large">
                    {selectedInstrument.instrument_type.slice(0, 2).toUpperCase()}
                  </span>
                  <div>
                    <span>{primaryIdentifier(selectedInstrument)}</span>
                    <h2>{selectedInstrument.instrument_name}</h2>
                    <small>
                      {selectedInstrument.instrument_type.toUpperCase()} ·{' '}
                      {selectedInstrument.currency} · {selectedInstrument.instrument_id}
                    </small>
                  </div>
                </div>
                <button
                  type="button"
                  className="ir-inspector-close"
                  onClick={clearSelectedInstrument}
                  aria-label="Close instrument inspector"
                >
                  ×
                </button>
                <div className="ir-inspector-actions">
                  {selectedInstrument.instrument_type === 'fund' ? (
                    <NavImportButton
                      instrumentType={selectedInstrument.instrument_type}
                      type="button"
                      className="registry-submit"
                      onClick={() => openActionPanel('nav')}
                    />
                  ) : (
                    <button
                      type="button"
                      className="registry-submit"
                      onClick={() => openActionPanel('quote')}
                    >
                      Add data point
                    </button>
                  )}
                  <button
                    type="button"
                    className="registry-submit secondary"
                    onClick={() => void handleRefreshClick()}
                  >
                    Refresh
                  </button>
                </div>
              </header>

              <nav className="ir-inspector-tabs" aria-label="Instrument inspector sections">
                {(
                  [
                    ['overview', 'Overview'],
                    ['series', 'Data series'],
                    ['source', 'Source'],
                    ['governance', 'Governance'],
                  ] as Array<[InspectorTab, string]>
                ).map(([tab, label]) => (
                  <button
                    type="button"
                    key={tab}
                    className={inspectorTab === tab ? 'is-active' : ''}
                    onClick={() => setInspectorTab(tab)}
                    aria-current={inspectorTab === tab ? 'page' : undefined}
                  >
                    {label}
                  </button>
                ))}
              </nav>

              {selectedInstrumentDetailError ? (
                <div className="ir-inspector-error">{selectedInstrumentDetailError}</div>
              ) : null}

              <div className="ir-inspector-body">
                {inspectorTab === 'overview' ? (
                  <div className="ir-inspector-stack">
                    <section className="ir-quote-hero">
                      <div>
                        <span>Selected valuation quote</span>
                        <strong>
                          {selectedQuoteSnapshot?.selectedQuote
                            ? formatDecimalValue(selectedQuoteSnapshot.selectedQuote.value)
                            : '—'}
                          {selectedQuoteSnapshot?.selectedQuote
                            ? ` ${selectedQuoteSnapshot.selectedQuote.currency}`
                            : ''}
                        </strong>
                        <small>
                          {selectedQuoteSnapshot?.selectedQuote
                            ? `${formatBasisLabel(
                                selectedQuoteSnapshot.selectedQuote.quote_basis,
                              )} · ${selectedQuoteSnapshot.selectedQuote.as_of_date}`
                            : 'No selected valuation quote'}
                        </small>
                      </div>
                      <span
                        className={`coverage-badge coverage-badge-${selectedInstrument.coverage_state}`}
                      >
                        {formatCoverageLabel(selectedInstrument.coverage_state)}
                      </span>
                    </section>


                    <section className="ir-inspector-section">
                      <div className="ir-subsection-head">
                        <div>
                          <span>Current read model</span>
                          <h3>Quote roles</h3>
                        </div>
                        <small>
                          {selectedQuoteSummary.length} selected
                        </small>
                      </div>
                      <div className="ir-role-list">
                        {selectedQuoteSummary.length ? (
                          selectedQuoteSummary.map(({ role, point }) => (
                            <div key={`${role}-${point.metric_family}-${point.quote_basis}`}>
                              <span>{ROLE_LABELS[role]}</span>
                              <strong title={point.value}>
                                {formatDecimalValue(point.value)} {point.currency}
                              </strong>
                              <small>
                                {formatBasisLabel(point.quote_basis)} · {point.as_of_date}
                              </small>
                            </div>
                          ))
                        ) : (
                          <div className="ir-inline-empty">No role-selected market data.</div>
                        )}
                      </div>
                    </section>

                    <section className="ir-inspector-section">
                      <div className="ir-subsection-head">
                        <div>
                          <span>Registry metadata</span>
                          <h3>Canonical definition</h3>
                        </div>
                      </div>
                      <dl className="ir-definition-list">
                        <div>
                          <dt>Primary identifier</dt>
                          <dd>{primaryIdentifier(selectedInstrument)}</dd>
                        </div>
                        <div>
                          <dt>Instrument ID</dt>
                          <dd>{selectedInstrument.instrument_id}</dd>
                        </div>
                        <div>
                          <dt>Currency</dt>
                          <dd>{selectedInstrument.currency}</dd>
                        </div>
                        <div>
                          <dt>Expected frequency</dt>
                          <dd>
                            {formatFrequency(
                              selectedInstrument.source_settings.expected_frequency,
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt>Valuation path</dt>
                          <dd>
                            {formatPolicyPath(
                              selectedInstrument.quote_selection_policy.valuation,
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt>Total return path</dt>
                          <dd>
                            {formatPolicyPath(
                              selectedInstrument.quote_selection_policy.total_return,
                            )}
                          </dd>
                        </div>
                      </dl>
                    </section>

                    <section className="ir-inspector-section">
                      <div className="ir-subsection-head">
                        <div>
                          <span>Identifiers</span>
                          <h3>Resolution keys</h3>
                        </div>
                      </div>
                      <div className="ir-identifier-list">
                        {selectedInstrument.identifiers.map((identifier) => (
                          <div
                            key={`${identifier.identifier_type}-${identifier.identifier_value}`}
                          >
                            <span>{identifier.identifier_type}</span>
                            <strong>{identifier.identifier_value}</strong>
                            {identifier.is_primary ? <small>Primary</small> : null}
                          </div>
                        ))}
                      </div>
                    </section>
                  </div>
                ) : null}

                {inspectorTab === 'series' ? (
                  <div className="ir-inspector-stack">
                    <section className="ir-inspector-section ir-series-section">
                      <div className="ir-subsection-head">
                        <div>
                          <span>Typed observations</span>
                          <h3>Market-data series</h3>
                        </div>
                        <div className="ir-series-actions">
                          <button
                            type="button"
                            disabled={!filteredSeries.length}
                            onClick={downloadFilteredSeries}
                          >
                            Download CSV
                          </button>
                        </div>
                      </div>
                      <div className="ir-series-filters">
                        <label>
                          <span>Family</span>
                          <select
                            value={seriesFamilyFilter}
                            onChange={(event) =>
                              setSeriesFamilyFilter(
                                event.target.value as 'all' | MetricFamily,
                              )
                            }
                          >
                            <option value="all">All</option>
                            {seriesFamilyOptions.map((family) => (
                              <option key={family} value={family}>
                                {family.toUpperCase()}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>Basis</span>
                          <select
                            value={seriesBasisFilter}
                            onChange={(event) =>
                              setSeriesBasisFilter(
                                event.target.value as 'all' | QuoteBasis,
                              )
                            }
                          >
                            <option value="all">All</option>
                            {seriesBasisOptions.map((basis) => (
                              <option key={basis} value={basis}>
                                {formatBasisLabel(basis)}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>From</span>
                          <input
                            type="date"
                            value={seriesStartDate}
                            onChange={(event) => setSeriesStartDate(event.target.value)}
                          />
                        </label>
                        <label>
                          <span>To</span>
                          <input
                            type="date"
                            value={seriesEndDate}
                            onChange={(event) => setSeriesEndDate(event.target.value)}
                          />
                        </label>
                      </div>
                      {selectedInstrumentDetailLoading ? (
                        <div className="ir-series-loading">Loading full series…</div>
                      ) : (
                        <div className="ir-series-table-wrap">
                          <table className="ir-series-table">
                            <thead>
                              <tr>
                                <th>Date / basis</th>
                                <th>Value</th>
                                <th>Source</th>
                                <th />
                              </tr>
                            </thead>
                            <tbody>
                              {pagedSeries.map((point) => (
                                <tr
                                  key={`${point.metric_family}-${point.quote_basis}-${point.as_of_date}-${point.currency}`}
                                >
                                  <td>
                                    <strong>{point.as_of_date}</strong>
                                    <span>
                                      {point.metric_family.toUpperCase()} ·{' '}
                                      {formatBasisLabel(point.quote_basis)}
                                    </span>
                                  </td>
                                  <td>
                                    <strong title={point.value}>
                                      {formatDecimalValue(point.value)} {point.currency}
                                    </strong>
                                    <span>
                                      {formatPriceUnit(point.price_unit)} · {point.status}
                                    </span>
                                  </td>
                                  <td>
                                    <strong>{point.provider ?? 'Unspecified'}</strong>
                                    <span>Scale {point.price_scale}</span>
                                  </td>
                                  <td>
                                    {selectedInstrument.instrument_type !== 'fund' ? (
                                      <button
                                        type="button"
                                        onClick={() => handleEditPoint(point)}
                                      >
                                        Edit
                                      </button>
                                    ) : null}
                                  </td>
                                </tr>
                              ))}
                              {!pagedSeries.length ? (
                                <tr>
                                  <td colSpan={4}>
                                    <div className="ir-inline-empty">
                                      No observations match this range.
                                    </div>
                                  </td>
                                </tr>
                              ) : null}
                            </tbody>
                          </table>
                        </div>
                      )}
                      <div className="ir-series-pagination">
                        <span>{filteredSeries.length} observations</span>
                        <div>
                          <button
                            type="button"
                            disabled={effectiveSeriesPage <= 1}
                            onClick={() =>
                              setSeriesPage((current) => Math.max(1, current - 1))
                            }
                          >
                            Previous
                          </button>
                          <span>{effectiveSeriesPage} / {seriesPageCount}</span>
                          <button
                            type="button"
                            disabled={effectiveSeriesPage >= seriesPageCount}
                            onClick={() =>
                              setSeriesPage((current) =>
                                Math.min(seriesPageCount, current + 1),
                              )
                            }
                          >
                            Next
                          </button>
                        </div>
                      </div>
                      <p className="ir-series-policy">
                        Corrections overwrite the same typed date/basis key through the
                        existing audited write path. Fund NAV corrections remain in the NAV
                        import and corporate-action workflow.
                      </p>
                    </section>
                  </div>
                ) : null}

                {inspectorTab === 'source' ? (
                  <div className="ir-inspector-stack">
                    <section className="ir-source-status">
                      <div className="ir-source-status-head">
                        <span className={`ir-source-mark is-${selectedInstrument.source_settings.source_mode}`}>
                          {selectedInstrument.source_settings.source_mode.slice(0, 1).toUpperCase()}
                        </span>
                        <div>
                          <span>Configured source</span>
                          <strong>
                            {formatSourceMode(selectedInstrument.source_settings.source_mode)}
                          </strong>
                          <small>
                            {selectedInstrument.source_settings.source_api_profile ||
                              selectedInstrument.source_settings.source_email ||
                              'No provider identity configured'}
                          </small>
                        </div>
                      </div>
                      <button
                        type="button"
                        className="registry-submit secondary"
                        onClick={() => openActionPanel('source')}
                      >
                        Edit source settings
                      </button>
                    </section>
                    <section className="ir-inspector-section">
                      <div className="ir-subsection-head">
                        <div>
                          <span>Acquisition policy</span>
                          <h3>Schedule &amp; routing</h3>
                        </div>
                      </div>
                      <dl className="ir-definition-list">
                        <div>
                          <dt>Mode</dt>
                          <dd>
                            {formatSourceMode(selectedInstrument.source_settings.source_mode)}
                          </dd>
                        </div>
                        <div>
                          <dt>Frequency</dt>
                          <dd>
                            {formatFrequency(
                              selectedInstrument.source_settings.expected_frequency,
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt>Market calendar</dt>
                          <dd>
                            {selectedInstrument.source_settings.market_calendar ??
                              'Not configured'}
                          </dd>
                        </div>
                        <div>
                          <dt>Release lag</dt>
                          <dd>
                            {selectedInstrument.source_settings.release_lag_days} day(s)
                          </dd>
                        </div>
                        <div>
                          <dt>Folder / rule</dt>
                          <dd>
                            {selectedInstrument.source_settings.source_location ||
                              'Not configured'}
                          </dd>
                        </div>
                        <div>
                          <dt>Return semantics</dt>
                          <dd>
                            {selectedInstrument.source_settings.return_semantics ?? 'unknown'}
                          </dd>
                        </div>
                      </dl>
                    </section>
                    <section className="ir-inspector-section">
                      <div className="ir-subsection-head">
                        <div>
                          <span>Last source operation</span>
                          <h3>Refresh state</h3>
                        </div>
                        <span className="ir-refresh-state">
                          {selectedInstrument.refresh_status.status || 'idle'}
                        </span>
                      </div>
                      <div className="ir-refresh-detail">
                        <p>
                          {selectedInstrument.refresh_status.message ||
                            'No refresh message has been recorded.'}
                        </p>
                        <dl>
                          <div>
                            <dt>Requested</dt>
                            <dd>
                              {formatDateTime(
                                selectedInstrument.refresh_status.requested_at,
                              )}
                            </dd>
                          </div>
                          <div>
                            <dt>Last success</dt>
                            <dd>
                              {formatDateTime(
                                selectedInstrument.refresh_status
                                  .last_successful_requested_at,
                              )}
                            </dd>
                          </div>
                          <div>
                            <dt>Requested by</dt>
                            <dd>
                              {selectedInstrument.refresh_status.requested_by ?? '—'}
                            </dd>
                          </div>
                        </dl>
                      </div>
                    </section>
                  </div>
                ) : null}

                {inspectorTab === 'governance' ? (
                  <div className="ir-inspector-stack">
                    <section className="ir-inspector-section">
                      <div className="ir-subsection-head">
                        <div>
                          <span>Lifecycle control</span>
                          <h3>Registry state</h3>
                        </div>
                        <span
                          className={`lifecycle-badge lifecycle-badge-${selectedInstrument.lifecycle_state.status}`}
                        >
                          {formatLifecycleLabel(
                            selectedInstrument.lifecycle_state.status,
                          )}
                        </span>
                      </div>
                      <p className="ir-governance-copy">
                        Archive removes the instrument from default downstream discovery
                        while retaining its identifiers, observations, and audit history.
                      </p>
                      <dl className="ir-definition-list">
                        <div>
                          <dt>Last state change</dt>
                          <dd>
                            {formatDateTime(selectedInstrument.lifecycle_state.changed_at)}
                          </dd>
                        </div>
                        <div>
                          <dt>Changed by</dt>
                          <dd>{selectedInstrument.lifecycle_state.changed_by ?? '—'}</dd>
                        </div>
                      </dl>
                      {selectedInstrument.lifecycle_state.status === 'archived' ? (
                        <button
                          type="button"
                          className="registry-submit secondary"
                          onClick={() => void handleRestoreSelectedInstrument()}
                        >
                          Restore instrument
                        </button>
                      ) : archiveConfirmation ? (
                        <div className="ir-archive-confirm">
                          <strong>Archive this instrument?</strong>
                          <p>
                            It will disappear from default search in Portfolio and Watchlist.
                            Historical records remain recoverable.
                          </p>
                          <div>
                            <button
                              type="button"
                              className="registry-submit danger"
                              onClick={() => void handleArchiveSelectedInstrument()}
                            >
                              Confirm archive
                            </button>
                            <button
                              type="button"
                              className="registry-submit secondary"
                              onClick={() => setArchiveConfirmation(false)}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="ir-text-danger"
                          onClick={() => setArchiveConfirmation(true)}
                        >
                          Archive instrument
                        </button>
                      )}
                    </section>

                    {selectedInstrument.instrument_type === 'fund' ? (
                      <FundNavActionReview
                        instrumentId={selectedInstrument.instrument_id}
                        request={fetchJson}
                        onMutationCommitted={async (instrumentId) => {
                          const detail = await onFundNavMutationCommitted(instrumentId)
                          if (selectedInstrumentIdRef.current === instrumentId) {
                            setSelectedInstrumentDetail(detail)
                            setSelectedInstrumentDetailError(null)
                          }
                        }}
                      />
                    ) : (
                      <section className="ir-inspector-section">
                        <div className="ir-subsection-head">
                          <div>
                            <span>Change model</span>
                            <h3>Controlled corrections</h3>
                          </div>
                        </div>
                        <p className="ir-governance-copy">
                          Market-data corrections are key-preserving upserts. Instrument
                          removal is intentionally reversible; hard deletion is not exposed
                          by this administration surface.
                        </p>
                      </section>
                    )}
                  </div>
                ) : null}
              </div>
            </>
          ) : (
            <div className="ir-inspector-empty">
              <span className="ir-empty-glyph" aria-hidden="true">⌁</span>
              <h2>Select an instrument</h2>
              <p>
                Open a registry record to inspect its selected quote, full typed series,
                source policy, refresh state, and lifecycle controls.
              </p>
              <div>
                <span>1</span>
                <p><strong>Locate</strong>Search by name, code, or identifier.</p>
              </div>
              <div>
                <span>2</span>
                <p><strong>Diagnose</strong>Check coverage, source, and freshness.</p>
              </div>
              <div>
                <span>3</span>
                <p><strong>Repair</strong>Use controlled writes or imports.</p>
              </div>
            </div>
          )}
        </aside>
      </section>

      {activePanel ? (
        <div
          className="ir-drawer-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setActivePanel(null)
          }}
        >
          <aside
            ref={actionDrawerRef}
            className="ir-action-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="ir-drawer-title"
            tabIndex={-1}
          >
            <header>
              <div>
                <span>
                  {activePanel === 'create'
                    ? 'Registry'
                    : activePanel === 'fx'
                      ? 'Settlement data'
                      : selectedInstrument
                        ? primaryIdentifier(selectedInstrument)
                        : 'Instrument operation'}
                </span>
                <h2 id="ir-drawer-title">
                  {activePanel === 'create'
                    ? 'Register instrument'
                    : activePanel === 'quote'
                      ? metricValue
                        ? 'Revise data point'
                        : 'Add data point'
                      : activePanel === 'source'
                        ? 'Source settings'
                      : activePanel === 'nav'
                        ? 'Import fund NAV'
                        : 'Maintain FX rates'}
                </h2>
                <p>
                  {activePanel === 'create'
                    ? 'Create one canonical identity for all downstream consumers.'
                    : activePanel === 'quote'
                      ? 'Writing the same date, family, and basis revises that typed observation.'
                      : activePanel === 'source'
                        ? 'Define where data originates and when it is expected.'
                        : activePanel === 'nav'
                          ? 'Preview parsed rows before they enter the fund NAV history.'
                          : 'Maintain the direct settlement pairs used for cross-currency valuation.'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setActivePanel(null)}
                aria-label="Close operation drawer"
              >
                ×
              </button>
            </header>
            <div className="ir-drawer-body">
              {error ? (
                <div className="ir-message ir-message-error" role="alert">
                  <span className="ir-message-dot" aria-hidden="true" />
                  <span>{error}</span>
                </div>
              ) : null}
              {activePanel === 'create' ? (
                <form
                  className="registry-form ir-drawer-form"
                  onSubmit={(event) => void handleCreate(event)}
                >
                  <div className="ir-form-section">
                    <div>
                      <span>Canonical identity</span>
                      <h3>Instrument definition</h3>
                    </div>
                    <div className="registry-action-form-grid">
                      <label className="registry-field-wide">
                        <span>Instrument name</span>
                        <input
                          value={instrumentName}
                          onChange={(event) => setInstrumentName(event.target.value)}
                          placeholder="Official or operational display name"
                          required
                          autoFocus
                        />
                      </label>
                      <label>
                        <span>Type</span>
                        <select
                          value={instrumentType}
                          onChange={(event) =>
                            setInstrumentType(event.target.value as InstrumentType)
                          }
                        >
                          <option value="equity">Equity</option>
                          <option value="index">Index</option>
                          <option value="fund">Fund</option>
                          <option value="etf">ETF</option>
                          <option value="cash">Cash</option>
                          <option value="fx">FX</option>
                          <option value="other">Other</option>
                        </select>
                      </label>
                      <label>
                        <span>Currency</span>
                        <input
                          value={currency}
                          onChange={(event) => setCurrency(event.target.value)}
                          maxLength={3}
                          required
                        />
                      </label>
                    </div>
                  </div>
                  <div className="ir-form-section">
                    <div>
                      <span>Resolution key</span>
                      <h3>Primary identifier</h3>
                    </div>
                    <div className="registry-action-form-grid">
                      <label>
                        <span>Identifier type</span>
                        <select
                          value={identifierType}
                          onChange={(event) =>
                            setIdentifierType(event.target.value as IdentifierType)
                          }
                        >
                          <option value="ticker">Ticker</option>
                          <option value="isin">ISIN</option>
                          <option value="cusip">CUSIP</option>
                          <option value="sedol">SEDOL</option>
                          <option value="internal">Internal</option>
                          <option value="other">Other</option>
                        </select>
                      </label>
                      <label>
                        <span>Identifier value</span>
                        <input
                          value={identifierValue}
                          onChange={(event) => setIdentifierValue(event.target.value)}
                          placeholder="Unique canonical key"
                          required
                        />
                      </label>
                    </div>
                  </div>
                  <div className="ir-drawer-footer">
                    <button type="button" onClick={() => setActivePanel(null)}>
                      Cancel
                    </button>
                    <button type="submit" className="registry-submit">
                      Register instrument
                    </button>
                  </div>
                </form>
              ) : null}

              {activePanel === 'quote' && selectedInstrument ? (
                <form
                  className="registry-form ir-drawer-form"
                  onSubmit={(event) => void handleMetricSubmit(event)}
                >
                  <div className="ir-drawer-context">
                    <span>{selectedInstrument.instrument_type.toUpperCase()}</span>
                    <strong>{selectedInstrument.instrument_name}</strong>
                    <small>{primaryIdentifier(selectedInstrument)}</small>
                  </div>
                  <div className="ir-form-section">
                    <div>
                      <span>Typed key</span>
                      <h3>Observation identity</h3>
                    </div>
                    <div className="registry-action-form-grid">
                      <label>
                        <span>Metric family</span>
                        <select
                          value={metricFamily}
                          onChange={(event) => {
                            const nextFamily = event.target.value as MetricFamily
                            setMetricFamily(nextFamily)
                            setQuoteBasis(
                              quoteBasisOptions(nextFamily)[0].value,
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
                        <span>Quote basis</span>
                        <select
                          value={quoteBasis}
                          onChange={(event) =>
                            setQuoteBasis(event.target.value as QuoteBasis)
                          }
                        >
                          {availableQuoteBases.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>As of date</span>
                        <input
                          type="date"
                          value={metricDate}
                          onChange={(event) => setMetricDate(event.target.value)}
                          required
                        />
                      </label>
                      <label>
                        <span>Status</span>
                        <select
                          value={metricStatus}
                          onChange={(event) =>
                            setMetricStatus(event.target.value as DataStatus)
                          }
                        >
                          <option value="complete">Complete</option>
                          <option value="partial">Partial</option>
                          <option value="unavailable">Unavailable</option>
                        </select>
                      </label>
                    </div>
                  </div>
                  <div className="ir-form-section">
                    <div>
                      <span>Observation</span>
                      <h3>Value &amp; contract</h3>
                    </div>
                    <div className="registry-action-form-grid">
                      <label>
                        <span>Value</span>
                        <input
                          value={metricValue}
                          onChange={(event) => setMetricValue(event.target.value)}
                          inputMode="decimal"
                          required
                        />
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
                      <PriceContractFields
                        instrumentType={selectedInstrument.instrument_type}
                        metricFamily={metricFamily}
                      />
                    </div>
                    <p className="ir-form-help">
                      {formatPriceContract(
                        priceContract.price_unit,
                        priceContract.price_scale,
                      )}
                      {' · '}Valuation path{' '}
                      {formatPolicyPath(
                        selectedInstrument.quote_selection_policy.valuation,
                      )}
                    </p>
                  </div>
                  <div className="ir-drawer-footer">
                    <button type="button" onClick={() => setActivePanel(null)}>
                      Cancel
                    </button>
                    <button type="submit" className="registry-submit">
                      {metricValue ? 'Save revision' : 'Save data point'}
                    </button>
                  </div>
                </form>
              ) : null}

              {activePanel === 'source' && selectedInstrument ? (
                <form
                  className="registry-form ir-drawer-form"
                  onSubmit={(event) => void handleSourceSettingsSubmit(event)}
                >
                  <div className="ir-drawer-context">
                    <span>Source owner</span>
                    <strong>{selectedInstrument.instrument_name}</strong>
                    <small>{primaryIdentifier(selectedInstrument)}</small>
                  </div>
                  <div className="ir-form-section">
                    <div>
                      <span>Acquisition</span>
                      <h3>Source &amp; schedule</h3>
                    </div>
                    <div className="registry-action-form-grid">
                      <label>
                        <span>Source mode</span>
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
                        <span>Email source</span>
                        <input
                          value={sourceEmail}
                          onChange={(event) => setSourceEmail(event.target.value)}
                          disabled={sourceMode !== 'email'}
                          placeholder="pricing@provider.example"
                        />
                      </label>
                      <label>
                        <span>API profile</span>
                        <input
                          value={sourceApiProfile}
                          onChange={(event) => setSourceApiProfile(event.target.value)}
                          disabled={sourceMode !== 'api'}
                          placeholder="tushare"
                        />
                      </label>
                      <label className="registry-field-wide">
                        <span>Folder / rule / endpoint</span>
                        <input
                          value={sourceLocation}
                          onChange={(event) => setSourceLocation(event.target.value)}
                          placeholder="Operational routing reference"
                        />
                      </label>
                      <label>
                        <span>Index return semantics</span>
                        <select
                          value={
                            selectedInstrument.instrument_type === 'index'
                              ? returnSemantics
                              : 'unknown'
                          }
                          onChange={(event) =>
                            setReturnSemantics(event.target.value as ReturnSemantics)
                          }
                          disabled={selectedInstrument.instrument_type !== 'index'}
                        >
                          <option value="unknown">Unknown</option>
                          <option value="price_return">Price return</option>
                          <option value="total_return">Total return</option>
                        </select>
                      </label>
                    </div>
                  </div>
                  <div className="ir-drawer-footer">
                    <button type="button" onClick={() => void handleRefreshClick()}>
                      Refresh now
                    </button>
                    <button type="submit" className="registry-submit">
                      Save source settings
                    </button>
                  </div>
                </form>
              ) : null}

              {activePanel === 'nav' &&
              selectedInstrument &&
              supportsNavHistoryImport(selectedInstrument.instrument_type) ? (
                <form
                  className="registry-form ir-drawer-form"
                  onSubmit={(event) => void handleNavImportSubmit(event)}
                >
                  <div className="ir-drawer-context">
                    <span>Fund NAV history</span>
                    <strong>{selectedInstrument.instrument_name}</strong>
                    <small>{primaryIdentifier(selectedInstrument)}</small>
                  </div>
                  <div className="ir-form-section">
                    <div>
                      <span>Input</span>
                      <h3>Choose one import method</h3>
                    </div>
                    <div className="registry-action-form-grid">
                      <label className="registry-field-wide ir-file-input">
                        <span>Excel, CSV, TSV, or text file</span>
                        <input
                          key={navImportFileInputKey}
                          type="file"
                          accept=".csv,.tsv,.txt,.xlsx,.xls"
                          onChange={(event) => void handleNavFileChange(event)}
                        />
                        <small>
                          {navImportFileName || 'No file selected'}
                        </small>
                      </label>
                      <div className="ir-form-divider"><span>or paste rows</span></div>
                      <label className="registry-field-wide">
                        <span>Pasted rows</span>
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
                          placeholder={`date,nav,nav_with_dividend,currency\nYYYY-MM-DD,1.2084,1.2346,CNY`}
                        />
                      </label>
                    </div>
                    <p className="ir-form-help">
                      Accepted fund series: Unit NAV and dividend-reinvested total return NAV.
                      Corporate-action corrections remain evidence controlled.
                    </p>
                  </div>
                  {navPreviewError ? (
                    <div className="ir-inspector-error">{navPreviewError}</div>
                  ) : null}
                  {navPreview ? (
                    <div className="ir-import-preview">
                      <div>
                        <span>Import preview</span>
                        <strong>{navPreview.row_count} parsed rows</strong>
                      </div>
                      <div className="ir-table-scroll">
                        <table className="ir-series-table">
                          <thead>
                            <tr>
                              <th>Date</th>
                              <th>Unit NAV</th>
                              <th>Total return NAV</th>
                              <th>Currency</th>
                            </tr>
                          </thead>
                          <tbody>
                            {navPreview.rows.slice(0, 12).map((row) => (
                              <tr
                                key={`${row.as_of_date}-${row.nav ?? ''}-${row.nav_with_dividend ?? ''}`}
                              >
                                <td>{row.as_of_date}</td>
                                <td>{row.nav ?? '—'}</td>
                                <td>{row.nav_with_dividend ?? '—'}</td>
                                <td>{row.currency}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : null}
                  <div className="ir-drawer-footer">
                    <button
                      type="button"
                      disabled={!navImportText.trim() && !navImportFileContent}
                      onClick={() => void handlePreviewNavImport()}
                    >
                      Preview rows
                    </button>
                    <button
                      type="submit"
                      className="registry-submit"
                      disabled={!navImportText.trim() && !navImportFileContent}
                    >
                      Import NAV history
                    </button>
                  </div>
                </form>
              ) : null}

              {activePanel === 'fx' ? (
                <div className="ir-fx-layout">
                  <div className="ir-form-section">
                    <div>
                      <span>Current settlement graph</span>
                      <h3>Maintained pairs</h3>
                    </div>
                    <div className="ir-table-scroll">
                      <table className="ir-series-table">
                        <thead>
                          <tr>
                            <th>Pair</th>
                            <th>Rate / date</th>
                            <th>Source</th>
                          </tr>
                        </thead>
                        <tbody>
                          {fxPanelRates.map(({ pairLabel, record }) => (
                            <tr key={pairLabel}>
                              <td><strong>{pairLabel}</strong></td>
                              <td>
                                <strong title={record?.rate ?? undefined}>
                                  {record ? formatDecimalValue(record.rate) : '—'}
                                </strong>
                                <span>{record?.as_of_date ?? 'Missing'}</span>
                              </td>
                              <td>
                                {record ? formatFxSourceKind(record.source_kind) : 'Missing'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <form
                    className="registry-form ir-drawer-form"
                    onSubmit={(event) => void handleFxSubmit(event)}
                  >
                    <div className="ir-form-section">
                      <div>
                        <span>Direct observation</span>
                        <h3>Update settlement rate</h3>
                      </div>
                      <div className="registry-action-form-grid">
                        <label>
                          <span>Pair</span>
                          <select
                            value={editableFxPair}
                            onChange={(event) => setEditableFxPair(event.target.value)}
                          >
                            {editableFxPairs.map(([baseCurrency, quoteCurrency]) => {
                              const value = formatFxPairLabel(baseCurrency, quoteCurrency)
                              return <option key={value} value={value}>{value}</option>
                            })}
                          </select>
                        </label>
                        <label>
                          <span>Spot rate</span>
                          <input
                            value={fxRateValue}
                            onChange={(event) => setFxRateValue(event.target.value)}
                            inputMode="decimal"
                            required
                          />
                        </label>
                        <label>
                          <span>As of date</span>
                          <input
                            type="date"
                            value={fxRateDate}
                            onChange={(event) => setFxRateDate(event.target.value)}
                            required
                          />
                        </label>
                        <label>
                          <span>Status</span>
                          <select
                            value={fxRateStatus}
                            onChange={(event) =>
                              setFxRateStatus(event.target.value as DataStatus)
                            }
                          >
                            <option value="complete">Complete</option>
                            <option value="partial">Partial</option>
                            <option value="unavailable">Unavailable</option>
                          </select>
                        </label>
                      </div>
                    </div>
                    <div className="ir-drawer-footer">
                      <button
                        type="submit"
                        className="registry-submit"
                        disabled={!selectedEditableFxPair}
                      >
                        Save FX rate
                      </button>
                    </div>
                  </form>
                </div>
              ) : null}
            </div>
          </aside>
        </div>
      ) : null}
    </main>
  )
}
