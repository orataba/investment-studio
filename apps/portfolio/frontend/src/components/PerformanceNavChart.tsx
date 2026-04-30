import { useMemo, useState, type CSSProperties, type MouseEvent } from 'react'

import { formatCurrency, formatPercent, formatSignedCurrency } from '../lib/format'

type PerformanceNavChartPoint = {
  date: string
  value: number
}

type PerformanceNavChartProps = {
  points: PerformanceNavChartPoint[]
  currency: string
  showRangeControls?: boolean
}

const CHART_WIDTH = 960
const NAV_CHART_HEIGHT = 248
const DRAWDOWN_CHART_HEIGHT = 92
const NAV_CHART_PADDING = { top: 18, right: 62, bottom: 22, left: 8 }
const DRAWDOWN_CHART_PADDING = { top: 10, right: 62, bottom: 18, left: 8 }

type RangeKey = '1M' | '3M' | '6M' | 'YTD' | '1Y' | '3Y' | 'MAX'

const RANGE_OPTIONS: Array<{ key: RangeKey; label: string }> = [
  { key: '1M', label: '1M' },
  { key: '3M', label: '3M' },
  { key: '6M', label: '6M' },
  { key: 'YTD', label: 'YTD' },
  { key: '1Y', label: '1Y' },
  { key: '3Y', label: '3Y' },
  { key: 'MAX', label: 'MAX' },
]

type Coordinate = {
  x: number
  y: number
}

