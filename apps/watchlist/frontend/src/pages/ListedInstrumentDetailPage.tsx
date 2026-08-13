import { useEffect, useMemo, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { Link } from 'react-router'

import LoadingOverlay from '../components/LoadingOverlay'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import {
  getInstrumentAttributes,
  getInstrumentPerformance,
  getInstrumentPriceBars,
  getInstrumentRisk,
  getInstrumentSummary,
  getInstrumentChart,
  getInstrumentTaxonomyTree,
  updateInstrumentSettings,
  type FundChartResponse,
  type FundPerformanceResponse,
  type FundRiskResponse,
  type FundSummaryResponse,
  type InstrumentTaxonomyTreeResponse,
  type InstrumentAttributeValuesResponse,
  type InstrumentResolveResponse,
} from '../lib/api'
import { formatDate, formatLabel, formatNumber, formatPercent, signedValueClass } from '../lib/format'
import {
  adjustPriceBars,
  hasCompleteAdjustmentFactors,
  priceReturnStats,
  priceRiskStats,
  slicePriceBars,
  type DisplayPriceBar,
  type PriceAdjustmentMode,
  type PriceRange,
} from '../lib/priceBars'
import { buildWatchlistPath, PLATFORM_HOME_URL } from '../lib/navigation'

type ListedTab = 'overview' | 'performance' | 'risk' | 'price'

type WatchlistBreadcrumbContext = {
  watchlistId: string
  watchlistName: string
}

type Props = {
  instrument: InstrumentResolveResponse
  watchlistContext: WatchlistBreadcrumbContext | null
}

const RANGE_OPTIONS: PriceRange[] = ['1M', '3M', '6M', '1Y', 'ALL']

function instrumentTypeLabel(value: string) {
  if (value === 'etf') return 'ETF'
  if (value === 'equity') return 'Stock'
  if (value === 'index') return 'Index'
  return formatLabel(value)
}

function compactValue(value: number | null, maximumFractionDigits = 2) {
  if (value === null || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('en-US', {
    notation: Math.abs(value) >= 100_000 ? 'compact' : 'standard',
    maximumFractionDigits,
  }).format(value)
}

function percentValue(value: number | null) {
  return value === null ? '—' : formatPercent(value)
}

type StandardizedMetric = {
  present: boolean
  value: number | null
}

function standardizedReturn(
  performance: FundPerformanceResponse | null,
  window: string,
): StandardizedMetric {
  const row = performance?.trailing_returns.find(
    (item) => String(item.window || '').toUpperCase() === window.toUpperCase(),
  )
  if (!row) return { present: false, value: null }
  const value = row?.investment_nav
  if (typeof value === 'number' && Number.isFinite(value)) {
    return { present: true, value }
  }
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return { present: true, value: Number(value) }
  }
  return { present: true, value: null }
}

function standardizedRiskMetric(
  risk: FundRiskResponse | null,
  metric: string,
): StandardizedMetric {
  const row = risk?.risk_metrics.find(
    (item) => String(item.metric || '').toLowerCase() === metric.toLowerCase(),
  )
  if (!row) return { present: false, value: null }
  const value = row?.investment
  if (typeof value === 'number' && Number.isFinite(value)) {
    return { present: true, value }
  }
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
    return { present: true, value: Number(value) }
  }
  return { present: true, value: null }
}

function MetricCard({
  label,
  value,
  tone,
  note,
}: {
  label: string
  value: string
  tone?: string
  note?: string
}) {
  return (
    <div className="listed-metric-card">
      <div className="listed-metric-label">{label}</div>
      <div className={`listed-metric-value ${tone || ''}`}>{value}</div>
      {note ? <div className="listed-metric-note">{note}</div> : null}
    </div>
  )
}

