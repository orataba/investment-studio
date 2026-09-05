import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'

import {
  getPortfolioInstrumentPriceChart,
  type PortfolioDerivativeContractRecord,
  type PortfolioPositionHoldingRow,
  type PortfolioInstrumentChartRangeKey,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioTransactionRecord,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatSignedCurrency,
  formatUnitPrice,
  signedValueClass,
} from '../lib/format'
import { buildPortfolioHoldingDetailPath } from '../lib/navigation'
import InstrumentPriceChart from './InstrumentPriceChart'
import OptionPayoffChart from './OptionPayoffChart'
import { DerivativeSettlementFacts } from './DerivativeSettlementFields'

type DerivativeHoldingOverviewProps = {
  portfolioId: string
  asOfDate: string
  baseCurrency: string
  holding: PortfolioPositionHoldingRow | null
  contract: PortfolioDerivativeContractRecord
  rangeKey: PortfolioInstrumentChartRangeKey
  onRangeChange: (rangeKey: PortfolioInstrumentChartRangeKey) => void
  transactions: PortfolioTransactionRecord[]
  transactionsLoading: boolean
  transactionsError: string | null
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
    case 'open': return 'Outstanding'
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
    case 'open': return 'Outstanding'
    default: return state ? formatLabel(state) : 'Unavailable'
  }
}

