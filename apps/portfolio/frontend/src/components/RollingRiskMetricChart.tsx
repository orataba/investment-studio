import { useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from 'react'

export type RollingRiskMetricPoint = { date: string; value: number | null }
export type RiskChartDisplayStyle = 'mountain' | 'line' | 'dot'

type RollingRiskMetricChartProps = {
  title: string
  points: RollingRiskMetricPoint[]
  benchmarkPoints?: RollingRiskMetricPoint[]
  benchmarkLabel?: string | null
  displayStyle?: RiskChartDisplayStyle
  formatValue: (value: number | null | undefined) => string
  emptyLabel: string
}

type Coordinate = { date: string; value: number | null; x: number; y: number | null }
const DEFAULT_CHART_WIDTH = 640
const CHART_HEIGHT = 260
const PADDING = { top: 18, right: 70, bottom: 30, left: 12 }
const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value)

function normalizePoints(points: RollingRiskMetricPoint[]) {
  return points.filter((point) => point.date).map((point) => ({ ...point, value: finite(point.value) ? point.value : null }))
    .sort((left, right) => left.date.localeCompare(right.date))
}

function segments(points: Coordinate[]) {
  const result: Coordinate[][] = []
  let segment: Coordinate[] = []
  for (const point of points) {
    if (point.y == null) {
      if (segment.length) result.push(segment)
      segment = []
    } else segment.push(point)
  }
  if (segment.length) result.push(segment)
  return result
}

