import InstrumentRiskPanel from '../components/InstrumentRiskPanel'
import { useEffect, useMemo, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { Link } from 'react-router'

import LoadingOverlay from '../components/LoadingOverlay'
import InvestmentResearchWorkspace from '../components/InvestmentResearchWorkspace'
import InstrumentResearchAttributes from '../components/InstrumentResearchAttributes'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import { LanguageSelector, useLanguage } from '../../../../../packages/ui/src/i18n'
import {
  emptyInstrumentResearchResponse,
  getInstrumentAttributes,
  getInstrumentPerformance,
  getInstrumentPriceBars,
  getInstrumentRisk,
  getInstrumentResearch,
  getInstrumentMonitoring,
  getInstrumentSummary,
  getInstrumentChart,
  getInstrumentTaxonomyTree,
  getInstrumentReferenceData,
  updateInstrumentSettings,
  type InstrumentChartResponse,
  type InstrumentPerformanceResponse,
  type InstrumentRiskResponse,
  type InstrumentSummaryResponse,
  type InstrumentTaxonomyTreeResponse,
  type InstrumentAttributeValuesResponse,
  type InstrumentResolveResponse,
  type InstrumentReferenceData,
  type InstrumentResearchResponse,
  type InstrumentMonitoringResponse,
} from '../lib/api'
import { formatDate, formatDateTime, formatLabel, formatNumber, formatPercent, signedValueClass } from '../lib/format'
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

function IndexLevelChart({ bars }: { bars: DisplayPriceBar[] }) {
  if (bars.length < 2) {
    return <div className="listed-empty-chart">Not enough index history for a level chart.</div>
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
    <svg className="listed-index-level-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Index level chart">
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

function referenceRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function referenceRows(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === 'object' && !Array.isArray(item),
      )
    : []
}

function referenceCoverageMessage(message: string) {
  const normalized = message.toLowerCase()
  if (
    normalized.includes('entitlement') ||
    normalized.includes('subscription') ||
    normalized.includes('plan') ||
    normalized.includes('403')
  ) {
    return 'Not included in the current provider entitlement.'
  }
  if (
    normalized.includes('not found') ||
    normalized.includes('no data') ||
    normalized.includes('empty') ||
    normalized.includes('404')
  ) {
    return 'The primary provider returned no data for this instrument.'
  }
  return 'The primary provider could not load this section. Canonical price history is unaffected.'
}

function ReferenceFacts({
  title,
  record,
  fields,
}: {
  title: string
  record: Record<string, unknown>
  fields: Array<{ key: string; label: string; format?: ReferenceValueFormat }>
}) {
  const visible = fields.filter(({ key }) => record[key] !== null && record[key] !== undefined && record[key] !== '')
  if (!visible.length) return null
  return (
    <section className="panel listed-data-panel">
      <div className="panel-header"><div className="panel-title">{title}</div></div>
      <div className="listed-key-value-grid">
        {visible.map(({ key, label, format }) => (
          <div key={key}><span>{label}</span><strong>{displayObjectValue(record[key], format)}</strong></div>
        ))}
      </div>
    </section>
  )
}

const EQUITY_PROFILE_FIELDS = [
  { key: 'companyName', label: 'Company' },
  { key: 'sector', label: 'Sector' },
  { key: 'industry', label: 'Industry' },
  { key: 'country', label: 'Country' },
  { key: 'exchange', label: 'Exchange' },
  { key: 'marketCap', label: 'Market Cap', format: 'compact' as const },
  { key: 'ipoDate', label: 'IPO Date', format: 'date' as const },
  { key: 'ceo', label: 'CEO' },
  { key: 'fullTimeEmployees', label: 'Employees', format: 'compact' as const },
  { key: 'website', label: 'Website' },
]

const FUND_INFO_FIELDS = [
  { key: 'name', label: 'Name' },
  { key: 'assetClass', label: 'Asset Class' },
  { key: 'fund_type', label: 'Fund Type' },
  { key: 'invest_type', label: 'Investment Type' },
  { key: 'etfCompany', label: 'Fund Company' },
  { key: 'management', label: 'Manager' },
  { key: 'custodian', label: 'Custodian' },
  { key: 'assetsUnderManagement', label: 'AUM', format: 'compact' as const },
  { key: 'expenseRatio', label: 'Expense Ratio', format: 'percent_points' as const },
  { key: 'm_fee', label: 'Management Fee', format: 'percent_points' as const },
  { key: 'c_fee', label: 'Custodian Fee', format: 'percent_points' as const },
  { key: 'holdingsCount', label: 'Holdings' },
  { key: 'inceptionDate', label: 'Inception', format: 'date' as const },
  { key: 'found_date', label: 'Inception', format: 'date' as const },
  { key: 'benchmark', label: 'Benchmark' },
  { key: 'avgVolume', label: 'Average Volume', format: 'compact' as const },
]

const INDEX_INFO_FIELDS = [
  { key: 'name', label: 'Name' },
  { key: 'fullname', label: 'Full Name' },
  { key: 'publisher', label: 'Publisher' },
  { key: 'market', label: 'Market' },
  { key: 'index_type', label: 'Index Type' },
  { key: 'category', label: 'Category' },
  { key: 'base_date', label: 'Base Date', format: 'date' as const },
  { key: 'base_point', label: 'Base Point', format: 'compact' as const },
  { key: 'list_date', label: 'Launch Date', format: 'date' as const },
  { key: 'weight_rule', label: 'Weighting Rule' },
]

const HOLDING_COLUMNS = [
  { key: 'asset', label: 'Symbol' },
  { key: 'symbol', label: 'Symbol' },
  { key: 'name', label: 'Name' },
  { key: 'weightPercentage', label: 'Weight', format: 'percent_points' },
  { key: 'stk_mkv_ratio', label: 'Weight', format: 'percent_points' },
  { key: 'sharesNumber', label: 'Shares', format: 'compact' },
  { key: 'amount', label: 'Shares', format: 'compact' },
  { key: 'marketValue', label: 'Market Value', format: 'compact' },
  { key: 'mkv', label: 'Market Value', format: 'compact' },
] as const

const INCOME_STATEMENT_COLUMNS = [
  { key: 'date' },
  { key: 'fiscalYear', label: 'Fiscal Year', format: 'integer' },
  { key: 'period' },
  { key: 'reportedCurrency', label: 'Currency' },
  { key: 'revenue', format: 'compact' },
  { key: 'grossProfit', label: 'Gross Profit', format: 'compact' },
  { key: 'operatingIncome', label: 'Operating Income', format: 'compact' },
  { key: 'netIncome', label: 'Net Income', format: 'compact' },
  { key: 'eps', label: 'EPS' },
] as const

const KEY_METRIC_COLUMNS = [
  { key: 'date' },
  { key: 'marketCap', label: 'Market Cap', format: 'compact' },
  { key: 'enterpriseValue', label: 'Enterprise Value', format: 'compact' },
  { key: 'evToSales', label: 'EV / Sales' },
  { key: 'evToOperatingCashFlow', label: 'EV / Operating CF' },
  { key: 'evToFreeCashFlow', label: 'EV / Free CF' },
  { key: 'earningsYield', label: 'Earnings Yield', format: 'ratio_percent' },
  { key: 'freeCashFlowYield', label: 'FCF Yield', format: 'ratio_percent' },
] as const

const RATIO_COLUMNS = [
  { key: 'date' },
  { key: 'priceToEarningsRatio', label: 'P / E' },
  { key: 'priceToBookRatio', label: 'P / B' },
  { key: 'priceToSalesRatio', label: 'P / Sales' },
  { key: 'grossProfitMargin', label: 'Gross Margin', format: 'ratio_percent' },
  { key: 'operatingProfitMargin', label: 'Operating Margin', format: 'ratio_percent' },
  { key: 'netProfitMargin', label: 'Net Margin', format: 'ratio_percent' },
  { key: 'returnOnEquity', label: 'ROE', format: 'ratio_percent' },
] as const

const DIVIDEND_COLUMNS = [
  { key: 'date', format: 'date' },
  { key: 'declarationDate', label: 'Declared', format: 'date' },
  { key: 'recordDate', label: 'Record Date', format: 'date' },
  { key: 'paymentDate', label: 'Payment Date', format: 'date' },
  { key: 'dividend' },
  { key: 'adjDividend', label: 'Adjusted Dividend' },
  { key: 'yield', format: 'percent_points' },
] as const

const TRAILING_RETURN_COLUMNS = [
  { key: 'window' },
  { key: 'investment_nav', label: 'Investment Return', format: 'percent_points' },
  { key: 'category_nav', label: 'Peer Median', format: 'percent_points' },
  { key: 'anchor_date', label: 'Anchor', format: 'date' },
  { key: 'end_date', label: 'End', format: 'date' },
] as const

const ANNUAL_RETURN_COLUMNS = [
  { key: 'year', format: 'integer' },
  { key: 'investment_nav', label: 'Investment Return', format: 'percent_points' },
  { key: 'category_nav', label: 'Peer Median', format: 'percent_points' },
  { key: 'anchor_date', label: 'Anchor', format: 'date' },
  { key: 'end_date', label: 'End', format: 'date' },
] as const

const SPLIT_COLUMNS = [
  { key: 'date', format: 'date' },
  { key: 'numerator' },
  { key: 'denominator' },
] as const

export default function ListedInstrumentDetailPage({ instrument, watchlistContext }: Props) {
  const { language } = useLanguage()
  const instrumentId = instrument.detail_subject_id || instrument.canonical_instrument_id || instrument.requested_instrument_id
  const listedInstrumentType = instrument.instrument_type as 'etf' | 'equity' | 'index'
  const isIndex = listedInstrumentType === 'index'
  const tabs = listedDetailTabs(listedInstrumentType)
  const [tab, setTab] = useState<ListedDetailTab>('overview')
  const [range, setRange] = useState<PriceRange>('6M')
  const [mode, setMode] = useState<PriceAdjustmentMode>('raw')
  const [barsResponse, setBarsResponse] = useState<Awaited<ReturnType<typeof getInstrumentPriceBars>> | null>(null)
  const [summary, setSummary] = useState<InstrumentSummaryResponse | null>(null)
  const [chart, setChart] = useState<InstrumentChartResponse | null>(null)
  const [performance, setPerformance] = useState<InstrumentPerformanceResponse | null>(null)
  const [risk, setRisk] = useState<InstrumentRiskResponse | null>(null)
  const [research, setResearch] = useState<InstrumentResearchResponse>(() => emptyInstrumentResearchResponse())
  const [researchError, setResearchError] = useState<string | null>(null)
  const [monitoring, setMonitoring] = useState<InstrumentMonitoringResponse | null>(null)
  const [monitoringError, setMonitoringError] = useState<string | null>(null)
  const [reference, setReference] = useState<InstrumentReferenceData | null>(null)
  const [referenceError, setReferenceError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [standardizedError, setStandardizedError] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [attributeValues, setAttributeValues] = useState<InstrumentAttributeValuesResponse | null>(null)
  const [attributeError, setAttributeError] = useState<string | null>(null)
  const [attributeRetryToken, setAttributeRetryToken] = useState(0)
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
      setResearchError(null)
      setMonitoringError(null)
      setAttributeError(null)
      const [barsResult, summaryResult, chartResult, performanceResult, riskResult, researchResult, monitoringResult, attributesResult] = await Promise.allSettled([
        getInstrumentPriceBars(instrumentId, { limit: 1250 }),
        getInstrumentSummary(instrumentId),
        getInstrumentChart(instrumentId),
        getInstrumentPerformance(instrumentId),
        getInstrumentRisk(instrumentId),
        getInstrumentResearch(instrumentId),
        getInstrumentMonitoring(instrumentId),
        getInstrumentAttributes(instrumentId),
      ])
      if (cancelled) return
      if (barsResult.status === 'rejected') {
        setBarsResponse(null)
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
      setMonitoring(monitoringResult.status === 'fulfilled' ? monitoringResult.value : null)
      setMonitoringError(
        monitoringResult.status === 'rejected'
          ? 'Monitoring is available after the instrument is added to a Watchlist.'
          : null,
      )
      setAttributeValues(attributesResult.status === 'fulfilled' ? attributesResult.value : null)
      setAttributeError(
        attributesResult.status === 'rejected'
          ? attributesResult.reason instanceof Error
            ? attributesResult.reason.message
            : 'Research fields are unavailable.'
          : null,
      )
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
  }, [attributeRetryToken, instrument.instrument_type, instrumentId])

  useEffect(() => {
    let cancelled = false
    setReference(null)
    setReferenceError(null)

    async function loadReference() {
      try {
        const nextReference = await getInstrumentReferenceData(instrumentId)
        if (!cancelled) setReference(nextReference)
      } catch (loadError) {
        if (!cancelled) {
          setReferenceError(
            loadError instanceof Error ? loadError.message : 'Reference data is unavailable.',
          )
        }
      }
    }

    void loadReference()
    return () => {
      cancelled = true
    }
  }, [instrumentId])

  useEffect(() => {
    if (!tabs.includes(tab)) setTab('overview')
  }, [tab, tabs])

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
  const analysisBars = canonicalCloseAnalysisBars.length
    ? canonicalCloseAnalysisBars
    : allBars
  const visibleBars = useMemo(() => slicePriceBars(allBars, range), [allBars, range])
  const visibleAnalysisBars = useMemo(
    () => slicePriceBars(analysisBars, range),
    [analysisBars, range],
  )
  const performanceMetricPeriodSnapshots = useMemo(
    () => buildPerformanceMetricPeriodSnapshots(calculationSeries),
    [calculationSeries],
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
    dailyChange: isIndex ? returns.dailyChange : quoteReturns.dailyChange ?? returns.dailyChange,
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
  }), [isIndex, quoteReturns.dailyChange, returns, standardizedReturns])
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
  const latest = isIndex
    ? analysisBars[analysisBars.length - 1]
    : allBars[allBars.length - 1] || analysisBars[analysisBars.length - 1]
  const latestPriceBar = allBars[allBars.length - 1]
  const visibleHigh = visibleBars.length ? Math.max(...visibleBars.map((bar) => bar.high)) : null
  const visibleLow = visibleBars.length ? Math.min(...visibleBars.map((bar) => bar.low)) : null
  const sourceRefreshFailed = ['failed', 'blocked'].includes(barsResponse?.source_refresh_status || '')
  const analysisBasisLabel = canonicalCloseAnalysisBars.length
    ? chart?.selected_series?.label || chart?.base_series_type || 'canonical return series'
    : mode === 'qfq' ? 'QFQ price' : 'raw price'
  const indexReturnKind = chart?.selected_series?.return_kind || summary?.series_snapshot?.return_kind || null
  const indexSemanticsLabel = indexReturnKind === 'total_return'
    ? 'Total Return Index'
    : indexReturnKind === 'price_return'
      ? 'Price Index'
      : 'Index Series'
  const indexChartDescription = language === 'zh-Hans'
    ? `图表使用标准${indexReturnKind === 'total_return' ? '全收益' : '价格'}指数序列，不代表存在可交易的开高低收量行情。`
    : `This chart uses the canonical ${indexSemanticsLabel.toLowerCase()} series. It does not imply tradable OHLCV data.`
  const indexPerformanceDescription = language === 'zh-Hans'
    ? `基于标准${indexReturnKind === 'total_return' ? '全收益' : '价格'}指数序列计算；风险调整比率使用零无风险利率。`
    : `Calculated from the canonical ${indexSemanticsLabel.toLowerCase()} series; ratios use a zero risk-free rate.`
  const performanceAsOfNote = performance?.snapshot_metadata?.as_of_date
    ? `Standardized · as of ${formatDate(performance.snapshot_metadata.as_of_date)}`
    : 'Standardized performance unavailable'
  const riskAsOfNote = risk?.snapshot_metadata?.as_of_date
    ? `${risk.data_quality?.gap_count ? 'Available observations' : 'Standardized'} · as of ${formatDate(risk.snapshot_metadata.as_of_date)}`
    : 'Standardized risk unavailable'
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
  const referenceSections = reference?.sections || {}
  const referenceProfile = referenceRecord(referenceSections.profile)
  const referenceFundInfo = referenceRecord(referenceSections.fund_info)
  const referenceIndexInfo = referenceRecord(referenceSections.index_info)
  const indexMtdSnapshot = performanceMetricSnapshotByKey.get('MTD')
  const indexYtdSnapshot = performanceMetricSnapshotByKey.get('YTD')
  const indexOneYearSnapshot = performanceMetricSnapshotByKey.get('1Y')
  const indexSinceInceptionSnapshot = performanceMetricSnapshotByKey.get('SI')
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

  const indexChartPanel = (
    <section className="panel listed-chart-panel">
      <div className="listed-chart-toolbar">
        <div>
          <div className="panel-title">Index Level</div>
          <div className="listed-chart-caption">
            {analysisBasisLabel} · {visibleAnalysisBars.length} observations
          </div>
        </div>
        <div className="listed-chart-controls">
          <div className="listed-segmented-control" aria-label="Date range">
            {RANGE_OPTIONS.map((option) => (
              <button type="button" key={option} className={range === option ? 'active' : ''} onClick={() => setRange(option)}>{option}</button>
            ))}
          </div>
        </div>
      </div>
      <div className="listed-chart-shell">
        <IndexLevelChart bars={visibleAnalysisBars} />
      </div>
      <div className="listed-source-note">
        {indexChartDescription}
      </div>
    </section>
  )

  return (
    <div className="instrument-detail-page listed-detail-page">
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
        <div className="instrument-detail-actions">
            <LanguageSelector />
          <button type="button" onClick={() => setSettingsOpen(true)}>Settings</button>
        </div>
      </div>

      <section className="panel listed-detail-hero">
        <div>
          <div className="instrument-detail-eyebrow">{`${instrumentTypeLabel(instrument.instrument_type)} Detail`}</div>
          <h1 className="instrument-detail-title" translate="no">{instrument.instrument_name}</h1>
          <div className="instrument-detail-badges">
            {instrument.primary_identifier ? <span className="context-chip">{instrument.primary_identifier}</span> : null}
            <span className="context-chip">{barsResponse?.currency || latest?.currency || '—'}</span>
            {isIndex ? (
              <>
                <span className="context-chip">{indexSemanticsLabel}</span>
                {barsResponse?.source_refresh_status ? (
                  <span className="context-chip">Source {formatLabel(barsResponse.source_refresh_status)}</span>
                ) : null}
              </>
            ) : (
              <>
                {barsResponse?.count ? <span className="context-chip">Raw OHLCV retained</span> : null}
                <span className="context-chip">
                  {qfqAvailable ? 'OHLCV QFQ available' : barsResponse?.count ? 'Raw OHLCV only' : 'OHLCV pending'}
                </span>
              </>
            )}
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
          {tabs.filter(value => ['overview', 'performance', 'research', 'risk'].includes(value)).map((item) => (
            <button
              type="button"
              key={item}
              className={`instrument-detail-tab ${tab === item ? 'instrument-detail-tab-active' : ''}`}
              onClick={() => setTab(item)}
            >
              {item === 'portfolio' ? 'Holdings' : formatLabel(item)}
            </button>
          ))}
        <button type="button" className={`instrument-detail-tab ${!['overview', 'performance', 'research', 'risk'].includes(tab) ? 'instrument-detail-tab-active' : ''}`} onClick={() => setTab('price')}>{language === 'zh-Hans' ? '资料与明细' : 'Details'}</button>
</div>
      </div>
        {!['overview', 'performance', 'research', 'risk'].includes(tab) && <div className="instrument-detail-tabs">{tabs.filter(value => !['overview', 'performance', 'research', 'risk'].includes(value)).map(item => <button key={item} className={`instrument-detail-tab ${item === tab ? 'instrument-detail-tab-active' : ''}`} onClick={() => setTab(item)}>{formatLabel(item)}</button>)}</div>}


      {tab === 'overview' ? (
        <div className="listed-tab-stack">
          {isIndex ? (
            <>
              <section className="listed-metric-grid">
                <MetricCard label="Index Level" value={latest ? formatNumber(latest.close, latest.close < 10 ? 4 : 2) : '—'} note={analysisBasisLabel} />
                <MetricCard label="Daily Change" value={percentValue(displayReturns.dailyChange)} tone={signedValueClass(displayReturns.dailyChange)} />
                <MetricCard label="MTD" value={percentValue(indexMtdSnapshot?.periodReturn ?? null)} tone={signedValueClass(indexMtdSnapshot?.periodReturn ?? null)} />
                <MetricCard label="YTD" value={percentValue(indexYtdSnapshot?.periodReturn ?? null)} tone={signedValueClass(indexYtdSnapshot?.periodReturn ?? null)} />
                <MetricCard label="1 Year" value={percentValue(indexOneYearSnapshot?.periodReturn ?? null)} tone={signedValueClass(indexOneYearSnapshot?.periodReturn ?? null)} />
                <MetricCard label="Ann. Volatility" value={percentValue(indexSinceInceptionSnapshot?.annualizedVolatility ?? null)} note="Since inception" />
              </section>
              {indexChartPanel}
            </>
          ) : (
            <>
              <section className="listed-metric-grid">
                <MetricCard label="Close" value={latest ? formatNumber(latest.close, latest.close < 10 ? 4 : 2) : '—'} note={analysisBasisLabel} />
                <MetricCard label="Daily Change" value={percentValue(displayReturns.dailyChange)} tone={signedValueClass(displayReturns.dailyChange)} />
                <MetricCard label={`${range} High`} value={visibleHigh === null ? '—' : formatNumber(visibleHigh, visibleHigh < 10 ? 4 : 2)} />
                <MetricCard label={`${range} Low`} value={visibleLow === null ? '—' : formatNumber(visibleLow, visibleLow < 10 ? 4 : 2)} />
                <MetricCard label="Volume" value={compactValue(latestPriceBar?.volume ?? null, 2)} note={latestPriceBar?.volumeUnit === 'lot' ? 'Trading lots' : latestPriceBar?.volumeUnit === 'shares' ? 'Shares traded' : latestPriceBar?.volumeUnit || undefined} />
                <MetricCard label="Turnover" value={compactValue(latestPriceBar?.turnover ?? null, 2)} note={latestPriceBar?.turnoverUnit === 'thousand_cny' ? 'Thousands of CNY' : latestPriceBar?.turnoverUnit || undefined} />
              </section>
              {chartPanel}
              <section className="listed-metric-grid listed-return-strip">
                <MetricCard label="1 Month" value={percentValue(displayReturns.oneMonth)} tone={signedValueClass(displayReturns.oneMonth)} note={performanceAsOfNote} />
                <MetricCard label="3 Months" value={percentValue(displayReturns.threeMonth)} tone={signedValueClass(displayReturns.threeMonth)} note={performanceAsOfNote} />
                <MetricCard label="YTD" value={percentValue(displayReturns.ytd)} tone={signedValueClass(displayReturns.ytd)} note={performanceAsOfNote} />
                <MetricCard label="1 Year" value={percentValue(displayReturns.oneYear)} tone={signedValueClass(displayReturns.oneYear)} note={performanceAsOfNote} />
              </section>
            </>
          )}
        </div>
      ) : null}

      {tab === 'research' ? (
        <div className="listed-tab-stack listed-research-tab">
          {researchError ? (
            <div className="listed-source-alert" role="alert">
              Investment research unavailable: {researchError}
            </div>
          ) : null}
          <InvestmentResearchWorkspace
            instrumentId={instrumentId}
            instrumentType={listedInstrumentType}
            research={research}
            language={language}
            defaultNoteDate={latest?.date || ''}
            onChange={setResearch}
          >
            <InstrumentResearchAttributes
              instrumentId={instrumentId}
              attributeValues={attributeValues}
              loadError={attributeError}
              language={language}
              onRetry={() => setAttributeRetryToken((current) => current + 1)}
              onChange={setAttributeValues}
            />
          </InvestmentResearchWorkspace>
        </div>
      ) : null}

      {tab === 'performance' ? (
        isIndex ? (
          <section className="panel instrument-performance-shell listed-index-performance-shell">
            <div className="instrument-price-topline" />
            <section className="instrument-performance-section instrument-performance-section-metrics">
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
            </section>

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
                    Monthly returns will appear once adjacent month-end index observations are available.
                  </div>
                )}
              </div>
            </section>
          </section>
        ) : (
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
            <DataTable title="Trailing Returns · Standardized Engine" rows={performance?.trailing_returns ?? []} columns={TRAILING_RETURN_COLUMNS} />
            <DataTable title="Annual Returns · Standardized Engine" rows={performance?.annual_returns ?? []} columns={ANNUAL_RETURN_COLUMNS} />
          </div>
        )
      ) : null}

      {tab === 'risk' ? (
        <div className="listed-tab-stack">
          <InstrumentRiskPanel instrumentId={instrumentId} />
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

      {tab === 'portfolio' ? (
        <div className="listed-tab-stack">
          <ReferenceFacts title="Fund Profile" record={referenceFundInfo} fields={FUND_INFO_FIELDS} />
          <DataTable
            title="Sector Allocation"
            rows={referenceRows(referenceSections.sector_weights)}
            columns={[
              { key: 'sector' },
              { key: 'weightPercentage', label: 'Weight', format: 'percent_points' },
            ]}
          />
          <DataTable
            title="Country Allocation"
            rows={referenceRows(referenceSections.country_weights)}
            columns={[
              { key: 'country' },
              { key: 'weightPercentage', label: 'Weight', format: 'percent_points' },
            ]}
          />
          <DataTable title="Latest Holdings" rows={referenceRows(referenceSections.holdings)} columns={HOLDING_COLUMNS} />
        </div>
      ) : null}

      {tab === 'fundamentals' ? (
        <div className="listed-tab-stack">
          <ReferenceFacts title="Company Profile" record={referenceProfile} fields={EQUITY_PROFILE_FIELDS} />
          <DataTable title="Income Statements · FMP" rows={referenceRows(referenceSections.financials)} columns={INCOME_STATEMENT_COLUMNS} />
          <DataTable title="Key Metrics · FMP" rows={referenceRows(referenceSections.key_metrics)} columns={KEY_METRIC_COLUMNS} />
          <DataTable title="Valuation & Financial Ratios · FMP" rows={referenceRows(referenceSections.ratios)} columns={RATIO_COLUMNS} />
        </div>
      ) : null}

      {tab === 'events' ? (
        <div className="listed-tab-stack">
          <DataTable title="Dividends" rows={referenceRows(referenceSections.dividends)} columns={DIVIDEND_COLUMNS} />
          <DataTable title="Share Splits" rows={referenceRows(referenceSections.splits)} columns={SPLIT_COLUMNS} />
        </div>
      ) : null}

      {tab === 'profile' ? (
        <div className="listed-tab-stack">
          <ReferenceFacts title="Index Profile" record={referenceIndexInfo} fields={INDEX_INFO_FIELDS} />
        </div>
      ) : null}

      {tab === 'monitoring' ? (
        <div className="listed-tab-stack">
          <InstrumentResearchAttributes
            instrumentId={instrumentId}
            attributeValues={attributeValues}
            loadError={attributeError}
            language={language}
            domains={['monitoring']}
            onRetry={() => setAttributeRetryToken((current) => current + 1)}
            onChange={setAttributeValues}
          />
          {monitoringError || !monitoring ? (
            <section className="panel listed-data-panel">
              <div className="panel-header"><div className="panel-title">Monitoring</div></div>
              <div className="instrument-placeholder">
                {monitoringError || 'Monitoring is not available for this instrument.'}
              </div>
            </section>
          ) : (
            <>
              <section className="listed-metric-grid">
                <MetricCard
                  label="Freshness"
                  value={formatLabel(monitoring.instrument.data_freshness_status)}
                  note={monitoring.instrument.staleness_reason || undefined}
                />
                <MetricCard
                  label="Latest Quote Date"
                  value={formatDate(monitoring.instrument.latest_quote_date)}
                />
                <MetricCard
                  label="Required Fields Missing"
                  value={String(monitoring.instrument.missing_attribute_count)}
                  note={monitoring.instrument.missing_attribute_labels.join(' / ') || 'Complete'}
                />
                <MetricCard
                  label="Research Records"
                  value={String(monitoring.instrument.research.active_note_count)}
                />
                <MetricCard
                  label="Next Review"
                  value={formatDate(monitoring.instrument.research.next_review_date)}
                />
                <MetricCard
                  label="Next Follow-up"
                  value={formatDate(monitoring.instrument.research.next_follow_up_date)}
                />
              </section>
              <section className="panel listed-data-panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-title">Investment Follow-up</div>
                    <div className="listed-chart-caption">
                      {monitoring.instrument.research.primary_analyst || 'No primary analyst assigned'}
                    </div>
                  </div>
                  <Link className="table-action" to="/monitoring">Open Monitoring Dashboard</Link>
                </div>
                <div className="listed-key-value-grid">
                  <div>
                    <span>Current View</span>
                    <strong>{monitoring.instrument.research.current_view || '—'}</strong>
                  </div>
                  <div>
                    <span>Research Rating</span>
                    <strong>
                      {monitoring.instrument.research.manual_rating == null
                        ? '—'
                        : `${'★'.repeat(monitoring.instrument.research.manual_rating)}${'☆'.repeat(
                            Math.max(0, 5 - monitoring.instrument.research.manual_rating),
                          )}`}
                    </strong>
                  </div>
                  <div>
                    <span>Research Updated</span>
                    <strong>{formatDateTime(monitoring.instrument.research.last_updated_at)}</strong>
                  </div>
                </div>
                {monitoring.instrument.issue_flags.length ? (
                  <div className="listed-source-alert">
                    {monitoring.instrument.issue_flags.map(formatLabel).join(' / ')}
                  </div>
                ) : (
                  <div className="listed-source-note">No open monitoring issues.</div>
                )}
              </section>
              {monitoring.open_recalc_jobs.length ? (
                <DataTable
                  title="Open Recalculation Jobs"
                  rows={monitoring.open_recalc_jobs.map((job) => ({
                    job_type: job.job_type,
                    job_status: job.job_status,
                    enqueued_at: job.enqueued_at,
                    error_message: job.error_message,
                  }))}
                  columns={[
                    { key: 'job_type', label: 'Job' },
                    { key: 'job_status', label: 'Status' },
                    { key: 'enqueued_at', label: 'Enqueued', format: 'date' },
                    { key: 'error_message', label: 'Error' },
                  ]}
                />
              ) : null}
            </>
          )}
          <section className="panel listed-data-panel">
            <div className="panel-header"><div className="panel-title">Market Data Status</div></div>
            <div className="listed-key-value-grid">
              <div><span>Series</span><strong>{analysisBasisLabel}</strong></div>
              {isIndex ? <div><span>Return Semantics</span><strong>{indexSemanticsLabel}</strong></div> : null}
              <div><span>History Start</span><strong>{formatDate(calculationSeries[0]?.date)}</strong></div>
              <div><span>Latest Observation</span><strong>{formatDate(latest?.date)}</strong></div>
              <div><span>Observations</span><strong>{formatNumber(calculationSeries.length, 0)}</strong></div>
              <div><span>Source Refresh</span><strong>{formatLabel(barsResponse?.source_refresh_status || 'unknown')}</strong></div>
              <div><span>Asset Data Updated</span><strong>{formatDateTime(summary?.freshness.last_fact_update_at)}</strong></div>
              <div><span>Provider</span><strong>{reference?.provider || '—'}</strong></div>
              <div><span>Provider Symbol</span><strong>{reference?.provider_symbol || '—'}</strong></div>
              <div><span>Source</span><strong>{reference?.source.source_location || '—'}</strong></div>
              <div><span>Frequency</span><strong>{reference?.source.expected_frequency || '—'}</strong></div>
              <div><span>Market Calendar</span><strong>{reference?.source.market_calendar || '—'}</strong></div>
              <div><span>Last Data Update</span><strong>{formatDateTime(reference?.source.market_data_updated_at)}</strong></div>
            </div>
            {barsResponse?.source_refresh_message ? (
              <div className="listed-source-note">
                {language === 'zh-Hans' ? '最近一次来源结果：' : 'Last source result: '}
                {barsResponse.source_refresh_message}
              </div>
            ) : null}
          </section>
          {referenceError ? (
            <div className="listed-source-alert" role="alert">
              Some provider reference sections are unavailable. Canonical market history is unaffected.
            </div>
          ) : null}
          {reference && Object.keys(reference.section_errors).length ? (
            <section className="panel listed-data-panel">
              <div className="panel-header"><div className="panel-title">Reference Data Coverage</div></div>
              <div className="listed-source-note">
                {Object.entries(reference.section_errors).map(([section, message]) => (
                  <div key={section}>
                    <strong>{formatLabel(section)}:</strong> {referenceCoverageMessage(message)}
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
