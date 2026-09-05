import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'

import {
  formatNumber,
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

function formatChartDate(value: string) {
  return value
}

type InstrumentPriceChartProps = {
  chart: PortfolioInstrumentPriceChartResponse | null
  loading: boolean
  error: string | null
  rangeKey: PortfolioInstrumentChartRangeKey
  onRangeChange: (rangeKey: PortfolioInstrumentChartRangeKey) => void
  variant?: 'default' | 'instrument'
  referenceLines?: Array<{
    label: string
    value: number
    tone?: 'strike' | 'knock-in' | 'knock-out' | 'reference'
  }>
}

function chartSeriesLabel(chart: PortfolioInstrumentPriceChartResponse | null) {
  return chart?.series_role === 'price_level'
    ? 'Price level'
    : performanceSeriesLabel(chart?.chart_basis ?? chart?.metric_family)
}

export default function InstrumentPriceChart({
  chart,
  loading,
  error,
  rangeKey,
  onRangeChange,
  variant = 'default',
  referenceLines = [],
}: InstrumentPriceChartProps) {
  const { t } = useLanguage()
  const gradientId = useId()
  const chartRef = useRef<HTMLElement>(null)
  const [containerWidth, setContainerWidth] = useState(900)
  useEffect(() => {
    if (variant !== 'instrument' || !chartRef.current) return
    const observer = new ResizeObserver(([entry]) => setContainerWidth(Math.round(entry.contentRect.width)))
    observer.observe(chartRef.current)
    return () => observer.disconnect()
  }, [variant])
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)
  const points = chart?.points ?? []
  const firstPoint = points[0] ?? null
  const activeIndex = hoverIndex == null ? points.length - 1 : Math.min(hoverIndex, points.length - 1)
  const activePoint = points[activeIndex] ?? null
  const currency = chart?.currency ?? chart?.instrument_core.currency ?? 'USD'
  const width = variant === 'instrument' ? Math.max(containerWidth, 300) : 760
  const height = variant === 'instrument' && width >= 520 ? 340 : 240
  const paddingLeft = 10
  const paddingRight = variant === 'instrument' ? 76 : 10
  const paddingTop = variant === 'instrument' ? 24 : 18
  const paddingBottom = variant === 'instrument' ? 40 : 26

  const chartGeometry = useMemo(() => {
    if (points.length === 0) {
      return null
    }

    const values = [
      ...points.map((point) => point.value),
      ...referenceLines.map((line) => Number(line.value)).filter(Number.isFinite),
    ]
    const low = Math.min(...values)
    const high = Math.max(...values)
    const breathingRoom = (high - low || Math.max(Math.abs(high) * 0.02, 1)) * 0.08
    const minValue = low - breathingRoom
    const maxValue = high + breathingRoom
    const span = maxValue - minValue
    const innerWidth = width - paddingLeft - paddingRight
    const innerHeight = height - paddingTop - paddingBottom
    const startTime = Date.parse(points[0].date)
    const timeSpan = Date.parse(points[points.length - 1].date) - startTime
    const projectedPoints = points.map((point) => {
      const x = paddingLeft + (timeSpan > 0 ? (Date.parse(point.date) - startTime) / timeSpan : 0.5) * innerWidth
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
      label: formatNumber(maxValue - span * fraction, 4),
      y: paddingTop + innerHeight * fraction,
    }))
    const bands = [0, 1, 2, 3].map((index) => ({
      x: paddingLeft + (innerWidth / 4) * index,
      y: paddingTop,
      width: innerWidth / 4,
      height: innerHeight,
    }))
    const projectedReferenceLines = referenceLines.map((line) => ({ ...line, value: Number(line.value) }))
      .filter((line) => Number.isFinite(line.value))
      .map((line) => ({
        ...line,
        y: height - paddingBottom - ((line.value - minValue) / span) * innerHeight,
      }))
    return {
      projectedPoints,
      linePath,
      areaPath,
      gridValues,
      bands,
      projectedReferenceLines,
    }
  }, [currency, height, paddingBottom, paddingLeft, paddingRight, paddingTop, points, referenceLines, width])

  const activeProjectedPoint =
    chartGeometry && activePoint
      ? chartGeometry.projectedPoints[activeIndex]
      : null
  const activeChangeValue =
    firstPoint && activePoint
      ? activePoint.value - firstPoint.value
      : null
  const activeChangePct =
    firstPoint && activePoint && Math.abs(firstPoint.value) > 1e-9
      ? (activePoint.value - firstPoint.value) / firstPoint.value
      : null
  const periodDrawdown = useMemo(() => {
    if (points.length < 2 || points.some((point) => point.value <= 0)) return null
    let peak = points[0].value
    let drawdown = 0
    for (const point of points) {
      peak = Math.max(peak, point.value)
      drawdown = Math.min(drawdown, point.value / peak - 1)
    }
    return drawdown
  }, [points])

  return (
    <section
      ref={chartRef}
      className={`instrument-price-chart ${variant === 'instrument' ? 'instrument-price-chart-instrument' : ''}`}
      aria-busy={loading}
    >
      <div className="instrument-price-chart-toolbar">
        {variant === 'instrument' && chart ? (
          <div className="instrument-series-label portfolio-instrument-series-label">
            <strong>{chart.instrument_core.identifiers.find((item) => item.is_primary)?.identifier_value ?? chart.instrument_core.instrument_id}</strong>
            <span>
              {chartSeriesLabel(chart)}
            </span>
          </div>
        ) : null}
        <div className="price-chart-readout">
          <strong>{activePoint ? formatUnitPrice(activePoint.value, currency) : loading ? 'Loading' : '—'}</strong>
          <span title={t('Change in the instrument series, not your portfolio P/L.')}>
            {activePoint
              ? formatChartDate(activePoint.date)
              : loading
                ? 'Loading chart'
                : chart
                  ? `As of ${chart.as_of_date}`
                  : 'No data'}
          </span>
          <span>
            {activeChangeValue != null
              ? `${formatSignedCurrency(activeChangeValue, currency)} · ${formatPercent(activeChangePct)}`
              : '—'}
          </span>
        </div>
        <div className="price-chart-range-strip" role="group" aria-label={t('Chart range')}>
          {RANGE_OPTIONS.map((option) => {
            const isActive = option.key === rangeKey
            return (
              <button
                key={option.key}
                type="button"
                aria-pressed={isActive}
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
            aria-label={`${chart?.instrument_core.instrument_name ?? t('Instrument')} · ${t(chartSeriesLabel(chart))}`}
            onMouseLeave={() => setHoverIndex(null)}
            onMouseMove={(event) => {
              const bounds = event.currentTarget.getBoundingClientRect()
              const x = ((event.clientX - bounds.left) / bounds.width) * width
              const nextIndex = chartGeometry.projectedPoints.reduce((nearest, point, index, projected) =>
                Math.abs(point.x - x) < Math.abs(projected[nearest].x - x) ? index : nearest, 0)
              setHoverIndex(nextIndex)
            }}
          >
            <defs><linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#287c8e" stopOpacity="0.18" /><stop offset="100%" stopColor="#287c8e" stopOpacity="0.01" /></linearGradient></defs>
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
                <text x={width - 8} y={gridValue.y - 6} textAnchor="end" className="price-chart-axis-label">
                  {gridValue.label}
                </text>
              </g>
            ))}
            <path d={chartGeometry.areaPath} className="price-chart-area" style={{ fill: `url(#${gradientId})` }} />
            {chartGeometry.projectedReferenceLines.map((line) => (
              <g key={`${line.label}-${line.value}`}>
                <line
                  x1={paddingLeft}
                  x2={width - paddingRight}
                  y1={line.y}
                  y2={line.y}
                  className={`price-chart-reference-line price-chart-reference-line-${line.tone ?? 'reference'}`}
                />
                <text
                  x={paddingLeft + 6}
                  y={line.y - 6}
                  className="price-chart-reference-label"
                >
                  {t(line.label)} {formatUnitPrice(line.value, currency)}
                </text>
              </g>
            ))}
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
              {t('Instrument series · not portfolio P/L')}
            </span>
            <span>{points[points.length - 1] ? formatChartDate(points[points.length - 1].date) : '—'}</span>
          </div>
          {variant === 'instrument' && chart?.series_role !== 'price_level' && <dl className="holding-chart-statistics">
            <div><dt>{t('Selected-period high')}</dt><dd>{formatUnitPrice(chart?.summary.high, currency)}</dd></div>
            <div><dt>{t('Selected-period low')}</dt><dd>{formatUnitPrice(chart?.summary.low, currency)}</dd></div>
            <div><dt>{t('Observed max drawdown')}</dt><dd>{formatPercent(periodDrawdown)}</dd></div>
          </dl>}
        </div>
      ) : null}
    </section>
  )
}
