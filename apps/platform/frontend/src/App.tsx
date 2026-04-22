import { FormEvent, useEffect, useMemo, useState } from 'react'
import type {
  AssetIdentifier as PlatformAssetIdentifier,
  AssetType,
  DataStatus,
  IdentifierType,
  MetricFamily,
  QuoteBasis,
  QuoteRole,
  QuoteSelectionPolicy as PlatformQuoteSelectionPolicy,
} from '../../../../packages/asset-core/ts/src'

type PlatformAppCard = {
  app_id: string
  name: string
  url: string
  api_url?: string | null
  eyebrow: string
  description: string
  availability: string
}

type PlatformAppsResponse = {
  platform_name: string
  apps: PlatformAppCard[]
}
type SourceMode = 'manual' | 'email' | 'api'
type InstrumentLifecycleStatus = 'active' | 'archived'

type PlatformMarketDataPoint = {
  metric_family: MetricFamily
  quote_basis: QuoteBasis
  as_of_date: string
  value: string
  currency: string
  provider?: string | null
  status: DataStatus
}

type PlatformLifecycleState = {
  status: InstrumentLifecycleStatus
  changed_at: string | null
  changed_by: string | null
}

type PlatformInstrumentRecord = {
  asset_id: string
  asset_name: string
  asset_type: AssetType
  currency: string
  identifiers: PlatformAssetIdentifier[]
  latest_market_data: PlatformMarketDataPoint[]
  quote_selection_policy: PlatformQuoteSelectionPolicy
  coverage_state: DataStatus
  source_settings: {
    source_mode: SourceMode
    source_email: string
    source_location: string
    source_api_profile: string
  }
  refresh_status: {
    status: string
    message: string
    requested_at: string | null
    requested_by: string | null
    mode: SourceMode
  }
  lifecycle_state: PlatformLifecycleState
}

type PlatformInstrumentsResponse = {
  registry_name: string
  instruments: PlatformInstrumentRecord[]
}

type SupportedCurrency = 'USD' | 'HKD' | 'CNY'
type FxRateSourceKind = 'direct' | 'inverse' | 'cross'

type PlatformFxRateRecord = {
  base_currency: SupportedCurrency
  quote_currency: SupportedCurrency
  rate: string
  as_of_date: string
  source_kind: FxRateSourceKind
  asset_id?: string | null
  source_asset_ids: string[]
  provider?: string | null
  status: DataStatus
}

type PlatformFxRatesResponse = {
  supported_currencies: SupportedCurrency[]
  maintained_pairs: string[]
  rates: PlatformFxRateRecord[]
}

const PLATFORM_NAME_FALLBACK = (import.meta.env.VITE_PLATFORM_NAME || 'Yungu').trim() || 'Yungu'
const PLATFORM_API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const FX_PANEL_PAIRS: Array<[SupportedCurrency, SupportedCurrency]> = [
  ['USD', 'HKD'],
  ['USD', 'CNY'],
  ['HKD', 'CNY'],
]
const EDITABLE_FX_PAIRS: Array<[SupportedCurrency, SupportedCurrency]> = [
  ['USD', 'HKD'],
  ['USD', 'CNY'],
]

const QUOTE_BASIS_OPTIONS: Record<MetricFamily, Array<{ value: QuoteBasis; label: string }>> = {
  price: [
    { value: 'last', label: 'Last Trade' },
    { value: 'close', label: 'Close' },
    { value: 'adjusted_close', label: 'Adjusted Close' },
    { value: 'clean_price', label: 'Clean Price' },
    { value: 'dirty_price', label: 'Dirty Price' },
    { value: 'par', label: 'Par' },
  ],
  nav: [
    { value: 'official_nav', label: 'Official NAV' },
    { value: 'total_return_nav', label: 'Total Return NAV' },
  ],
  fx: [{ value: 'spot', label: 'Spot' }],
}

const ROLE_ORDER: QuoteRole[] = ['valuation', 'trading', 'total_return']
const ROLE_LABELS: Record<QuoteRole, string> = {
  trading: 'Trading',
  valuation: 'Valuation',
  total_return: 'Total Return',
  chart: 'Chart',
  reference: 'Reference',
}

function normalizeConfiguredUrl(value: string | null | undefined): string | null {
  const normalized = (value || '').trim().replace(/\/$/, '')
  return normalized || null
}

function buildFallbackApps(): PlatformAppCard[] {
  const watchlistUrl = normalizeConfiguredUrl(import.meta.env.VITE_WATCHLIST_URL)
  const watchlistApiUrl = normalizeConfiguredUrl(import.meta.env.VITE_WATCHLIST_API_URL)
  const portfolioUrl = normalizeConfiguredUrl(import.meta.env.VITE_PORTFOLIO_URL)
  const portfolioApiUrl = normalizeConfiguredUrl(import.meta.env.VITE_PORTFOLIO_API_URL)
  const apps: PlatformAppCard[] = []

  if (watchlistUrl) {
    apps.push({
      app_id: 'watchlist',
      name: 'Watchlist',
      url: watchlistUrl,
      api_url: watchlistApiUrl,
      eyebrow: 'Research and monitoring',
      description:
        'Fund and asset watchlists, detail pages, facts ingest, read models, and copilot-assisted review.',
      availability: 'ready',
    })
  }
  if (portfolioUrl) {
    apps.push({
      app_id: 'portfolio',
      name: 'Portfolio',
      url: portfolioUrl,
      api_url: portfolioApiUrl,
      eyebrow: 'Portfolio management',
      description:
        'Portfolio, account, transaction, risk, and review workflows built on top of the shared asset core.',
      availability: 'ready',
    })
  }
  return apps
}

