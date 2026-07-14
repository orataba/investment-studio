import { useMemo, useState, type MouseEvent } from 'react'

import { formatCurrency, formatPercent, formatSignedCurrency } from '../lib/format'

export type PerformanceNavChartPoint = {
  date: string
  value: number | null
}

export type PerformanceNavChartSummary = {
  start_date: string | null
  end_date: string | null
  cumulative_twr: number | null
  absolute_change: number | null
  current_drawdown: number | null
  max_drawdown: number | null
}

type PerformanceNavChartProps = {
  points: PerformanceNavChartPoint[]
  currency: string
  twrPoints?: PerformanceNavChartPoint[]
  drawdownPoints?: PerformanceNavChartPoint[]
  summary?: PerformanceNavChartSummary | null
  showRangeControls?: boolean
}

type ChartSeriesMode = 'portfolio_value' | 'twr_index'
type RangeKey = '1M' | '3M' | '6M' | 'YTD' | '1Y' | '3Y' | 'MAX'

type Coordinate = {
  date: string
  value: number
  x: number
  y: number
}

type SeriesGeometry = {
  coordinates: Coordinate[]
  linePaths: string[]
  areaPaths: string[]
  yMin: number
  yMax: number
  guideValues: number[]
}

const CHART_WIDTH = 960
const NAV_CHART_HEIGHT = 318
const DRAWDOWN_CHART_HEIGHT = 120
const NAV_PADDING = { top: 24, right: 18, bottom: 40, left: 58 }
const DRAWDOWN_PADDING = { top: 18, right: 18, bottom: 30, left: 58 }

const RANGE_OPTIONS: Array<{ key: RangeKey; label: string }> = [
  { key: '1M', label: '1M' },
  { key: '3M', label: '3M' },
  { key: '6M', label: '6M' },
  { key: 'YTD', label: 'YTD' },
  { key: '1Y', label: '1Y' },
  { key: '3Y', label: '3Y' },
  { key: 'MAX', label: 'MAX' },
]

function toDateMs(value: string) {
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value
  const time = Date.parse(normalized)
  return Number.isNaN(time) ? null : time
}

function normalizePoints(points: PerformanceNavChartPoint[]) {
  return points
    .filter((point) => point.date && toDateMs(point.date) != null)
    .map((point) => ({
      date: point.date,
      value: point.value != null && Number.isFinite(point.value) ? point.value : null,
    }))
    .sort((left, right) => left.date.localeCompare(right.date))
}

function shiftMonths(date: Date, months: number) {
  const next = new Date(date)
  next.setUTCMonth(next.getUTCMonth() + months)
  return next
}

function rangeStartTime(latestDate: string | undefined, rangeKey: RangeKey) {
  if (!latestDate || rangeKey === 'MAX') {
    return null
  }
  const latestTime = toDateMs(latestDate)
  if (latestTime == null) {
    return null
  }
  const latest = new Date(latestTime)
  if (rangeKey === 'YTD') {
    return Date.UTC(latest.getUTCFullYear(), 0, 1)
  }
  const offsets: Record<Exclude<RangeKey, 'YTD' | 'MAX'>, number> = {
    '1M': -1,
    '3M': -3,
    '6M': -6,
    '1Y': -12,
    '3Y': -36,
  }
  return shiftMonths(latest, offsets[rangeKey]).getTime()
}

function filterForRange(points: PerformanceNavChartPoint[], rangeKey: RangeKey) {
  if (!points.length) {
    return []
  }
  const startTime = rangeStartTime(points[points.length - 1]?.date, rangeKey)
  if (startTime == null) {
    return points
  }
  return points.filter((point) => {
    const pointTime = toDateMs(point.date)
    return pointTime != null && pointTime >= startTime
  })
}

function contiguousSegments(points: PerformanceNavChartPoint[]) {
  const segments: Array<Array<{ date: string; value: number }>> = []
  let current: Array<{ date: string; value: number }> = []
  points.forEach((point) => {
    if (point.value == null) {
      if (current.length) {
        segments.push(current)
        current = []
      }
      return
    }
    current.push({ date: point.date, value: point.value })
  })
  if (current.length) {
    segments.push(current)
  }
  return segments
}

function linePath(points: Coordinate[]) {
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(' ')
}

