import { formatPercent } from '../lib/format'

const SEGMENT_COLORS = ['#1f4b99', '#0b72d7', '#2a9d8f', '#7c5c3a', '#d2a94f', '#7a8a4f', '#8b5cf6', '#c26d2d']

export type RiskExposureSegment = {
  id: string
  label: string
  value: number
  valueLabel?: string
  detail?: string
}

type RiskExposureRibbonProps = {
  segments: RiskExposureSegment[]
  ariaLabel: string
  emptyLabel: string
}

export default function RiskExposureRibbon({ segments, ariaLabel, emptyLabel }: RiskExposureRibbonProps) {
  const validSegments = segments.filter((segment) => segment.value > 0)
  const totalValue = validSegments.reduce((total, segment) => total + segment.value, 0)

  if (!validSegments.length || totalValue <= 0) {
    return <div className="price-chart-empty">{emptyLabel}</div>
  }

  return (
    <div className="risk-ribbon" role="img" aria-label={ariaLabel}>
      <div className="risk-ribbon-track" aria-hidden="true">
        {validSegments.map((segment, index) => (
          <div
            key={segment.id}
            className="risk-ribbon-segment"
            style={{
              flexGrow: segment.value,
              background: SEGMENT_COLORS[index % SEGMENT_COLORS.length],
            }}
          />
        ))}
      </div>

      <div className="risk-ribbon-legend">
        {validSegments.map((segment, index) => (
          <div className="risk-ribbon-legend-item" key={segment.id}>
            <span
              className="risk-ribbon-swatch"
              style={{ background: SEGMENT_COLORS[index % SEGMENT_COLORS.length] }}
            />
            <span className="risk-ribbon-label">{segment.label}</span>
            <span className="risk-ribbon-value">{segment.valueLabel ?? formatPercent(segment.value)}</span>
            {segment.detail ? <span className="risk-ribbon-detail">{segment.detail}</span> : null}
          </div>
        ))}
      </div>
    </div>
  )
}