const fallbackApps: PlatformAppCard[] = buildFallbackApps()
const fallbackSourceLabel = fallbackApps.length > 0 ? 'frontend env fallback' : 'backend unavailable'

function normalizePath(pathname: string) {
  const normalized = pathname.replace(/\/+$/, '')
  return normalized || '/'
}

function primaryIdentifier(record: PlatformInstrumentRecord) {
  return (
    record.identifiers.find((item) => item.is_primary)?.identifier_value ??
    record.identifiers[0]?.identifier_value ??
    record.asset_id
  )
}

function allowedFamiliesForAsset(assetType: AssetType): MetricFamily[] {
  if (assetType === 'fx') {
    return ['fx']
  }
  if (assetType === 'cash' || assetType === 'bond' || assetType === 'equity') {
    return ['price']
  }
  if (assetType === 'fund') {
    return ['nav', 'price']
  }
  return ['price', 'nav', 'fx']
}

function defaultQuoteInput(assetType: AssetType): { metric_family: MetricFamily; quote_basis: QuoteBasis } {
  if (assetType === 'fx') {
    return { metric_family: 'fx', quote_basis: 'spot' }
  }
  if (assetType === 'cash') {
    return { metric_family: 'price', quote_basis: 'par' }
  }
  if (assetType === 'bond') {
    return { metric_family: 'price', quote_basis: 'dirty_price' }
  }
  if (assetType === 'fund') {
    return { metric_family: 'nav', quote_basis: 'official_nav' }
  }
  return { metric_family: 'price', quote_basis: 'close' }
}

