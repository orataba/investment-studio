import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getPortfolioPerformanceCalculationGroups, type PortfolioPerformanceCalculationGroupsResponse } from '../lib/api'
import { formatCurrency, formatNumber, formatSignedCurrency, signedValueClass } from '../lib/format'

export default function HoldingPeriodPanel({
  portfolioId, holdingId, asOfDate, eventValued,
}: {
  portfolioId: string
  holdingId: string
  asOfDate: string
  eventValued: boolean
}) {
  const { t } = useLanguage()
  const [params, setParams] = useSearchParams()
  const period = params.get('pnl_period') ?? 'mtd'
  const monthBoundary = new Date(`${asOfDate.slice(0, 7)}-01T00:00:00Z`)
  monthBoundary.setUTCDate(0)
  const startDate = period === 'all' ? '' : period === 'custom'
    ? params.get('pnl_start') ?? ''
    : period === 'ytd' ? `${Number(asOfDate.slice(0, 4)) - 1}-12-31` : monthBoundary.toISOString().slice(0, 10)
  const endDate = period === 'custom' ? params.get('pnl_end') ?? asOfDate : asOfDate
  const [draftStart, setDraftStart] = useState(startDate)
  const [draftEnd, setDraftEnd] = useState(endDate)
  const [result, setResult] = useState<PortfolioPerformanceCalculationGroupsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { setDraftStart(startDate); setDraftEnd(endDate) }, [startDate, endDate])
  useEffect(() => {
    let cancelled = false
    setResult(null)
    setError(null)
    setLoading(true)
    if (endDate > asOfDate || (startDate && startDate > endDate)) {
      setError('Choose a valid period ending on or before the holdings date.')
      setLoading(false)
      return
    }
    getPortfolioPerformanceCalculationGroups(portfolioId, {
      axis: 'instrument', start_date: startDate || undefined, end_date: endDate,
    }).then((response) => { if (!cancelled) setResult(response) })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Failed to load period P/L.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [portfolioId, holdingId, startDate, endDate, asOfDate])

  const row = result?.groups.find((group) => group.group_key === holdingId)
  const summary = result?.summary
  const currency = result?.base_currency ?? ''
  const sum = (values: Array<number | null | undefined>) => values.some((value) => value == null)
    ? null : values.reduce<number>((total, value) => total + Number(value), 0)
  const fx = row ? sum([row.instrument_currency_gains, row.cash_currency_gains, row.pending_settlement_currency_gains ?? 0]) : null
  const expenses = row ? sum([row.expense_cash_amount, row.fees, row.taxes]) : null
  const lines = [
    ['Realized price P/L', row?.realized_capital_gains],
    ['Change in unrealized price P/L', row?.unrealized_pnl_change],
    ['Dividends / coupons', row?.earnings],
    ['FX P/L', fx],
    ['Expenses and taxes', expenses == null ? null : -expenses],
  ] as const

  function selectPeriod(value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current)
      next.set('pnl_period', value)
      if (value === 'custom') {
        if (draftStart) next.set('pnl_start', draftStart); else next.delete('pnl_start')
        next.set('pnl_end', draftEnd)
      } else { next.delete('pnl_start'); next.delete('pnl_end') }
      return next
    })
  }

  return (
    <section className="holding-period-panel" aria-label={t('Period P/L')} aria-busy={loading}>
      <div className="portfolio-security-panel-head">
        <div>
          <span className="portfolio-security-section-kicker">{t('Portfolio result')}</span>
          <h2>{t(eventValued ? 'Recorded contract P/L' : 'Period P/L')}</h2>
          <p>{t('This instrument only. Linked contracts are accounted for separately.')}</p>
        </div>
        <div className="holding-detail-segments" aria-label={t('P/L period')}>
          {(['mtd', 'ytd', 'all'] as const).map((key, index) => (
            <button key={key} type="button" aria-pressed={period === key} onClick={() => selectPeriod(key)}>
              {t(['Month to date', 'Year to date', 'Since inception'][index])}
            </button>
          ))}
        </div>
      </div>
      <form className="holding-period-dates" onSubmit={(event) => { event.preventDefault(); selectPeriod('custom') }}>
        <label>{t('Opening boundary')}<input type="date" aria-label={t('P/L opening boundary')} value={draftStart} max={draftEnd || asOfDate} onChange={(event) => setDraftStart(event.target.value)} /></label>
        <span aria-hidden="true">→</span>
        <label>{t('Closing boundary')}<input type="date" aria-label={t('P/L closing boundary')} required value={draftEnd} min={draftStart || undefined} max={asOfDate} onChange={(event) => setDraftEnd(event.target.value)} /></label>
        <button type="submit" className="secondary-button">{t('Apply period')}</button>
      </form>
      {loading ? <div className="empty-state" role="status">{t('Loading period P/L…')}</div> : error ? <div className="error-state" role="alert">{t(error)}</div> : !row ? (
        <div className="empty-state">{t('No attributable position activity in this period.')}</div>
      ) : (
        <>
          <div className="holding-period-result">
            <div className="holding-period-total">
              <span>{t(eventValued ? 'Recorded net P/L' : 'Net period P/L')} · {currency}</span>
              <strong className={signedValueClass(row.total_pnl)}>{formatSignedCurrency(row.total_pnl, currency)}</strong>
              <span>{summary?.effective_start_date ?? summary?.start_date} → {summary?.effective_end_date ?? summary?.end_date}</span>
              {!eventValued && <div className="holding-period-contribution"><span>{t('Contribution to portfolio return')}</span><b>{formatNumber(row.period_contribution == null ? null : row.period_contribution * 100, 3)} {t('percentage points')}</b></div>}
            </div>
            <dl className="holding-pnl-breakdown">
              {lines.map(([label, value]) => <div key={label}><dt>{t(label)}</dt><dd className={signedValueClass(value)}>{formatSignedCurrency(value, currency)}</dd></div>)}
            </dl>
          </div>
          <div className="holding-period-boundaries">
            <span>{t('Opening position value')} <strong>{formatCurrency(row.initial_value, currency)}</strong></span>
            <span>{t('Closing position value')} <strong>{formatCurrency(row.final_value, currency)}</strong></span>
          </div>
          <p className="holding-detail-note">{t(summary?.include_start_date_return
            ? 'The opening day is included, using the portfolio opening boundary.'
            : 'P/L runs from the opening close to the closing close; opening-day activity is excluded.')}</p>
          <p className="holding-detail-note">{t('Unrealized change is the movement during this period, not the current unrealized balance. Purchases and sales change position value without being profit.')}</p>
          {eventValued && <p className="derivative-accounting-note">{t('Contract-wide accounting result, including long and written positions. Unquoted fair-value changes are unavailable; recorded income is not a mark-to-market return.')}</p>}
        </>
      )}
    </section>
  )
}