function CandlestickChart({ bars }: { bars: DisplayPriceBar[] }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const width = 1000
  const height = 470
  const left = 18
  const right = 74
  const priceTop = 24
  const priceBottom = 332
  const volumeTop = 365
  const volumeBottom = 438
  const plotWidth = width - left - right
  const activeIndex = hoveredIndex ?? Math.max(0, bars.length - 1)
  const activeBar = bars[activeIndex]

  const geometry = useMemo(() => {
    if (!bars.length) return null
    const rawMin = Math.min(...bars.map((bar) => bar.low))
    const rawMax = Math.max(...bars.map((bar) => bar.high))
    const padding = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.005)
    const minimum = rawMin - padding
    const maximum = rawMax + padding
    const span = Math.max(maximum - minimum, 0.000001)
    const maximumVolume = Math.max(...bars.map((bar) => bar.volume ?? 0), 1)
    const x = (index: number) =>
      bars.length === 1
        ? left + plotWidth / 2
        : left + (index / (bars.length - 1)) * plotWidth
    const y = (value: number) =>
      priceTop + ((maximum - value) / span) * (priceBottom - priceTop)
    return { minimum, maximum, maximumVolume, x, y }
  }, [bars, plotWidth])

  function handleMouseMove(event: ReactMouseEvent<SVGSVGElement>) {
    if (!bars.length) return
    const bounds = event.currentTarget.getBoundingClientRect()
    const svgX = ((event.clientX - bounds.left) / bounds.width) * width
    const ratio = Math.min(1, Math.max(0, (svgX - left) / plotWidth))
    setHoveredIndex(Math.round(ratio * Math.max(0, bars.length - 1)))
  }

  if (!geometry || !activeBar) {
    return <div className="listed-empty-chart">No OHLCV history is available.</div>
  }

  const candleWidth = Math.max(0.7, Math.min(9, (plotWidth / Math.max(bars.length, 1)) * 0.62))
  const tickIndexes = Array.from(
    new Set(
      [0, 0.2, 0.4, 0.6, 0.8, 1].map((ratio) =>
        Math.round(ratio * Math.max(0, bars.length - 1)),
      ),
    ),
  )
  const activeX = geometry.x(activeIndex)

  return (
    <div className="listed-chart-shell">
      <div className="listed-chart-legend">
        <strong>{formatDate(activeBar.date)}</strong>
        <span>O {formatNumber(activeBar.open, 3)}</span>
        <span>H {formatNumber(activeBar.high, 3)}</span>
        <span>L {formatNumber(activeBar.low, 3)}</span>
        <span>C {formatNumber(activeBar.close, 3)}</span>
        <span>Vol {compactValue(activeBar.volume, 1)}</span>
      </div>
      <svg
        className="listed-candlestick-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Candlestick price and volume chart"
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoveredIndex(null)}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const value = geometry.maximum - (geometry.maximum - geometry.minimum) * ratio
          const y = priceTop + (priceBottom - priceTop) * ratio
          return (
            <g key={ratio}>
              <line className="listed-chart-grid" x1={left} x2={width - right} y1={y} y2={y} />
              <text className="listed-chart-axis" x={width - right + 10} y={y + 4}>
                {compactValue(value, value < 10 ? 3 : 2)}
              </text>
            </g>
          )
        })}
        {bars.map((bar, index) => {
          const x = geometry.x(index)
          const openY = geometry.y(bar.open)
          const closeY = geometry.y(bar.close)
          const highY = geometry.y(bar.high)
          const lowY = geometry.y(bar.low)
          const isUp = bar.close >= bar.open
          const volumeHeight = ((bar.volume ?? 0) / geometry.maximumVolume) * (volumeBottom - volumeTop)
          const className = isUp ? 'listed-candle-up' : 'listed-candle-down'
          return (
            <g key={bar.date} className={className}>
              <line x1={x} x2={x} y1={highY} y2={lowY} />
              <rect
                x={x - candleWidth / 2}
                y={Math.min(openY, closeY)}
                width={candleWidth}
                height={Math.max(1, Math.abs(closeY - openY))}
              />
              <rect
                className="listed-volume-bar"
                x={x - candleWidth / 2}
                y={volumeBottom - volumeHeight}
                width={candleWidth}
                height={Math.max(0.6, volumeHeight)}
              />
            </g>
          )
        })}
        <line className="listed-chart-divider" x1={left} x2={width - right} y1={352} y2={352} />
        {tickIndexes.map((index) => (
          <text
            key={bars[index].date}
            className="listed-chart-axis listed-chart-axis-x"
            x={geometry.x(index)}
            y={462}
            textAnchor={index === 0 ? 'start' : index === bars.length - 1 ? 'end' : 'middle'}
          >
            {bars[index].date.slice(0, 7)}
          </text>
        ))}
        {hoveredIndex !== null ? (
          <line
            className="listed-chart-crosshair"
            x1={activeX}
            x2={activeX}
            y1={priceTop}
            y2={volumeBottom}
          />
        ) : null}
      </svg>
    </div>
  )
}

