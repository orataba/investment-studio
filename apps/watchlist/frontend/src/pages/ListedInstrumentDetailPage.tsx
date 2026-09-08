import InstrumentRiskPanel from '../components/InstrumentRiskPanel'
import { useCanWriteTeam } from '../components/AccountBoundary'
import { useEffect, useMemo, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { Link, useSearchParams } from 'react-router'

import LoadingOverlay from '../components/LoadingOverlay'
import InfoHint from '../../../../../packages/ui/src/InfoHint'
import InvestmentOpinionTimeline, { latestInvestmentOpinion } from '../components/InvestmentOpinionTimeline'
import SectorResearchPanel from '../components/SectorResearchPanel'
import InstrumentAssistantDrawer from '../components/InstrumentAssistantDrawer'
import type { ResearchReference } from '../lib/researchDossierApi'
import InstrumentRiskDrawer from '../components/InstrumentRiskDrawer'
import WorkspaceTools from '../../../../../packages/ui/src/WorkspaceTools'
import EstimateHistoryPanel from '../components/EstimateHistoryPanel'
import EtfProfilePanel from '../components/EtfProfilePanel'
import './listed-workspace.css'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import { LanguageSelector, useLanguage } from '../../../../../packages/ui/src/i18n'
import {
  emptyInstrumentResearchResponse,
  getInstrumentAttributes,
  getInstrumentPerformance,
  getInstrumentPriceBars,
  getInstrumentRisk,
  getInstrumentResearch,
  getInstrumentSummary,
  getInstrumentChart,
  getInstrumentTaxonomyTree,
  updateInstrumentSettings,
  type InstrumentChartResponse,
  type InstrumentPerformanceResponse,
  type InstrumentRiskResponse,
  type InstrumentSummaryResponse,
  type InstrumentTaxonomyTreeResponse,
  type InstrumentAttributeValuesResponse,
  type InstrumentResolveResponse,
  type InstrumentResearchResponse,
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
import { buildWatchlistPath, HOME_URL } from '../lib/navigation'
import {
  listedDetailTabs,
  type ListedDetailTab,
} from '../lib/instrumentDetailArchitecture'
import { buildMonthlyReturnMatrix } from '../lib/calendarReturns'
import {
  buildPerformanceMetricPeriodSnapshots,
  PERFORMANCE_METRIC_PERIODS,
  type PerformanceMetricSnapshot,
} from '../lib/performanceMetrics'

type WatchlistBreadcrumbContext = {
  watchlistId: string
  watchlistName: string
}

type Props = {
  instrument: InstrumentResolveResponse
  watchlistContext: WatchlistBreadcrumbContext | null
}

const RANGE_OPTIONS: PriceRange[] = ['1M', '3M', '6M', '1Y', 'ALL']
const MONTH_SHORT_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function instrumentTypeLabel(value: string) {
  if (value === 'etf') return 'ETF'
  if (value === 'equity') return 'Stock'
  if (value === 'index') return 'Index'
  if (value === 'crypto') return 'Crypto'
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

function metricTone(value: number | null) {
  if (value == null) return 'empty'
  if (value > 0) return 'positive'
  if (value < 0) return 'negative'
  return ''
}

function formatRecoveryValue(snapshot: PerformanceMetricSnapshot) {
  if (snapshot.maxDrawdown == null) return '—'
  if (snapshot.maxDrawdown === 0) return '0 d'
  if (snapshot.recoveryOpen) return 'Unrecovered'
  return snapshot.recoveryDays == null ? '—' : `${formatNumber(snapshot.recoveryDays, 0)} d`
}

function getHeatmapCellStyle(value: number | null, maxAbsValue: number) {
  if (value == null) return undefined
  const normalized = Math.min(Math.abs(value) / Math.max(maxAbsValue, 1), 1)
  if (value >= 0) {
    return {
      backgroundColor: `rgba(13, 122, 56, ${0.08 + normalized * 0.26})`,
      color: '#0d5e31',
    }
  }
  return {
    backgroundColor: `rgba(175, 0, 0, ${0.08 + normalized * 0.24})`,
    color: '#8f1d1d',
  }
}

type StandardizedMetric = {
  present: boolean
  value: number | null
}

function standardizedReturn(
  performance: InstrumentPerformanceResponse | null,
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
  risk: InstrumentRiskResponse | null,
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
        <span>Open Price {formatNumber(activeBar.open, 3)}</span>
        <span>High {formatNumber(activeBar.high, 3)}</span>
        <span>Low {formatNumber(activeBar.low, 3)}</span>
        <span>Close {formatNumber(activeBar.close, 3)}</span>
        <span>Volume {compactValue(activeBar.volume, 1)}</span>
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

function GrowthChart({ bars, kind = 'return' }: { bars: DisplayPriceBar[]; kind?: 'return' | 'drawdown' }) {
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
  let peak = base
  const values = bars.map((bar) => {
    peak = Math.max(peak, bar.close)
    return (bar.close / (kind === 'drawdown' ? peak : base) - 1) * 100
  })
  const minimum = Math.min(...values, 0)
  const maximum = Math.max(...values, 0)
  const span = Math.max(maximum - minimum, 0.01)
  const x = (index: number) => left + (index / Math.max(1, bars.length - 1)) * (width - left - right)
  const y = (value: number) => top + ((maximum - value) / span) * (bottom - top)
  const path = values.map((value, index) => `${index ? 'L' : 'M'} ${x(index)} ${y(value)}`).join(' ')
  return (
    <svg className="listed-growth-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={kind === 'drawdown' ? 'Historical drawdown chart' : 'Cumulative return chart'}>
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
      <path className={kind === 'drawdown' ? 'listed-drawdown-line' : 'listed-growth-line'} d={path} fill="none" />
      <text className="listed-chart-axis" x={left} y={278}>{formatDate(bars[0].date)}</text>
      <text className="listed-chart-axis" x={width - right} y={278} textAnchor="end">{formatDate(bars[bars.length - 1]?.date)}</text>
    </svg>
  )
}

function IndexLevelChart({ bars, crypto = false }: { bars: DisplayPriceBar[]; crypto?: boolean }) {
  if (bars.length < 2) {
    return <div className="listed-empty-chart">{crypto ? 'Not enough spot price history.' : 'Not enough index history for a level chart.'}</div>
  }
  const width = 1000
  const height = 340
  const left = 20
  const right = 74
  const top = 24
  const bottom = 296
  const values = bars.map((bar) => bar.close)
  const rawMinimum = Math.min(...values)
  const rawMaximum = Math.max(...values)
  const padding = Math.max((rawMaximum - rawMinimum) * 0.08, rawMaximum * 0.002)
  const minimum = rawMinimum - padding
  const maximum = rawMaximum + padding
  const span = Math.max(maximum - minimum, 0.000001)
  const x = (index: number) => left + (index / Math.max(1, bars.length - 1)) * (width - left - right)
  const y = (value: number) => top + ((maximum - value) / span) * (bottom - top)
  const linePath = values.map((value, index) => `${index ? 'L' : 'M'} ${x(index)} ${y(value)}`).join(' ')
  const areaPath = `${linePath} L ${x(bars.length - 1)} ${bottom} L ${x(0)} ${bottom} Z`

  return (
    <svg className="listed-index-level-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={crypto ? 'Crypto spot price chart' : 'Index level chart'}>
      <defs>
        <linearGradient id="listed-index-level-gradient" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#425d7a" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#425d7a" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
        const value = maximum - span * ratio
        const gridY = top + (bottom - top) * ratio
        return (
          <g key={ratio}>
            <line className="listed-chart-grid" x1={left} x2={width - right} y1={gridY} y2={gridY} />
            <text className="listed-chart-axis" x={width - right + 10} y={gridY + 4}>{compactValue(value, value < 10 ? 3 : 2)}</text>
          </g>
        )
      })}
      <path className="listed-index-level-area" d={areaPath} />
      <path className="listed-index-level-line" d={linePath} />
      <text className="listed-chart-axis" x={left} y={328}>{formatDate(bars[0].date)}</text>
      <text className="listed-chart-axis" x={width - right} y={328} textAnchor="end">{formatDate(bars[bars.length - 1]?.date)}</text>
    </svg>
  )
}

type ReferenceValueFormat = 'compact' | 'date' | 'integer' | 'percent_points' | 'ratio_percent'

function displayReferenceDate(value: unknown): string {
  const raw = String(value ?? '').trim()
  const compactDate = /^(\d{4})(\d{2})(\d{2})$/.exec(raw)
  if (compactDate) return `${compactDate[1]}-${compactDate[2]}-${compactDate[3]}`
  return formatDate(raw)
}

function displayObjectValue(value: unknown, format?: ReferenceValueFormat): string {
  if (value === null || value === undefined || value === '') return '—'
  if (format === 'date') return displayReferenceDate(value)
  const numericValue = typeof value === 'number' ? value : Number(value)
  if (format && Number.isFinite(numericValue)) {
    if (format === 'compact') return compactValue(numericValue, 3)
    if (format === 'integer') return String(Math.trunc(numericValue))
    if (format === 'percent_points') return formatPercent(numericValue, 3)
    return formatPercent(numericValue * 100, 2)
  }
  if (typeof value === 'number') return compactValue(value, 3)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (Array.isArray(value)) return value.map((item) => displayObjectValue(item)).join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

type DataTableColumn = {
  key: string
  label?: string
  format?: ReferenceValueFormat
}

function DataTable({
  title,
  rows,
  columns,
  emptyMessage,
}: {
  title: string
  rows: Array<Record<string, unknown>>
  columns?: readonly DataTableColumn[]
  emptyMessage?: string
}) {
  if (!rows.length) {
    return (
      <section className="panel listed-data-panel">
        <div className="panel-header"><div className="panel-title">{title}</div></div>
        <div className="instrument-placeholder">
          {emptyMessage || 'No source data is available for this instrument.'}
        </div>
      </section>
    )
  }
  const candidateColumns: readonly DataTableColumn[] = columns || Array.from(
    new Set(rows.flatMap((row) => Object.keys(row))),
  ).map((key) => ({ key }))
  const visibleColumns = candidateColumns
    .filter(({ key }) => rows.some((row) => row[key] !== null && row[key] !== undefined && row[key] !== ''))
    .slice(0, 8)
  if (!visibleColumns.length) {
    return (
      <section className="panel listed-data-panel">
        <div className="panel-header"><div className="panel-title">{title}</div></div>
        <div className="instrument-placeholder">
          {emptyMessage || 'The source returned no displayable values.'}
        </div>
      </section>
    )
  }
  return (
    <section className="panel listed-data-panel">
      <div className="panel-header"><div className="panel-title">{title}</div></div>
      <div className="table-shell">
        <table className="listed-data-table">
          <thead><tr>{visibleColumns.map((column) => <th key={column.key}>{column.label || formatLabel(column.key)}</th>)}</tr></thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>{visibleColumns.map((column) => <td key={column.key}>{displayObjectValue(row[column.key], column.format)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

const TRAILING_RETURN_COLUMNS = [
  { key: 'window' },
  { key: 'investment_nav', label: 'Investment Return', format: 'percent_points' },
  { key: 'category_nav', label: 'Peer Median', format: 'percent_points' },
  { key: 'anchor_date', label: 'Anchor', format: 'date' },
  { key: 'end_date', label: 'End', format: 'date' },
] as const

export default function ListedInstrumentDetailPage({ instrument, watchlistContext }: Props) {
  const canWriteTeam = useCanWriteTeam()
  const { language } = useLanguage()
  const instrumentId = instrument.detail_subject_id || instrument.canonical_instrument_id || instrument.requested_instrument_id
  const listedInstrumentType = instrument.instrument_type as 'etf' | 'equity' | 'index' | 'crypto'
  const [assistant, setAssistant] = useState<{ instrumentId: string; question: string; researchReference?: ResearchReference } | null>(null)
  const [riskInstrumentId, setRiskInstrumentId] = useState<string | null>(null)
  function openAssistant(question = '', researchReference?: ResearchReference) { setRiskInstrumentId(null); setAssistant({ instrumentId, question, researchReference }) }
  function openRisk() { setAssistant(null); setRiskInstrumentId(instrumentId) }
  const isIndex = listedInstrumentType === 'index'
  const isCrypto = listedInstrumentType === 'crypto'
  const usesCanonicalPriceSeries = isIndex || isCrypto
  const tabs = listedDetailTabs(listedInstrumentType)
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab')
  const tab: ListedDetailTab = requestedTab === 'risk' ? 'performance'
    : requestedTab === 'analyst' ? 'events'
    : tabs.includes(requestedTab as ListedDetailTab) ? requestedTab as ListedDetailTab : 'overview'
  function setTab(nextTab: ListedDetailTab) {
    const next = new URLSearchParams(searchParams)
    next.set('tab', nextTab)
    setSearchParams(next, { replace: true })
  }
  const [range, setRange] = useState<PriceRange>('6M')
  const [chartView, setChartView] = useState<'price' | 'return'>('price')
  const [mode, setMode] = useState<PriceAdjustmentMode>('raw')
  const [barsResponse, setBarsResponse] = useState<Awaited<ReturnType<typeof getInstrumentPriceBars>> | null>(null)
  const [summary, setSummary] = useState<InstrumentSummaryResponse | null>(null)
  const [chart, setChart] = useState<InstrumentChartResponse | null>(null)
  const [performance, setPerformance] = useState<InstrumentPerformanceResponse | null>(null)
  const [risk, setRisk] = useState<InstrumentRiskResponse | null>(null)
  const [research, setResearch] = useState<InstrumentResearchResponse>(() => emptyInstrumentResearchResponse())
  const [researchError, setResearchError] = useState<string | null>(null)
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
    setRiskInstrumentId(null)
    setAssistant(null)
    async function load() {
      setLoading(true)
      setError(null)
      setStandardizedError(null)
      setResearchError(null)
      const [barsResult, summaryResult, chartResult, performanceResult, riskResult, researchResult, attributesResult] = await Promise.allSettled([
        getInstrumentPriceBars(instrumentId, { limit: 1250 }),
        getInstrumentSummary(instrumentId),
        getInstrumentChart(instrumentId),
        getInstrumentPerformance(instrumentId),
        getInstrumentRisk(instrumentId),
        getInstrumentResearch(instrumentId),
        getInstrumentAttributes(instrumentId),
      ])
      if (cancelled) return
      if (barsResult.status === 'rejected') {
        setBarsResponse(null)
        if (!usesCanonicalPriceSeries) {
          setError(barsResult.reason instanceof Error ? barsResult.reason.message : 'Failed to load OHLCV history.')
        }
      } else {
        setBarsResponse(barsResult.value)
        const qfqReady =
          !usesCanonicalPriceSeries &&
          hasCompleteAdjustmentFactors(barsResult.value.bars)
        setMode(qfqReady ? 'qfq' : 'raw')
      }
      setSummary(summaryResult.status === 'fulfilled' ? summaryResult.value : null)
      setChart(chartResult.status === 'fulfilled' ? chartResult.value : null)
      if (usesCanonicalPriceSeries && chartResult.status === 'rejected') {
        setError(chartResult.reason instanceof Error ? chartResult.reason.message : 'Failed to load the canonical price series.')
      }
      setPerformance(performanceResult.status === 'fulfilled' ? performanceResult.value : null)
      setRisk(riskResult.status === 'fulfilled' ? riskResult.value : null)
      setResearch(
        researchResult.status === 'fulfilled'
          ? researchResult.value
          : emptyInstrumentResearchResponse(),
      )
      setResearchError(
        researchResult.status === 'rejected'
          ? researchResult.reason instanceof Error
            ? researchResult.reason.message
            : 'Investment research is unavailable.'
          : null,
      )
      setAttributeValues(attributesResult.status === 'fulfilled' ? attributesResult.value : null)
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
    !usesCanonicalPriceSeries &&
    hasCompleteAdjustmentFactors(barsResponse?.bars ?? [])
  const calculationSeries = useMemo(
    () => chart?.series[0]?.points ?? [],
    [chart],
  )
  const allBars = useMemo(
    () => adjustPriceBars(barsResponse?.bars ?? [], mode),
    [barsResponse, mode],
  )
  const canonicalCloseAnalysisBars = useMemo<DisplayPriceBar[]>(() => {
    return calculationSeries.flatMap((point) => {
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
  }, [barsResponse?.currency, calculationSeries, chart?.currency])
  const analysisBars = usesCanonicalPriceSeries || canonicalCloseAnalysisBars.length
    ? canonicalCloseAnalysisBars
    : allBars
  const visibleBars = useMemo(() => slicePriceBars(allBars, range), [allBars, range])
  const visibleAnalysisBars = useMemo(
    () => slicePriceBars(analysisBars, range),
    [analysisBars, range],
  )
  const performanceMetricPeriodSnapshots = useMemo(
    () => buildPerformanceMetricPeriodSnapshots(calculationSeries, {
      continuousDaily: isCrypto,
      pathRiskAvailable: risk?.data_quality?.status === 'ready',
    }),
    [calculationSeries, isCrypto, risk?.data_quality?.status],
  )
  const performanceMetricSnapshotByKey = useMemo(
    () => new Map(performanceMetricPeriodSnapshots.map((period) => [period.key, period.snapshot])),
    [performanceMetricPeriodSnapshots],
  )
  const monthlyReturnMatrixRows = useMemo(
    () => buildMonthlyReturnMatrix(calculationSeries),
    [calculationSeries],
  )
  const monthlyReturnMatrixMaxAbs = useMemo(
    () => monthlyReturnMatrixRows.reduce((maxAbs, row) => {
      const rowMax = Math.max(
        ...[...row.months, row.ytd]
          .filter((value): value is number => value != null)
          .map((value) => Math.abs(value)),
        0,
      )
      return Math.max(maxAbs, rowMax)
    }, 0),
    [monthlyReturnMatrixRows],
  )
  const returns = useMemo(() => priceReturnStats(analysisBars), [analysisBars])
  const quoteReturns = useMemo(() => priceReturnStats(allBars), [allBars])
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
    dailyChange: usesCanonicalPriceSeries ? returns.dailyChange : quoteReturns.dailyChange ?? returns.dailyChange,
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
  }), [usesCanonicalPriceSeries, quoteReturns.dailyChange, returns, standardizedReturns])
  const riskStats = useMemo(() => priceRiskStats(analysisBars), [analysisBars])
  const standardizedRisk = useMemo(
    () => ({
      annualizedVolatility: standardizedRiskMetric(risk, 'volatility'),
      maximumDrawdown: standardizedRiskMetric(risk, 'max_drawdown'),
    }),
    [risk],
  )
  const displayRiskStats = useMemo(() => ({
    ...riskStats,
    annualizedVolatility: standardizedRisk.annualizedVolatility.present
      ? standardizedRisk.annualizedVolatility.value
      : null,
    maximumDrawdown: standardizedRisk.maximumDrawdown.present
      ? standardizedRisk.maximumDrawdown.value
      : null,
    currentDrawdown:
      !risk || typeof risk.current_drawdown !== 'number'
        ? null
        : risk.current_drawdown,
    observationCount:
      risk?.calculation_frequency_profile?.observation_count ?? riskStats.observationCount,
  }), [risk, riskStats, standardizedRisk])
  const latest = usesCanonicalPriceSeries
    ? analysisBars[analysisBars.length - 1]
    : allBars[allBars.length - 1] || analysisBars[analysisBars.length - 1]
  const latestPriceBar = allBars[allBars.length - 1]
  const sourceRefreshFailed = ['failed', 'blocked'].includes(barsResponse?.source_refresh_status || '')
  const analysisBasisLabel = usesCanonicalPriceSeries || canonicalCloseAnalysisBars.length
    ? chart?.selected_series?.label || chart?.base_series_type || 'canonical return series'
    : mode === 'qfq' ? 'QFQ price' : 'raw price'
  const indexReturnKind = chart?.selected_series?.return_kind || summary?.series_snapshot?.return_kind || null
  const indexSemanticsLabel = isCrypto ? 'Spot Price' : indexReturnKind === 'total_return'
    ? 'Total Return Index'
    : indexReturnKind === 'price_return'
      ? 'Price Index'
      : 'Index Series'
  const indexChartDescription = isCrypto
    ? language === 'zh-Hans' ? '使用美元现货价格的已完成UTC日线，全年交易；不代表基金份额或完整交易所成交数据。' : 'Completed UTC daily spot prices in USD, trading seven days a week; these are native assets and do not imply fund units or complete exchange trading data.'
    : language === 'zh-Hans'
    ? `图表使用标准${indexReturnKind === 'total_return' ? '全收益' : '价格'}指数序列，不代表存在可交易的开高低收量行情。`
    : `This chart uses the canonical ${indexSemanticsLabel.toLowerCase()} series. It does not imply tradable OHLCV data.`
  const indexPerformanceDescription = isCrypto
    ? language === 'zh-Hans' ? '收益与风险基于已完成UTC日线，使用全年实际观察间距年化；比率使用零无风险利率。' : 'Returns and risk use completed UTC daily prices, annualized from actual observation spacing across the full year; ratios use a zero risk-free rate.'
    : !isIndex
    ? (language === 'zh-Hans' ? `收益与风险基于${analysisBasisLabel}，截至 ${formatDate(chart?.date_range?.end)}。` : `Returns and risk use ${analysisBasisLabel}, through ${formatDate(chart?.date_range?.end)}.`)
    : language === 'zh-Hans'
    ? `基于标准${indexReturnKind === 'total_return' ? '全收益' : '价格'}指数序列计算；风险调整比率使用零无风险利率。`
    : `Calculated from the canonical ${indexSemanticsLabel.toLowerCase()} series; ratios use a zero risk-free rate.`
  const riskAsOfNote = risk?.snapshot_metadata?.as_of_date
    ? `${language === 'zh-Hans' ? '截至' : 'As of'} ${formatDate(risk.snapshot_metadata.as_of_date)}`
    : language === 'zh-Hans' ? '风险统计暂不可用' : 'Risk statistics unavailable'
  const coverageStatusDefinition = attributeValues?.definitions.find(
    (definition) => definition.attribute_key === 'coverage_status',
  )
  const compatibleTaxonomyNodes = (taxonomyTree?.nodes || [])
    .filter((node) => node.instrument_type === instrument.instrument_type && node.is_leaf)
    .sort((left, right) =>
      left.path_labels.join(' / ').localeCompare(right.path_labels.join(' / '), 'zh-Hans-CN'),
    )
  const currentTaxonomyPath = attributeValues?.taxonomy.path_labels.join(' / ') || 'Unclassified'
  const displayedCoverageStatus = summary?.instrument_attributes.coverage_status
  const indexYtdSnapshot = performanceMetricSnapshotByKey.get('YTD')
  const indexOneYearSnapshot = performanceMetricSnapshotByKey.get('1Y')
  const buildIndexMetricCells = (
    selectValue: (snapshot: PerformanceMetricSnapshot) => number | null,
    formatValue: (value: number) => string,
    signed = false,
  ) => performanceMetricPeriodSnapshots.map(({ snapshot }) => {
    const value = selectValue(snapshot)
    return {
      primary: value == null ? '—' : formatValue(value),
      tone: signed ? metricTone(value) : value == null ? 'empty' : '',
    }
  })
  const indexPerformanceMetricRows = [
    {
      key: 'period_return',
      label: 'Period Return',
      cells: buildIndexMetricCells((snapshot) => snapshot.periodReturn, (value) => formatPercent(value), true),
    },
    {
      key: 'annualized_return',
      label: 'Ann. Return',
      cells: buildIndexMetricCells((snapshot) => snapshot.annualizedReturn, (value) => formatPercent(value), true),
    },
    {
      key: 'annualized_volatility',
      label: 'Ann. Volatility',
      cells: buildIndexMetricCells((snapshot) => snapshot.annualizedVolatility, (value) => formatPercent(value)),
    },
    {
      key: 'sharpe_ratio',
      label: 'Sharpe Ratio',
      cells: buildIndexMetricCells((snapshot) => snapshot.sharpe, (value) => formatNumber(value, 2), true),
    },
    {
      key: 'sortino_ratio',
      label: 'Sortino Ratio',
      cells: buildIndexMetricCells((snapshot) => snapshot.sortino, (value) => formatNumber(value, 2), true),
    },
    {
      key: 'calmar_ratio',
      label: 'Calmar Ratio',
      cells: buildIndexMetricCells((snapshot) => snapshot.calmar, (value) => formatNumber(value, 2), true),
    },
    {
      key: 'max_drawdown',
      label: 'Max DD',
      cells: buildIndexMetricCells((snapshot) => snapshot.maxDrawdown, (value) => formatPercent(value), true),
    },
    {
      key: 'recovery_days',
      label: 'Recovery Days',
      cells: performanceMetricPeriodSnapshots.map(({ snapshot }) => ({
        primary: formatRecoveryValue(snapshot),
        tone: snapshot.maxDrawdown == null ? 'empty' : '',
      })),
    },
  ]

  const latestOpinion = latestInvestmentOpinion(research)
  const zh = language === 'zh-Hans'

  if (loading) return <LoadingOverlay label="Loading market detail" />

  const chartPanel = (
    <section className="panel listed-chart-panel">
      <div className="listed-chart-toolbar">
        <div>
          <div className="panel-title">{chartView === 'return' ? (zh ? '累计收益' : 'Cumulative Return') : (zh ? '价格与成交量' : 'Price & Volume')}</div>
          <div className="listed-chart-caption">
            {chartView === 'return' ? analysisBasisLabel : mode === 'qfq' ? (zh ? '前复权价格' : 'Forward-adjusted (QFQ)') : (zh ? '交易所原始价格' : 'Raw exchange price')} · {chartView === 'return' ? visibleAnalysisBars.length : visibleBars.length} {zh ? '个观测值' : 'observations'}
          </div>
        </div>
        <div className="listed-chart-controls">
          {chartView === 'return' && <InfoHint label={zh ? '累计收益口径' : 'Return chart basis'} detail={zh ? `区间起点归零 · ${analysisBasisLabel}` : `Rebased to zero at the period start · ${analysisBasisLabel}`} />}
          <div className="listed-segmented-control" aria-label={zh ? '图表内容' : 'Chart content'}>
            <button type="button" className={chartView === 'price' ? 'active' : ''} onClick={() => setChartView('price')}>{zh ? '价格' : 'Price'}</button>
            <button type="button" className={chartView === 'return' ? 'active' : ''} disabled={!canonicalCloseAnalysisBars.length} onClick={() => setChartView('return')}>{zh ? '累计收益' : 'Cumulative Return'}</button>
          </div>
          {chartView === 'price' && <div className="listed-segmented-control" aria-label="Price adjustment">
            <button type="button" className={mode === 'qfq' ? 'active' : ''} disabled={!qfqAvailable} onClick={() => setMode('qfq')}>{zh ? '前复权' : 'QFQ'}</button>
            <button type="button" className={mode === 'raw' ? 'active' : ''} onClick={() => setMode('raw')}>{zh ? '不复权' : 'Raw'}</button>
          </div>}
          <div className="listed-segmented-control" aria-label="Date range">
            {RANGE_OPTIONS.map((option) => (
              <button type="button" key={option} className={range === option ? 'active' : ''} onClick={() => setRange(option)}>{option}</button>
            ))}
          </div>
        </div>
      </div>
      {chartView === 'return' ? <GrowthChart bars={visibleAnalysisBars} /> : error ? <div className="error-state">{error}</div> : <CandlestickChart bars={visibleBars} />}
      {chartView === 'price' && !qfqAvailable && Boolean(barsResponse?.count) ? <div className="listed-source-alert">{zh ? '复权因子不完整，当前仅提供原始价格。' : 'Adjustment factors are incomplete; raw price only.'}</div> : null}
      {sourceRefreshFailed ? (
        <div className="listed-source-alert">
          Source update failed; existing canonical history was preserved. {barsResponse?.source_refresh_message}
        </div>
      ) : null}
    </section>
  )

  const indexChartPanel = (
    <section className="panel listed-chart-panel">
      <div className="listed-chart-toolbar">
        <div>
          <div className="panel-title">{chartView === 'return' ? (zh ? '累计收益' : 'Cumulative Return') : isCrypto ? (zh ? '现货价格' : 'Spot Price') : 'Index Level'}</div>
          <div className="listed-chart-caption">
            {analysisBasisLabel} · {visibleAnalysisBars.length} observations
          </div>
        </div>
        <div className="listed-chart-controls">
          <InfoHint label={isCrypto ? (zh ? '现货图表口径' : 'Spot price chart basis') : zh ? '指数图表口径' : 'Index chart basis'} detail={indexChartDescription} />
          <div className="listed-segmented-control" aria-label={zh ? '图表内容' : 'Chart content'}>
            <button type="button" className={chartView === 'price' ? 'active' : ''} onClick={() => setChartView('price')}>{zh ? '价格' : 'Price'}</button>
            <button type="button" className={chartView === 'return' ? 'active' : ''} disabled={!canonicalCloseAnalysisBars.length} onClick={() => setChartView('return')}>{zh ? '累计收益' : 'Cumulative Return'}</button>
          </div>
          <div className="listed-segmented-control" aria-label="Date range">
            {RANGE_OPTIONS.map((option) => (
              <button type="button" key={option} className={range === option ? 'active' : ''} onClick={() => setRange(option)}>{option}</button>
            ))}
          </div>
        </div>
      </div>
      <div className="listed-chart-shell">
        {error ? <div className="error-state">{error}</div> : chartView === 'return' ? <GrowthChart bars={visibleAnalysisBars} /> : <IndexLevelChart bars={visibleAnalysisBars} crypto={isCrypto} />}
      </div>
    </section>
  )

  return (
    <div className="instrument-detail-page listed-detail-page">
      {assistant?.instrumentId === instrumentId && <InstrumentAssistantDrawer instrumentId={instrumentId} watchlistId={watchlistContext?.watchlistId} question={assistant.question} researchReference={assistant.researchReference} onClose={() => setAssistant(null)} />}
      {riskInstrumentId === instrumentId && <InstrumentRiskDrawer instrumentId={instrumentId} instrumentName={instrument.instrument_name} watchlistId={watchlistContext?.watchlistId} onClose={() => setRiskInstrumentId(null)} onAskAssistant={openAssistant} />}
      <div className="instrument-detail-topbar">
        <div className="instrument-detail-breadcrumbs">
          <a data-workspace-link href={HOME_URL} className="watchlist-breadcrumb-link">Home</a>
          <span className="watchlist-breadcrumb-separator">/</span>
          <Link to="/watchlists" className="watchlist-breadcrumb-link">Watchlist</Link>
          {watchlistContext ? (
            <>
              <span className="watchlist-breadcrumb-separator">/</span>
              <Link to={buildWatchlistPath(watchlistContext.watchlistId)} className="watchlist-breadcrumb-link">{watchlistContext.watchlistName}</Link>
            </>
          ) : null}
          <span className="watchlist-breadcrumb-separator">/</span>
          <span className="watchlist-breadcrumb-current" translate="no">{instrument.instrument_name}</span>
        </div>
        <LanguageSelector />
      </div>

      <section className="panel listed-detail-hero">
        <div className="listed-hero-heading">
          <div className="instrument-detail-eyebrow">{`${instrumentTypeLabel(instrument.instrument_type)} Detail`}</div>
          <h1 className="instrument-detail-title" translate="no">{instrument.instrument_name}</h1>
          <div className="instrument-detail-badges">
            {instrument.primary_identifier ? <span className="context-chip">{instrument.primary_identifier}</span> : null}
            <span className="context-chip">{(usesCanonicalPriceSeries ? chart?.currency : barsResponse?.currency) || latest?.currency || '—'}</span>
            {usesCanonicalPriceSeries ? <span className="context-chip">{indexSemanticsLabel}</span> : null}
            {sourceRefreshFailed ? <span className="context-chip listed-source-failed-chip">Source update failed</span> : null}
            {!isIndex && summary?.freshness.data_freshness_status ? <span className="context-chip">{formatLabel(summary.freshness.data_freshness_status)}</span> : null}
            {typeof displayedCoverageStatus === 'string' && displayedCoverageStatus ? (
              <span className="context-chip">{formatLabel(displayedCoverageStatus)}</span>
            ) : null}
            {summary?.taxonomy.path_labels.length ? (
              <span className="context-chip">{summary.taxonomy.path_labels.join(' / ')}</span>
            ) : null}
          </div>
        </div>
        <div className="listed-hero-side">
          <WorkspaceTools
            settings={{ label: zh ? '标的设置' : 'Instrument settings', disabled: !canWriteTeam, onClick: () => setSettingsOpen(true) }}
            risk={{ onClick: openRisk }}
            assistant={{ onClick: () => openAssistant() }}
          />
          <div className="listed-hero-quote">
          <span>{formatDate(latest?.date)}</span>
          <strong>{latest ? formatNumber(latest.close, latest.close < 10 ? 4 : 2) : '—'}</strong>
          <em className={signedValueClass(displayReturns.dailyChange)}>{percentValue(displayReturns.dailyChange)}</em>
          </div>
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
          {tabs.map((item) => (
            <button type="button" key={item}
              className={`instrument-detail-tab ${tab === item ? 'instrument-detail-tab-active' : ''}`}
              onClick={() => setTab(item)}>
              {{ overview: zh ? '总览' : 'Overview', research: zh ? '投资观点' : 'Investment Views', events: zh ? '研究追踪' : 'Research Tracking', performance: zh ? '业绩与风险' : 'Performance & Risk' }[item]}
            </button>
          ))}
        </div>
      </div>

      {tab === 'overview' ? (
        <div className="listed-tab-stack">
          {listedInstrumentType === 'etf' && <EtfProfilePanel key={instrumentId} instrumentId={instrumentId} language={language} />}
          <section className="listed-metric-grid listed-overview-quote">
            <MetricCard label="YTD" value={percentValue(usesCanonicalPriceSeries ? indexYtdSnapshot?.periodReturn ?? null : displayReturns.ytd)} tone={signedValueClass(displayReturns.ytd)} />
            <MetricCard label="1 Year" value={percentValue(usesCanonicalPriceSeries ? indexOneYearSnapshot?.periodReturn ?? null : displayReturns.oneYear)} tone={signedValueClass(displayReturns.oneYear)} />
            <MetricCard label={usesCanonicalPriceSeries ? '1 Month' : 'Volume'} value={usesCanonicalPriceSeries ? percentValue(displayReturns.oneMonth) : compactValue(latestPriceBar?.volume ?? null, 2)} />
          </section>
          {usesCanonicalPriceSeries ? indexChartPanel : chartPanel}
          <section className="panel listed-overview-brief">
            <div className="listed-overview-brief-row">
              <div><h3>{zh ? '最新投资观点' : 'Latest Investment View'}</h3>
                {researchError ? <p role="alert">{researchError}</p> : latestOpinion ? <><time>{formatDate(latestOpinion.noteDate)}</time><p>{latestOpinion.body || latestOpinion.title}</p></> : <p>{zh ? '还没有投资观点。新的判断会按时间保留。' : 'No investment view yet. New judgments are kept in a timeline.'}</p>}
              </div>
              <button type="button" onClick={() => setTab('research')}>{zh ? '查看观点' : 'View opinions'} →</button>
            </div>
            <div className="listed-overview-brief-row">
              <div><h3>{zh ? '当前回撤' : 'Current Drawdown'}</h3><p>{percentValue(displayRiskStats.currentDrawdown)} <span className="listed-chart-caption">{riskAsOfNote}</span></p></div>
              <button type="button" onClick={() => setTab('performance')}>{zh ? '业绩与风险' : 'Performance & Risk'} →</button>
            </div>
          </section>
          <SectorResearchPanel instrumentId={instrumentId} variant="summary" onOpenEvents={() => setTab('events')} />
        </div>
      ) : null}

      {tab === 'research' ? (
        <div className="listed-tab-stack listed-research-tab">
          {researchError ? (
            <div className="listed-source-alert" role="alert">
              Investment research unavailable: {researchError}
            </div>
          ) : null}
          <InvestmentOpinionTimeline onAskAssistant={openAssistant} instrumentId={instrumentId} research={research} language={language} onChange={setResearch} />
        </div>
      ) : null}

      {tab === 'performance' ? (
        <div className="listed-tab-stack">
          <div className="listed-chart-caption">{zh ? '历史统计区间' : 'Historical statistics'} · {formatDate(risk?.calculation_frequency_profile?.start_date)} — {formatDate(risk?.calculation_frequency_profile?.end_date)}</div>
          <section className="listed-metric-grid listed-risk-metrics">
            <MetricCard label={zh ? '年化收益' : 'Annualized Return'} value={percentValue(standardizedRiskMetric(risk, 'annualized_return').value)} />
            <MetricCard label="Annualized Volatility" value={percentValue(displayRiskStats.annualizedVolatility)} note={riskAsOfNote} />
            <MetricCard label="Maximum Drawdown" value={percentValue(displayRiskStats.maximumDrawdown)} tone={signedValueClass(displayRiskStats.maximumDrawdown)} note={riskAsOfNote} />
            <MetricCard label="Current Drawdown" value={percentValue(displayRiskStats.currentDrawdown)} tone={signedValueClass(displayRiskStats.currentDrawdown)} note={riskAsOfNote} />
            <MetricCard label="Sharpe Ratio" value={formatNumber(standardizedRiskMetric(risk, 'sharpe_ratio').value, 2)} />
            <MetricCard label="Calmar Ratio" value={formatNumber(standardizedRiskMetric(risk, 'calmar_ratio').value, 2)} />
          </section>
          <section className="panel instrument-performance-shell listed-index-performance-shell">
            <div className="instrument-price-topline" />
            {usesCanonicalPriceSeries ? <section className="instrument-performance-section instrument-performance-section-metrics">
              <div className="instrument-performance-section-header">
                <div className="instrument-performance-title-group">
                  <div className="panel-title">Performance</div>
                  <div className="instrument-section-title">Metrics Matrix</div>
                  <div className="listed-chart-caption">{indexPerformanceDescription}</div>
                </div>
              </div>
              <div className="instrument-performance-section-body">
                <div className="table-shell instrument-performance-table-shell">
                  <table className="terminal-table terminal-table-compact instrument-metrics-table">
                    <thead>
                      <tr>
                        <th>Metric</th>
                        {PERFORMANCE_METRIC_PERIODS.map((period) => <th key={period.key}>{period.label}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {indexPerformanceMetricRows.map((row) => (
                        <tr key={row.key} className="instrument-metrics-row-single">
                          <td className="instrument-metrics-row-label">{row.label}</td>
                          {row.cells.map((cell, index) => (
                            <td key={`${row.key}-${PERFORMANCE_METRIC_PERIODS[index]?.key || index}`}>
                              <div className={`instrument-metrics-cell${cell.tone ? ` instrument-metrics-cell-${cell.tone}` : ''}`}>
                                <strong>{cell.primary}</strong>
                              </div>
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section> : <DataTable title={zh ? '区间收益' : 'Period Returns'} rows={(performance?.trailing_returns ?? []).map(row => ({ ...row, window: ['3Y', '5Y'].includes(String(row.window)) ? `${row.window} ${zh ? '（年化）' : '(annualized)'}` : row.window }))} columns={TRAILING_RETURN_COLUMNS} />}

            <section className="instrument-performance-section instrument-performance-section-monthly">
              <div className="instrument-performance-section-header">
                <div>
                  <div className="panel-title">Performance</div>
                  <div className="instrument-section-title">Monthly Return Matrix</div>
                </div>
              </div>
              <div className="instrument-performance-section-body">
                {monthlyReturnMatrixRows.length ? (
                  <div className="table-shell instrument-performance-table-shell">
                    <table className="terminal-table terminal-table-compact instrument-heatmap-table">
                      <thead>
                        <tr>
                          <th>Year</th>
                          {MONTH_SHORT_LABELS.map((label) => <th key={label}>{label}</th>)}
                          <th>Yearly / YTD</th>
                        </tr>
                      </thead>
                      <tbody>
                        {monthlyReturnMatrixRows.map((row) => (
                          <tr key={row.year}>
                            <td className="instrument-heatmap-row-label">{row.year}</td>
                            {row.months.map((value, index) => (
                              <td
                                key={`${row.year}-${MONTH_SHORT_LABELS[index]}`}
                                className={`instrument-heatmap-cell${value == null ? ' instrument-heatmap-cell-empty' : ''}`}
                                style={getHeatmapCellStyle(value, monthlyReturnMatrixMaxAbs)}
                                title={row.monthWindows[index] ? `Anchor: ${row.monthWindows[index]?.anchorDate} · End: ${row.monthWindows[index]?.endDate}` : undefined}
                              >
                                {value == null ? '—' : formatPercent(value, 2)}
                              </td>
                            ))}
                            <td
                              className={`instrument-heatmap-cell${row.ytd == null ? ' instrument-heatmap-cell-empty' : ''}`}
                              style={getHeatmapCellStyle(row.ytd, monthlyReturnMatrixMaxAbs)}
                              title={row.ytdWindow ? `Anchor: ${row.ytdWindow.anchorDate} · End: ${row.ytdWindow.endDate}` : undefined}
                            >
                              {row.ytd == null ? '—' : formatPercent(row.ytd, 2)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="instrument-placeholder">
                    {zh ? '取得相邻月末数据后显示月度收益。' : 'Monthly returns appear when adjacent month-end observations are available.'}
                  </div>
                )}
              </div>
            </section>
          </section>
          <section className="panel listed-chart-panel">
            <div className="listed-chart-toolbar"><div><div className="panel-title">{zh ? '历史回撤' : 'Historical Drawdown'}</div><div className="listed-chart-caption">{analysisBasisLabel} · {zh ? '相对历史高点' : 'Below the prior peak'}</div></div></div>
            {risk?.data_quality?.gap_count ? <div className="listed-source-alert">{zh ? '历史行情存在缺口，曲线仅反映已取得的观测值。' : 'History has gaps; the curve reflects available observations only.'}</div> : null}
            <GrowthChart bars={canonicalCloseAnalysisBars} kind="drawdown" />
          </section>
          <InstrumentRiskPanel instrumentId={instrumentId} mode="price" onAskAssistant={(_id, question) => openAssistant(question)} />
        </div>
      ) : null}

      {tab === 'events' ? <div className="listed-tab-stack">
        <SectorResearchPanel instrumentId={instrumentId} onAskAssistant={openAssistant} />
        {listedInstrumentType === 'etf' ? <EstimateHistoryPanel instrumentId={instrumentId} language={language} /> : null}
      </div> : null}
    </div>
  )
}