function linePath(points: Coordinate[]) {
  return points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y!.toFixed(2)}`).join(' ')
}

export default function RollingRiskMetricChart({
  title, points, benchmarkPoints = [], benchmarkLabel = null, displayStyle = 'mountain', formatValue, emptyLabel,
}: RollingRiskMetricChartProps) {
  const containerRef = useRef<HTMLElement | null>(null)
  const [chartWidth, setChartWidth] = useState(DEFAULT_CHART_WIDTH)
  const [hoveredDate, setHoveredDate] = useState<string | null>(null)
  useLayoutEffect(() => {
    const element = containerRef.current
    if (!element) return
    const measure = () => {
      const width = element.getBoundingClientRect().width
      if (width > 0) setChartWidth(Math.max(280, Math.round(width)))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const sortedPoints = useMemo(() => normalizePoints(points), [points])
  const sortedBenchmark = useMemo(() => normalizePoints(benchmarkPoints), [benchmarkPoints])
  const chart = useMemo(() => {
    const primaryStart = sortedPoints.find((point) => finite(point.value))?.date ?? sortedPoints[0]?.date
    const primaryEnd = sortedPoints[sortedPoints.length - 1]?.date
    const visibleBenchmark = benchmarkLabel ? sortedBenchmark.filter((point) =>
      (!primaryStart || point.date >= primaryStart) && (!primaryEnd || point.date <= primaryEnd),
    ) : []
    const primary = new Map(sortedPoints.map((point) => [point.date, point.value]))
    const benchmark = new Map(visibleBenchmark.map((point) => [point.date, point.value]))
    const all = [...sortedPoints, ...visibleBenchmark]
    const firstAvailable = all.filter((point) => finite(point.value)).map((point) => point.date).sort()[0]
    if (!firstAvailable) return null
    const dates = [...new Set(all.map((point) => point.date))].filter((date) => date >= firstAvailable).sort()
    const start = Date.parse(`${dates[0]}T00:00:00Z`)
    const end = Date.parse(`${dates[dates.length - 1]}T00:00:00Z`)
    const values = all.filter((point) => point.date >= firstAvailable).map((point) => point.value).filter(finite)
    const min = Math.min(0, ...values)
    const max = Math.max(0, ...values)
    const padding = Math.max((max - min) * 0.12, Math.abs(max || min || 1) * 0.04, 0.01)
    const yMin = min < 0 ? min - padding : 0
    const yMax = max > 0 || min === 0 ? max + padding : 0
    const x = (date: string) => PADDING.left + (end > start ? (Date.parse(`${date}T00:00:00Z`) - start) / (end - start) : 0.5) * (chartWidth - PADDING.left - PADDING.right)
    const y = (value: number) => PADDING.top + (1 - (value - yMin) / (yMax - yMin)) * (CHART_HEIGHT - PADDING.top - PADDING.bottom)
    const coordinates = (series: Map<string, number | null>): Coordinate[] => dates.map((date) => {
      const value = series.get(date) ?? null
      return { date, value, x: x(date), y: finite(value) ? y(value) : null }
    })
    const main = coordinates(primary)
    const comparison = coordinates(benchmark)
    return {
      dates, main, comparison, mainSegments: segments(main), comparisonSegments: segments(comparison),
      x, y, baseline: y(0), guideValues: [yMax, (yMin + yMax) / 2, yMin],
    }
  }, [sortedPoints, sortedBenchmark, chartWidth, benchmarkLabel])

  if (!chart) return <section ref={containerRef} className="rolling-risk-chart risk-chart-panel" aria-label={title}>
    <div className="rolling-risk-chart-head"><div className="portfolio-series-label"><strong>{title}</strong></div></div>
    <div className="risk-chart-empty">{emptyLabel}</div>
  </section>

  const activeDate = hoveredDate && chart.dates.includes(hoveredDate) ? hoveredDate : chart.dates[chart.dates.length - 1]
  const active = chart.main.find((point) => point.date === activeDate)!
  const comparison = chart.comparison.find((point) => point.date === activeDate)
  const hasBenchmark = Boolean(benchmarkLabel && chart.comparisonSegments.length)
  const last = chart.main[chart.main.length - 1]
  const hasPrimary = chart.mainSegments.length > 0
  const paths = chart.mainSegments.map(linePath)
  const areaPaths = chart.mainSegments.map((segment, index) => `${paths[index]} L ${segment[segment.length - 1].x.toFixed(2)} ${chart.baseline} L ${segment[0].x.toFixed(2)} ${chart.baseline} Z`)

  function handlePointerMove(event: MouseEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const pointer = bounds.width > 0 ? (event.clientX - bounds.left) / bounds.width * chartWidth : 0
    setHoveredDate(chart!.dates.reduce((nearest, date) => Math.abs(chart!.x(date) - pointer) < Math.abs(chart!.x(nearest) - pointer) ? date : nearest))
  }

  return <section ref={containerRef} className="rolling-risk-chart risk-chart-panel" aria-label={title}>
    <div className="rolling-risk-chart-head">
      <div className="portfolio-series-legend">
        <div className="portfolio-series-label"><strong>{title}</strong><em>{formatValue(active.value)}</em></div>
        {hasBenchmark ? <div className="portfolio-series-label portfolio-series-label-benchmark-row"><strong translate="no">{benchmarkLabel}</strong><em>{formatValue(comparison?.value)}</em></div> : null}
      </div>
      <span className="portfolio-detail-meta">{activeDate}</span>
    </div>
    {!hasPrimary ? <div className="portfolio-detail-meta">{emptyLabel}</div> : null}
    <div className="rolling-risk-chart-plot">
      {hoveredDate ? <div className={`portfolio-nav-chart-tooltip ${active.x > chartWidth * 0.72 ? 'portfolio-nav-chart-tooltip-left' : ''}`} style={{ left: `${active.x / chartWidth * 100}%` }}>
        <span>{activeDate}</span><strong>{formatValue(active.value)}</strong>
        {hasBenchmark ? <span><span translate="no">{benchmarkLabel}</span> {formatValue(comparison?.value)}</span> : null}
      </div> : null}
      <svg className="rolling-risk-chart-svg" viewBox={`0 0 ${chartWidth} ${CHART_HEIGHT}`} preserveAspectRatio="none" onMouseMove={handlePointerMove} onMouseLeave={() => setHoveredDate(null)} role="img" aria-label={title}>
        {chart.guideValues.map((value, index) => <g key={index}>
          <line x1={PADDING.left} x2={chartWidth - PADDING.right} y1={chart.y(value)} y2={chart.y(value)} className="rolling-risk-guide" />
          <text x={chartWidth - PADDING.right + 8} y={chart.y(value) + 4} className="rolling-risk-axis-label">{formatValue(value)}</text>
        </g>)}
        {displayStyle === 'mountain' ? <path d={areaPaths.join(' ')} className="rolling-risk-area" /> : null}
        {displayStyle !== 'dot' ? <path d={paths.join(' ')} className="rolling-risk-line" /> : null}
        {chart.mainSegments.flatMap((segment) => displayStyle === 'dot' || segment.length === 1 ? segment.map((point) => <circle key={point.date} cx={point.x} cy={point.y!} r="2.6" className="rolling-risk-point" />) : [])}
        {hasBenchmark ? <path d={chart.comparisonSegments.map(linePath).join(' ')} className="rolling-risk-line rolling-risk-line-benchmark" /> : null}
        {hasBenchmark ? chart.comparisonSegments.filter((segment) => segment.length === 1).map((segment) => <circle key={`benchmark:${segment[0].date}`} cx={segment[0].x} cy={segment[0].y!} r="2.6" className="rolling-risk-point rolling-risk-point-benchmark" />) : null}
        {last.y != null ? <circle cx={last.x} cy={last.y} r="4" className="rolling-risk-endpoint" /> : null}
        {hoveredDate ? <line x1={active.x} x2={active.x} y1={PADDING.top} y2={CHART_HEIGHT - PADDING.bottom} className="rolling-risk-guide-line" /> : null}
        {[...new Set([chart.dates[0], chart.dates[Math.floor((chart.dates.length - 1) / 2)], chart.dates[chart.dates.length - 1]])].map((date, index, labels) => <text key={date} x={chart.x(date)} y={CHART_HEIGHT - 9} textAnchor={labels.length === 1 ? 'middle' : index === 0 ? 'start' : index === labels.length - 1 ? 'end' : 'middle'} className="rolling-risk-axis-label rolling-risk-x-label">{date}</text>)}
      </svg>
    </div>
  </section>
}
