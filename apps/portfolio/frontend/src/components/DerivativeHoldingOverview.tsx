import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'

import {
  getPortfolioInstrumentPriceChart,
  type PortfolioDerivativeContractRecord,
  type PortfolioHoldingRow,
  type PortfolioInstrumentChartRangeKey,
  type PortfolioInstrumentPriceChartResponse,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatUnitPrice,
} from '../lib/format'
import { buildPortfolioHoldingDetailPath } from '../lib/navigation'
import InstrumentPriceChart from './InstrumentPriceChart'
import OptionPayoffChart from './OptionPayoffChart'

type DerivativeHoldingOverviewProps = {
  portfolioId: string
  asOfDate: string
  holding: PortfolioHoldingRow
  contract: PortfolioDerivativeContractRecord
  rangeKey: PortfolioInstrumentChartRangeKey
  onRangeChange: (rangeKey: PortfolioInstrumentChartRangeKey) => void
}

function riskPill(label: string, warning: boolean) {
  return (
    <span className={`coverage-pill ${warning ? 'coverage-pill-warning' : 'coverage-pill-live'}`}>
      {label}
    </span>
  )
}

function optionRiskLabel(state: string | undefined) {
  switch (state) {
    case 'expired_unresolved': return 'Action required'
    case 'uncovered': return 'Backing shortfall'
    case 'in_the_money': return 'In the money'
    case 'quote_unavailable': return 'Quote unavailable'
    case 'open': return 'Open'
    default: return state ? formatLabel(state) : 'Unavailable'
  }
}

function fcnRiskLabel(state: string | undefined) {
  switch (state) {
    case 'current_price_at_or_below_knock_in': return 'Current price at/below KI'
    case 'terms_incomplete': return 'Terms incomplete'
    case 'quote_unavailable': return 'Quote unavailable'
    case 'knocked_in': return 'Knock-in recorded'
    case 'knocked_out': return 'Knock-out recorded'
    case 'matured': return 'Maturity recorded'
    case 'open': return 'Open'
    default: return state ? formatLabel(state) : 'Unavailable'
  }
}

function distanceLabel(value: number | null, level: string) {
  if (value == null) return '—'
  return `${formatPercent(Math.abs(value))} ${value >= 0 ? 'above' : 'below'} ${level}`
}

