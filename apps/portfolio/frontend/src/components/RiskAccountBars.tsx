import { formatPercent } from '../lib/format'

export type RiskAccountBarItem = {
  id: string
  label: string
  grossValue: number
  cashValue: number
  positionValue: number
  totalLabel: string
  shareLabel?: string | null
  cashLabel: string
  positionLabel: string
  detail?: string
}

type RiskAccountBarsProps = {
  items: RiskAccountBarItem[]
  ariaLabel: string
  emptyLabel: string
}

export default function RiskAccountBars({ items, ariaLabel, emptyLabel }: RiskAccountBarsProps) {
  const validItems = items.filter((item) => item.grossValue > 0)
  const maxGrossValue = validItems.reduce((currentMax, item) => Math.max(currentMax, item.grossValue), 0)

  if (!validItems.length || maxGrossValue <= 0) {
    return <div className="price-chart-empty">{emptyLabel}</div>
  }

  return (
    <div className="risk-account-bars" role="img" aria-label={ariaLabel}>
      <div className="risk-account-legend" aria-hidden="true">
        <span className="risk-account-legend-item">
          <span className="risk-account-legend-swatch risk-account-legend-swatch-cash" />
          Cash
        </span>
        <span className="risk-account-legend-item">
          <span className="risk-account-legend-swatch risk-account-legend-swatch-position" />
          Positions
        </span>
      </div>

      {validItems.map((item) => {
        const totalMagnitude = Math.abs(item.cashValue) + Math.abs(item.positionValue)
        const scaledWidth = Math.max((item.grossValue / maxGrossValue) * 100, 6)
        const cashWidth = totalMagnitude > 0 ? (Math.abs(item.cashValue) / totalMagnitude) * 100 : 0
        const positionWidth = totalMagnitude > 0 ? (Math.abs(item.positionValue) / totalMagnitude) * 100 : 0

        return (
          <div className="risk-account-row" key={item.id}>
            <div className="risk-account-label">
              <span className="risk-account-title">{item.label}</span>
              {item.detail ? <span className="risk-account-meta">{item.detail}</span> : null}
            </div>

            <div className="risk-account-bar-stack">
              <div className="risk-account-track">
                <div className="risk-account-track-span" style={{ width: `${scaledWidth}%` }}>
                  {cashWidth > 0 ? (
                    <div
                      className={`risk-account-segment ${
                        item.cashValue < 0 ? 'risk-account-segment-negative' : 'risk-account-segment-cash'
                      }`}
                      style={{ width: `${cashWidth}%` }}
                    />
                  ) : null}
                  {positionWidth > 0 ? (
                    <div
                      className={`risk-account-segment ${
                        item.positionValue < 0
                          ? 'risk-account-segment-negative'
                          : 'risk-account-segment-position'
                      }`}
                      style={{ width: `${positionWidth}%` }}
                    />
                  ) : null}
                </div>
              </div>
              <div className="risk-account-detail-line">
                <span>{item.cashLabel}</span>
                <span>{item.positionLabel}</span>
              </div>
            </div>

            <div className="risk-account-value">
              <span>{item.totalLabel}</span>
              <span className="risk-account-value-meta">{item.shareLabel ?? formatPercent(null)}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
