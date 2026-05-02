import { useMemo } from 'react'

import { formatPercent } from '../lib/format'

export type RollingVolatilityPoint = {
  date: string
  value: number
}

type RollingVolatilityChartProps = {
  points: RollingVolatilityPoint[]
  windowDays: number
}

const CHART_WIDTH = 960
const CHART_HEIGHT = 260
const PADDING = { top: 18, right: 66, bottom: 28, left: 12 }

function buildLinePath(points: Array<{ x: number; y: number }>) {
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(' ')
}

export default function RollingVolatilityChart({ points, windowDays }: RollingVolatilityChartProps) {
  const sortedPoints = useMemo(
    () =>
      points
        .filter((point) => point.date && Number.isFinite(point.value))
        .slice()
        .sort((left, right) => left.date.localeCompare(right.date)),
    [points],
  )

  const chartState = useMemo(() => {
    if (sortedPoints.length < 2) {
      return null
    }

    const maxValue = Math.max(...sortedPoints.map((point) => point.value), 0.01)
    const yMax = maxValue * 1.12
    const drawableWidth = CHART_WIDTH - PADDING.left - PADDING.right
    const drawableHeight = CHART_HEIGHT - PADDING.top - PADDING.bottom
    const coordinates = sortedPoints.map((point, index) => {
      const x = PADDING.left + (index / (sortedPoints.length - 1)) * drawableWidth
      const y = PADDING.top + drawableHeight - (point.value / yMax) * drawableHeight
      return { x, y }
    })
    const linePath = buildLinePath(coordinates)
    const baselineY = CHART_HEIGHT - PADDING.bottom
    const areaPath = `${linePath} L ${coordinates[coordinates.length - 1].x.toFixed(2)} ${baselineY} L ${coordinates[0].x.toFixed(2)} ${baselineY} Z`
    const guideValues = [yMax, yMax * 0.5, 0]

    return { coordinates, linePath, areaPath, guideValues, yMax }
  }, [sortedPoints])

  if (!chartState) {
    return <div className="price-chart-empty">Not enough return observations for a rolling volatility curve.</div>
  }

  const firstPoint = sortedPoints[0]
  const middlePoint = sortedPoints[Math.floor((sortedPoints.length - 1) / 2)]
  const lastPoint = sortedPoints[sortedPoints.length - 1]
  const lastCoordinate = chartState.coordinates[chartState.coordinates.length - 1]

  return (
    <div className="rolling-vol-chart" role="img" aria-label={`${windowDays} observation rolling annualized volatility`}>
      <div className="rolling-vol-chart-summary">
        <span>{windowDays} obs window</span>
        <strong>{formatPercent(lastPoint.value)}</strong>
        <span>{lastPoint.date}</span>
      </div>
      <svg className="rolling-vol-chart-svg" viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="rolling-vol-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#0f4c81" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#0f4c81" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {chartState.guideValues.map((value) => {
          const y =
            PADDING.top +
            (CHART_HEIGHT - PADDING.top - PADDING.bottom) -
            (value / chartState.yMax) * (CHART_HEIGHT - PADDING.top - PADDING.bottom)
          return (
            <g key={value.toFixed(6)}>
              <line x1={PADDING.left} x2={CHART_WIDTH - PADDING.right} y1={y} y2={y} className="rolling-vol-guide" />
              <text x={CHART_WIDTH - PADDING.right + 8} y={y + 4} className="rolling-vol-axis-label">
                {formatPercent(value)}
              </text>
            </g>
          )
        })}
        <path d={chartState.areaPath} className="rolling-vol-area" />
        <path d={chartState.linePath} className="rolling-vol-line" />
        <circle cx={lastCoordinate.x} cy={lastCoordinate.y} r="4" className="rolling-vol-endpoint" />
        {[
          { point: firstPoint, anchor: 'start' as const, x: PADDING.left },
          { point: middlePoint, anchor: 'middle' as const, x: CHART_WIDTH / 2 },
          { point: lastPoint, anchor: 'end' as const, x: CHART_WIDTH - PADDING.right },
        ].map((label) => (
          <text
            key={`${label.point.date}:${label.anchor}`}
            x={label.x}
            y={CHART_HEIGHT - 8}
            textAnchor={label.anchor}
            className="rolling-vol-axis-label"
          >
            {label.point.date}
          </text>
        ))}
      </svg>
    </div>
  )
}