function buildSeriesGeometry(
  points: PerformanceNavChartPoint[],
  chartHeight: number,
  padding: typeof NAV_PADDING,
  includeZero = false,
): SeriesGeometry | null {
  const segments = contiguousSegments(points)
  const finitePoints = segments.flat()
  if (!finitePoints.length) {
    return null
  }

  const values = finitePoints.map((point) => point.value)
  const rawMin = Math.min(...values, ...(includeZero ? [0] : []))
  const rawMax = Math.max(...values, ...(includeZero ? [0] : []))
  const rawSpan = rawMax - rawMin
  const cushion = rawSpan > 0 ? rawSpan * 0.08 : Math.max(Math.abs(rawMax) * 0.02, 0.01)
  const yMin = includeZero ? Math.min(0, rawMin - cushion) : rawMin - cushion
  const yMax = includeZero ? Math.max(0, rawMax + cushion) : rawMax + cushion
  const startTime = toDateMs(points[0]?.date ?? '') ?? 0
  const endTime = toDateMs(points[points.length - 1]?.date ?? '') ?? startTime
  const timeSpan = Math.max(endTime - startTime, 1)
  const valueSpan = Math.max(yMax - yMin, Number.EPSILON)
  const drawableWidth = CHART_WIDTH - padding.left - padding.right
  const drawableHeight = chartHeight - padding.top - padding.bottom

  function project(point: { date: string; value: number }): Coordinate {
    const pointTime = toDateMs(point.date) ?? startTime
    return {
      ...point,
      x: padding.left + ((pointTime - startTime) / timeSpan) * drawableWidth,
      y: padding.top + drawableHeight - ((point.value - yMin) / valueSpan) * drawableHeight,
    }
  }

  const projectedSegments = segments.map((segment) => segment.map(project))
  const baselineY = chartHeight - padding.bottom
  return {
    coordinates: projectedSegments.flat(),
    linePaths: projectedSegments.map(linePath),
    areaPaths: projectedSegments
      .filter((segment) => segment.length > 1)
      .map((segment) => {
        const path = linePath(segment)
        return `${path} L ${segment[segment.length - 1].x.toFixed(2)} ${baselineY} L ${segment[0].x.toFixed(2)} ${baselineY} Z`
      }),
    yMin,
    yMax,
    guideValues: [yMax, yMin + valueSpan * 0.5, yMin],
  }
}

function coordinateForDateAtOrBefore(coordinates: Coordinate[], date: string) {
  let selected: Coordinate | null = null
  coordinates.forEach((coordinate) => {
    if (coordinate.date <= date) {
      selected = coordinate
    }
  })
  return selected
}

function valueAtOrBefore(points: PerformanceNavChartPoint[], date: string) {
  let selectedValue: number | null = null
  for (const point of points) {
    if (point.date <= date && point.value != null) {
      selectedValue = point.value
    }
  }
  return selectedValue
}

function toneClassName(value: number | null | undefined) {
  if (value == null || value === 0) {
    return ''
  }
  return value > 0 ? 'portfolio-nav-chart-change-positive' : 'portfolio-nav-chart-change-negative'
}

function signedPercent(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) {
    return '—'
  }
  if (value > 0) {
    return `+${formatPercent(value)}`
  }
  return formatPercent(value)
}

function seriesLabel(mode: ChartSeriesMode) {
  return mode === 'twr_index' ? 'TWR Index' : 'Portfolio Value'
}

function formatSeriesValue(value: number | null, mode: ChartSeriesMode, currency: string) {
  if (value == null) {
    return '—'
  }
  return mode === 'twr_index' ? value.toFixed(2) : formatCurrency(value, currency)
}

function formatAxisValue(value: number, mode: ChartSeriesMode, currency: string) {
  return mode === 'twr_index' ? value.toFixed(1) : formatCurrency(value, currency)
}

function dateTicks(points: PerformanceNavChartPoint[], count = 7) {
  if (points.length < 2) {
    return []
  }
  const startTime = toDateMs(points[0].date)
  const endTime = toDateMs(points[points.length - 1].date)
  if (startTime == null || endTime == null) {
    return []
  }
  const drawableWidth = CHART_WIDTH - NAV_PADDING.left - NAV_PADDING.right
  return Array.from({ length: count }, (_, index) => {
    const ratio = index / Math.max(count - 1, 1)
    const time = startTime + (endTime - startTime) * ratio
    const date = new Date(time)
    return {
      x: NAV_PADDING.left + drawableWidth * ratio,
      label: date.toLocaleDateString('en-US', { year: '2-digit', month: 'short' }),
    }
  })
}

