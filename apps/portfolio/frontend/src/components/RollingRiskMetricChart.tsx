import { useMemo, useState, type MouseEvent } from 'react'

export type RollingRiskMetricPoint = {
  date: string
  value: number
}

export type RiskChartDisplayStyle = 'mountain' | 'line' | 'dot'

type RollingRiskMetricChartProps = {
  title: string
  metricLabel: string
  windowLabel: string
  points: RollingRiskMetricPoint[]
  benchmarkPoints?: RollingRiskMetricPoint[]
  benchmarkLabel?: string | null
  displayStyle?: RiskChartDisplayStyle
  formatValue: (value: number | null | undefined) => string
  emptyLabel: string
}

const CHART_WIDTH = 960
const CHART_HEIGHT = 260
const PADDING = { top: 18, right: 70, bottom: 30, left: 12 }

function buildLinePath(points: Array<{ x: number; y: number }>) {
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(' ')
}

function normalizePoints(points: RollingRiskMetricPoint[]) {
  return points
    .filter((point) => point.date && Number.isFinite(point.value))
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
}

function filterPointsByDateWindow(points: RollingRiskMetricPoint[], startDate: string, endDate: string) {
  return points.filter((point) => point.date >= startDate && point.date <= endDate)
}

function buildChartCoordinates(
  points: RollingRiskMetricPoint[],
  yMin: number,
  yMax: number,
  startDate: string,
  endDate: string,
) {
  const drawableWidth = CHART_WIDTH - PADDING.left - PADDING.right
  const drawableHeight = CHART_HEIGHT - PADDING.top - PADDING.bottom
  const range = yMax - yMin || 1
  const startTime = Date.parse(`${startDate}T00:00:00`)
  const endTime = Date.parse(`${endDate}T00:00:00`)
  const timeRange =
    Number.isFinite(startTime) && Number.isFinite(endTime) && endTime > startTime ? endTime - startTime : null
  return points.map((point, index) => {
    const pointTime = Date.parse(`${point.date}T00:00:00`)
    const xRatio =
      timeRange != null && Number.isFinite(pointTime)
        ? (pointTime - startTime) / timeRange
        : index / Math.max(points.length - 1, 1)
    const x = PADDING.left + Math.min(1, Math.max(0, xRatio)) * drawableWidth
    const y = PADDING.top + drawableHeight - ((point.value - yMin) / range) * drawableHeight
    return { x, y }
  })
}

function yCoordinate(value: number, yMin: number, yMax: number) {
  const drawableHeight = CHART_HEIGHT - PADDING.top - PADDING.bottom
  return PADDING.top + drawableHeight - ((value - yMin) / (yMax - yMin || 1)) * drawableHeight
}

function dateLabel(point: RollingRiskMetricPoint | undefined) {
  return point?.date ?? '-'
}

