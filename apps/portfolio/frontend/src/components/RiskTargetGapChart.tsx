import { formatPercent, signedValueClass } from '../lib/format'

export type RiskTargetGapChartRow = {
  id: string
  label: string
  current: number | null
  target: number | null
  gap: number | null
  detail?: string
}

type RiskTargetGapChartProps = {
  rows: RiskTargetGapChartRow[]
  ariaLabel: string
  emptyLabel: string
  currentLabel?: string
  targetLabel?: string
}

function pctWidth(value: number | null | undefined, maxValue: number) {
  if (value == null || Number.isNaN(value) || maxValue <= 0) {
    return 0
  }
  return Math.min(100, (Math.abs(value) / maxValue) * 100)
}

function signedPercent(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }
  const absolute = formatPercent(Math.abs(value), digits)
  if (value > 0) {
    return `+${absolute}`
  }
  if (value < 0) {
    return `-${absolute}`
  }
  return absolute
}

export default function RiskTargetGapChart({
  rows,
  ariaLabel,
  emptyLabel,
  currentLabel = 'Current',
  targetLabel = 'Target',
}: RiskTargetGapChartProps) {
  if (!rows.length) {
    return <div className="price-chart-empty">{emptyLabel}</div>
  }

  const maxValue = Math.max(
    0.01,
    ...rows.flatMap((row) => [Math.abs(row.current ?? 0), Math.abs(row.target ?? 0)]),
  )

  return (
    <div className="risk-target-gap-chart" role="img" aria-label={ariaLabel}>
      <div className="risk-target-gap-legend" aria-hidden="true">
        <span className="risk-target-gap-legend-item">
          <span className="risk-target-gap-swatch risk-target-gap-swatch-current" />
          {currentLabel}
        </span>
        <span className="risk-target-gap-legend-item">
          <span className="risk-target-gap-swatch risk-target-gap-swatch-target" />
          {targetLabel}
        </span>
      </div>
      {rows.map((row) => (
        <div className="risk-target-gap-row" key={row.id}>
          <div className="risk-target-gap-label">
            <span className="risk-target-gap-title">{row.label}</span>
            {row.detail ? <span className="risk-target-gap-meta">{row.detail}</span> : null}
          </div>
          <div className="risk-target-gap-bars">
            <div className="risk-target-gap-track" aria-hidden="true">
              <span
                className="risk-target-gap-fill risk-target-gap-fill-target"
                style={{ width: `${pctWidth(row.target, maxValue)}%` }}
              />
            </div>
            <div className="risk-target-gap-track" aria-hidden="true">
              <span
                className="risk-target-gap-fill risk-target-gap-fill-current"
                style={{ width: `${pctWidth(row.current, maxValue)}%` }}
              />
            </div>
          </div>
          <div className="risk-target-gap-values">
            <span>{formatPercent(row.current)}</span>
            <span>{formatPercent(row.target)}</span>
            <strong className={signedValueClass(row.gap)}>{signedPercent(row.gap)}</strong>
          </div>
        </div>
      ))}
    </div>
  )
}
