import { Link } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { buildPortfolioHoldingDetailPath } from '../lib/navigation'
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
  portfolioId: string
  asOfDate: string
  optionLots: PortfolioPositionLotRecord[]
  optionObligations: PortfolioOptionObligationRecord[]
  optionContracts: Array<Extract<PortfolioDerivativeContractRecord, { contract_type: 'option' }>>
  transactions: PortfolioTransactionRecord[]
}

export default function SecurityLinkedOptionsPanel({
  portfolioId,
  asOfDate,
  optionLots,
  optionObligations,
  optionContracts,
  transactions,
}: SecurityLinkedOptionsPanelProps) {
  const { t } = useLanguage()
  const activeContracts = optionContracts.filter((contract) => (transactions.some((transaction) => transaction.derivative_contract_id === contract.derivative_contract_id) || optionLots.some((lot) => lot.derivative_contract_id === contract.derivative_contract_id) || optionObligations.some((obligation) => obligation.derivative_contract_id === contract.derivative_contract_id)))
  if (!activeContracts.length) return null

  return (
    <section className="security-linked-options-panel">
      <div className="portfolio-security-section-head">
        <div>
          <span className="portfolio-security-section-kicker">{t("Separate contract accounting")}</span>
          <h2>{t("Linked option results")}</h2>
        </div>
        <span>Through {asOfDate || '—'}</span>
      </div>
      <div className="security-result-grid">
        <article>
            <div className="linked-option-contract-list">
              {activeContracts.map((contract) => {
                const lots = optionLots.filter(
                  (row) => row.derivative_contract_id === contract.derivative_contract_id,
                )
                const obligations = optionObligations.filter(
                  (row) => row.derivative_contract_id === contract.derivative_contract_id,
                )
                const realized =
                  lots.reduce((sum, row) => sum + row.realized_pnl, 0) +
                  obligations.reduce((sum, row) => sum + (row.realized_pnl ?? 0), 0)
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
                      <Link translate="no" to={`${buildPortfolioHoldingDetailPath(portfolioId, contract.derivative_contract_id)}?as_of_date=${asOfDate}`}>{contract.contract_name}</Link>
                      <span>
                        {formatNumber(transactionCount, 0)} transactions · {contract.terms.option_type} · expiry {contract.terms.expiry_date}
                      </span>
                    </div>
                    <dl>
                      <div><dt>Realized</dt><dd className={signedValueClass(realized)}>{formatSignedCurrency(realized, contract.currency)}</dd></div>
                      {lots.some((lot) => lot.remaining_quantity > 0) && <div><dt>{t("Long fair-value P/L")}</dt><dd>{t("Unavailable")}</dd></div>}
                      <div><dt>Open written liability</dt><dd>{formatCurrency(obligations.length ? openLiability : null, contract.currency)}</dd></div>
                    </dl>
                  </div>
                )
              })}
            </div>
        </article>
      </div>
    </section>
  )
}
