import { useMemo, useState } from 'react'

import {
  formatPercent,
  formatSignedCurrency,
  formatUnitPrice,
} from '../lib/format'
import type {
  PortfolioInstrumentChartRangeKey,
  PortfolioInstrumentPriceChartResponse,
} from '../lib/api'
import { performanceSeriesLabel } from '../lib/instrumentMetricLabels'

const RANGE_OPTIONS: { key: PortfolioInstrumentChartRangeKey; label: string }[] = [
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: '6m', label: '6M' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1Y' },
  { key: 'all', label: 'ALL' },
]

const AXIS_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
})

function formatChartDate(value: string) {
  const parsed = new Date(`${value}T00:00:00`)
  return Number.isNaN(parsed.getTime()) ? value : AXIS_DATE_FORMATTER.format(parsed)
}

type InstrumentPriceChartProps = {
  chart: PortfolioInstrumentPriceChartResponse | null
  loading: boolean
  error: string | null
  rangeKey: PortfolioInstrumentChartRangeKey
  onRangeChange: (rangeKey: PortfolioInstrumentChartRangeKey) => void
  variant?: 'default' | 'instrument'
}

export default function InstrumentPriceChart({
  chart,
  loading,
  error,
  rangeKey,
  onRangeChange,
  variant = 'default',
}: InstrumentPriceChartProps) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)
  const points = chart?.points ?? []
  const activePoint = points[hoverIndex ?? points.length - 1] ?? null
  const reportedChangeValue = chart?.summary.change_value ?? null
  const reportedChangePct = chart?.summary.change_pct ?? null
  const reportedHigh = chart?.summary.high ?? null
  const reportedLow = chart?.summary.low ?? null
  const currency = chart?.currency ?? chart?.instrument_core.currency ?? ''
  const width = variant === 'instrument' ? 900 : 760
  const height = variant === 'instrument' ? 340 : 240
  const paddingLeft = variant === 'instrument' ? 58 : 10
  const paddingRight = variant === 'instrument' ? 18 : 10
  const paddingTop = variant === 'instrument' ? 24 : 18
  const paddingBottom = variant === 'instrument' ? 40 : 26

  const chartGeometry = useMemo(() => {
    if (points.length === 0) {
      return null
    }

    const values = points.map((point) => point.value)
    const minValue = reportedLow != null && Number.isFinite(reportedLow) ? reportedLow : Math.min(...values)
    const maxValue = reportedHigh != null && Number.isFinite(reportedHigh) ? reportedHigh : Math.max(...values)
    const span = maxValue - minValue || Math.max(Math.abs(maxValue) * 0.02, 1)
    const innerWidth = width - paddingLeft - paddingRight
    const innerHeight = height - paddingTop - paddingBottom
    const projectedPoints = points.map((point, index) => {
      const x = paddingLeft + (index / Math.max(points.length - 1, 1)) * innerWidth
      const normalized = (point.value - minValue) / span
      const y = height - paddingBottom - normalized * innerHeight
      return {
        ...point,
        x,
        y,
      }
    })
    const linePath = projectedPoints
      .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
      .join(' ')
    const areaPath = `${linePath} L ${projectedPoints[projectedPoints.length - 1].x.toFixed(1)} ${(height - paddingBottom).toFixed(1)} L ${projectedPoints[0].x.toFixed(1)} ${(height - paddingBottom).toFixed(1)} Z`
    const gridValues = [0, 0.5, 1].map((fraction) => ({
      label: formatUnitPrice(maxValue - span * fraction, currency),
      y: paddingTop + innerHeight * fraction,
    }))
    const bands = [0, 1, 2, 3].map((index) => ({
      x: paddingLeft + (innerWidth / 4) * index,
      y: paddingTop,
      width: innerWidth / 4,
      height: innerHeight,
    }))
    return {
      projectedPoints,
      linePath,
      areaPath,
      gridValues,
      bands,
    }
  }, [currency, height, paddingBottom, paddingLeft, paddingRight, paddingTop, points, reportedHigh, reportedLow, width])

  const activeProjectedPoint =
    chartGeometry && activePoint
      ? chartGeometry.projectedPoints[Math.max(0, hoverIndex ?? chartGeometry.projectedPoints.length - 1)]
      : null
  return (
    <section className={`instrument-price-chart ${variant === 'instrument' ? 'instrument-price-chart-instrument' : ''}`}>
      <div className="instrument-price-chart-toolbar">
        {variant === 'instrument' && chart ? (
          <div className="instrument-series-label portfolio-instrument-series-label">
            <strong>{chart.instrument_core.identifiers.find((item) => item.is_primary)?.identifier_value ?? chart.instrument_core.instrument_id}</strong>
            <span>
              {performanceSeriesLabel(chart.chart_basis ?? chart.metric_family)}
              {chart.metric_family ? ` · ${performanceSeriesLabel(chart.metric_family)} family` : ''}
            </span>
            <em>
              {reportedChangeValue != null
                ? `Range ${formatSignedCurrency(reportedChangeValue, currency)} · ${formatPercent(reportedChangePct)}`
                : '—'}
            </em>
          </div>
        ) : null}
        <div className="price-chart-readout">
          <strong>{activePoint ? formatUnitPrice(activePoint.value, currency) : '—'}</strong>
          <span>{activePoint ? formatChartDate(activePoint.date) : chart ? `As of ${chart.as_of_date}` : 'No data'}</span>
          <span>
            {reportedChangeValue != null
              ? `Range ${formatSignedCurrency(reportedChangeValue, currency)} · ${formatPercent(reportedChangePct)}`
              : '—'}
          </span>
        </div>
        <div className="price-chart-range-strip" role="tablist" aria-label="Chart range">
          {RANGE_OPTIONS.map((option) => {
            const isActive = option.key === rangeKey
            return (
              <button
                key={option.key}
                type="button"
                className={`price-chart-range-button ${isActive ? 'price-chart-range-button-active' : ''}`}
                onClick={() => onRangeChange(option.key)}
              >
                {option.label}
              </button>
            )
          })}
        </div>
      </div>

      {error ? <div className="price-chart-empty price-chart-empty-error">{error}</div> : null}
      {!error && !chartGeometry ? (
        <div className="price-chart-empty">
          {loading ? 'Loading' : 'No chart data.'}
        </div>
      ) : null}

      {!error && chartGeometry ? (
        <div className="price-chart-shell">
          {loading ? <div className="price-chart-overlay-note">Loading</div> : null}
          <svg
            className="price-chart-svg"
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label={`${chart?.instrument_core.instrument_name ?? 'Instrument'} ${performanceSeriesLabel(chart?.chart_basis ?? chart?.metric_family)} performance series`}
            onMouseLeave={() => setHoverIndex(null)}
            onMouseMove={(event) => {
              const bounds = event.currentTarget.getBoundingClientRect()
              const relativeX = Math.min(Math.max(event.clientX - bounds.left, 0), bounds.width)
              const nextIndex = Math.round((relativeX / bounds.width) * (points.length - 1))
              setHoverIndex(nextIndex)
            }}
          >
            {chartGeometry.bands.map((band, index) => (
              <rect
                key={`band-${index}`}
                className="price-chart-band"
                x={band.x}
                y={band.y}
                width={band.width}
                height={band.height}
              />
            ))}
            {chartGeometry.gridValues.map((gridValue) => (
              <g key={`grid-${gridValue.y}`}>
                <line
                  x1={paddingLeft}
                  x2={width - paddingRight}
                  y1={gridValue.y}
                  y2={gridValue.y}
                  className="price-chart-grid-line"
                />
                <text x={width - paddingRight} y={gridValue.y - 6} textAnchor="end" className="price-chart-axis-label">
                  {gridValue.label}
                </text>
              </g>
            ))}
            <path d={chartGeometry.areaPath} className="price-chart-area" />
            <path d={chartGeometry.linePath} className="price-chart-line" />
            {activeProjectedPoint ? (
              <>
                <line
                  x1={activeProjectedPoint.x}
                  x2={activeProjectedPoint.x}
                  y1={paddingTop}
                  y2={height - paddingBottom}
                  className="price-chart-guide-line"
                />
                <circle
                  cx={activeProjectedPoint.x}
                  cy={activeProjectedPoint.y}
                  r={4.5}
                  className="price-chart-point"
                />
              </>
            ) : null}
          </svg>
          <div className="price-chart-footer">
            <span>{points[0] ? formatChartDate(points[0].date) : '—'}</span>
            <span>
              {chart ? `Series: ${performanceSeriesLabel(chart.chart_basis ?? chart.metric_family)}` : 'Series: —'}
            </span>
            <span>
              Range high {formatUnitPrice(reportedHigh, currency)} · low {formatUnitPrice(reportedLow, currency)}
            </span>
            <span>{points[points.length - 1] ? formatChartDate(points[points.length - 1].date) : '—'}</span>
          </div>
        </div>
      ) : null}
    </section>
  )
}
