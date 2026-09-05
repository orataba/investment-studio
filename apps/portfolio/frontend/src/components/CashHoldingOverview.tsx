import { Link } from 'react-router'

import {
  type PortfolioPositionHoldingRow,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatSignedCurrency,
  signedValueClass,
} from '../lib/format'
import { buildPortfolioHoldingDetailPath } from '../lib/navigation'

type CashHoldingOverviewProps = {
  portfolioId: string
  asOfDate: string
  baseCurrency: string
  holding: PortfolioPositionHoldingRow
  accountNames: Map<string, string>
}

const PENDING_HOLDING_KINDS = new Set([
  'pending_subscription',
  'settlement_receivable',
  'settlement_payable',
  'position_recognition_adjustment',
])

function monetaryTypeLabel(holdingKind: string | undefined) {
  switch (holdingKind) {
    case 'settled_cash': return 'Settled cash'
    case 'restricted_cash': return 'Restricted cash'
    case 'pending_subscription': return 'Subscription receivable'
    case 'settlement_receivable': return 'Settlement receivable'
    case 'settlement_payable': return 'Settlement payable'
    case 'position_recognition_adjustment': return 'Position recognition adjustment'
    default: return formatLabel(holdingKind ?? 'monetary balance')
  }
}

function coverageTone(coverage: string | null | undefined) {
  return coverage === 'complete' || coverage === 'cash'
    ? 'coverage-pill-live'
    : 'coverage-pill-warning'
}

export default function CashHoldingOverview({
  portfolioId,
  asOfDate,
  baseCurrency,
  holding,
  accountNames,
}: CashHoldingOverviewProps) {
  const isPending = PENDING_HOLDING_KINDS.has(holding.holding_kind ?? '')
  const localCurrency = holding.instrument_core?.currency || baseCurrency
  const localAmount = isPending
    ? holding.settlement_amount ?? holding.market_value
    : holding.market_value
  const reportingAmount = isPending
    ? holding.settlement_amount_base ?? holding.market_value_base
    : holding.market_value_base
  const accountIds = holding.account_ids ?? []
  const accountLabels = accountIds.map((accountId) => accountNames.get(accountId) ?? accountId)
  const economicInstrument = holding.economic_instrument_ref
  const isNegativeCash = !isPending && (localAmount ?? 0) < 0
  const isFinancing = holding.cash_purpose === 'margin' || holding.cash_purpose === 'financing'

  return (
    <div className="cash-detail-overview">
      <section className="cash-detail-summary">
        <div className="portfolio-security-section-head">
          <div>
            <span className="portfolio-security-section-kicker">Monetary position</span>
            <h2>{monetaryTypeLabel(holding.holding_kind)}</h2>
          </div>
          <span className={`coverage-pill ${coverageTone(holding.cost_basis_fx_coverage_status ?? holding.coverage_status)}`}>
            {formatLabel(holding.pending_status ?? holding.coverage_status)}
          </span>
        </div>

        <dl className="cash-detail-facts">
          <div>
            <dt>{isPending ? 'Amount due' : 'Local balance'}</dt>
            <dd>{formatCurrency(localAmount, localCurrency)}</dd>
          </div>
          <div>
            <dt>Reporting value</dt>
            <dd>{formatCurrency(reportingAmount, baseCurrency)}</dd>
          </div>
          <div>
            <dt>Historical FX basis</dt>
            <dd>{formatCurrency(holding.cost_basis_historical_base, baseCurrency)}</dd>
          </div>
          <div>
            <dt>Unrealized FX P/L</dt>
            <dd className={signedValueClass(holding.unrealized_fx_pnl_base)}>
              {formatSignedCurrency(holding.unrealized_fx_pnl_base, baseCurrency)}
            </dd>
          </div>
          <div>
            <dt>Current FX to {baseCurrency}</dt>
            <dd>{formatNumber(holding.fx_rate_to_base, 6)}</dd>
          </div>
          <div>
            <dt>Historical FX rate</dt>
            <dd>{formatNumber(holding.cost_basis_fx_rate_to_base, 6)}</dd>
          </div>
          <div>
            <dt>FX observation</dt>
            <dd>{holding.fx_rate_as_of_date ?? (localCurrency === baseCurrency ? 'Base currency' : '—')}</dd>
          </div>
          <div>
            <dt>Account</dt>
            <dd>{accountLabels.join(', ') || '—'}</dd>
          </div>
        </dl>
      </section>

      <section className="cash-detail-operations">
        <div className="portfolio-security-section-head">
          <div>
            <span className="portfolio-security-section-kicker">Operational state</span>
            <h2>{isPending ? 'Settlement and recognition' : 'Cash availability'}</h2>
          </div>
          <span>As of {asOfDate || '—'}</span>
        </div>
        <dl className="cash-detail-facts cash-detail-operational-facts">
          {holding.cash_purpose ? <div><dt>现金用途</dt><dd>{{ operating: '普通结算', margin: '券商保证金', collateral: '已抵押', financing: '融资负债' }[holding.cash_purpose]}</dd></div> : null}
          {holding.collateral_reference ? <div><dt>抵押确认依据</dt><dd translate="no">{holding.collateral_reference}</dd></div> : null}
          <div>
            <dt>Available for trading</dt>
            <dd>{holding.available_for_trading ? 'Yes' : 'No'}</dd>
          </div>
          <div>
            <dt>Settlement date</dt>
            <dd>{holding.settlement_date ?? 'Not applicable'}</dd>
          </div>
          <div>
            <dt>Position recognition</dt>
            <dd>{holding.pending_until_date ?? holding.monetary_recognition_date ?? 'Not applicable'}</dd>
          </div>
          <div>
            <dt>Linked ledger facts</dt>
            <dd>{formatNumber(holding.transaction_ids?.length ?? 0, 0)}</dd>
          </div>
        </dl>

        {economicInstrument ? (
          <div className="cash-detail-linked-instrument">
            <div>
              <span>Economic exposure</span>
              <strong translate="no">{economicInstrument.instrument_name}</strong>
              <em>{formatLabel(economicInstrument.instrument_type)}</em>
            </div>
            <Link
              className="portfolio-security-secondary-link"
              to={`${buildPortfolioHoldingDetailPath(portfolioId, economicInstrument.instrument_id)}?as_of_date=${asOfDate}`}
            >
              Open linked security
            </Link>
          </div>
        ) : null}

        {isNegativeCash && isFinancing ? <p className="cash-detail-notice">券商融资借方余额计入负债并扣减组合净资产。融资额度、维持保证金和可用购买力须以券商确认为准。</p> : null}
        {isNegativeCash && !isFinancing ? (
          <div className="cash-detail-notice cash-detail-notice-critical" role="alert">
            Negative settled cash is not available capital. Record the missing funding or financing fact.
          </div>
        ) : isPending ? (
          <div className="cash-detail-notice">
            This amount contributes to NAV as a monetary receivable or payable, but it is not settled cash and cannot be traded.
          </div>
        ) : (
          <div className="cash-detail-notice">
            FX translation is shown separately from the local cash balance so reporting-currency gains and losses are not confused with cash movement.
          </div>
        )}
      </section>
    </div>
  )
}
