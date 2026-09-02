import { useMemo } from 'react'

import type { PortfolioDerivativeContractRecord, PortfolioHoldingRow } from '../lib/api'
import { formatCurrency, formatUnitPrice } from '../lib/format'
import { isOptionObligationHolding } from '../lib/holdingPresentation'

type OptionContract = Extract<PortfolioDerivativeContractRecord, { contract_type: 'option' }>

type OptionPayoffChartProps = {
  contract: OptionContract
  holding: PortfolioHoldingRow
}

export default function OptionPayoffChart({ contract, holding }: OptionPayoffChartProps) {
  const terms = contract.terms
  const isWritten = isOptionObligationHolding(holding) || holding.quantity < 0
  const openContracts =
    holding.open_contract_quantity ?? Math.abs(holding.quantity)
  const underlyingUnits = openContracts * terms.contract_multiplier
  const remainingBasis = isWritten
    ? holding.premium_basis_remaining
    : holding.cost_basis
  const premiumPerShare =
    remainingBasis != null && underlyingUnits > 0
      ? remainingBasis / underlyingUnits
      : null
  const spot = holding.option_risk?.underlying_spot ?? null
  const underlyingCurrency = holding.option_risk?.underlying_quote_currency ?? ''
  const payoffPremiumPerShare =
    underlyingCurrency && underlyingCurrency === contract.currency
      ? premiumPerShare
      : null

  const geometry = useMemo(() => {
    if (payoffPremiumPerShare == null) {
      return null
    }
    const width = 560
    const height = 220
    const padding = { left: 48, right: 18, top: 20, bottom: 36 }
    const maxUnderlying = Math.max(
      terms.strike * 2,
      (spot ?? terms.strike) * 1.35,
      1,
    )
    const payoff = (underlyingPrice: number) => {
      const intrinsic =
        terms.option_type === 'call'
          ? Math.max(underlyingPrice - terms.strike, 0)
          : Math.max(terms.strike - underlyingPrice, 0)
      return isWritten
        ? payoffPremiumPerShare - intrinsic
        : intrinsic - payoffPremiumPerShare
    }
    const points = Array.from({ length: 81 }, (_, index) => {
      const underlyingPrice = (maxUnderlying * index) / 80
      return { underlyingPrice, value: payoff(underlyingPrice) }
    })
    const allValues = [...points.map((point) => point.value), 0]
    const minValue = Math.min(...allValues)
    const maxValue = Math.max(...allValues)
    const valueSpan = maxValue - minValue || 1
    const innerWidth = width - padding.left - padding.right
    const innerHeight = height - padding.top - padding.bottom
    const projectX = (value: number) => padding.left + (value / maxUnderlying) * innerWidth
    const projectY = (value: number) =>
      height - padding.bottom - ((value - minValue) / valueSpan) * innerHeight
    return {
      width,
      height,
      padding,
      maxUnderlying,
      zeroY: projectY(0),
      strikeX: projectX(terms.strike),
      spotX: spot == null ? null : projectX(spot),
      path: points
        .map(
          (point, index) =>
            `${index === 0 ? 'M' : 'L'} ${projectX(point.underlyingPrice).toFixed(1)} ${projectY(point.value).toFixed(1)}`,
        )
        .join(' '),
      minValue,
      maxValue,
    }
  }, [isWritten, payoffPremiumPerShare, spot, terms.option_type, terms.strike])

  const breakEven =
    payoffPremiumPerShare == null
      ? null
      : terms.option_type === 'call'
        ? terms.strike + payoffPremiumPerShare
        : Math.max(terms.strike - payoffPremiumPerShare, 0)
  const maximumLossPerShare =
    premiumPerShare == null
      ? null
      : !isWritten
        ? premiumPerShare
        : terms.option_type === 'put'
          ? payoffPremiumPerShare == null
            ? null
            : Math.max(terms.strike - payoffPremiumPerShare, 0)
          : null

  return (
    <section className="option-payoff-panel" aria-label="Option expiry payoff">
      <div className="portfolio-security-section-head">
        <div>
          <span className="portfolio-security-section-kicker">Expiry payoff</span>
          <h2>{isWritten ? 'Written' : 'Long'} {terms.option_type} payoff</h2>
        </div>
        <span>Per underlying share</span>
      </div>
      {geometry ? (
        <svg
          className="option-payoff-chart"
          viewBox={`0 0 ${geometry.width} ${geometry.height}`}
          role="img"
          aria-label={`${isWritten ? 'Written' : 'Long'} ${terms.option_type} expiry payoff excluding opening charges`}
        >
          <line
            x1={geometry.padding.left}
            x2={geometry.width - geometry.padding.right}
            y1={geometry.zeroY}
            y2={geometry.zeroY}
            className="option-payoff-zero"
          />
          <line
            x1={geometry.strikeX}
            x2={geometry.strikeX}
            y1={geometry.padding.top}
            y2={geometry.height - geometry.padding.bottom}
            className="option-payoff-strike"
          />
          {geometry.spotX != null ? (
            <line
              x1={geometry.spotX}
              x2={geometry.spotX}
              y1={geometry.padding.top}
              y2={geometry.height - geometry.padding.bottom}
              className="option-payoff-spot"
            />
          ) : null}
          <path d={geometry.path} className="option-payoff-line" />
          <text x={geometry.strikeX + 4} y={geometry.padding.top + 12} className="option-payoff-label">
            Strike
          </text>
          <text x={geometry.padding.left} y={geometry.height - 10} className="option-payoff-label">
            0
          </text>
          <text x={geometry.width - geometry.padding.right} y={geometry.height - 10} textAnchor="end" className="option-payoff-label">
            {formatUnitPrice(geometry.maxUnderlying, holding.option_risk?.underlying_quote_currency)}
          </text>
          <text x={geometry.padding.left} y={geometry.padding.top + 10} className="option-payoff-label">
            {formatCurrency(geometry.maxValue, contract.currency)}
          </text>
          <text x={geometry.padding.left} y={geometry.height - geometry.padding.bottom - 6} className="option-payoff-label">
            {formatCurrency(geometry.minValue, contract.currency)}
          </text>
        </svg>
      ) : (
        <div className="price-chart-empty">
          {premiumPerShare == null
            ? 'Remaining premium basis is unavailable.'
            : 'Payoff needs a confirmed common currency for premium and strike.'}
        </div>
      )}
      <dl className="derivative-risk-facts">
        <div><dt>Premium basis / share</dt><dd>{formatUnitPrice(premiumPerShare, contract.currency)}</dd></div>
        <div><dt>Break-even</dt><dd>{formatUnitPrice(breakEven, holding.option_risk?.underlying_quote_currency)}</dd></div>
        <div>
          <dt>Maximum loss / share</dt>
          <dd>{isWritten && terms.option_type === 'call' ? 'Unlimited' : formatUnitPrice(maximumLossPerShare, contract.currency)}</dd>
        </div>
      </dl>
    </section>
  )
}
