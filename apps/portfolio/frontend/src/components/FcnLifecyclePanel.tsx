import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getPortfolioFcnLifecycles } from '../lib/api'
import type { FcnLifecyclesResponse } from '../lib/fcnLifecycleApi'
import { formatCurrency, formatQuantity, formatSignedCurrency, signedValueClass } from '../lib/format'
import { buildPortfolioHoldingDetailPath, buildPortfolioSectionPath } from '../lib/navigation'

export default function FcnLifecyclePanel({ portfolioId, positionReferenceId, asOfDate }: {
  portfolioId: string
  positionReferenceId: string
  asOfDate: string
}) {
  const { t } = useLanguage()
  const [response, setResponse] = useState<FcnLifecyclesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  function warningText(message: string) {
    const fx = message.match(/^Missing (\w+)\/(\w+) FX on ([\d-]+)$/)
    if (fx) return t('Missing {source}/{target} FX on {date}', { source: fx[1], target: fx[2], date: fx[3] })
    if (message.startsWith('Missing valuation for ')) return t('Missing valuation for {instrument}', { instrument: message.slice('Missing valuation for '.length) })
    return t(message)
  }
  useEffect(() => {
    let cancelled = false
    setResponse(null)
    setError(null)
    getPortfolioFcnLifecycles(portfolioId, positionReferenceId, asOfDate)
      .then((result) => { if (!cancelled) setResponse(result) })
      .catch((failure: unknown) => { if (!cancelled) setError(failure instanceof Error ? failure.message : t('Failed to load FCN lifecycle.')) })
    return () => { cancelled = true }
  }, [portfolioId, positionReferenceId, asOfDate, t])

  if (error) return <p className="form-error">{t('Failed to load FCN lifecycle.')} {error}</p>
  if (!response?.lifecycles.length) return null
  return (
    <section className="security-linked-options-panel fcn-lifecycle-panel">
      <div className="portfolio-security-section-head">
        <div><span className="portfolio-security-section-kicker">{t('FCN → delivered shares')}</span><h2>{t('FCN lifecycle results')}</h2></div>
        <span>{asOfDate}</span>
      </div>
      <p className="holding-detail-note">{t('Results use the FCN currency, including subsequent stock FX. Delivered share costs include acquisition charges. Share quantities and proceeds follow the original acquisition quantities through partial sales and transfers.')}</p>
      {response.lifecycles.map((lifecycle) => (
        <article className="fcn-lifecycle-contract" key={lifecycle.derivative_contract_id}>
          <div className="portfolio-security-section-head">
            <h3><Link translate="no" to={`${buildPortfolioHoldingDetailPath(portfolioId, lifecycle.derivative_contract_id)}?as_of_date=${asOfDate}`}>{lifecycle.contract_name}</Link></h3>
            <span>{t(lifecycle.contract_status === 'closed' ? 'Closed' : 'Open')} · {lifecycle.currency}</span>
          </div>
          <dl className="derivative-risk-facts">
            {([
              ['FCN phase P/L', lifecycle.contract_pnl],
              ['Delivered stock phase P/L', lifecycle.stock_pnl],
              ['Whole investment P/L', lifecycle.total_pnl],
            ] as const).map(([label, value]) => <div key={label}><dt>{t(label)}</dt><dd className={signedValueClass(value)}>{formatSignedCurrency(value, lifecycle.currency)}</dd></div>)}
          </dl>
          <details className="holding-detail-disclosure">
            <summary>{t('Income and realized results')}</summary>
            <dl className="derivative-risk-facts">
              {([
                ['FCN disposal P/L', lifecycle.contract_disposal_pnl],
                ['Coupon income', lifecycle.coupon_income],
                ['Contract charges', lifecycle.contract_charges],
                ['Stock realized P/L', lifecycle.stock_realized_pnl],
                ['Stock unrealized P/L', lifecycle.stock_unrealized_pnl],
                ['Stock income net of charges', lifecycle.stock_income],
              ] as const).map(([label, value]) => <div key={label}><dt>{t(label)}</dt><dd>{label === 'Contract charges' ? formatCurrency(value, lifecycle.currency) : formatSignedCurrency(value, lifecycle.currency)}</dd></div>)}
            </dl>
          </details>
          {lifecycle.deliveries.length > 0 && <div className="table-shell portfolio-security-table-shell">
            <table className="holdings-table"><caption>{t('Delivery status')}</caption><thead><tr><th>{t('Security')}</th><th>{t('Quantity')}</th><th>{t('Economic effective date')}</th><th>{t('Delivery date')}</th><th>{t('Status')}</th><th>{t('Acquisition charges')}</th></tr></thead>
              <tbody>{lifecycle.deliveries.map((delivery, index) => <tr key={`${delivery.transaction_id}-${index}`}>
                <td><Link translate="no" to={`${buildPortfolioHoldingDetailPath(portfolioId, delivery.instrument_id)}?as_of_date=${asOfDate}`}>{delivery.instrument_name}</Link></td>
                <td>{formatQuantity(delivery.quantity)}</td><td>{delivery.effective_date}</td><td>{delivery.delivery_date ?? '—'}</td>
                <td><Link to={`${buildPortfolioSectionPath(portfolioId, '/transactions')}?transaction_id=${encodeURIComponent(delivery.transaction_id)}`}>{t(delivery.status === 'unknown' ? 'Delivery date not recorded' : delivery.status === 'pending' ? 'Pending delivery' : 'Delivered')}</Link></td>
                <td>{formatCurrency(delivery.capitalized_charges, delivery.currency)}</td>
              </tr>)}</tbody>
            </table>
          </div>}
          {lifecycle.deliveries.some((delivery) => delivery.status === 'pending') && <p className="holding-detail-note">{t('Pending shares already carry market risk; they are not yet available for sale.')}</p>}
          {lifecycle.stocks.length > 0 && <div className="table-shell portfolio-security-table-shell">
            <table className="holdings-table"><caption>{t('Shares attributed to this FCN')}</caption><thead><tr><th>{t('Security')}</th><th>{t('Remaining quantity')}</th><th>{t('Sold quantity')}</th><th>{t('Market value')} · {lifecycle.currency}</th><th>{t('Realized P/L')} · {lifecycle.currency}</th><th>{t('Unrealized P/L')} · {lifecycle.currency}</th></tr></thead>
              <tbody>{lifecycle.stocks.map((stock) => <tr key={`${stock.account_id}-${stock.instrument_id}`}>
                <td><Link translate="no" to={`${buildPortfolioHoldingDetailPath(portfolioId, stock.instrument_id)}?as_of_date=${asOfDate}`}>{stock.instrument_name}</Link><small className="portfolio-detail-meta" style={{ display: 'block', marginTop: 4 }} translate="no">{stock.account_name}</small></td>
                <td>{formatQuantity(stock.remaining_quantity)}</td><td>{formatQuantity(stock.realized_quantity)}</td><td>{formatCurrency(stock.current_market_value, lifecycle.currency)}</td>
                <td className={signedValueClass(stock.realized_pnl)}>{formatSignedCurrency(stock.realized_pnl, lifecycle.currency)}</td><td className={signedValueClass(stock.unrealized_pnl)}>{formatSignedCurrency(stock.unrealized_pnl, lifecycle.currency)}</td>
              </tr>)}</tbody>
            </table>
          </div>}
          {lifecycle.warnings.length > 0 && <p className="form-error">{t('Lifecycle result is incomplete:')} {lifecycle.warnings.map(warningText).join(' · ')}</p>}
        </article>
      ))}
    </section>
  )
}
