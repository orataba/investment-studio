import { useMemo } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'

import type { PortfolioDerivativeContractRecord, PortfolioPositionHoldingRow } from '../lib/api'
import { formatCurrency, formatUnitPrice } from '../lib/format'
import { isOptionObligationHolding } from '../lib/holdingPresentation'

type OptionContract = Extract<PortfolioDerivativeContractRecord, { contract_type: 'option' }>

type OptionPayoffChartProps = {
  contract: OptionContract
  holding: PortfolioPositionHoldingRow
}

export default function OptionPayoffChart({ contract, holding }: OptionPayoffChartProps) {
  const { t } = useLanguage()
  const terms = contract.terms
  // Decimal contract terms arrive as JSON strings from the contract endpoint.
  const strike = Number(terms.strike)
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
      strike * 2,
      (spot ?? strike) * 1.35,
      1,
    )
    const payoff = (underlyingPrice: number) => {
      const intrinsic =
        terms.option_type === 'call'
          ? Math.max(underlyingPrice - strike, 0)
          : Math.max(strike - underlyingPrice, 0)
      return isWritten
        ? payoffPremiumPerShare - intrinsic
        : intrinsic - payoffPremiumPerShare
    }
    const points = [0, strike, maxUnderlying].map((underlyingPrice) => ({ underlyingPrice, value: payoff(underlyingPrice) }))
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
      strikeX: projectX(strike),
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
  }, [isWritten, payoffPremiumPerShare, spot, terms.option_type, strike])

  const breakEven =
    payoffPremiumPerShare == null
      ? null
      : terms.option_type === 'call'
        ? strike + payoffPremiumPerShare
        : strike - payoffPremiumPerShare
  const maximumLossPerShare =
    premiumPerShare == null
      ? null
      : !isWritten
        ? premiumPerShare
        : terms.option_type === 'put'
          ? payoffPremiumPerShare == null
            ? null
            : Math.max(strike - payoffPremiumPerShare, 0)
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
          aria-label={t('Standalone option expiry payoff based on remaining premium basis')}
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
            {t('Strike')}
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
        <div><dt>Break-even</dt><dd>{breakEven != null && breakEven < 0 ? t('No non-negative break-even') : formatUnitPrice(breakEven, holding.option_risk?.underlying_quote_currency)}</dd></div>
        <div>
          <dt>Maximum loss / share</dt>
          <dd>{isWritten && terms.option_type === 'call' ? 'Unlimited' : formatUnitPrice(maximumLossPerShare, contract.currency)}</dd>
        </div>
      </dl>
      <p className="holding-detail-note">{t('Expiry scenario for the option alone; underlying holdings and other hedges are excluded. This is not the current option value.')}</p>
      <p className="holding-detail-note">{t(isWritten ? 'Written payoff uses remaining gross premium before fees and taxes.' : 'Long payoff uses remaining cost including allocated opening charges; future closing charges are excluded.')}</p>
    </section>
  )
}
