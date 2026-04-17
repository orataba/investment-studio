import { useMemo, useState, type MouseEvent } from 'react'

import { formatCurrency, formatPercent, formatSignedCurrency } from '../lib/format'

type PerformanceNavChartPoint = {
  date: string
  value: number
}

type PerformanceNavChartProps = {
  points: PerformanceNavChartPoint[]
  currency: string
}

const CHART_WIDTH = 960
const CHART_HEIGHT = 280
const CHART_PADDING = { top: 16, right: 8, bottom: 28, left: 8 }

export default function PerformanceNavChart({ points, currency }: PerformanceNavChartProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)

  const geometry = useMemo(() => {
    if (points.length < 2) {
      return null
    }

    const values = points.map((point) => point.value)
    const minValue = Math.min(...values)
    const maxValue = Math.max(...values)
    const span = maxValue - minValue || Math.max(Math.abs(maxValue) * 0.02, 1)
    const drawableWidth = CHART_WIDTH - CHART_PADDING.left - CHART_PADDING.right
    const drawableHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom

    const coordinates = points.map((point, index) => {
      const x = CHART_PADDING.left + (index / (points.length - 1)) * drawableWidth
      const y =
        CHART_PADDING.top +
        drawableHeight -
        ((point.value - minValue) / span) * drawableHeight
      return { x, y }
    })

    const linePath = coordinates.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ')
    const areaPath = `${linePath} L ${coordinates[coordinates.length - 1].x} ${CHART_HEIGHT - CHART_PADDING.bottom} L ${coordinates[0].x} ${CHART_HEIGHT - CHART_PADDING.bottom} Z`

    const guideValues = [maxValue, minValue + span / 2, minValue]

    return {
      coordinates,
      linePath,
      areaPath,
      guideValues,
      minValue,
      maxValue,
    }
  }, [points])

  if (!geometry) {
    return <div className="price-chart-empty">Not enough NAV observations for a trend line.</div>
  }

  const activeIndex = hoveredIndex ?? points.length - 1
  const activePoint = points[activeIndex]
  const activeCoordinate = geometry.coordinates[activeIndex]
  const firstPoint = points[0]
  const changeValue = activePoint.value - firstPoint.value
  const changePct = firstPoint.value !== 0 ? changeValue / firstPoint.value : null

  function handlePointerMove(event: MouseEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const relativeX = event.clientX - bounds.left
    const ratio = bounds.width > 0 ? relativeX / bounds.width : 0
    const nextIndex = Math.min(
      points.length - 1,
      Math.max(0, Math.round(ratio * (points.length - 1))),
    )
    setHoveredIndex(nextIndex)
  }

  return (
    <section className="asset-price-chart">
      <div className="asset-price-chart-toolbar">
        <div className="price-chart-readout">
          <strong>{formatCurrency(activePoint.value, currency)}</strong>
          <span>
            {changeValue === 0 ? formatCurrency(changeValue, currency) : formatSignedCurrency(changeValue, currency)} ·{' '}
            {formatPercent(changePct)}
          </span>
          <span>{activePoint.date}</span>
        </div>
      </div>

      <div className="price-chart-shell">
        <div className="price-chart-overlay-note">NAV line · hover for daily readout</div>
        <svg
          className="price-chart-svg"
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          role="img"
          aria-label="Portfolio NAV trend"
          onMouseMove={handlePointerMove}
          onMouseLeave={() => setHoveredIndex(null)}
        >
          {geometry.guideValues.map((guideValue, guideIndex) => {
            const guideRatio =
              geometry.maxValue === geometry.minValue
                ? 0.5
                : (guideValue - geometry.minValue) / (geometry.maxValue - geometry.minValue)
            const y =
              CHART_PADDING.top +
              (CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom) * (1 - guideRatio)
            return (
              <g key={`${guideIndex}:${guideValue}`}>
                <line
                  className="price-chart-grid-line"
                  x1={CHART_PADDING.left}
                  x2={CHART_WIDTH - CHART_PADDING.right}
                  y1={y}
                  y2={y}
                />
                <text className="price-chart-axis-label" x={CHART_WIDTH - CHART_PADDING.right} y={y - 6} textAnchor="end">
                  {formatCurrency(guideValue, currency)}
                </text>
              </g>
            )
          })}

          <path className="price-chart-area" d={geometry.areaPath} />
          <path className="price-chart-line" d={geometry.linePath} />
          <line
            className="price-chart-guide-line"
            x1={activeCoordinate.x}
            x2={activeCoordinate.x}
            y1={CHART_PADDING.top}
            y2={CHART_HEIGHT - CHART_PADDING.bottom}
          />
          <circle className="price-chart-point" cx={activeCoordinate.x} cy={activeCoordinate.y} r={4.5} />
        </svg>
      </div>

      <div className="price-chart-footer">
        <span>{points[0].date}</span>
        <span>{points[points.length - 1].date}</span>
      </div>
    </section>
  )
}
