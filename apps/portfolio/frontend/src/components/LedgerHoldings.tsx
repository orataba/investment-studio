import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getPortfolioAccountsWorkspace, getPortfolioPositions, type PortfolioAccountsWorkspaceResponse, type PortfolioPositionListResponse } from '../lib/api'
import { formatCurrency, formatNumber } from '../lib/format'
import CalculationStatus from './CalculationStatus'

/** Confirmed quantities and local cash remain useful when portfolio valuation cannot publish. */
export default function LedgerHoldings({ portfolioId, asOfDate }: { portfolioId: string; asOfDate?: string }) {
  const zh = useLanguage().language.startsWith('zh')
  const date = asOfDate || new Date().toLocaleDateString('en-CA')
  const [positions, setPositions] = useState<PortfolioPositionListResponse | null>(null)
  const [accounts, setAccounts] = useState<PortfolioAccountsWorkspaceResponse | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    setPositions(null); setAccounts(null); setErrors([]); setLoading(true)
    Promise.allSettled([
      getPortfolioPositions(portfolioId, date, controller.signal, false),
      getPortfolioAccountsWorkspace(portfolioId, undefined, controller.signal, date, false),
    ]).then(([positionResult, accountResult]) => {
      if (controller.signal.aborted) return
      if (positionResult.status === 'fulfilled') setPositions(positionResult.value)
      if (accountResult.status === 'fulfilled') setAccounts(accountResult.value)
      setErrors([positionResult, accountResult].flatMap((result) => result.status === 'rejected' ? [String(result.reason?.message ?? result.reason)] : []))
      setLoading(false)
    })
    return () => controller.abort()
  }, [portfolioId, date])
  const names = new Map(accounts?.accounts.map((row) => [row.account.account_id, row.account.account_name]))
  return <section className="portfolio-section-block" aria-label={zh ? '已确认账本' : 'Confirmed ledger'}>
    <h2>{zh ? '已确认账本' : 'Confirmed ledger'} · {date}</h2>
    <p className="portfolio-detail-meta">{zh ? '数量、账面成本与原币现金来自已记录交易，不要求已发布组合净值。下表不构成当前 NAV；待结算现金单列，市场值与收益待估值完成。' : 'Quantities, book cost and local cash come from recorded transactions. This table is not current NAV. Pending cash is separate; market values and returns await valuation.'}</p>
    {loading ? <CalculationStatus /> : null}
    {errors.map((message) => <p className="inline-notice inline-notice-error" key={message}>{message}</p>)}
    {positions ? <HorizontalTableScroll className="table-shell"><table className="transactions-table">
      <thead><tr><th>{zh ? '证券 / 合约' : 'Security / Contract'}</th><th>{zh ? '数量' : 'Quantity'}</th><th>{zh ? '成本（原币）' : 'Book cost (local)'}</th><th>{zh ? '账户' : 'Account'}</th></tr></thead>
      <tbody>{positions.positions.map((row) => <tr key={row.position_id}><td translate="no">{row.instrument_ref?.instrument_name ?? row.derivative_contract?.contract_name ?? row.position_reference_id}</td><td>{formatNumber(row.quantity, 6)}</td><td>{formatCurrency(row.cost_basis, row.currency)}</td><td translate="no">{row.account_ids.map((id) => names.get(id) ?? id).join(', ')}</td></tr>)}{!positions.positions.length ? <tr><td colSpan={4}>{zh ? '此日无开放证券或多头合约仓位。期权义务请查看账户。' : 'No open security or long contract position on this date. See Accounts for option obligations.'}</td></tr> : null}</tbody>
    </table></HorizontalTableScroll> : null}
    {accounts ? <HorizontalTableScroll className="table-shell"><table className="transactions-table">
      <thead><tr><th>{zh ? '现金账户' : 'Cash account'}</th><th>{zh ? '币种' : 'Currency'}</th><th>{zh ? '已结算余额' : 'Settled balance'}</th><th>{zh ? '待结算净额' : 'Pending settlement'}</th></tr></thead>
      <tbody>{accounts.accounts.filter((row) => row.account.account_type === 'deposit_account' || row.derived_cash_balance !== 0 || row.pending_settlement !== 0).map((row) => <tr key={row.account.account_id}><td translate="no">{row.account.account_name}</td><td>{row.account.currency}</td><td>{formatCurrency(row.derived_cash_balance, row.account.currency)}</td><td>{formatCurrency(row.pending_settlement, row.account.currency)}</td></tr>)}</tbody>
    </table></HorizontalTableScroll> : null}
    <p><Link to={`/portfolios/${portfolioId}/transactions`}>{zh ? '查看交易事实' : 'View recorded transactions'}</Link> · <Link to={`/portfolios/${portfolioId}/accounts`}>{zh ? '查看账户与义务' : 'View accounts and obligations'}</Link></p>
  </section>
}