function formatBasisLabel(quoteBasis: QuoteBasis) {
  if (quoteBasis === 'official_nav') {
    return 'Official NAV'
  }
  if (quoteBasis === 'total_return_nav') {
    return 'Total Return NAV'
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

function resolveRoleQuote(
  record: PlatformInstrumentRecord,
  role: QuoteRole,
): PlatformMarketDataPoint | null {
  const latestByBasis = new Map<QuoteBasis, PlatformMarketDataPoint>()
  record.latest_market_data.forEach((point) => {
    const current = latestByBasis.get(point.quote_basis)
    if (!current || point.as_of_date >= current.as_of_date) {
      latestByBasis.set(point.quote_basis, point)
    }
  })

  const bases = [...(record.quote_selection_policy[role] || []), ...(record.quote_selection_policy.reference || [])]
  for (const basis of bases) {
    const point = latestByBasis.get(basis)
    if (point) {
      return point
    }
  }

  return record.latest_market_data[0] ?? null
}

function summaryQuoteChips(record: PlatformInstrumentRecord) {
  const seenBases = new Set<QuoteBasis>()
  return ROLE_ORDER.map((role) => {
    const point = resolveRoleQuote(record, role)
    if (!point || seenBases.has(point.quote_basis)) {
      return null
    }
    seenBases.add(point.quote_basis)
    return {
      role,
      point,
    }
  }).filter((value): value is { role: QuoteRole; point: PlatformMarketDataPoint } => value !== null)
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
  const next = current.filter((item) => item.asset_id !== updated.asset_id)
  if (!includeInactive && updated.lifecycle_state.status === 'archived') {
    return next.sort((left, right) => left.asset_name.localeCompare(right.asset_name))
  }
  return [...next, updated].sort((left, right) => left.asset_name.localeCompare(right.asset_name))
}

function formatFxPairLabel(baseCurrency: string, quoteCurrency: string) {
  return `${baseCurrency}/${quoteCurrency}`
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

function findFxRate(
  rates: PlatformFxRateRecord[],
  baseCurrency: SupportedCurrency,
  quoteCurrency: SupportedCurrency,
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

function HomePage({
  platformName,
  apps,
  sourceLabel,
  instrumentCount,
  completeCount,
}: {
  platformName: string
  apps: PlatformAppCard[]
  sourceLabel: string
  instrumentCount: number
  completeCount: number
}) {
  return (
    <main className="platform-shell">
      <header className="platform-masthead">
        <a className="platform-nav-link platform-nav-link-active" href="/">
          Home
        </a>
        <a className="platform-nav-link" href="/instruments">
          Instruments
        </a>
      </header>

      <section className="hero">
        <div className="hero-kicker">{platformName}</div>
        <h1>Home chooses the app. The platform maintains the shared asset core.</h1>
        <p className="hero-copy">
          Watchlist and Portfolio stay decoupled at the workflow layer. Shared instruments,
          identifiers, typed market data, and quote selection policies live at the platform
          level so both apps can consume the same master list without forcing business fusion.
        </p>
        <div className="hero-meta">Registry source: {sourceLabel}</div>
      </section>

      <section className="app-grid" aria-label="Yungu app switcher">
        {apps.map((app) => (
          <article className="app-card" key={app.app_id}>
            <div className="app-eyebrow">{app.eyebrow}</div>
            <h2>{app.name}</h2>
            <p>{app.description}</p>
            <div className="app-actions">
              <a className="app-link" href={app.url}>
                Open {app.name}
              </a>
              {app.api_url ? (
                <a className="app-link secondary" href={app.api_url}>
                  API
                </a>
              ) : null}
            </div>
          </article>
        ))}
      </section>

      <section className="registry-panel">
        <div className="registry-panel-header">
          <div>
            <div className="registry-kicker">Shared Asset Core</div>
            <h2>Instrument Registry</h2>
          </div>
          <a className="app-link" href="/instruments">
            Open Instruments
          </a>
        </div>
        <div className="registry-stats">
          <div className="registry-stat">
            <span className="registry-stat-label">Instruments</span>
            <strong>{instrumentCount}</strong>
          </div>
          <div className="registry-stat">
            <span className="registry-stat-label">Complete Coverage</span>
            <strong>{completeCount}</strong>
          </div>
          <div className="registry-stat">
            <span className="registry-stat-label">Use Case</span>
            <strong>Transaction instrument picker</strong>
          </div>
        </div>
      </section>
    </main>
  )
}

function InstrumentsPage({
  registryName,
  instruments,
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
  onImportNavText,
  onArchiveInstrument,
  onRestoreInstrument,
}: {
  registryName: string
  instruments: PlatformInstrumentRecord[]
  fxRates: PlatformFxRatesResponse | null
  loading: boolean
  error: string | null
  notice: string | null
  showInactive: boolean
  onToggleShowInactive: () => void
  onCreateInstrument: (payload: {
    asset_name: string
    asset_type: AssetType
    currency: string
    identifiers: PlatformAssetIdentifier[]
  }) => Promise<void>
  onUpsertFxRate: (payload: {
    base_currency: SupportedCurrency
    quote_currency: SupportedCurrency
    rate: string
    as_of_date: string
    provider?: string | null
    status: DataStatus
  }) => Promise<void>
  onUpsertMarketData: (payload: {
    asset_id: string
    metric_family: MetricFamily
    quote_basis: QuoteBasis
    as_of_date: string
    value: string
    currency: string
    provider?: string | null
    status: DataStatus
  }) => Promise<void>
  onUpdateSourceSettings: (payload: {
    asset_id: string
    source_mode: SourceMode
    source_email: string
    source_location: string
    source_api_profile: string
  }) => Promise<void>
  onTriggerRefresh: (payload: { asset_id: string; updated_by?: string | null }) => Promise<void>
  onImportNavText: (payload: {
    asset_id: string
    raw_text: string
    provider?: string | null
    status: DataStatus
    updated_by?: string | null
  }) => Promise<void>
  onArchiveInstrument: (payload: { asset_id: string; updated_by?: string | null }) => Promise<void>
  onRestoreInstrument: (payload: { asset_id: string; updated_by?: string | null }) => Promise<void>
}) {
  const [assetName, setAssetName] = useState('')
  const [assetType, setAssetType] = useState<AssetType>('equity')
  const [currency, setCurrency] = useState('USD')
  const [identifierType, setIdentifierType] = useState<IdentifierType>('ticker')
  const [identifierValue, setIdentifierValue] = useState('')
  const [selectedAssetId, setSelectedAssetId] = useState('')
  const [metricFamily, setMetricFamily] = useState<MetricFamily>('price')
  const [quoteBasis, setQuoteBasis] = useState<QuoteBasis>('close')
  const [metricValue, setMetricValue] = useState('')
  const [metricCurrency, setMetricCurrency] = useState('USD')
  const [metricDate, setMetricDate] = useState('2026-04-15')
  const [metricStatus, setMetricStatus] = useState<DataStatus>('complete')
  const [sourceMode, setSourceMode] = useState<SourceMode>('manual')
  const [sourceEmail, setSourceEmail] = useState('')
  const [sourceLocation, setSourceLocation] = useState('Shared data ops')
  const [sourceApiProfile, setSourceApiProfile] = useState('')
  const [navImportText, setNavImportText] = useState('')
  const [editableFxPair, setEditableFxPair] = useState('USD/HKD')
  const [fxRateValue, setFxRateValue] = useState('')
  const [fxRateDate, setFxRateDate] = useState('2026-04-15')
  const [fxRateStatus, setFxRateStatus] = useState<DataStatus>('complete')

  const selectedEditableFxPair = useMemo(() => {
    const [baseCurrency = 'USD', quoteCurrency = 'HKD'] = editableFxPair.split('/')
    return {
      baseCurrency: baseCurrency as SupportedCurrency,
      quoteCurrency: quoteCurrency as SupportedCurrency,
    }
  }, [editableFxPair])
  const selectedEditableFxRate = useMemo(
    () =>
      fxRates
        ? findFxRate(
            fxRates.rates,
            selectedEditableFxPair.baseCurrency,
            selectedEditableFxPair.quoteCurrency,
          )
        : null,
    [fxRates, selectedEditableFxPair.baseCurrency, selectedEditableFxPair.quoteCurrency],
  )
  const fxPanelRates = useMemo(
    () =>
      FX_PANEL_PAIRS.map(([baseCurrency, quoteCurrency]) => ({
        pairLabel: formatFxPairLabel(baseCurrency, quoteCurrency),
        record: fxRates ? findFxRate(fxRates.rates, baseCurrency, quoteCurrency) : null,
      })),
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

  const selectedInstrument = instruments.find((item) => item.asset_id === selectedAssetId) ?? null
  const allowedMetricFamilies = useMemo(
    () =>
      selectedInstrument
        ? allowedFamiliesForAsset(selectedInstrument.asset_type)
        : (['price', 'nav', 'fx'] as MetricFamily[]),
    [selectedInstrument],
  )
  const availableQuoteBases = useMemo(() => QUOTE_BASIS_OPTIONS[metricFamily], [metricFamily])
  const selectedQuoteSummary = useMemo(
    () => (selectedInstrument ? summaryQuoteChips(selectedInstrument) : []),
    [selectedInstrument],
  )

  function syncSelectedInstrument(assetId: string) {
    setSelectedAssetId(assetId)
    const selected = instruments.find((item) => item.asset_id === assetId)
    if (!selected) {
      return
    }
    const defaults = defaultQuoteInput(selected.asset_type)
    setMetricFamily(defaults.metric_family)
    setQuoteBasis(defaults.quote_basis)
    setMetricCurrency(selected.currency)
  }

  useEffect(() => {
    if (!instruments.length) {
      if (selectedAssetId) {
        setSelectedAssetId('')
      }
      return
    }
    if (!selectedAssetId || !instruments.some((item) => item.asset_id === selectedAssetId)) {
      syncSelectedInstrument(instruments[0].asset_id)
    }
  }, [instruments, selectedAssetId])

  useEffect(() => {
    if (!selectedInstrument) {
      return
    }
    setSourceMode(selectedInstrument.source_settings.source_mode)
    setSourceEmail(selectedInstrument.source_settings.source_email)
    setSourceLocation(selectedInstrument.source_settings.source_location)
    setSourceApiProfile(selectedInstrument.source_settings.source_api_profile)
  }, [selectedInstrument])

  useEffect(() => {
    if (!allowedMetricFamilies.includes(metricFamily)) {
      const nextFamily = allowedMetricFamilies[0]
      setMetricFamily(nextFamily)
      setQuoteBasis(QUOTE_BASIS_OPTIONS[nextFamily][0].value)
      return
    }
    if (!availableQuoteBases.some((item) => item.value === quoteBasis)) {
      setQuoteBasis(availableQuoteBases[0].value)
    }
  }, [allowedMetricFamilies, availableQuoteBases, metricFamily, quoteBasis])

  useEffect(() => {
    if (!selectedEditableFxRate) {
      return
    }
    setFxRateValue(selectedEditableFxRate.rate)
    setFxRateDate(selectedEditableFxRate.as_of_date)
    setFxRateStatus(selectedEditableFxRate.status)
  }, [selectedEditableFxRate])

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await onCreateInstrument({
      asset_name: assetName.trim(),
      asset_type: assetType,
      currency: currency.trim().toUpperCase(),
      identifiers: [
        {
          identifier_type: identifierType,
          identifier_value: identifierValue.trim(),
          is_primary: true,
        },
      ],
    })
    setAssetName('')
    setIdentifierValue('')
  }

  async function handleFxSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
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
      asset_id: selectedAssetId,
      metric_family: metricFamily,
      quote_basis: quoteBasis,
      as_of_date: metricDate,
      value: metricValue.trim(),
      currency: metricCurrency.trim().toUpperCase(),
      status: metricStatus,
      provider: 'platform_manual',
    })
    setMetricValue('')
  }

  async function handleSourceSettingsSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedAssetId) {
      return
    }
    await onUpdateSourceSettings({
      asset_id: selectedAssetId,
      source_mode: sourceMode,
      source_email: sourceEmail.trim(),
      source_location: sourceLocation.trim(),
      source_api_profile: sourceApiProfile.trim(),
    })
  }

  async function handleRefreshClick() {
    if (!selectedAssetId) {
      return
    }
    await onTriggerRefresh({
      asset_id: selectedAssetId,
      updated_by: 'platform_ui',
    })
  }

  async function handleNavImportSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedAssetId || !navImportText.trim()) {
      return
    }
    await onImportNavText({
      asset_id: selectedAssetId,
      raw_text: navImportText.trim(),
      provider: 'platform_paste_import',
      status: 'complete',
      updated_by: 'platform_ui',
    })
    setNavImportText('')
  }

  async function handleArchiveClick(assetId: string) {
    await onArchiveInstrument({
      asset_id: assetId,
      updated_by: 'platform_ui',
    })
  }

  async function handleRestoreClick(assetId: string) {
    await onRestoreInstrument({
      asset_id: assetId,
      updated_by: 'platform_ui',
    })
  }

  return (
    <main className="platform-shell">
      <header className="platform-masthead">
        <a className="platform-nav-link" href="/">
          Home
        </a>
        <a className="platform-nav-link platform-nav-link-active" href="/instruments">
          Instruments
        </a>
      </header>

      <section className="registry-pagehead">
        <div className="registry-breadcrumbs">
          <a href="/">Home</a>
          <span>/</span>
          <span>Instruments</span>
        </div>
        <div className="registry-kicker">Shared Asset Core</div>
        <h1>{registryName}</h1>
        <p className="hero-copy">
          This is the platform-owned instrument master and typed quote registry.
          Portfolio and Watchlist now read role-based quotes from this layer instead of
          guessing from a single generic price or NAV field inside app-local pages.
        </p>
        <div className="registry-pagehead-actions">
          <div className="registry-table-meta">
            {showInactive
              ? `${activeInstrumentCount} active · ${archivedInstrumentCount} archived shown`
              : `${activeInstrumentCount} active in shared search · archived hidden by default`}
          </div>
          <button type="button" className="app-link secondary" onClick={onToggleShowInactive}>
            {showInactive ? 'Hide Archived' : 'Show Archived'}
          </button>
        </div>
      </section>

      {notice ? <div className="registry-notice">{notice}</div> : null}
      {error ? <div className="registry-error">{error}</div> : null}

      <section className="registry-table-shell">
        <div className="registry-table-header">
          <div>
            <div className="registry-form-title">FX Spot Desk</div>
            <div className="registry-table-meta">
              Maintain direct `USD/HKD` and `USD/CNY` quotes here. `HKD/CNY` stays derived from the shared registry.
            </div>
          </div>
        </div>
        <div className="fx-panel-grid">
          <div className="fx-board">
            {fxPanelRates.map(({ pairLabel, record }) => (
              <article className="fx-board-row" key={pairLabel}>
                <div className="fx-board-row-main">
                  <strong>{pairLabel}</strong>
                  <span>{record ? record.rate : '—'}</span>
                </div>
                <div className="fx-board-row-meta">
                  <span>{record ? record.as_of_date : 'No quote'}</span>
                  <span>{record ? formatFxSourceKind(record.source_kind) : 'Missing'}</span>
                  <span>{record ? record.status : 'unavailable'}</span>
                </div>
              </article>
            ))}
          </div>

          <form className="registry-form registry-form-compact" onSubmit={(event) => void handleFxSubmit(event)}>
            <div className="registry-form-title">Update FX Spot</div>
            <label>
              <span>Pair</span>
              <select value={editableFxPair} onChange={(event) => setEditableFxPair(event.target.value)}>
                {EDITABLE_FX_PAIRS.map(([baseCurrency, quoteCurrency]) => {
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
            <button type="submit" className="registry-submit">
              Save FX Rate
            </button>
            <div className="registry-form-note">
              Supported settlement currencies in this MVP: {(fxRates?.supported_currencies ?? ['USD', 'HKD', 'CNY']).join(' / ')}.
            </div>
          </form>
        </div>
      </section>

      <section className="registry-form-grid">
        <form className="registry-form" onSubmit={(event) => void handleCreate(event)}>
          <div className="registry-form-title">Create Instrument</div>
          <label>
            <span>Name</span>
            <input value={assetName} onChange={(event) => setAssetName(event.target.value)} required />
          </label>
          <label>
            <span>Type</span>
            <select value={assetType} onChange={(event) => setAssetType(event.target.value as AssetType)}>
              <option value="equity">Equity</option>
              <option value="fund">Fund</option>
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
          <label>
            <span>Primary Identifier</span>
            <input
              value={identifierValue}
              onChange={(event) => setIdentifierValue(event.target.value)}
              required
            />
          </label>
          <button type="submit" className="registry-submit">
            Create Instrument
          </button>
          <div className="registry-form-note">
            Watchlist and Portfolio only search and reference this registry. Asset master creation stays here.
            Archive assets here when they should stop appearing in downstream search.
          </div>
        </form>

        <form className="registry-form" onSubmit={(event) => void handleMetricSubmit(event)}>
          <div className="registry-form-title">Upsert Market Quote</div>
          <label>
            <span>Instrument</span>
            <select
              value={selectedAssetId}
              onChange={(event) => syncSelectedInstrument(event.target.value)}
            >
              {instruments.map((item) => (
                <option key={item.asset_id} value={item.asset_id}>
                  {item.asset_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Family</span>
            <select
              value={metricFamily}
              onChange={(event) => {
                const nextFamily = event.target.value as MetricFamily
                setMetricFamily(nextFamily)
                setQuoteBasis(QUOTE_BASIS_OPTIONS[nextFamily][0].value)
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
          <label>
            <span>Value</span>
            <input value={metricValue} onChange={(event) => setMetricValue(event.target.value)} required />
          </label>
          <label>
            <span>Currency</span>
            <input
              value={metricCurrency}
              onChange={(event) => setMetricCurrency(event.target.value)}
              required
            />
          </label>
          <label>
            <span>As Of</span>
            <input value={metricDate} onChange={(event) => setMetricDate(event.target.value)} required />
          </label>
          <label>
            <span>Status</span>
            <select value={metricStatus} onChange={(event) => setMetricStatus(event.target.value as DataStatus)}>
              <option value="complete">Complete</option>
              <option value="partial">Partial</option>
              <option value="unavailable">Unavailable</option>
            </select>
          </label>
          <button type="submit" className="registry-submit">
            Save Market Data
          </button>
          {selectedInstrument ? (
            <div className="registry-form-note">
              Valuation path: {formatPolicyPath(selectedInstrument.quote_selection_policy.valuation)}
              {' · '}
              Total return path: {formatPolicyPath(selectedInstrument.quote_selection_policy.total_return)}
            </div>
          ) : null}
        </form>
      </section>

      <section className="registry-table-shell">
        <div className="registry-table-header">
          <div>
            <div className="registry-form-title">Shared Data Ops</div>
            <div className="registry-table-meta">
              Source configuration and refresh ownership now sit at the platform layer.
            </div>
          </div>
        </div>
        <div className="registry-ops-grid">
          <div className="registry-ops-stack">
            <form className="registry-form registry-form-compact" onSubmit={(event) => void handleSourceSettingsSubmit(event)}>
              <div className="registry-form-title">Source Settings</div>
              <label>
                <span>Instrument</span>
                <select
                  value={selectedAssetId}
                  onChange={(event) => syncSelectedInstrument(event.target.value)}
                >
                  {instruments.map((item) => (
                    <option key={item.asset_id} value={item.asset_id}>
                      {item.asset_name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Source Mode</span>
                <select value={sourceMode} onChange={(event) => setSourceMode(event.target.value as SourceMode)}>
                  <option value="manual">Manual</option>
                  <option value="email">Email</option>
                  <option value="api">API</option>
                </select>
              </label>
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
                  placeholder="vendor_profile"
                />
              </label>
              <label>
                <span>Folder / Rule</span>
                <input
                  value={sourceLocation}
                  onChange={(event) => setSourceLocation(event.target.value)}
                  placeholder="Shared ops queue / mailbox folder / endpoint rule"
                />
              </label>
              <div className="registry-form-actions">
                <button type="submit" className="registry-submit">
                  Save Source Settings
                </button>
                <button type="button" className="app-link secondary" onClick={() => void handleRefreshClick()}>
                  Update Now
                </button>
              </div>
              <div className="registry-form-note">
                Email refresh is now executed from platform shared data ops. API mode remains explicit but is not yet wired to a vendor adapter.
              </div>
            </form>

            <form className="registry-form registry-form-compact" onSubmit={(event) => void handleNavImportSubmit(event)}>
              <div className="registry-form-title">Import NAV History</div>
              <div className="registry-form-note">
                Paste CSV or TSV with `date`, `nav`, `nav_with_dividend`, `currency`, `frequency`.
                Existing shared NAV rows for the same dates will be replaced.
              </div>
              <label>
                <span>Selected Instrument</span>
                <input value={selectedInstrument?.asset_name || ''} readOnly placeholder="Select an instrument above" />
              </label>
              <label>
                <span>Pasted Rows</span>
                <textarea
                  value={navImportText}
                  onChange={(event) => setNavImportText(event.target.value)}
                  placeholder={`date,nav,nav_with_dividend,currency\n2026-04-15,12.84,18.12,USD\n2026-04-14,12.81,18.07,USD`}
                />
              </label>
              <div className="registry-form-actions">
                <button type="submit" className="registry-submit" disabled={!selectedAssetId || !navImportText.trim()}>
                  Import NAV Rows
                </button>
              </div>
            </form>
          </div>

          <section className="registry-panel registry-panel-compact">
            <div className="registry-panel-header">
              <div>
                <div className="registry-kicker">Selected Instrument</div>
                <h2>{selectedInstrument?.asset_name || 'No instrument selected'}</h2>
              </div>
            </div>
            <div className="registry-stats registry-stats-ops">
              <div className="registry-stat">
                <span className="registry-stat-label">Primary Identifier</span>
                <strong>{selectedInstrument ? primaryIdentifier(selectedInstrument) : '—'}</strong>
              </div>
              <div className="registry-stat">
                <span className="registry-stat-label">Source Mode</span>
                <strong>{selectedInstrument ? formatSourceMode(selectedInstrument.source_settings.source_mode) : '—'}</strong>
              </div>
              <div className="registry-stat">
                <span className="registry-stat-label">Lifecycle</span>
                <strong>{selectedInstrument ? formatLifecycleLabel(selectedInstrument.lifecycle_state.status) : '—'}</strong>
              </div>
            </div>
            <div className="registry-op-meta">
              <div>
                <span>Refresh Message</span>
                <strong>{selectedInstrument?.refresh_status.message || 'No refresh requested yet.'}</strong>
              </div>
              <div>
                <span>Lifecycle Changed At</span>
                <strong>{selectedInstrument?.lifecycle_state.changed_at || '—'}</strong>
              </div>
              <div>
                <span>Lifecycle Changed By</span>
                <strong>{selectedInstrument?.lifecycle_state.changed_by || '—'}</strong>
              </div>
              <div>
                <span>Refresh Status</span>
                <strong>{selectedInstrument?.refresh_status.status || '—'}</strong>
              </div>
              <div>
                <span>Requested At</span>
                <strong>{selectedInstrument?.refresh_status.requested_at || '—'}</strong>
              </div>
              <div>
                <span>Requested By</span>
                <strong>{selectedInstrument?.refresh_status.requested_by || '—'}</strong>
              </div>
              <div>
                <span>Valuation Path</span>
                <strong>
                  {selectedInstrument ? formatPolicyPath(selectedInstrument.quote_selection_policy.valuation) : '—'}
                </strong>
              </div>
              <div>
                <span>Total Return Path</span>
                <strong>
                  {selectedInstrument ? formatPolicyPath(selectedInstrument.quote_selection_policy.total_return) : '—'}
                </strong>
              </div>
            </div>
            <div className="instrument-metric-stack">
              {selectedQuoteSummary.length ? (
                selectedQuoteSummary.map(({ role, point }) => (
                  <span key={`${selectedInstrument?.asset_id}-${role}-${point.quote_basis}`} className="metric-chip">
                    {ROLE_LABELS[role]} {point.value} {point.currency}
                  </span>
                ))
              ) : (
                <span className="metric-chip metric-chip-muted">No selected quotes</span>
              )}
            </div>
          </section>
        </div>
      </section>

      <section className="registry-table-shell">
        <div className="registry-table-header">
          <div>
            <div className="registry-form-title">Instrument Registry</div>
            <div className="registry-table-meta">
              {loading
                ? 'Loading instruments...'
                : `${instruments.length} instruments ${showInactive ? 'visible' : 'searchable'}`}
            </div>
          </div>
          <button type="button" className="app-link secondary" onClick={onToggleShowInactive}>
            {showInactive ? 'Show Active Only' : 'Include Archived'}
          </button>
        </div>
        <div className="registry-table-wrap">
          <table className="registry-table">
            <thead>
              <tr>
                <th>Identifier</th>
                <th>Name</th>
                <th>Type</th>
                <th>Currency</th>
                <th>Selected Quotes</th>
                <th>Source</th>
                <th>Coverage</th>
                <th>Lifecycle</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {instruments.map((item) => (
                <tr
                  key={item.asset_id}
                  className={item.lifecycle_state.status === 'archived' ? 'registry-row-archived' : undefined}
                >
                  <td>
                    <div className="instrument-id-stack">
                      <strong>{primaryIdentifier(item)}</strong>
                      <span>{item.asset_id}</span>
                    </div>
                  </td>
                  <td>{item.asset_name}</td>
                  <td>{item.asset_type}</td>
                  <td>{item.currency}</td>
                  <td>
                    <div className="instrument-metric-stack">
                      {summaryQuoteChips(item).length ? (
                        summaryQuoteChips(item).map(({ role, point }) => (
                          <span
                            key={`${item.asset_id}-${role}-${point.quote_basis}`}
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
                      {item.lifecycle_state.status === 'archived' ? (
                        <button
                          type="button"
                          className="registry-submit secondary"
                          onClick={() => void handleRestoreClick(item.asset_id)}
                        >
                          Restore
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="registry-submit danger"
                          onClick={() => void handleArchiveClick(item.asset_id)}
                        >
                          Archive
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  )
}

export default function App() {
  const currentPath = normalizePath(window.location.pathname)
  const [platformName, setPlatformName] = useState(PLATFORM_NAME_FALLBACK)
  const [apps, setApps] = useState<PlatformAppCard[]>(fallbackApps)
  const [sourceLabel, setSourceLabel] = useState(fallbackSourceLabel)
  const [registryName, setRegistryName] = useState('Yungu Shared Instruments')
  const [instruments, setInstruments] = useState<PlatformInstrumentRecord[]>([])
  const [fxRates, setFxRates] = useState<PlatformFxRatesResponse | null>(null)
  const [loadingInstruments, setLoadingInstruments] = useState(false)
  const [registryError, setRegistryError] = useState<string | null>(null)
  const [registryNotice, setRegistryNotice] = useState<string | null>(null)
  const [showInactive, setShowInactive] = useState(false)

  useEffect(() => {
    let cancelled = false

    async function loadApps() {
      try {
        const payload = await fetchJson<PlatformAppsResponse>('/api/apps')
        if (!cancelled) {
          setPlatformName(payload.platform_name)
          setApps(payload.apps)
          setSourceLabel('platform backend')
        }
      } catch {
        if (!cancelled) {
          setPlatformName(PLATFORM_NAME_FALLBACK)
          setApps(fallbackApps)
          setSourceLabel(fallbackSourceLabel)
        }
      }
    }

    void loadApps()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoadingInstruments(true)
    const instrumentPath = showInactive ? '/api/instruments?include_inactive=true' : '/api/instruments'

    Promise.all([
      fetchJson<PlatformInstrumentsResponse>(instrumentPath),
      fetchJson<PlatformFxRatesResponse>('/api/fx-rates'),
    ])
      .then(([instrumentPayload, fxPayload]) => {
        if (!cancelled) {
          setRegistryName(instrumentPayload.registry_name)
          setInstruments(instrumentPayload.instruments)
          setFxRates(fxPayload)
          setRegistryError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setRegistryError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load instruments.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingInstruments(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [showInactive])

  const completeCount = useMemo(
    () => instruments.filter((item) => item.coverage_state === 'complete').length,
    [instruments],
  )

  async function handleCreateInstrument(payload: {
    asset_name: string
    asset_type: AssetType
    currency: string
    identifiers: PlatformAssetIdentifier[]
  }) {
    try {
      const created = await fetchJson<PlatformInstrumentRecord>('/api/instruments', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setInstruments((current) => upsertInstrumentRecord(current, created, showInactive))
      setRegistryNotice(`Created instrument "${created.asset_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to create instrument.',
      )
    }
  }

  async function handleUpsertFxRate(payload: {
    base_currency: SupportedCurrency
    quote_currency: SupportedCurrency
    rate: string
    as_of_date: string
    provider?: string | null
    status: DataStatus
  }) {
    try {
      const instrumentPath = showInactive ? '/api/instruments?include_inactive=true' : '/api/instruments'
      const [updated, refreshedInstruments, refreshedFxRates] = await Promise.all([
        fetchJson<PlatformFxRateRecord>('/api/fx-rates', {
          method: 'POST',
          body: JSON.stringify(payload),
        }),
        fetchJson<PlatformInstrumentsResponse>(instrumentPath),
        fetchJson<PlatformFxRatesResponse>('/api/fx-rates'),
      ])
      setInstruments(refreshedInstruments.instruments)
      setFxRates(refreshedFxRates)
      setRegistryNotice(
        `Updated ${formatFxPairLabel(updated.base_currency, updated.quote_currency)} to ${updated.rate}.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to update FX rate.',
      )
    }
  }

  async function handleUpsertMarketData(payload: {
    asset_id: string
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
        `/api/instruments/${encodeURIComponent(payload.asset_id)}/market-data`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setRegistryNotice(`Updated ${formatBasisLabel(payload.quote_basis)} for "${updated.asset_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to save market data.',
      )
    }
  }

  async function handleUpdateSourceSettings(payload: {
    asset_id: string
    source_mode: SourceMode
    source_email: string
    source_location: string
    source_api_profile: string
  }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.asset_id)}/source-settings`,
        {
          method: 'PUT',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setRegistryNotice(`Saved shared source settings for "${updated.asset_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to save source settings.',
      )
    }
  }

  async function handleTriggerRefresh(payload: { asset_id: string; updated_by?: string | null }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.asset_id)}/refresh`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setRegistryNotice(updated.refresh_status.message || `Triggered refresh for "${updated.asset_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to trigger refresh.',
      )
    }
  }

  async function handleImportNavText(payload: {
    asset_id: string
    raw_text: string
    provider?: string | null
    status: DataStatus
    updated_by?: string | null
  }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.asset_id)}/nav-import`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setRegistryNotice(updated.refresh_status.message || `Imported NAV history for "${updated.asset_name}".`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to import NAV history.',
      )
    }
  }

  async function handleArchiveInstrument(payload: { asset_id: string; updated_by?: string | null }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.asset_id)}/archive`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setRegistryNotice(`Archived "${updated.asset_name}". Downstream search now hides it by default.`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to archive instrument.',
      )
    }
  }

  async function handleRestoreInstrument(payload: { asset_id: string; updated_by?: string | null }) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.asset_id)}/restore`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) => upsertInstrumentRecord(current, updated, showInactive))
      setRegistryNotice(`Restored "${updated.asset_name}" to downstream search.`)
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error ? requestError.message : 'Failed to restore instrument.',
      )
    }
  }

  if (currentPath === '/instruments') {
    return (
      <InstrumentsPage
        registryName={registryName}
        instruments={instruments}
        fxRates={fxRates}
        loading={loadingInstruments}
        error={registryError}
        notice={registryNotice}
        showInactive={showInactive}
        onToggleShowInactive={() => setShowInactive((current) => !current)}
        onCreateInstrument={handleCreateInstrument}
        onUpsertFxRate={handleUpsertFxRate}
        onUpsertMarketData={handleUpsertMarketData}
        onUpdateSourceSettings={handleUpdateSourceSettings}
        onTriggerRefresh={handleTriggerRefresh}
        onImportNavText={handleImportNavText}
        onArchiveInstrument={handleArchiveInstrument}
        onRestoreInstrument={handleRestoreInstrument}
      />
    )
  }

  return (
    <HomePage
      platformName={platformName}
      apps={apps}
      sourceLabel={sourceLabel}
      instrumentCount={instruments.length}
      completeCount={completeCount}
    />
  )
}
