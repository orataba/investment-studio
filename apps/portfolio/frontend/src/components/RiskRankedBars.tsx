import { formatPercent } from '../lib/format'

const BAR_COLORS = ['#1f4b99', '#0b72d7', '#2a9d8f', '#7c5c3a', '#d2a94f', '#7a8a4f', '#8b5cf6', '#c26d2d']

export type RiskRankedBarItem = {
  id: string
  label: string
  value: number
  valueLabel?: string
  subtitle?: string
  detail?: string
}

type RiskRankedBarsProps = {
  items: RiskRankedBarItem[]
  ariaLabel: string
  emptyLabel: string
}

export default function RiskRankedBars({ items, ariaLabel, emptyLabel }: RiskRankedBarsProps) {
  const validItems = items.filter((item) => item.value > 0)
  const maxValue = validItems.reduce((currentMax, item) => Math.max(currentMax, item.value), 0)

  if (!validItems.length || maxValue <= 0) {
    return <div className="price-chart-empty">{emptyLabel}</div>
  }

  return (
    <div className="risk-ranked-bars" role="img" aria-label={ariaLabel}>
      {validItems.map((item, index) => (
        <div className="risk-ranked-row" key={item.id}>
          <div className="risk-ranked-label">
            <span className="risk-ranked-title">{item.label}</span>
            {item.subtitle ? <span className="risk-ranked-meta">{item.subtitle}</span> : null}
          </div>

          <div className="risk-ranked-bar-stack">
            <div className="risk-ranked-bar-track">
              <div
                className="risk-ranked-bar-fill"
                style={{
                  width: `${Math.max((item.value / maxValue) * 100, 6)}%`,
                  background: BAR_COLORS[index % BAR_COLORS.length],
                }}
              />
            </div>
            {item.detail ? <span className="risk-ranked-detail">{item.detail}</span> : null}
          </div>

          <div className="risk-ranked-value">{item.valueLabel ?? formatPercent(item.value)}</div>
        </div>
      ))}
    </div>
  )
}
