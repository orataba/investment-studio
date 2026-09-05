import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getPortfolioInstrumentEventTasks, type PortfolioInstrumentEventTaskRecord, type PortfolioPositionLotRecord, type PortfolioTransactionRecord } from '../lib/api'
import { formatCurrency, formatLabel, formatQuantity, formatSignedCurrency, signedValueClass } from '../lib/format'
import { buildPortfolioHoldingDetailPath, buildPortfolioSectionPath } from '../lib/navigation'
import { transactionActivityLabel } from '../lib/transactionPresentation'

export default function HoldingEventsPanel({ portfolioId, holdingId, asOfDate, currency, security, transactions, lots, loading, error }: {
  portfolioId: string
  holdingId: string
  asOfDate: string
  currency: string
  security: boolean
  transactions: PortfolioTransactionRecord[]
  lots: PortfolioPositionLotRecord[]
  loading: boolean
  error: string | null
}) {
  const { t } = useLanguage()
  const [start, setStart] = useState('')
  const [end, setEnd] = useState(asOfDate)
  const [tasks, setTasks] = useState<PortfolioInstrumentEventTaskRecord[]>([])
  const [taskError, setTaskError] = useState<string | null>(null)
  const [taskLoading, setTaskLoading] = useState(security)
  useEffect(() => {
    let cancelled = false
    if (!security) return
    setTaskLoading(true)
    getPortfolioInstrumentEventTasks(portfolioId).then((response) => {
      if (!cancelled) setTasks(response.tasks.filter((task) => task.instrument_id === holdingId))
    }).catch((reason) => { if (!cancelled) setTaskError(reason instanceof Error ? reason.message : 'Failed to load event reviews.') })
      .finally(() => { if (!cancelled) setTaskLoading(false) })
    return () => { cancelled = true }
  }, [portfolioId, holdingId, security])

  const inPeriod = (date: string) => Boolean(date && date <= end && date <= asOfDate && (!start || date >= start))
  const bookedEvents = transactions.filter((transaction) =>
    (transaction.instrument_id === holdingId || transaction.derivative_contract_id === holdingId) &&
    (['dividend', 'coupon', 'return_of_capital', 'maturity_redemption'].includes(transaction.transaction_type) || Boolean(transaction.lifecycle_event_type)),
  ).filter((transaction) => inPeriod(transaction.entitlement_date ?? transaction.economic_date ?? transaction.trade_date))
    .sort((a, b) => (b.entitlement_date ?? b.economic_date ?? b.trade_date).localeCompare(a.entitlement_date ?? a.economic_date ?? a.trade_date))
  const corporateLots = lots.filter((lot) => lot.corporate_action_event && inPeriod(String(lot.corporate_action_event.effective_date ?? lot.opened_at)))
    .sort((a, b) => String(b.corporate_action_event?.effective_date ?? b.opened_at).localeCompare(String(a.corporate_action_event?.effective_date ?? a.opened_at)))
  const visibleTasks = tasks.filter((task) => inPeriod(task.effective_date))
  const ledgerPath = buildPortfolioSectionPath(portfolioId, '/transactions')

  return <section aria-label={t('Income and events')}>
    <div className="portfolio-security-panel-head">
      <div><span className="portfolio-security-section-kicker">{t('Position lifecycle')}</span><h2>{t('Income and events')}</h2><p>{t('Booked dividends, coupons, contract outcomes and applied share changes.')}</p></div>
    </div>
    <div className="holding-period-dates">
      <label>{t('Event date from')}<input type="date" value={start} max={end} onChange={(event) => setStart(event.target.value)} /></label>
      <span aria-hidden="true">→</span>
      <label>{t('Event date to')}<input type="date" value={end} min={start || undefined} max={asOfDate} onChange={(event) => setEnd(event.target.value)} /></label>
    </div>
    {loading ? <div className="empty-state">{t('Loading')}</div> : error ? <div className="error-state" role="alert">{error}</div> : (
      <div className="holding-event-list">
        {bookedEvents.length > 0 && <h3>{t('Booked cash and contract events')}</h3>}
        {bookedEvents.map((transaction) => <article key={transaction.transaction_id}>
          <time>{transaction.entitlement_date ?? transaction.economic_date ?? transaction.trade_date}</time>
          <div><strong>{t(transactionActivityLabel(transaction.transaction_type, transaction.derivative_contract?.contract_type ?? transaction.instrument_ref?.instrument_type, transaction.option_action, transaction.lifecycle_event_type))}</strong>
            <span translate="no">{transaction.account.account_name}</span>
            <span>{t('Settlement')} · {transaction.settlement_date}</span>
            {transaction.note && <p translate="no">{transaction.note}</p>}
          </div>
          <div className="holding-event-amount"><strong className={signedValueClass(transaction.net_cash_effect)}>{formatSignedCurrency(transaction.net_cash_effect, transaction.currency)}</strong><span>{t(transaction.settlement_date > asOfDate ? 'Booked, awaiting settlement' : 'Net cash movement')}</span><Link to={`${ledgerPath}?transaction_id=${encodeURIComponent(transaction.transaction_id)}`}>{t('View transaction')}</Link></div>
        </article>)}
        {corporateLots.length > 0 && <h3>{t('Applied share adjustments')}</h3>}
        {corporateLots.map((lot) => <article key={lot.position_lot_id}>
          <time>{String(lot.corporate_action_event?.effective_date ?? lot.opened_at)}</time>
          <div><strong>{t(formatLabel(String(lot.corporate_action_event?.event_type ?? 'share_adjustment')))}</strong><span>{t('Applied to position lot')}</span><span>{formatQuantity(lot.predecessor_quantity)} → {formatQuantity(lot.entry_quantity)} {t('units')}</span></div>
          <div className="holding-event-amount"><span>{t('Cost carried forward')}</span><strong>{formatCurrency(lot.entry_cost_basis, lot.currency)}</strong><Link to={`${buildPortfolioHoldingDetailPath(portfolioId, holdingId)}?as_of_date=${asOfDate}&detail_tab=lots&position_lot_id=${encodeURIComponent(lot.position_lot_id)}`}>{t('View position lot')}</Link></div>
        </article>)}
        {!bookedEvents.length && !corporateLots.length && <div className="empty-state">{t('No booked events in this date range.')}</div>}
      </div>
    )}
    {security && <section className="holding-event-reviews">
      <div className="portfolio-security-section-head"><h2>{t('Distribution review')}</h2><span>{t('Current review status')}</span></div>
      <p className="holding-detail-note">{t('Source notices and expected entitlements are not booked income. Review status is current, not a historical snapshot.')}</p>
      {taskLoading ? <div className="empty-state">{t('Loading')}</div> : taskError ? <div className="error-state">{taskError}</div> : visibleTasks.length ? <div className="holding-event-list">{visibleTasks.map((task) => <article key={task.instrument_event_task_id}>
        <time>{task.effective_date}</time>
        <div><strong>{t(formatLabel(task.event_type))}</strong><span translate="no">{task.account_id}</span><span className={task.attention_required ? 'negative-cell' : ''}>{t(formatLabel(task.status))}</span>{task.resolution_note && <p translate="no">{task.resolution_note}</p>}</div>
        <div className="holding-event-amount"><span>{t('Expected gross amount')}</span><strong>{formatCurrency(task.expected_gross_amount, currency)}</strong><Link to={buildPortfolioSectionPath(portfolioId, '/holdings')}>{t('Review in holdings')}</Link></div>
      </article>)}</div> : <div className="empty-state">{t('No distribution notices in this date range.')}</div>}
    </section>}
  </section>
}