export default function RollingRiskMetricChart({
  title,
  metricLabel,
  windowLabel,
  points,
  benchmarkPoints = [],
  benchmarkLabel = null,
  displayStyle = 'mountain',
  formatValue,
  emptyLabel,
}: RollingRiskMetricChartProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const sortedPoints = useMemo(() => normalizePoints(points), [points])
  const sortedBenchmarkPoints = useMemo(() => normalizePoints(benchmarkPoints), [benchmarkPoints])

  const chartState = useMemo(() => {
    if (sortedPoints.length < 2) {
      return null
    }

    const firstDate = sortedPoints[0].date
    const lastDate = sortedPoints[sortedPoints.length - 1].date
    const visibleBenchmarkPoints = filterPointsByDateWindow(sortedBenchmarkPoints, firstDate, lastDate)
    const allValues = [
      ...sortedPoints.map((point) => point.value),
      ...visibleBenchmarkPoints.map((point) => point.value),
      0,
    ].filter(Number.isFinite)
    const minValue = Math.min(...allValues)
    const maxValue = Math.max(...allValues)
    const padding = Math.max((maxValue - minValue) * 0.12, Math.abs(maxValue || minValue || 1) * 0.04, 0.01)
    const yMin = Math.min(0, minValue - padding)
    const yMax = maxValue + padding
    const coordinates = buildChartCoordinates(sortedPoints, yMin, yMax, firstDate, lastDate)
    const linePath = buildLinePath(coordinates)
    const baselineY = yCoordinate(0, yMin, yMax)
    const areaPath = `${linePath} L ${coordinates[coordinates.length - 1].x.toFixed(2)} ${baselineY} L ${coordinates[0].x.toFixed(2)} ${baselineY} Z`
    const benchmarkCoordinates =
      visibleBenchmarkPoints.length > 1
        ? buildChartCoordinates(visibleBenchmarkPoints, yMin, yMax, firstDate, lastDate)
        : []
    const benchmarkLinePath = benchmarkCoordinates.length > 1 ? buildLinePath(benchmarkCoordinates) : null
    const guideValues = [yMax, yMin + (yMax - yMin) / 2, yMin]

    return {
      visibleBenchmarkPoints,
      coordinates,
      linePath,
      areaPath,
      benchmarkCoordinates,
      benchmarkLinePath,
      guideValues,
      yMin,
      yMax,
    }
  }, [sortedBenchmarkPoints, sortedPoints])

  if (!chartState) {
    return <div className="price-chart-empty">{emptyLabel}</div>
  }

  const resolvedChartState = chartState
  const activeIndex = Math.min(hoveredIndex ?? sortedPoints.length - 1, sortedPoints.length - 1)
  const activePoint = sortedPoints[activeIndex]
  const activeCoordinate = resolvedChartState.coordinates[activeIndex]
  const activeBenchmarkPoint =
    resolvedChartState.visibleBenchmarkPoints.find((point) => point.date === activePoint.date) ??
    resolvedChartState.visibleBenchmarkPoints
      .slice()
      .reverse()
      .find((point) => point.date <= activePoint.date) ??
    null
  const firstPoint = sortedPoints[0]
  const middlePoint = sortedPoints[Math.floor((sortedPoints.length - 1) / 2)]
  const lastPoint = sortedPoints[sortedPoints.length - 1]
  const lastCoordinate = resolvedChartState.coordinates[resolvedChartState.coordinates.length - 1]
  const hasBenchmark = Boolean(benchmarkLabel && resolvedChartState.benchmarkLinePath)

  function handlePointerMove(event: MouseEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const chartX = bounds.width > 0 ? ((event.clientX - bounds.left) / bounds.width) * CHART_WIDTH : 0
    const nextIndex = resolvedChartState.coordinates.reduce((bestIndex, coordinate, index) => {
      const bestDistance = Math.abs(resolvedChartState.coordinates[bestIndex].x - chartX)
      const nextDistance = Math.abs(coordinate.x - chartX)
      return nextDistance < bestDistance ? index : bestIndex
    }, 0)
    setHoveredIndex(nextIndex)
  }

  return (
    <section className="rolling-risk-chart" aria-label={`${windowLabel} ${title}`}>
      <div className="rolling-risk-chart-head">
        <div className="portfolio-series-legend">
          <div className="portfolio-series-label">
            <strong>{title}</strong>
            <span>{metricLabel}</span>
            <em>{windowLabel}</em>
            <em>{formatValue(activePoint.value)}</em>
          </div>
          {hasBenchmark ? (
            <div className="portfolio-series-label portfolio-series-label-benchmark-row">
              <strong>{benchmarkLabel}</strong>
              <span>Benchmark</span>
              <em>{formatValue(activeBenchmarkPoint?.value)}</em>
            </div>
          ) : null}
        </div>
      </div>

      <div className="rolling-risk-chart-plot">
        {hoveredIndex != null ? (
          <div
            className={`portfolio-nav-chart-tooltip ${activeCoordinate.x > CHART_WIDTH * 0.72 ? 'portfolio-nav-chart-tooltip-left' : ''}`}
            style={{ left: `${(activeCoordinate.x / CHART_WIDTH) * 100}%` }}
          >
            <span>{activePoint.date}</span>
            <strong>{formatValue(activePoint.value)}</strong>
            {hasBenchmark ? <span>{benchmarkLabel} {formatValue(activeBenchmarkPoint?.value)}</span> : null}
          </div>
        ) : null}
        <svg
          className="rolling-risk-chart-svg"
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          preserveAspectRatio="none"
          onMouseMove={handlePointerMove}
          onMouseLeave={() => setHoveredIndex(null)}
        >
          {resolvedChartState.guideValues.map((value, index) => {
            const y =
              PADDING.top +
              (CHART_HEIGHT - PADDING.top - PADDING.bottom) -
              ((value - resolvedChartState.yMin) / (resolvedChartState.yMax - resolvedChartState.yMin || 1)) *
                (CHART_HEIGHT - PADDING.top - PADDING.bottom)
            return (
              <g key={`${value.toFixed(6)}:${index}`}>
                <line x1={PADDING.left} x2={CHART_WIDTH - PADDING.right} y1={y} y2={y} className="rolling-risk-guide" />
                <text x={CHART_WIDTH - PADDING.right + 8} y={y + 4} className="rolling-risk-axis-label">
                  {formatValue(value)}
                </text>
              </g>
            )
          })}
          {displayStyle === 'mountain' ? <path d={resolvedChartState.areaPath} className="rolling-risk-area" /> : null}
          {displayStyle !== 'dot' ? <path d={resolvedChartState.linePath} className="rolling-risk-line" /> : null}
          {displayStyle === 'dot'
            ? resolvedChartState.coordinates.map((coordinate, index) => (
                <circle
                  key={`${sortedPoints[index].date}:dot`}
                  cx={coordinate.x}
                  cy={coordinate.y}
                  r="2.6"
                  className="rolling-risk-point"
                />
              ))
            : null}
          {resolvedChartState.benchmarkLinePath ? (
            <path d={resolvedChartState.benchmarkLinePath} className="rolling-risk-line rolling-risk-line-benchmark" />
          ) : null}
          <circle cx={lastCoordinate.x} cy={lastCoordinate.y} r="4" className="rolling-risk-endpoint" />
          {hoveredIndex != null ? (
            <line
              x1={activeCoordinate.x}
              x2={activeCoordinate.x}
              y1={PADDING.top}
              y2={CHART_HEIGHT - PADDING.bottom}
              className="rolling-risk-guide-line"
            />
          ) : null}
          {[
            { point: firstPoint, anchor: 'start' as const, x: PADDING.left },
            { point: middlePoint, anchor: 'middle' as const, x: CHART_WIDTH / 2 },
            { point: lastPoint, anchor: 'end' as const, x: CHART_WIDTH - PADDING.right },
          ].map((label) => (
            <text
              key={`${dateLabel(label.point)}:${label.anchor}`}
              x={label.x}
              y={CHART_HEIGHT - 9}
              textAnchor={label.anchor}
              className="rolling-risk-axis-label rolling-risk-x-label"
            >
              {dateLabel(label.point)}
            </text>
          ))}
        </svg>
      </div>
    </section>
  )
}
