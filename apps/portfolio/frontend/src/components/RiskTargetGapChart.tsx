import { formatPercent, signedValueClass } from '../lib/format'

export type RiskTargetGapChartRow = {
  id: string
  label: string
  current: number | null
  detail?: string
  saaTarget: number | null
  taaTarget: number | null
  saaGap: number | null
  taaGap: number | null
}

type RiskTargetGapChartProps = {
  rows: RiskTargetGapChartRow[]
  ariaLabel: string
  emptyLabel: string
  currentLabel?: string
  showTargets?: boolean
}

function pctWidth(value: number | null | undefined, maxValue: number) {
  if (value == null || Number.isNaN(value) || maxValue <= 0) {
    return 0
  }
  return Math.min(100, (Math.abs(value) / maxValue) * 100)
}

export default function RiskTargetGapChart({
  rows,
  ariaLabel,
  emptyLabel,
  currentLabel = 'Current',
  showTargets = true,
}: RiskTargetGapChartProps) {
  if (!rows.length) {
    return <div className="risk-chart-empty">{emptyLabel}</div>
  }

  const maxValue = Math.max(
    0.01,
    ...rows.flatMap((row) => [Math.abs(row.current ?? 0), Math.abs(row.saaTarget ?? 0), Math.abs(row.taaTarget ?? 0)]),
  )

  return (
    <div className="risk-target-gap-chart" role="img" aria-label={ariaLabel}>
      <div className="risk-target-gap-legend" aria-hidden="true">
        <span className="risk-target-gap-legend-item">
          <span className="risk-target-gap-swatch risk-target-gap-swatch-current" />
          {currentLabel}
        </span>
        {showTargets ? <span className="risk-target-gap-legend-item">
          <span className="risk-target-gap-swatch risk-target-gap-swatch-saa" />
          SAA
        </span> : null}
        {showTargets ? <span className="risk-target-gap-legend-item">
          <span className="risk-target-gap-swatch risk-target-gap-swatch-taa" />
          TAA
        </span> : null}
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
                className="risk-target-gap-fill risk-target-gap-fill-current"
                style={{ width: `${pctWidth(row.current, maxValue)}%` }}
              />
            </div>
            {showTargets ? <div className="risk-target-gap-track" aria-hidden="true">
              <span
                className="risk-target-gap-fill risk-target-gap-fill-saa"
                style={{ width: `${pctWidth(row.saaTarget, maxValue)}%` }}
              />
            </div> : null}
            {showTargets ? <div className="risk-target-gap-track" aria-hidden="true">
              <span
                className="risk-target-gap-fill risk-target-gap-fill-taa"
                style={{ width: `${pctWidth(row.taaTarget, maxValue)}%` }}
              />
            </div> : null}
          </div>
          <div className="risk-target-gap-values">
            <span>{formatPercent(row.current)}</span>
            {showTargets ? <span className={signedValueClass(row.saaGap)} title={`SAA gap ${formatPercent(row.saaGap)}`}>
              {formatPercent(row.saaTarget)}
            </span> : null}
            {showTargets ? <span className={signedValueClass(row.taaGap)} title={`TAA gap ${formatPercent(row.taaGap)}`}>
              {formatPercent(row.taaTarget)}
            </span> : null}
          </div>
        </div>
      ))}
    </div>
  )
}
