import type {
  PortfolioDerivativeContractRecord,
  PortfolioOptionObligationRecord,
  PortfolioPositionLotRecord,
  PortfolioTransactionRecord,
} from '../lib/api'
import {
  formatCurrency,
  formatNumber,
  formatSignedCurrency,
  signedValueClass,
} from '../lib/format'

type SecurityLinkedOptionsPanelProps = {
  asOfDate: string
  securityLots: PortfolioPositionLotRecord[]
  optionLots: PortfolioPositionLotRecord[]
  optionObligations: PortfolioOptionObligationRecord[]
  optionContracts: Array<Extract<PortfolioDerivativeContractRecord, { contract_type: 'option' }>>
  transactions: PortfolioTransactionRecord[]
}

function completeSum(
  rows: PortfolioPositionLotRecord[],
  value: (row: PortfolioPositionLotRecord) => number | null | undefined,
) {
  if (!rows.length || rows.some((row) => value(row) == null)) return null
  return rows.reduce((sum, row) => sum + Number(value(row)), 0)
}

function lotCurrency(rows: PortfolioPositionLotRecord[]) {
  const currencies = new Set(rows.map((row) => row.currency).filter(Boolean))
  return currencies.size === 1 ? [...currencies][0] : null
}

export default function SecurityLinkedOptionsPanel({
  asOfDate,
  securityLots,
  optionLots,
  optionObligations,
  optionContracts,
  transactions,
}: SecurityLinkedOptionsPanelProps) {
  const securityCurrency = lotCurrency(securityLots)
  const securityRealized = securityLots.length
    ? securityLots.reduce((sum, row) => sum + row.realized_pnl, 0)
    : null
  const securityUnrealized = completeSum(
    securityLots.filter((row) => row.status === 'open'),
    (row) => row.unrealized_pnl,
  )
  const securityIncome = securityLots.length
    ? securityLots.reduce(
        (sum, row) => sum + row.income_cash_amount - row.expense_cash_amount,
        0,
      )
    : null

  return (
    <section className="security-linked-options-panel">
      <div className="portfolio-security-section-head">
        <div>
          <span className="portfolio-security-section-kicker">Position result</span>
          <h2>Security and linked options</h2>
        </div>
        <span>Through {asOfDate || '—'}</span>
      </div>
      <div className="security-result-grid">
        <article>
          <strong>Security itself</strong>
          <dl className="derivative-risk-facts">
            <div>
              <dt>Realized P&amp;L</dt>
              <dd className={signedValueClass(securityRealized)}>{formatSignedCurrency(securityRealized, securityCurrency ?? '')}</dd>
            </div>
            <div>
              <dt>Unrealized P&amp;L</dt>
              <dd className={signedValueClass(securityUnrealized)}>{formatSignedCurrency(securityUnrealized, securityCurrency ?? '')}</dd>
            </div>
            <div>
              <dt>Net distributions</dt>
              <dd className={signedValueClass(securityIncome)}>{formatSignedCurrency(securityIncome, securityCurrency ?? '')}</dd>
            </div>
          </dl>
        </article>
        <article>
          <strong>Linked options</strong>
          {optionContracts.length ? (
            <div className="linked-option-contract-list">
              {optionContracts.map((contract) => {
                const lots = optionLots.filter(
                  (row) => row.derivative_contract_id === contract.derivative_contract_id,
                )
                const obligations = optionObligations.filter(
                  (row) => row.derivative_contract_id === contract.derivative_contract_id,
                )
                const realized =
                  lots.reduce((sum, row) => sum + row.realized_pnl, 0) +
                  obligations.reduce((sum, row) => sum + (row.realized_pnl ?? 0), 0)
                const unrealized = completeSum(
                  lots.filter((row) => row.status === 'open'),
                  (row) => row.unrealized_pnl,
                )
                const openLiability = obligations.reduce(
                  (sum, row) => sum + row.carrying_liability,
                  0,
                )
                const transactionCount = transactions.filter(
                  (transaction) =>
                    transaction.derivative_contract_id === contract.derivative_contract_id,
                ).length
                return (
                  <div key={contract.derivative_contract_id} className="linked-option-contract-row">
                    <div>
                      <strong>{contract.contract_name}</strong>
                      <span>
                        {formatNumber(transactionCount, 0)} transactions · {contract.terms.option_type} · expiry {contract.terms.expiry_date}
                      </span>
                    </div>
                    <dl>
                      <div><dt>Realized</dt><dd className={signedValueClass(realized)}>{formatSignedCurrency(realized, contract.currency)}</dd></div>
                      <div><dt>Long unrealized</dt><dd className={signedValueClass(unrealized)}>{formatSignedCurrency(unrealized, contract.currency)}</dd></div>
                      <div><dt>Open written liability</dt><dd>{formatCurrency(openLiability || null, contract.currency)}</dd></div>
                    </dl>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="empty-state">No option contracts linked to this security.</div>
          )}
        </article>
      </div>
    </section>
  )
}