function GrowthChart({ bars }: { bars: DisplayPriceBar[] }) {
  if (bars.length < 2) {
    return <div className="listed-empty-chart">Not enough price history for a performance chart.</div>
  }
  const width = 1000
  const height = 285
  const left = 20
  const right = 70
  const top = 22
  const bottom = 248
  const base = bars[0].close
  const values = bars.map((bar) => (bar.close / base - 1) * 100)
  const minimum = Math.min(...values, 0)
  const maximum = Math.max(...values, 0)
  const span = Math.max(maximum - minimum, 0.01)
  const x = (index: number) => left + (index / Math.max(1, bars.length - 1)) * (width - left - right)
  const y = (value: number) => top + ((maximum - value) / span) * (bottom - top)
  const path = values.map((value, index) => `${index ? 'L' : 'M'} ${x(index)} ${y(value)}`).join(' ')
  return (
    <svg className="listed-growth-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Cumulative price return chart">
      {[0, 0.5, 1].map((ratio) => {
        const value = maximum - span * ratio
        const gridY = top + (bottom - top) * ratio
        return (
          <g key={ratio}>
            <line className="listed-chart-grid" x1={left} x2={width - right} y1={gridY} y2={gridY} />
            <text className="listed-chart-axis" x={width - right + 10} y={gridY + 4}>{formatPercent(value, 1)}</text>
          </g>
        )
      })}
      <line className="listed-growth-zero" x1={left} x2={width - right} y1={y(0)} y2={y(0)} />
      <path className="listed-growth-line" d={path} fill="none" />
      <text className="listed-chart-axis" x={left} y={278}>{formatDate(bars[0].date)}</text>
      <text className="listed-chart-axis" x={width - right} y={278} textAnchor="end">{formatDate(bars[bars.length - 1]?.date)}</text>
    </svg>
  )
}

function displayObjectValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return compactValue(value, 3)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (Array.isArray(value)) return value.map(displayObjectValue).join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function DataTable({ title, rows }: { title: string; rows: Array<Record<string, unknown>> }) {
  if (!rows.length) return null
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 8)
  return (
    <section className="panel listed-data-panel">
      <div className="panel-header"><div className="panel-title">{title}</div></div>
      <div className="table-shell">
        <table className="listed-data-table">
          <thead><tr>{columns.map((column) => <th key={column}>{formatLabel(column)}</th>)}</tr></thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>{columns.map((column) => <td key={column}>{displayObjectValue(row[column])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function RiskOverview({ overview }: { overview: Record<string, unknown> | null | undefined }) {
  if (!overview || !Object.keys(overview).length) return null
  return (
    <section className="panel listed-data-panel">
      <div className="panel-header"><div className="panel-title">Standardized Risk Metrics</div></div>
      <div className="listed-key-value-grid">
        {Object.entries(overview).map(([key, value]) => (
          <div key={key}><span>{formatLabel(key)}</span><strong>{displayObjectValue(value)}</strong></div>
        ))}
      </div>
    </section>
  )
}

export default function ListedInstrumentDetailPage({ instrument, watchlistContext }: Props) {
  const instrumentId = instrument.detail_subject_id || instrument.canonical_instrument_id || instrument.requested_instrument_id
  const [tab, setTab] = useState<ListedTab>('overview')
  const [range, setRange] = useState<PriceRange>('6M')
  const [mode, setMode] = useState<PriceAdjustmentMode>('raw')
  const [barsResponse, setBarsResponse] = useState<Awaited<ReturnType<typeof getInstrumentPriceBars>> | null>(null)
  const [summary, setSummary] = useState<FundSummaryResponse | null>(null)
  const [chart, setChart] = useState<FundChartResponse | null>(null)
  const [performance, setPerformance] = useState<FundPerformanceResponse | null>(null)
  const [risk, setRisk] = useState<FundRiskResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [standardizedError, setStandardizedError] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [attributeValues, setAttributeValues] = useState<InstrumentAttributeValuesResponse | null>(null)
  const [taxonomyTree, setTaxonomyTree] = useState<InstrumentTaxonomyTreeResponse | null>(null)
  const [taxonomyDraftNodeId, setTaxonomyDraftNodeId] = useState('')
  const [coverageStatusDraft, setCoverageStatusDraft] = useState('')

  function closeSettings() {
    if (!settingsSaving) setSettingsOpen(false)
  }

  const settingsDialogRef = useModalDialog(settingsOpen, closeSettings)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      setStandardizedError(null)
      const [barsResult, summaryResult, chartResult, performanceResult, riskResult] = await Promise.allSettled([
        getInstrumentPriceBars(instrumentId, { limit: 1250 }),
        getInstrumentSummary(instrumentId),
        getInstrumentChart(instrumentId),
        getInstrumentPerformance(instrumentId),
        getInstrumentRisk(instrumentId),
      ])
      if (cancelled) return
      if (barsResult.status === 'rejected') {
        setError(barsResult.reason instanceof Error ? barsResult.reason.message : 'Failed to load OHLCV history.')
      } else {
        setBarsResponse(barsResult.value)
        const qfqReady =
          instrument.instrument_type !== 'index' &&
          hasCompleteAdjustmentFactors(barsResult.value.bars)
        setMode(qfqReady ? 'qfq' : 'raw')
      }
      setSummary(summaryResult.status === 'fulfilled' ? summaryResult.value : null)
      setChart(chartResult.status === 'fulfilled' ? chartResult.value : null)
      setPerformance(performanceResult.status === 'fulfilled' ? performanceResult.value : null)
      setRisk(riskResult.status === 'fulfilled' ? riskResult.value : null)
      const failedStandardizedSurfaces = [
        performanceResult.status === 'rejected' ? 'performance' : null,
        riskResult.status === 'rejected' ? 'risk' : null,
      ].filter((value): value is string => Boolean(value))
      if (failedStandardizedSurfaces.length) {
        setStandardizedError(
          `Standardized ${failedStandardizedSurfaces.join(' and ')} data is unavailable; affected metrics are withheld.`,
        )
      }
      setLoading(false)
    }
    void load()
    return () => { cancelled = true }
  }, [instrument.instrument_type, instrumentId])

  useEffect(() => {
    if (!settingsOpen) return
    let cancelled = false

    async function loadSettings() {
      setSettingsLoading(true)
      setSettingsError(null)
      try {
        const [attributes, taxonomy] = await Promise.all([
          getInstrumentAttributes(instrumentId),
          getInstrumentTaxonomyTree(),
        ])
        if (cancelled) return
        setAttributeValues(attributes)
        setTaxonomyTree(taxonomy)
        setTaxonomyDraftNodeId(attributes.taxonomy.assigned_node_id || '')
        const status = attributes.values.coverage_status
        setCoverageStatusDraft(typeof status === 'string' ? status : '')
      } catch (loadError) {
        if (!cancelled) {
          setSettingsError(
            loadError instanceof Error ? loadError.message : 'Failed to load instrument settings.',
          )
        }
      } finally {
        if (!cancelled) setSettingsLoading(false)
      }
    }

    void loadSettings()
    return () => {
      cancelled = true
    }
  }, [instrumentId, settingsOpen])

  async function saveSettings() {
    if (settingsSaving || settingsLoading || !attributeValues) return
    setSettingsSaving(true)
    setSettingsError(null)
    try {
      const taxonomyChanged =
        taxonomyDraftNodeId !== (attributeValues.taxonomy.assigned_node_id || '')
      const currentStatus = attributeValues.values.coverage_status
      const statusChanged =
        coverageStatusDraft !== (typeof currentStatus === 'string' ? currentStatus : '')

      if (taxonomyChanged || statusChanged) {
        await updateInstrumentSettings(instrumentId, {
          taxonomy_node_id: taxonomyDraftNodeId || null,
          coverage_status: coverageStatusDraft || null,
          updated_by: 'terminal_ui',
        })
      }

      const [nextAttributes, nextSummary] = await Promise.all([
        getInstrumentAttributes(instrumentId),
        getInstrumentSummary(instrumentId),
      ])
      setAttributeValues(nextAttributes)
      setSummary(nextSummary)
      setSettingsOpen(false)
    } catch (saveError) {
      setSettingsError(
        saveError instanceof Error ? saveError.message : 'Failed to save instrument settings.',
      )
    } finally {
      setSettingsSaving(false)
    }
  }

  const qfqAvailable =
    instrument.instrument_type !== 'index' &&
    hasCompleteAdjustmentFactors(barsResponse?.bars ?? [])
  const allBars = useMemo(
    () => adjustPriceBars(barsResponse?.bars ?? [], mode),
    [barsResponse, mode],
  )
  const fallbackCloseBars = useMemo<DisplayPriceBar[]>(() => {
    const points = chart?.series[0]?.points ?? []
    return points.flatMap((point) => {
      if (!Number.isFinite(point.value) || point.value <= 0) return []
      return [{
        date: point.date,
        open: point.value,
        high: point.value,
        low: point.value,
        close: point.value,
        previousClose: null,
        volume: null,
        turnover: null,
        adjustmentFactor: null,
        currency: chart?.currency || barsResponse?.currency || '',
        volumeUnit: null,
        turnoverUnit: null,
        provider: 'canonical_chart_series',
        status: 'complete' as const,
      }]
    })
  }, [barsResponse?.currency, chart])
  const analysisBars = allBars.length ? allBars : fallbackCloseBars
  const visibleBars = useMemo(() => slicePriceBars(allBars, range), [allBars, range])
  const visibleAnalysisBars = useMemo(() => slicePriceBars(analysisBars, range), [analysisBars, range])
  const returns = useMemo(() => priceReturnStats(analysisBars), [analysisBars])
  const standardizedReturns = useMemo(
    () => ({
      oneMonth: standardizedReturn(performance, '1M'),
      threeMonth: standardizedReturn(performance, '3M'),
      sixMonth: standardizedReturn(performance, '6M'),
      ytd: standardizedReturn(performance, 'YTD'),
      oneYear: standardizedReturn(performance, '1Y'),
    }),
    [performance],
  )
  const displayReturns = useMemo(() => ({
    ...returns,
    oneMonth: standardizedReturns.oneMonth.present
      ? standardizedReturns.oneMonth.value
      : null,
    threeMonth: standardizedReturns.threeMonth.present
      ? standardizedReturns.threeMonth.value
      : null,
    sixMonth: standardizedReturns.sixMonth.present
      ? standardizedReturns.sixMonth.value
      : null,
    ytd: standardizedReturns.ytd.present
      ? standardizedReturns.ytd.value
      : null,
    oneYear: standardizedReturns.oneYear.present
      ? standardizedReturns.oneYear.value
      : null,
  }), [returns, standardizedReturns])
  const riskStats = useMemo(() => priceRiskStats(analysisBars), [analysisBars])
  const standardizedRisk = useMemo(
    () => ({
      annualizedVolatility: standardizedRiskMetric(risk, 'volatility'),
      maximumDrawdown: standardizedRiskMetric(risk, 'max_drawdown'),
    }),
    [risk],
  )
  const riskPathMetricsWithheld =
    risk?.data_quality?.status === 'withheld_missing_observations'
  const displayRiskStats = useMemo(() => ({
    ...riskStats,
    annualizedVolatility: standardizedRisk.annualizedVolatility.present
      ? standardizedRisk.annualizedVolatility.value
      : null,
    maximumDrawdown: standardizedRisk.maximumDrawdown.present
      ? standardizedRisk.maximumDrawdown.value
      : null,
    currentDrawdown:
      riskPathMetricsWithheld || !risk || typeof risk.current_drawdown !== 'number'
        ? null
        : risk.current_drawdown,
  }), [riskPathMetricsWithheld, riskStats, standardizedRisk])
  const latest = analysisBars[analysisBars.length - 1]
  const latestPriceBar = allBars[allBars.length - 1]
  const visibleHigh = visibleAnalysisBars.length ? Math.max(...visibleAnalysisBars.map((bar) => bar.high)) : null
  const visibleLow = visibleAnalysisBars.length ? Math.min(...visibleAnalysisBars.map((bar) => bar.low)) : null
  const sourceRefreshFailed = ['failed', 'blocked'].includes(barsResponse?.source_refresh_status || '')
  const analysisBasisLabel = allBars.length
    ? mode === 'qfq' ? 'QFQ price' : 'raw price'
    : chart?.selected_series?.label || chart?.base_series_type || 'canonical close series'
  const performanceAsOfNote = performance?.snapshot_metadata?.as_of_date
    ? `Standardized · as of ${formatDate(performance.snapshot_metadata.as_of_date)}`
    : 'Standardized performance unavailable'
  const riskAsOfNote = riskPathMetricsWithheld
    ? `Withheld · ${risk?.data_quality?.gap_count ?? 0} missing observation(s)`
    : risk?.snapshot_metadata?.as_of_date
      ? `Standardized · as of ${formatDate(risk.snapshot_metadata.as_of_date)}`
      : 'Standardized risk unavailable'
  const coverageStatusDefinition = attributeValues?.definitions.find(
    (definition) => definition.attribute_key === 'coverage_status',
  )
  const compatibleTaxonomyNodes = (taxonomyTree?.nodes || [])
    .filter(
      (node) =>
        node.instrument_type === instrument.instrument_type ||
        (instrument.instrument_type === 'etf' && node.instrument_type === 'fund'),
    )
    .sort((left, right) =>
      left.path_labels.join(' / ').localeCompare(right.path_labels.join(' / '), 'zh-Hans-CN'),
    )
  const currentTaxonomyPath = attributeValues?.taxonomy.path_labels.join(' / ') || 'Unclassified'
  const displayedCoverageStatus = summary?.instrument_attributes.coverage_status

  if (loading) return <LoadingOverlay label="Loading market detail" />

  const chartPanel = (
    <section className="panel listed-chart-panel">
      <div className="listed-chart-toolbar">
        <div>
          <div className="panel-title">Price & Volume</div>
          <div className="listed-chart-caption">
            {mode === 'qfq' ? 'Forward-adjusted (QFQ)' : 'Raw exchange price'} · {visibleBars.length} observations
          </div>
        </div>
        <div className="listed-chart-controls">
          <div className="listed-segmented-control" aria-label="Price adjustment">
            <button type="button" className={mode === 'qfq' ? 'active' : ''} disabled={!qfqAvailable} onClick={() => setMode('qfq')}>QFQ</button>
            <button type="button" className={mode === 'raw' ? 'active' : ''} onClick={() => setMode('raw')}>Raw</button>
          </div>
          <div className="listed-segmented-control" aria-label="Date range">
            {RANGE_OPTIONS.map((option) => (
              <button type="button" key={option} className={range === option ? 'active' : ''} onClick={() => setRange(option)}>{option}</button>
            ))}
          </div>
        </div>
      </div>
      {error ? <div className="error-state">{error}</div> : <CandlestickChart bars={visibleBars} />}
      <div className="listed-source-note">
        {barsResponse?.count
          ? qfqAvailable
            ? 'Raw OHLCV is retained in the database. QFQ is calculated for display from complete provider adjustment factors; volume remains raw.'
            : 'Raw OHLCV is retained in the database. QFQ is unavailable because one or more bars lack a valid adjustment factor.'
          : 'No canonical OHLCV bars are stored yet.'}
      </div>
      {sourceRefreshFailed ? (
        <div className="listed-source-alert">
          Source update failed; existing canonical history was preserved. {barsResponse?.source_refresh_message}
        </div>
      ) : null}
    </section>
  )

  return (
    <div className="instrument-detail-page listed-detail-page">
      <div className="instrument-detail-topbar">
        <div className="stub-breadcrumbs">
          <a href={PLATFORM_HOME_URL} className="watchlist-breadcrumb-link">Home</a>
          <span className="watchlist-breadcrumb-separator">/</span>
          <Link to="/watchlists" className="watchlist-breadcrumb-link">Watchlist</Link>
          {watchlistContext ? (
            <>
              <span className="watchlist-breadcrumb-separator">/</span>
              <Link to={buildWatchlistPath(watchlistContext.watchlistId)} className="watchlist-breadcrumb-link">{watchlistContext.watchlistName}</Link>
            </>
          ) : null}
          <span className="watchlist-breadcrumb-separator">/</span>
          <span className="watchlist-breadcrumb-current">{instrument.instrument_name}</span>
        </div>
        <div className="instrument-detail-actions">
          <button type="button" onClick={() => setSettingsOpen(true)}>Settings</button>
        </div>
      </div>

      <section className="panel listed-detail-hero">
        <div>
          <div className="instrument-detail-eyebrow">{`${instrumentTypeLabel(instrument.instrument_type)} Detail`}</div>
          <h1 className="instrument-detail-title">{instrument.instrument_name}</h1>
          <div className="instrument-detail-badges">
            {instrument.primary_identifier ? <span className="context-chip">{instrument.primary_identifier}</span> : null}
            <span className="context-chip">{barsResponse?.currency || latest?.currency || '—'}</span>
            {barsResponse?.count ? <span className="context-chip">Raw OHLCV retained</span> : null}
            <span className="context-chip">
              {qfqAvailable ? 'OHLCV QFQ available' : barsResponse?.count ? 'Raw OHLCV only' : 'OHLCV pending'}
            </span>
            {sourceRefreshFailed ? <span className="context-chip listed-source-failed-chip">Source update failed</span> : null}
            {summary?.freshness.data_freshness_status ? <span className="context-chip">{formatLabel(summary.freshness.data_freshness_status)}</span> : null}
            {typeof displayedCoverageStatus === 'string' && displayedCoverageStatus ? (
              <span className="context-chip">{formatLabel(displayedCoverageStatus)}</span>
            ) : null}
            {summary?.taxonomy.path_labels.length ? (
              <span className="context-chip">{summary.taxonomy.path_labels.join(' / ')}</span>
            ) : null}
          </div>
        </div>
        <div className="listed-hero-quote">
          <span>{formatDate(latest?.date)}</span>
          <strong>{latest ? formatNumber(latest.close, latest.close < 10 ? 4 : 2) : '—'}</strong>
          <em className={signedValueClass(displayReturns.dailyChange)}>{percentValue(displayReturns.dailyChange)}</em>
        </div>
      </section>

      {settingsOpen ? (
        <div className="instrument-modal-backdrop" onClick={closeSettings}>
          <div
            ref={settingsDialogRef}
            className="instrument-modal instrument-settings-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Settings"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="instrument-modal-header">
              <div>
                <div className="panel-title">Settings</div>
                <div className="instrument-quote-source-title">Instrument Settings</div>
              </div>
              <div className="toolbar">
                <button type="button" disabled={settingsSaving} onClick={closeSettings}>Cancel</button>
                <button
                  type="button"
                  className="button-primary"
                  disabled={settingsSaving || settingsLoading || !attributeValues}
                  onClick={() => void saveSettings()}
                >
                  {settingsSaving ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
            <div className="instrument-settings-body">
              {settingsError ? (
                <div className="inline-notice inline-notice-error" role="alert">{settingsError}</div>
              ) : null}
              <section className="instrument-settings-section">
                <div className="instrument-settings-section-header">
                  <div>
                    <div className="instrument-settings-title">Investment Status</div>
                    <div className="instrument-settings-description">
                      Set this specific instrument to Watch, Proposed, Invested, Paused, or Exited.
                    </div>
                  </div>
                </div>
                <div className="instrument-settings-taxonomy-stack">
                  <label className="instrument-settings-taxonomy-row">
                    <span className="instrument-settings-taxonomy-label">Status</span>
                    <select
                      className="instrument-settings-taxonomy-select"
                      value={coverageStatusDraft}
                      disabled={settingsLoading || !coverageStatusDefinition}
                      onChange={(event) => setCoverageStatusDraft(event.target.value)}
                    >
                      <option value="">Unspecified</option>
                      {(coverageStatusDefinition?.options || []).map((status) => (
                        <option key={status} value={status}>{formatLabel(status)}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </section>
              <section className="instrument-settings-section">
                <div className="instrument-settings-section-header">
                  <div>
                    <div className="instrument-settings-title">Classification Path</div>
                    <div className="instrument-settings-current-path">
                      <span>Current path</span>
                      <strong>{currentTaxonomyPath}</strong>
                    </div>
                  </div>
                </div>
                <div className="instrument-settings-taxonomy-stack">
                  <label className="instrument-settings-taxonomy-row">
                    <span className="instrument-settings-taxonomy-label">Taxonomy</span>
                    <select
                      className="instrument-settings-taxonomy-select"
                      value={taxonomyDraftNodeId}
                      disabled={settingsLoading || !taxonomyTree}
                      onChange={(event) => setTaxonomyDraftNodeId(event.target.value)}
                    >
                      <option value="">Unclassified</option>
                      {compatibleTaxonomyNodes.map((node) => (
                        <option key={node.node_id} value={node.node_id}>
                          {node.path_labels.join(' / ')}
                        </option>
                      ))}
                    </select>
                  </label>
                  {settingsLoading ? <div className="instrument-settings-loading">Loading settings…</div> : null}
                </div>
              </section>
            </div>
          </div>
        </div>
      ) : null}

      {standardizedError ? (
        <div className="listed-source-alert" role="alert">{standardizedError}</div>
      ) : null}

      <div className="instrument-detail-tabs-row">
        <div className="instrument-detail-tabs">
          {(['overview', 'performance', 'risk', 'price'] as ListedTab[]).map((item) => (
            <button
              type="button"
              key={item}
              className={`instrument-detail-tab ${tab === item ? 'instrument-detail-tab-active' : ''}`}
              onClick={() => setTab(item)}
            >
              {formatLabel(item)}
            </button>
          ))}
        </div>
      </div>

      {tab === 'overview' ? (
        <div className="listed-tab-stack">
          <section className="listed-metric-grid">
            <MetricCard label="Close" value={latest ? formatNumber(latest.close, latest.close < 10 ? 4 : 2) : '—'} note={analysisBasisLabel} />
            <MetricCard label="Daily Change" value={percentValue(displayReturns.dailyChange)} tone={signedValueClass(displayReturns.dailyChange)} />
            <MetricCard label={`${range} High`} value={visibleHigh === null ? '—' : formatNumber(visibleHigh, visibleHigh < 10 ? 4 : 2)} />
            <MetricCard label={`${range} Low`} value={visibleLow === null ? '—' : formatNumber(visibleLow, visibleLow < 10 ? 4 : 2)} />
            <MetricCard label="Volume" value={compactValue(latestPriceBar?.volume ?? null, 2)} note={latestPriceBar?.volumeUnit || undefined} />
            <MetricCard label="Turnover" value={compactValue(latestPriceBar?.turnover ?? null, 2)} note={latestPriceBar?.turnoverUnit || undefined} />
          </section>
          {chartPanel}
          <section className="listed-metric-grid listed-return-strip">
            <MetricCard label="1 Month" value={percentValue(displayReturns.oneMonth)} tone={signedValueClass(displayReturns.oneMonth)} note={performanceAsOfNote} />
            <MetricCard label="3 Months" value={percentValue(displayReturns.threeMonth)} tone={signedValueClass(displayReturns.threeMonth)} note={performanceAsOfNote} />
            <MetricCard label="YTD" value={percentValue(displayReturns.ytd)} tone={signedValueClass(displayReturns.ytd)} note={performanceAsOfNote} />
            <MetricCard label="1 Year" value={percentValue(displayReturns.oneYear)} tone={signedValueClass(displayReturns.oneYear)} note={performanceAsOfNote} />
          </section>
        </div>
      ) : null}

      {tab === 'performance' ? (
        <div className="listed-tab-stack">
          <section className="listed-metric-grid">
            <MetricCard label="1 Month" value={percentValue(displayReturns.oneMonth)} tone={signedValueClass(displayReturns.oneMonth)} note={performanceAsOfNote} />
            <MetricCard label="3 Months" value={percentValue(displayReturns.threeMonth)} tone={signedValueClass(displayReturns.threeMonth)} note={performanceAsOfNote} />
            <MetricCard label="6 Months" value={percentValue(displayReturns.sixMonth)} tone={signedValueClass(displayReturns.sixMonth)} note={performanceAsOfNote} />
            <MetricCard label="YTD" value={percentValue(displayReturns.ytd)} tone={signedValueClass(displayReturns.ytd)} note={performanceAsOfNote} />
            <MetricCard label="1 Year" value={percentValue(displayReturns.oneYear)} tone={signedValueClass(displayReturns.oneYear)} note={performanceAsOfNote} />
            <MetricCard label="Available History" value={percentValue(displayReturns.sinceStart)} tone={signedValueClass(displayReturns.sinceStart)} />
          </section>
          <section className="panel listed-chart-panel">
            <div className="listed-chart-toolbar"><div><div className="panel-title">Growth of Price</div><div className="listed-chart-caption">Cumulative return from {analysisBasisLabel}</div></div></div>
            <GrowthChart bars={analysisBars} />
          </section>
          <DataTable title="Trailing Returns · Standardized Engine" rows={performance?.trailing_returns ?? []} />
          <DataTable title="Annual Returns · Standardized Engine" rows={performance?.annual_returns ?? []} />
        </div>
      ) : null}

      {tab === 'risk' ? (
        <div className="listed-tab-stack">
          <section className="listed-metric-grid">
            <MetricCard label="Annualized Volatility" value={percentValue(displayRiskStats.annualizedVolatility)} note={riskAsOfNote} />
            <MetricCard label="Maximum Drawdown" value={percentValue(displayRiskStats.maximumDrawdown)} tone={signedValueClass(displayRiskStats.maximumDrawdown)} note={riskAsOfNote} />
            <MetricCard label="Current Drawdown" value={percentValue(displayRiskStats.currentDrawdown)} tone={signedValueClass(displayRiskStats.currentDrawdown)} note={riskAsOfNote} />
            <MetricCard label="Observations" value={String(displayRiskStats.observationCount)} />
          </section>
          <RiskOverview overview={risk?.risk_overview} />
          <DataTable title="Risk Detail · Standardized Engine" rows={risk?.risk_metrics ?? []} />
          {risk?.drawdown_summary ? <RiskOverview overview={risk.drawdown_summary} /> : null}
        </div>
      ) : null}

      {tab === 'price' ? (
        <div className="listed-tab-stack">
          {chartPanel}
          <section className="panel listed-data-panel">
            <div className="panel-header"><div className="panel-title">Recent Daily Bars</div></div>
            <div className="table-shell listed-price-table-shell">
              <table className="listed-data-table listed-price-table">
                <thead><tr><th>Date</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Change</th><th>Volume</th><th>Turnover</th></tr></thead>
                <tbody>
                  {[...allBars].reverse().slice(0, 60).map((bar, index, rows) => {
                    const prior = rows[index + 1]
                    const change = prior ? (bar.close / prior.close - 1) * 100 : null
                    return (
                      <tr key={bar.date}>
                        <td>{formatDate(bar.date)}</td><td>{formatNumber(bar.open, 4)}</td><td>{formatNumber(bar.high, 4)}</td><td>{formatNumber(bar.low, 4)}</td><td>{formatNumber(bar.close, 4)}</td>
                        <td className={signedValueClass(change)}>{percentValue(change)}</td><td>{compactValue(bar.volume, 2)}</td><td>{compactValue(bar.turnover, 2)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  )
}