export default function PerformanceNavChart({
  points,
  currency,
  twrPoints = [],
  drawdownPoints = [],
  summary = null,
  showRangeControls = false,
}: PerformanceNavChartProps) {
  const [seriesMode, setSeriesMode] = useState<ChartSeriesMode>('portfolio_value')
  const [rangeKey, setRangeKey] = useState<RangeKey>('MAX')
  const [hoveredDate, setHoveredDate] = useState<string | null>(null)
  const [showDrawdown, setShowDrawdown] = useState(true)

  const normalizedNavPoints = useMemo(() => normalizePoints(points), [points])
  const normalizedTwrPoints = useMemo(() => normalizePoints(twrPoints), [twrPoints])
  const normalizedDrawdownPoints = useMemo(() => normalizePoints(drawdownPoints), [drawdownPoints])
  const hasTwrSeries = normalizedTwrPoints.some((point) => point.value != null)
  const effectiveSeriesMode = seriesMode === 'twr_index' && hasTwrSeries ? 'twr_index' : 'portfolio_value'
  const sourcePoints = effectiveSeriesMode === 'twr_index' ? normalizedTwrPoints : normalizedNavPoints
  const visiblePoints = useMemo(() => filterForRange(sourcePoints, rangeKey), [rangeKey, sourcePoints])
  const visibleDrawdownPoints = useMemo(() => {
    const startDate = visiblePoints[0]?.date
    const endDate = visiblePoints[visiblePoints.length - 1]?.date
    if (!startDate || !endDate) {
      return []
    }
    return normalizedDrawdownPoints.filter((point) => point.date >= startDate && point.date <= endDate)
  }, [normalizedDrawdownPoints, visiblePoints])

  const chartGeometry = useMemo(
    () => buildSeriesGeometry(visiblePoints, NAV_CHART_HEIGHT, NAV_PADDING),
    [visiblePoints],
  )
  const drawdownGeometry = useMemo(
    () => buildSeriesGeometry(visibleDrawdownPoints, DRAWDOWN_CHART_HEIGHT, DRAWDOWN_PADDING, true),
    [visibleDrawdownPoints],
  )

  if (!chartGeometry || chartGeometry.coordinates.length < 2) {
    return <div className="price-chart-empty">Insufficient authoritative series data.</div>
  }

  const mainCoordinates = chartGeometry.coordinates
  const latestCoordinate = mainCoordinates[mainCoordinates.length - 1]
  const activeCoordinate = hoveredDate
    ? coordinateForDateAtOrBefore(mainCoordinates, hoveredDate) ?? latestCoordinate
    : latestCoordinate
  const activeDrawdown = valueAtOrBefore(visibleDrawdownPoints, activeCoordinate.date)
  const ticks = dateTicks(visiblePoints)
  const reportedPeriod =
    summary?.start_date && summary?.end_date ? `${summary.start_date} - ${summary.end_date}` : 'Unavailable'

  function handlePointerMove(event: MouseEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const chartX = bounds.width > 0 ? ((event.clientX - bounds.left) / bounds.width) * CHART_WIDTH : 0
    const selected = mainCoordinates.reduce((closest, coordinate) =>
      Math.abs(coordinate.x - chartX) < Math.abs(closest.x - chartX) ? coordinate : closest,
    )
    setHoveredDate(selected.date)
  }

  return (
    <section className="portfolio-nav-chart portfolio-nav-chart-overview">
      <div className="portfolio-nav-chart-series-head">
        <div className="portfolio-series-legend">
          <div className="portfolio-series-label">
            <strong>Portfolio</strong>
            <span>{seriesLabel(effectiveSeriesMode)}</span>
            <em>{formatSeriesValue(activeCoordinate.value, effectiveSeriesMode, currency)}</em>
            <em className={toneClassName(summary?.cumulative_twr)}>
              Reported TWR {signedPercent(summary?.cumulative_twr)}
            </em>
          </div>
        </div>
        <div className="portfolio-nav-chart-actions">
          <button
            type="button"
            className={effectiveSeriesMode === 'portfolio_value' ? 'portfolio-nav-option portfolio-nav-option-active' : 'portfolio-nav-option'}
            onClick={() => setSeriesMode('portfolio_value')}
          >
            Value
          </button>
          <button
            type="button"
            className={effectiveSeriesMode === 'twr_index' ? 'portfolio-nav-option portfolio-nav-option-active' : 'portfolio-nav-option'}
            disabled={!hasTwrSeries}
            onClick={() => setSeriesMode('twr_index')}
          >
            TWR
          </button>
          <button
            type="button"
            className={showDrawdown ? 'portfolio-nav-option portfolio-nav-option-active' : 'portfolio-nav-option'}
            disabled={!drawdownGeometry}
            onClick={() => setShowDrawdown((current) => !current)}
          >
            Drawdown
          </button>
        </div>
      </div>

      {showRangeControls ? (
        <div className="portfolio-nav-range-strip" role="group" aria-label="Portfolio chart display range">
          {RANGE_OPTIONS.map((option) => (
            <button
              type="button"
              className={`portfolio-nav-range-button ${rangeKey === option.key ? 'portfolio-nav-range-button-active' : ''}`}
              key={option.key}
              onClick={() => {
                setRangeKey(option.key)
                setHoveredDate(null)
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      ) : null}

      <div className="portfolio-nav-chart-plot">
        {hoveredDate ? (
          <div
            className={`portfolio-nav-chart-tooltip ${activeCoordinate.x > CHART_WIDTH * 0.72 ? 'portfolio-nav-chart-tooltip-left' : ''}`}
            style={{ left: `${(activeCoordinate.x / CHART_WIDTH) * 100}%` }}
          >
            <span>{activeCoordinate.date}</span>
            <strong>{formatSeriesValue(activeCoordinate.value, effectiveSeriesMode, currency)}</strong>
            <span>Authoritative DD {formatPercent(activeDrawdown)}</span>
          </div>
        ) : null}
        <svg
          className="portfolio-nav-chart-svg portfolio-nav-main-svg"
          viewBox={`0 0 ${CHART_WIDTH} ${NAV_CHART_HEIGHT}`}
          role="img"
          aria-label={`${seriesLabel(effectiveSeriesMode)} authoritative series`}
          onMouseMove={handlePointerMove}
          onMouseLeave={() => setHoveredDate(null)}
        >
          {chartGeometry.guideValues.map((guideValue) => {
            const ratio = (guideValue - chartGeometry.yMin) / Math.max(chartGeometry.yMax - chartGeometry.yMin, Number.EPSILON)
            const y = NAV_PADDING.top + (NAV_CHART_HEIGHT - NAV_PADDING.top - NAV_PADDING.bottom) * (1 - ratio)
            return (
              <g key={guideValue}>
                <line className="portfolio-nav-grid-line" x1={NAV_PADDING.left} x2={CHART_WIDTH - NAV_PADDING.right} y1={y} y2={y} />
                <text className="portfolio-nav-axis-label" x={NAV_PADDING.left - 8} y={y - 5} textAnchor="end">
                  {formatAxisValue(guideValue, effectiveSeriesMode, currency)}
                </text>
              </g>
            )
          })}
          {ticks.map((tick) => (
            <text key={`${tick.x}:${tick.label}`} className="portfolio-nav-x-axis-label" x={tick.x} y={NAV_CHART_HEIGHT - 10} textAnchor="middle">
              {tick.label}
            </text>
          ))}
          {chartGeometry.areaPaths.map((path, index) => <path key={`area:${index}`} className="portfolio-nav-area" d={path} />)}
          {chartGeometry.linePaths.map((path, index) => <path key={`line:${index}`} className="portfolio-nav-line" d={path} />)}
          <line className="portfolio-nav-guide-line" x1={activeCoordinate.x} x2={activeCoordinate.x} y1={NAV_PADDING.top} y2={NAV_CHART_HEIGHT - NAV_PADDING.bottom} />
          <circle className="portfolio-nav-point" cx={activeCoordinate.x} cy={activeCoordinate.y} r={4.5} />
        </svg>
      </div>

      {showDrawdown && drawdownGeometry ? (
        <div className="portfolio-nav-drawdown-shell">
          <div className="portfolio-nav-drawdown-header">
            <span>Drawdown</span>
            <strong className="portfolio-nav-chart-change-negative">{formatPercent(activeDrawdown)}</strong>
            <em>
              Backend series · Current {formatPercent(summary?.current_drawdown)} · Max {formatPercent(summary?.max_drawdown)}
            </em>
          </div>
          <svg
            className="portfolio-nav-chart-svg"
            viewBox={`0 0 ${CHART_WIDTH} ${DRAWDOWN_CHART_HEIGHT}`}
            role="img"
            aria-label="Authoritative portfolio drawdown"
            onMouseMove={handlePointerMove}
            onMouseLeave={() => setHoveredDate(null)}
          >
            {drawdownGeometry.linePaths.map((path, index) => <path key={`dd-line:${index}`} className="portfolio-nav-drawdown-line" d={path} />)}
          </svg>
        </div>
      ) : null}

      <div className="portfolio-nav-chart-stats" aria-label="Backend reported portfolio statistics">
        <div>
          <span>Reported TWR</span>
          <strong className={toneClassName(summary?.cumulative_twr)}>{signedPercent(summary?.cumulative_twr)}</strong>
        </div>
        <div>
          <span>Absolute Change</span>
          <strong className={toneClassName(summary?.absolute_change)}>
            {summary?.absolute_change == null ? '—' : formatSignedCurrency(summary.absolute_change, currency)}
          </strong>
        </div>
        <div>
          <span>Current DD</span>
          <strong className="portfolio-nav-chart-change-negative">{formatPercent(summary?.current_drawdown)}</strong>
        </div>
        <div>
          <span>Max DD</span>
          <strong className="portfolio-nav-chart-change-negative">{formatPercent(summary?.max_drawdown)}</strong>
        </div>
      </div>

      <div className="portfolio-nav-chart-footer">
        <div className="portfolio-nav-period-meta">
          <span>Backend reported period</span>
          <strong>{reportedPeriod}</strong>
        </div>
      </div>
    </section>
  )
}