function toDateMs(date: string) {
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T00:00:00` : date
  const time = Date.parse(normalized)
  return Number.isNaN(time) ? null : time
}

function addMonths(date: Date, months: number) {
  const nextDate = new Date(date)
  nextDate.setMonth(nextDate.getMonth() + months)
  return nextDate
}

function rangeStartDate(latestDate: string | undefined, rangeKey: RangeKey) {
  if (!latestDate) {
    return null
  }

  const latestTime = toDateMs(latestDate)
  if (latestTime == null || rangeKey === 'MAX') {
    return null
  }

  const latest = new Date(latestTime)

  if (rangeKey === 'YTD') {
    return new Date(latest.getFullYear(), 0, 1)
  }

  const monthOffsetByRange: Record<Exclude<RangeKey, 'YTD' | 'MAX'>, number> = {
    '1M': -1,
    '3M': -3,
    '6M': -6,
    '1Y': -12,
    '3Y': -36,
  }

  return addMonths(latest, monthOffsetByRange[rangeKey])
}

function getRangeStartIndex(points: PerformanceNavChartPoint[], rangeKey: RangeKey) {
  const targetDate = rangeStartDate(points[points.length - 1]?.date, rangeKey)
  if (!targetDate) {
    return 0
  }

  const targetTime = targetDate.getTime()
  const startIndex = points.findIndex((point) => {
    const pointTime = toDateMs(point.date)
    return pointTime != null && pointTime >= targetTime
  })
  return startIndex === -1 ? 0 : startIndex
}

function buildLinePath(coordinates: Coordinate[]) {
  return coordinates
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(' ')
}

function buildNavGeometry(points: PerformanceNavChartPoint[]) {
  const values = points.map((point) => point.value)
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const rawSpan = maxValue - minValue
  const cushion = rawSpan > 0 ? rawSpan * 0.08 : Math.max(Math.abs(maxValue) * 0.02, 1)
  const yMin = minValue - cushion
  const yMax = maxValue + cushion
  const span = yMax - yMin || 1
  const drawableWidth = CHART_WIDTH - NAV_CHART_PADDING.left - NAV_CHART_PADDING.right
  const drawableHeight = NAV_CHART_HEIGHT - NAV_CHART_PADDING.top - NAV_CHART_PADDING.bottom

  const coordinates = points.map((point, index) => {
    const x = NAV_CHART_PADDING.left + (index / (points.length - 1)) * drawableWidth
    const y =
      NAV_CHART_PADDING.top +
      drawableHeight -
      ((point.value - yMin) / span) * drawableHeight
    return { x, y }
  })

  const linePath = buildLinePath(coordinates)
  const baselineY = NAV_CHART_HEIGHT - NAV_CHART_PADDING.bottom
  const areaPath = `${linePath} L ${coordinates[coordinates.length - 1].x.toFixed(2)} ${baselineY} L ${coordinates[0].x.toFixed(2)} ${baselineY} Z`
  const guideValues = [yMax, yMin + span * 0.67, yMin + span * 0.33, yMin]

  return {
    coordinates,
    linePath,
    areaPath,
    guideValues,
    yMin,
    yMax,
  }
}

function buildDrawdownPoints(points: PerformanceNavChartPoint[]) {
  let runningHigh = points[0]?.value ?? 0
  return points.map((point) => {
    runningHigh = Math.max(runningHigh, point.value)
    const drawdown = runningHigh > 0 ? point.value / runningHigh - 1 : 0
    return { date: point.date, drawdown }
  })
}

function buildDrawdownGeometry(points: Array<{ date: string; drawdown: number }>) {
  const minDrawdown = Math.min(...points.map((point) => point.drawdown), 0)
  const yMin = minDrawdown < 0 ? minDrawdown : -0.01
  const drawableWidth = CHART_WIDTH - DRAWDOWN_CHART_PADDING.left - DRAWDOWN_CHART_PADDING.right
  const drawableHeight =
    DRAWDOWN_CHART_HEIGHT - DRAWDOWN_CHART_PADDING.top - DRAWDOWN_CHART_PADDING.bottom

  const coordinates = points.map((point, index) => {
    const x = DRAWDOWN_CHART_PADDING.left + (index / (points.length - 1)) * drawableWidth
    const y =
      DRAWDOWN_CHART_PADDING.top +
      ((0 - point.drawdown) / (0 - yMin)) * drawableHeight
    return { x, y }
  })

  const linePath = buildLinePath(coordinates)
  const zeroY = DRAWDOWN_CHART_PADDING.top
  const areaPath = `${linePath} L ${coordinates[coordinates.length - 1].x.toFixed(2)} ${zeroY} L ${coordinates[0].x.toFixed(2)} ${zeroY} Z`
  const guideValues = [0, yMin / 2, yMin]

  return {
    coordinates,
    linePath,
    areaPath,
    guideValues,
    yMin,
  }
}

export default function PerformanceNavChart({ points, currency, showRangeControls = true }: PerformanceNavChartProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [selectedRange, setSelectedRange] = useState<RangeKey | 'CUSTOM'>('MAX')
  const [windowStartIndex, setWindowStartIndex] = useState(0)
  const [windowEndIndex, setWindowEndIndex] = useState<number | null>(null)

  const sortedPoints = useMemo(
    () =>
      points
        .filter((point) => point.date && Number.isFinite(point.value))
        .slice()
        .sort((firstPoint, secondPoint) => firstPoint.date.localeCompare(secondPoint.date)),
    [points],
  )

  const maxIndex = Math.max(0, sortedPoints.length - 1)
  const rawEndIndex = windowEndIndex ?? maxIndex
  const clampedWindowEndIndex = Math.min(Math.max(rawEndIndex, 1), maxIndex)
  const clampedWindowStartIndex = Math.min(
    Math.max(windowStartIndex, 0),
    Math.max(0, clampedWindowEndIndex - 1),
  )
  const visiblePoints = sortedPoints.slice(clampedWindowStartIndex, clampedWindowEndIndex + 1)

  const chartState = useMemo(() => {
    if (visiblePoints.length < 2) {
      return null
    }

    const drawdownPoints = buildDrawdownPoints(visiblePoints)

    return {
      navGeometry: buildNavGeometry(visiblePoints),
      drawdownGeometry: buildDrawdownGeometry(drawdownPoints),
      drawdownPoints,
    }
  }, [visiblePoints])

  if (!chartState) {
    return <div className="price-chart-empty">Not enough NAV observations for a trend line.</div>
  }

  const activeIndex = Math.min(hoveredIndex ?? visiblePoints.length - 1, visiblePoints.length - 1)
  const activePoint = visiblePoints[activeIndex]
  const activeCoordinate = chartState.navGeometry.coordinates[activeIndex]
  const activeDrawdown = chartState.drawdownPoints[activeIndex]?.drawdown ?? null
  const firstPoint = visiblePoints[0]
  const lastPoint = visiblePoints[visiblePoints.length - 1]
  const changeValue = activePoint.value - firstPoint.value
  const changePct = firstPoint.value !== 0 ? changeValue / firstPoint.value : null
  const activeChangeClassName =
    changeValue > 0
      ? 'portfolio-nav-chart-change-positive'
      : changeValue < 0
        ? 'portfolio-nav-chart-change-negative'
        : ''

  const windowHigh = Math.max(...visiblePoints.map((point) => point.value))
  const windowLow = Math.min(...visiblePoints.map((point) => point.value))
  const maxDrawdown = Math.min(...chartState.drawdownPoints.map((point) => point.drawdown))
  const windowReturn = firstPoint.value !== 0 ? (lastPoint.value - firstPoint.value) / firstPoint.value : null
  const zoomStartPct = maxIndex > 0 ? (clampedWindowStartIndex / maxIndex) * 100 : 0
  const zoomEndPct = maxIndex > 0 ? (clampedWindowEndIndex / maxIndex) * 100 : 100
  const zoomSliderStyle = {
    '--portfolio-nav-zoom-start': `${zoomStartPct}%`,
    '--portfolio-nav-zoom-end': `${zoomEndPct}%`,
  } as CSSProperties
  const windowReturnClassName =
    windowReturn != null && windowReturn > 0
      ? 'portfolio-nav-chart-change-positive'
      : windowReturn != null && windowReturn < 0
        ? 'portfolio-nav-chart-change-negative'
        : ''

  function handleRangeSelect(rangeKey: RangeKey) {
    setSelectedRange(rangeKey)
    setHoveredIndex(null)
    setWindowStartIndex(getRangeStartIndex(sortedPoints, rangeKey))
    setWindowEndIndex(maxIndex)
  }

  function handleZoomStartChange(nextStartIndex: number) {
    setSelectedRange('CUSTOM')
    setHoveredIndex(null)
    setWindowStartIndex(Math.min(nextStartIndex, clampedWindowEndIndex - 1))
  }

  function handleZoomEndChange(nextEndIndex: number) {
    setSelectedRange('CUSTOM')
    setHoveredIndex(null)
    setWindowEndIndex(Math.max(nextEndIndex, clampedWindowStartIndex + 1))
  }

  function handlePointerMove(event: MouseEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const chartX = bounds.width > 0 ? ((event.clientX - bounds.left) / bounds.width) * CHART_WIDTH : 0
    const drawableWidth = CHART_WIDTH - NAV_CHART_PADDING.left - NAV_CHART_PADDING.right
    const ratio = drawableWidth > 0 ? (chartX - NAV_CHART_PADDING.left) / drawableWidth : 0
    const nextIndex = Math.min(
      visiblePoints.length - 1,
      Math.max(0, Math.round(ratio * (visiblePoints.length - 1))),
    )
    setHoveredIndex(nextIndex)
  }

  return (
    <section className="portfolio-nav-chart">
      <div className="portfolio-nav-chart-header">
        <div className="portfolio-nav-chart-readout">
          <strong>{formatCurrency(activePoint.value, currency)}</strong>
          <span className={activeChangeClassName}>
            {changeValue === 0 ? formatCurrency(changeValue, currency) : formatSignedCurrency(changeValue, currency)} ·{' '}
            {formatPercent(changePct)}
          </span>
          <span>{activePoint.date}</span>
        </div>
        <div className="portfolio-nav-chart-actions">
          <span className="portfolio-nav-chart-currency">{currency}</span>
          {showRangeControls ? (
            <div className="portfolio-nav-range-strip" role="group" aria-label="Portfolio NAV time range">
              {RANGE_OPTIONS.map((option) => (
                <button
                  type="button"
                  className={`portfolio-nav-range-button ${selectedRange === option.key ? 'portfolio-nav-range-button-active' : ''}`}
                  key={option.key}
                  onClick={() => handleRangeSelect(option.key)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="portfolio-nav-chart-plot">
        {hoveredIndex != null ? (
          <div
            className={`portfolio-nav-chart-tooltip ${activeCoordinate.x > CHART_WIDTH * 0.72 ? 'portfolio-nav-chart-tooltip-left' : ''}`}
            style={{ left: `${(activeCoordinate.x / CHART_WIDTH) * 100}%` }}
          >
            <span>{activePoint.date}</span>
            <strong>{formatCurrency(activePoint.value, currency)}</strong>
            <span>Period {formatPercent(changePct)}</span>
            <span>DD {formatPercent(activeDrawdown)}</span>
          </div>
        ) : null}
        <svg
          className="portfolio-nav-chart-svg portfolio-nav-main-svg"
          viewBox={`0 0 ${CHART_WIDTH} ${NAV_CHART_HEIGHT}`}
          role="img"
          aria-label="Portfolio NAV trend"
          onMouseMove={handlePointerMove}
          onMouseLeave={() => setHoveredIndex(null)}
        >
          {chartState.navGeometry.guideValues.map((guideValue, guideIndex) => {
            const guideRatio =
              chartState.navGeometry.yMax === chartState.navGeometry.yMin
                ? 0.5
                : (guideValue - chartState.navGeometry.yMin) /
                  (chartState.navGeometry.yMax - chartState.navGeometry.yMin)
            const y =
              NAV_CHART_PADDING.top +
              (NAV_CHART_HEIGHT - NAV_CHART_PADDING.top - NAV_CHART_PADDING.bottom) * (1 - guideRatio)
            return (
              <g key={`${guideIndex}:${guideValue}`}>
                <line
                  className="portfolio-nav-grid-line"
                  x1={NAV_CHART_PADDING.left}
                  x2={CHART_WIDTH - NAV_CHART_PADDING.right}
                  y1={y}
                  y2={y}
                />
                <text className="portfolio-nav-axis-label" x={CHART_WIDTH - 4} y={y - 6} textAnchor="end">
                  {formatCurrency(guideValue, currency)}
                </text>
              </g>
            )
          })}

          <path className="portfolio-nav-area" d={chartState.navGeometry.areaPath} />
          <path className="portfolio-nav-line" d={chartState.navGeometry.linePath} />
          <line
            className="portfolio-nav-guide-line"
            x1={activeCoordinate.x}
            x2={activeCoordinate.x}
            y1={NAV_CHART_PADDING.top}
            y2={NAV_CHART_HEIGHT - NAV_CHART_PADDING.bottom}
          />
          <circle className="portfolio-nav-point" cx={activeCoordinate.x} cy={activeCoordinate.y} r={4.5} />
        </svg>
      </div>

      <div className="portfolio-nav-drawdown-plot">
        <svg
          className="portfolio-nav-chart-svg"
          viewBox={`0 0 ${CHART_WIDTH} ${DRAWDOWN_CHART_HEIGHT}`}
          role="img"
          aria-label="Portfolio NAV drawdown"
          onMouseMove={handlePointerMove}
          onMouseLeave={() => setHoveredIndex(null)}
        >
          {chartState.drawdownGeometry.guideValues.map((guideValue, guideIndex) => {
            const y =
              DRAWDOWN_CHART_PADDING.top +
              ((0 - guideValue) / (0 - chartState.drawdownGeometry.yMin)) *
                (DRAWDOWN_CHART_HEIGHT - DRAWDOWN_CHART_PADDING.top - DRAWDOWN_CHART_PADDING.bottom)
            return (
              <g key={`${guideIndex}:${guideValue}`}>
                <line
                  className="portfolio-nav-grid-line"
                  x1={DRAWDOWN_CHART_PADDING.left}
                  x2={CHART_WIDTH - DRAWDOWN_CHART_PADDING.right}
                  y1={y}
                  y2={y}
                />
                <text className="portfolio-nav-axis-label" x={CHART_WIDTH - 4} y={y - 4} textAnchor="end">
                  {formatPercent(guideValue)}
                </text>
              </g>
            )
          })}
          <path className="portfolio-nav-drawdown-area" d={chartState.drawdownGeometry.areaPath} />
          <path className="portfolio-nav-drawdown-line" d={chartState.drawdownGeometry.linePath} />
          <line
            className="portfolio-nav-guide-line"
            x1={activeCoordinate.x}
            x2={activeCoordinate.x}
            y1={DRAWDOWN_CHART_PADDING.top}
            y2={DRAWDOWN_CHART_HEIGHT - DRAWDOWN_CHART_PADDING.bottom}
          />
        </svg>
      </div>

      <div className="portfolio-nav-chart-stats" aria-label="Portfolio NAV window statistics">
        <div>
          <span>Window Return</span>
          <strong className={windowReturnClassName}>{formatPercent(windowReturn)}</strong>
        </div>
        <div>
          <span>Max DD</span>
          <strong className="portfolio-nav-chart-change-negative">{formatPercent(maxDrawdown)}</strong>
        </div>
        <div>
          <span>High</span>
          <strong>{formatCurrency(windowHigh, currency)}</strong>
        </div>
        <div>
          <span>Low</span>
          <strong>{formatCurrency(windowLow, currency)}</strong>
        </div>
      </div>

      <div className="portfolio-nav-zoom">
        <span>{firstPoint.date}</span>
        <div className="portfolio-nav-zoom-slider" style={zoomSliderStyle}>
          <input
            type="range"
            min={0}
            max={maxIndex}
            value={clampedWindowStartIndex}
            aria-label="Portfolio NAV zoom start date"
            onChange={(event) => handleZoomStartChange(Number(event.currentTarget.value))}
          />
          <input
            type="range"
            min={0}
            max={maxIndex}
            value={clampedWindowEndIndex}
            aria-label="Portfolio NAV zoom end date"
            onChange={(event) => handleZoomEndChange(Number(event.currentTarget.value))}
          />
        </div>
        <span>{lastPoint.date}</span>
      </div>
    </section>
  )
}