export default function DerivativeHoldingOverview({
  portfolioId,
  asOfDate,
  baseCurrency,
  holding,
  contract,
  rangeKey,
  onRangeChange,
  transactions,
  transactionsLoading,
  transactionsError,
}: DerivativeHoldingOverviewProps) {
  const { t } = useLanguage()
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
  const [selectedUnderlyingId, setSelectedUnderlyingId] = useState<string | null>(null)
  const couponTransactions = transactions.filter((transaction) => transaction.derivative_contract_id === contract.derivative_contract_id && transaction.transaction_type === 'coupon')
  const paidCoupons = couponTransactions.filter((transaction) => transaction.settlement_date <= asOfDate)
  const couponCashComplete = paidCoupons.every((transaction) => transaction.net_cash_effect != null && transaction.currency === contract.currency)
  const couponCash = transactionsLoading || transactionsError || !couponCashComplete ? null
    : paidCoupons.reduce((total, transaction) => total + Number(transaction.net_cash_effect), 0)
  const couponSummary = contract.contract_type === 'fcn' ? (
    <div className="fcn-income-summary">
      <div><span>{t('Coupons received')}</span><strong className={signedValueClass(couponCash)}>{formatSignedCurrency(couponCash, contract.currency)}</strong><small>{t('Net cash settled through')} {asOfDate}</small></div>
      <div><span>{t('Coupon payments')}</span><strong>{transactionsLoading || transactionsError ? '—' : paidCoupons.length}</strong><small>{t('Confirmed cash records')}</small></div>
      <div><span>{t('Next maturity')}</span><strong>{contract.terms.maturity_date}</strong><small>{t('Final observation')} · {contract.terms.final_observation_date ?? '—'}</small></div>
      {transactionsError && <p className="error-state">{transactionsError}</p>}
    </div>
  ) : null

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

  if (!holding) {
    return (
      <div className="derivative-detail-overview">
        {couponSummary}
        <section className="option-contract-accounting">
          <div className="portfolio-security-section-head">
            <h2>Contract terms</h2>
            <span>No open position</span>
          </div>
          <dl className="derivative-risk-facts">
            <div><dt>Contract currency</dt><dd>{contract.currency}</dd></div>
            <div><dt>External reference</dt><dd>{contract.external_reference || '—'}</dd></div>
            <DerivativeSettlementFacts contract={contract} />
            {contract.contract_type === 'option' ? (
              <>
                <div><dt>Type</dt><dd>{formatLabel(contract.terms.option_type)}</dd></div>
                <div><dt>Strike</dt><dd>{charts[contract.terms.underlying_instrument_id]?.currency
                  ? formatUnitPrice(contract.terms.strike, charts[contract.terms.underlying_instrument_id].currency) : '—'}</dd></div>
                <div><dt>Multiplier</dt><dd>{formatQuantity(contract.terms.contract_multiplier)}</dd></div>
                <div><dt>Expiry</dt><dd>{contract.terms.expiry_date}</dd></div>
              </>
            ) : (
              <>
                <div><dt>Notional</dt><dd>{formatCurrency(contract.terms.notional, contract.currency)}</dd></div>
                <div><dt>Annual coupon</dt><dd>{contract.terms.annual_coupon_rate_pct == null ? '—' : `${formatNumber(contract.terms.annual_coupon_rate_pct, 2)}%`}</dd></div>
                <div><dt>Maturity</dt><dd>{contract.terms.maturity_date}</dd></div>
                <div><dt>Issuer</dt><dd>{contract.terms.issuer || '—'}</dd></div>
              </>
            )}
          </dl>
          <p className="derivative-accounting-note">No exposure or payoff is shown without an open position. Transactions and position lots retain the contract history.</p>
        </section>
        {underlyingIds.map((underlyingId) => (
          <section key={underlyingId} className="portfolio-security-chart-panel">
            <div className="portfolio-security-section-head derivative-chart-heading">
              <h2 translate="no">{charts[underlyingId]?.instrument_core.instrument_name ?? underlyingId}</h2>
              <Link className="portfolio-security-secondary-link" to={`${buildPortfolioHoldingDetailPath(portfolioId, underlyingId)}?as_of_date=${asOfDate}`}>
                Open security detail
              </Link>
            </div>
            <InstrumentPriceChart
              chart={charts[underlyingId] ?? null}
              loading={loading}
              error={errors[underlyingId] ?? null}
              rangeKey={rangeKey}
              onRangeChange={onRangeChange}
              variant="instrument"
              referenceLines={contract.contract_type === 'option' ? [{ label: 'Strike', value: Number(contract.terms.strike), tone: 'strike' }] : []}
            />
          </section>
        ))}
      </div>
    )
  }

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
    const accountingValue = isWritten
      ? holding.liability_value_base ?? holding.liability_value
      : holding.carrying_value_base ?? holding.carrying_value ?? holding.market_value_base ?? holding.market_value
    const accountingCurrency =
      (isWritten ? holding.liability_value_base : holding.carrying_value_base ?? holding.market_value_base) != null
        ? baseCurrency
        : contract.currency
    const premiumBasis = isWritten
      ? holding.premium_basis_remaining
      : holding.cost_basis_base ?? holding.cost_basis
    const premiumBasisCurrency = !isWritten && holding.cost_basis_base != null
      ? baseCurrency
      : contract.currency
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
                { label: 'Strike', value: Number(contract.terms.strike), tone: 'strike' },
              ]}
            />
          </section>
          <aside className="portfolio-security-position-brief">
            <div className="portfolio-security-section-head">
              <div>
                <span className="portfolio-security-section-kicker">Contract risk</span>
                <h2>Current exposure</h2>
              </div>
              {riskPill(t(optionRiskLabel(risk?.risk_state)), warning)}
            </div>
            <dl className="portfolio-security-position-facts">
              <div><dt>Side / type</dt><dd>{isWritten ? 'Written' : 'Long'} {formatLabel(contract.terms.option_type)}</dd></div>
              <div><dt>Strike</dt><dd>{risk?.underlying_quote_currency ? formatUnitPrice(contract.terms.strike, risk.underlying_quote_currency) : '—'}</dd></div>
              <div><dt>Underlying spot</dt><dd>{formatUnitPrice(risk?.underlying_spot, risk?.underlying_quote_currency)}</dd></div>
              <div><dt>Moneyness</dt><dd>{formatPercent(risk?.moneyness_pct)}</dd></div>
              <div><dt>Intrinsic / share</dt><dd>{formatUnitPrice(risk?.intrinsic_value_per_share, risk?.underlying_quote_currency)}</dd></div>
              <div><dt>{t('Position intrinsic value')}</dt><dd>{formatCurrency(risk?.intrinsic_value_per_share == null ? null : risk.intrinsic_value_per_share * Number(contract.terms.contract_multiplier) * (holding.open_contract_quantity ?? Math.abs(holding.quantity)), risk?.underlying_quote_currency)}</dd></div>
              <div><dt>Expiry</dt><dd>{contract.terms.expiry_date} · {formatNumber(risk?.days_to_expiry, 0)} days</dd></div>
              <div><dt>Open contracts</dt><dd>{formatQuantity(holding.open_contract_quantity ?? Math.abs(holding.quantity))}</dd></div>
              <div><dt>Multiplier</dt><dd>{formatQuantity(contract.terms.contract_multiplier)}</dd></div>
              {isWritten && <div><dt>Portfolio backing</dt><dd>{backingLabel}</dd></div>}
              {!isWritten && <div><dt>Maximum loss basis</dt><dd>{formatCurrency(risk?.max_loss_local, contract.currency)}</dd></div>}
            </dl>
            <p className="holding-detail-note">{t('Spot date')} · {risk?.underlying_quote_as_of_date ?? '—'} · {t(formatLabel(risk?.underlying_quote_status ?? 'unavailable'))}</p>
            <p className="holding-detail-note">{t('Intrinsic value excludes time value and is not an option quote.')}</p>
            {risk?.underlying_quote_currency && risk.underlying_quote_currency !== contract.currency && <p className="holding-detail-note">{t('Intrinsic value uses the underlying currency; contract settlement conversion is not included.')}</p>}
            {isWritten && <p className="holding-detail-note">{t('Backing is shared across written options in this portfolio; it is not broker margin or reserved collateral.')}</p>}
          </aside>
        </div>
        <OptionPayoffChart contract={contract} holding={holding} />
        <details className="holding-detail-disclosure">
          <summary>{t('Contract terms')}</summary>
          <dl className="derivative-risk-facts"><DerivativeSettlementFacts contract={contract} /></dl>
        </details>
        <details className="option-contract-accounting holding-detail-disclosure">
          <summary>{t('Accounting and valuation')}</summary>
          <div className="portfolio-security-section-head">
            <div>
              <span className="portfolio-security-section-kicker">Position accounting</span>
              <h2>{isWritten ? 'Written obligation' : 'Long option asset'}</h2>
            </div>
            <span>{formatLabel(holding.valuation_basis ?? 'unavailable')}</span>
          </div>
          <dl className="derivative-risk-facts">
            <div>
              <dt>{isWritten ? 'Remaining premium basis' : 'Open premium cost'}</dt>
              <dd>{formatCurrency(premiumBasis, premiumBasisCurrency)}</dd>
            </div>
            <div>
              <dt>{isWritten ? 'Carrying liability' : 'Carrying value'}</dt>
              <dd>{formatCurrency(accountingValue, accountingCurrency)}</dd>
            </div>
            <div><dt>Strike notional</dt><dd>{holding.strike_notional_base != null
              ? formatCurrency(holding.strike_notional_base, baseCurrency)
              : holding.strike_currency ? formatCurrency(holding.strike_notional, holding.strike_currency) : '—'}</dd></div>
            <div><dt>Fair value status</dt><dd>{formatLabel(holding.fair_value_coverage_status ?? 'unavailable')}</dd></div>
          </dl>
          <p className="holding-detail-note">{t('Carrying basis is not the current option value. Time value and Greeks require an option market quote.')}</p>
        </details>
      </div>
    )
  }

  const risk = holding.fcn_risk
  const warning = !['open', 'knocked_out', 'matured'].includes(risk?.risk_state ?? '')
  const activeUnderlyingId = risk?.underlyings.some((item) => item.instrument_id === selectedUnderlyingId)
    ? selectedUnderlyingId : risk?.delivery_buffer_underlying_instrument_id ?? risk?.underlyings[0]?.instrument_id
  return (
    <div className="derivative-detail-overview fcn-detail-overview">
      {couponSummary}
      <section className="fcn-risk-summary">
        <div className="portfolio-security-section-head">
          <div><span className="portfolio-security-section-kicker">{t('Underlying risk')}</span><h2>{t('FCN current state')}</h2></div>
          {riskPill(t(fcnRiskLabel(risk?.risk_state)), warning)}
        </div>
        <div className="table-shell">
          <table className="holdings-table fcn-monitor-table">
            <thead><tr><th>{t('Underlying')}</th><th>{t('Current')}</th><th>{t('Vs initial')}</th><th>{t('Strike distance')}</th><th>{t('KI distance')}</th><th>{t('KO distance')}</th><th>{t('Spot date')}</th></tr></thead>
            <tbody>{(risk?.underlyings ?? []).map((underlying) => {
              const terms = contract.terms.underlyings.find((item) => item.instrument_id === underlying.instrument_id)
              return <tr key={underlying.instrument_id}>
              <td><button type="button" className="holding-underlying-button" onClick={() => setSelectedUnderlyingId(underlying.instrument_id)} translate="no">{underlying.instrument_name}</button><small>{t(underlying.deliverable ? 'Deliverable underlying' : 'Reference underlying')}</small></td>
              <td>{formatUnitPrice(underlying.spot, underlying.currency)}</td>
              <td className={signedValueClass(underlying.performance_to_reference_pct)}>{formatPercent(underlying.performance_to_reference_pct)}</td>
              <td className={underlying.distance_to_strike_pct != null && underlying.distance_to_strike_pct < 0 ? 'negative-cell' : ''}>{formatPercent(underlying.distance_to_strike_pct)}</td>
              <td className={underlying.distance_to_knock_in_pct != null && underlying.distance_to_knock_in_pct <= 0 ? 'negative-cell' : ''}>{formatPercent(underlying.distance_to_knock_in_pct)}{terms?.knock_in_level_pct != null && <small>{t('KI level')} {formatNumber(terms.knock_in_level_pct, 2)}%</small>}</td>
              <td>{formatPercent(underlying.distance_to_knock_out_pct)}</td>
              <td>{underlying.quote_as_of_date ?? '—'}<small>{t(formatLabel(underlying.quote_status))}</small></td>
            </tr>})}</tbody>
          </table>
        </div>
        <p className="holding-detail-note">{t('Distances = spot / contractual level − 1. Positive means above the level. Missing terms stay blank; these are price distances, not probabilities.')}</p>
        <p className="holding-detail-note">{t('A barrier crossing on this chart does not confirm knock-in, knock-out or stock delivery. Observation rules and confirmed contract events determine the outcome.')}</p>
      </section>
      <div className="holding-detail-segments fcn-underlying-selector" aria-label={t('Choose underlying')}>
        {(risk?.underlyings ?? []).map((underlying) => <button type="button" key={underlying.instrument_id} aria-pressed={underlying.instrument_id === activeUnderlyingId} onClick={() => setSelectedUnderlyingId(underlying.instrument_id)} translate="no">{underlying.instrument_name}</button>)}
      </div>
      <div className="fcn-underlying-chart-list">
        {(risk?.underlyings ?? []).filter((underlying) => underlying.instrument_id === activeUnderlyingId).map((underlying) => (
          <section key={underlying.instrument_id} className="portfolio-security-chart-panel fcn-underlying-chart-panel">
            <div className="portfolio-security-section-head derivative-chart-heading">
              <div>
                <span className="portfolio-security-section-kicker">Underlying risk</span>
                <h2 translate="no">{underlying.instrument_name}</h2>
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
            <p className="holding-detail-note">{t('Current region')} · {t(formatLabel(underlying.current_region))}</p>
            {underlying.missing_terms.length > 0 && <p className="holding-detail-note">{t('Missing terms')}: {underlying.missing_terms.map((term) => t(formatLabel(term))).join(' · ')}</p>}
          </section>
        ))}
      </div>
      <details className="holding-detail-disclosure">
        <summary>{t("Contract terms")}</summary>
        <dl className="derivative-risk-facts">
          <div><dt>Notional</dt><dd>{formatCurrency(contract.terms.notional, contract.currency)}</dd></div>
          <div><dt>Annual coupon</dt><dd>{contract.terms.annual_coupon_rate_pct == null ? '—' : `${formatNumber(contract.terms.annual_coupon_rate_pct, 2)}%`}</dd></div>
          <div><dt>Issue date</dt><dd>{contract.terms.issue_date ?? '—'}</dd></div>
          <div><dt>Final observation</dt><dd>{contract.terms.final_observation_date ?? '—'}</dd></div>
          <div><dt>Maturity</dt><dd>{contract.terms.maturity_date}</dd></div>
          <div><dt>External reference</dt><dd>{contract.external_reference || '—'}</dd></div>
          <div><dt>Issuer</dt><dd>{contract.terms.issuer || '—'}</dd></div>
          <div><dt>Counterparty</dt><dd>{contract.terms.counterparty || '—'}</dd></div>
          <DerivativeSettlementFacts contract={contract} />
        </dl>
      </details>
      <details className="fcn-position-accounting holding-detail-disclosure">
        <summary>{t("Accounting and valuation")}</summary>
        <div className="portfolio-security-section-head">
          <div>
            <span className="portfolio-security-section-kicker">Position accounting</span>
            <h2>Carrying value and valuation coverage</h2>
          </div>
          <span>{formatLabel(holding.valuation_basis ?? 'unavailable')}</span>
        </div>
        <dl className="derivative-risk-facts">
          <div><dt>Carrying value</dt><dd>{formatCurrency(holding.carrying_value_base ?? holding.carrying_value, holding.carrying_value_base != null ? baseCurrency : contract.currency)}</dd></div>
          <div><dt>Historical base basis</dt><dd>{formatCurrency(holding.carrying_value_historical_base, baseCurrency)}</dd></div>
          <div>
            <dt>FX translation</dt>
            <dd className={signedValueClass(holding.carrying_fx_translation_base)}>
              {formatSignedCurrency(holding.carrying_fx_translation_base, baseCurrency)}
            </dd>
          </div>
          <div><dt>Fair value</dt><dd>{formatCurrency(holding.fair_value, contract.currency)}</dd></div>
          <div><dt>Fair value status</dt><dd>{formatLabel(holding.fair_value_coverage_status ?? 'unavailable')}</dd></div>
          <div><dt>Carrying FX coverage</dt><dd>{formatLabel(holding.carrying_fx_coverage_status ?? 'unavailable')}</dd></div>
        </dl>
        {holding.fair_value_coverage_status !== 'complete' ? (
          <p className="derivative-accounting-note">
            Carrying value is an accounting measure and is not presented as current fair value while valuation coverage is incomplete.
          </p>
        ) : null}
      </details>
    </div>
  )
}