export default function DerivativeHoldingOverview({
  portfolioId,
  asOfDate,
  holding,
  contract,
  rangeKey,
  onRangeChange,
}: DerivativeHoldingOverviewProps) {
  const underlyingIds = useMemo(
    () =>
      contract.contract_type === 'option'
        ? [contract.terms.underlying_instrument_id]
        : contract.terms.underlyings.map((underlying) => underlying.instrument_id),
    [contract],
  )
  const [charts, setCharts] = useState<Record<string, PortfolioInstrumentPriceChartResponse>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setCharts({})
    setErrors({})
    setLoading(true)
    Promise.all(
      underlyingIds.map(async (underlyingId) => {
        try {
          const chart = await getPortfolioInstrumentPriceChart(portfolioId, underlyingId, {
            as_of_date: asOfDate,
            range: rangeKey,
            price_level: true,
          })
          return { underlyingId, chart, error: null }
        } catch (requestError) {
          return {
            underlyingId,
            chart: null,
            error: requestError instanceof Error ? requestError.message : 'Failed to load price history.',
          }
        }
      }),
    ).then((results) => {
      if (cancelled) return
      setCharts(
        Object.fromEntries(
          results
            .filter((result) => result.chart)
            .map((result) => [result.underlyingId, result.chart!]),
        ),
      )
      setErrors(
        Object.fromEntries(
          results
            .filter((result) => result.error)
            .map((result) => [result.underlyingId, result.error!]),
        ),
      )
      setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [asOfDate, portfolioId, rangeKey, underlyingIds])

  if (contract.contract_type === 'option') {
    const risk = holding.option_risk
    const backing = risk?.backing
    const isWritten = holding.quantity < 0 || holding.holding_kind === 'option_obligation'
    const warning =
      risk?.risk_state === 'expired_unresolved' ||
      risk?.risk_state === 'uncovered' ||
      risk?.risk_state === 'quote_unavailable' ||
      (isWritten && risk?.risk_state === 'in_the_money')
    const backingLabel = backing
      ? `${formatPercent(backing.ratio)} · ${backing.shortfall > 0 ? `${formatQuantity(backing.shortfall)} short` : 'fully backed'}`
      : 'Not applicable'
    return (
      <div className="derivative-detail-overview">
        <div className="portfolio-security-overview-workbench">
          <section className="portfolio-security-chart-panel">
            <div className="portfolio-security-section-head derivative-chart-heading">
              <div>
                <span className="portfolio-security-section-kicker">Underlying price</span>
                <h2>{risk?.underlying_name ?? contract.terms.underlying_instrument_id}</h2>
              </div>
              <Link
                className="portfolio-security-secondary-link"
                to={`${buildPortfolioHoldingDetailPath(portfolioId, contract.terms.underlying_instrument_id)}?as_of_date=${asOfDate}`}
              >
                Open security detail
              </Link>
            </div>
            <InstrumentPriceChart
              chart={charts[contract.terms.underlying_instrument_id] ?? null}
              loading={loading}
              error={errors[contract.terms.underlying_instrument_id] ?? null}
              rangeKey={rangeKey}
              onRangeChange={onRangeChange}
              variant="instrument"
              referenceLines={[
                { label: 'Strike', value: contract.terms.strike, tone: 'strike' },
              ]}
            />
          </section>
          <aside className="portfolio-security-position-brief">
            <div className="portfolio-security-section-head">
              <div>
                <span className="portfolio-security-section-kicker">Contract risk</span>
                <h2>Current exposure</h2>
              </div>
              {riskPill(optionRiskLabel(risk?.risk_state), warning)}
            </div>
            <dl className="portfolio-security-position-facts">
              <div><dt>Side / type</dt><dd>{isWritten ? 'Written' : 'Long'} {formatLabel(contract.terms.option_type)}</dd></div>
              <div><dt>Strike</dt><dd>{formatUnitPrice(contract.terms.strike, risk?.underlying_quote_currency)}</dd></div>
              <div><dt>Underlying spot</dt><dd>{formatUnitPrice(risk?.underlying_spot, risk?.underlying_quote_currency)}</dd></div>
              <div><dt>Moneyness</dt><dd>{formatPercent(risk?.moneyness_pct)}</dd></div>
              <div><dt>Intrinsic / share</dt><dd>{formatUnitPrice(risk?.intrinsic_value_per_share, risk?.underlying_quote_currency)}</dd></div>
              <div><dt>Expiry</dt><dd>{contract.terms.expiry_date} · {formatNumber(risk?.days_to_expiry, 0)} days</dd></div>
              <div><dt>Open contracts</dt><dd>{formatQuantity(holding.open_contract_quantity ?? Math.abs(holding.quantity))}</dd></div>
              <div><dt>Multiplier</dt><dd>{formatQuantity(contract.terms.contract_multiplier)}</dd></div>
              <div><dt>Portfolio backing</dt><dd>{backingLabel}</dd></div>
              <div><dt>Maximum loss basis</dt><dd>{formatCurrency(risk?.max_loss_local, contract.currency)}</dd></div>
            </dl>
          </aside>
        </div>
        <OptionPayoffChart contract={contract} holding={holding} />
      </div>
    )
  }

  const risk = holding.fcn_risk
  const warning = !['open', 'knocked_out', 'matured'].includes(risk?.risk_state ?? '')
  return (
    <div className="derivative-detail-overview fcn-detail-overview">
      <section className="fcn-risk-summary">
        <div className="portfolio-security-section-head">
          <div>
            <span className="portfolio-security-section-kicker">Contract risk</span>
            <h2>FCN current state</h2>
          </div>
          {riskPill(fcnRiskLabel(risk?.risk_state), warning)}
        </div>
        <dl className="derivative-risk-facts">
          <div><dt>Notional</dt><dd>{formatCurrency(contract.terms.notional, contract.currency)}</dd></div>
          <div><dt>Annual coupon</dt><dd>{contract.terms.annual_coupon_rate_pct == null ? '—' : `${formatNumber(contract.terms.annual_coupon_rate_pct, 2)}%`}</dd></div>
          <div><dt>Final observation</dt><dd>{contract.terms.final_observation_date ?? '—'}</dd></div>
          <div><dt>Maturity</dt><dd>{contract.terms.maturity_date}</dd></div>
          <div><dt>Issuer</dt><dd>{contract.terms.issuer || '—'}</dd></div>
          <div><dt>Counterparty</dt><dd>{contract.terms.counterparty || '—'}</dd></div>
        </dl>
      </section>
      <div className="fcn-underlying-chart-list">
        {(risk?.underlyings ?? []).map((underlying) => (
          <section key={underlying.instrument_id} className="portfolio-security-chart-panel fcn-underlying-chart-panel">
            <div className="portfolio-security-section-head derivative-chart-heading">
              <div>
                <span className="portfolio-security-section-kicker">Underlying risk</span>
                <h2>{underlying.instrument_name}</h2>
              </div>
              <Link
                className="portfolio-security-secondary-link"
                to={`${buildPortfolioHoldingDetailPath(portfolioId, underlying.instrument_id)}?as_of_date=${asOfDate}`}
              >
                Open security detail
              </Link>
            </div>
            <InstrumentPriceChart
              chart={charts[underlying.instrument_id] ?? null}
              loading={loading}
              error={errors[underlying.instrument_id] ?? null}
              rangeKey={rangeKey}
              onRangeChange={onRangeChange}
              variant="instrument"
              referenceLines={[
                { label: 'Initial', value: underlying.initial_reference_price, tone: 'reference' },
                { label: 'Strike', value: underlying.strike_price, tone: 'strike' },
                { label: 'KI', value: underlying.knock_in_price, tone: 'knock-in' },
                { label: 'KO', value: underlying.knock_out_price, tone: 'knock-out' },
              ].filter((line): line is { label: string; value: number; tone: 'reference' | 'strike' | 'knock-in' | 'knock-out' } => line.value != null)}
            />
            <dl className="derivative-risk-facts">
              <div><dt>Current</dt><dd>{formatUnitPrice(underlying.spot, underlying.currency)}</dd></div>
              <div><dt>Vs initial</dt><dd>{formatPercent(underlying.performance_to_reference_pct)}</dd></div>
              <div><dt>Strike distance</dt><dd>{distanceLabel(underlying.distance_to_strike_pct, 'strike')}</dd></div>
              <div><dt>KI distance</dt><dd>{distanceLabel(underlying.distance_to_knock_in_pct, 'KI')}</dd></div>
              <div><dt>KO distance</dt><dd>{distanceLabel(underlying.distance_to_knock_out_pct, 'KO')}</dd></div>
              <div><dt>Current region</dt><dd>{formatLabel(underlying.current_region)}</dd></div>
            </dl>
          </section>
        ))}
      </div>
    </div>
  )
}
