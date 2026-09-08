import type { PortfolioAccountRecord, PortfolioDerivativeContractRecord, PortfolioFeeCategory, PortfolioTransactionCreatePayload, SharedInstrumentRecord } from '../lib/api'
import { formatLabel } from '../lib/format'
import { FcnSettlementReview } from './FcnSettlementReview'

type DeliveryFields = Pick<PortfolioTransactionCreatePayload, 'asset_deliveries' | 'settlement_cashflows' | 'option_delivery' | 'lot_selections'> & Partial<Pick<PortfolioTransactionCreatePayload, 'currency' | 'trade_date' | 'position_effective_date' | 'settlement_date' | 'settlement_cash_account_id' | 'derivative_contract_id'>> & { gross_amount?: string | number | null }

export function TransactionDeliveryReview({ record, accounts, instruments = [], contracts = [], portfolioId, canAddAssetDelivery = false, canSettleFcn = false, canSelectLots = false, requireOptionDelivery = false, onChange }: {
  record: DeliveryFields
  portfolioId?: string
  contracts?: PortfolioDerivativeContractRecord[]
  accounts: PortfolioAccountRecord[]
  instruments?: SharedInstrumentRecord[]
  canAddAssetDelivery?: boolean
  canSettleFcn?: boolean
  canSelectLots?: boolean
  requireOptionDelivery?: boolean
  onChange?: (changes: DeliveryFields) => void
}) {
  const fcnContract = contracts.find(contract => contract.derivative_contract_id === record.derivative_contract_id && contract.contract_type === 'fcn')
  const optionDelivery = record.option_delivery ?? (requireOptionDelivery ? { stock_account_id: '', settlement_cash_account_id: '' } : undefined)
  if (!record.asset_deliveries?.length && !record.settlement_cashflows?.length && !optionDelivery && !record.lot_selections?.length && !(onChange && (canAddAssetDelivery || canSettleFcn || canSelectLots))) return null
  return <fieldset className="transaction-capture-review-wide">
    <legend>关联交付与指定批次</legend>
    {(canAddAssetDelivery || canSettleFcn || record.asset_deliveries?.length || record.settlement_cashflows?.length) ? <FcnSettlementReview
      allowAssetDelivery={canAddAssetDelivery}
      record={record} accounts={accounts} instruments={instruments} currency={record.currency}
      portfolioId={portfolioId} contractId={record.derivative_contract_id}
      terms={fcnContract?.contract_type === 'fcn' ? fcnContract.terms : undefined}
      economicDate={record.position_effective_date || record.trade_date} settlementDate={record.settlement_date ?? undefined}
      residualCash={record.gross_amount ?? undefined} residualAccountId={record.settlement_cash_account_id}
      onChange={onChange}
    /> : null}
    {optionDelivery ? <div className="transaction-capture-review-grid">
      <strong>期权与股票腿将一起入账；不另记股票交付。</strong>
      {(['stock_account_id', 'settlement_cash_account_id'] as const).map(field => <label key={field}><span>{field === 'stock_account_id' ? '交付证券账户' : '交付现金账户'}</span>
        <select disabled={!onChange} value={optionDelivery?.[field]} onChange={event => onChange?.({ option_delivery: { ...optionDelivery!, [field]: event.target.value } })}>
          <option value="">选择账户</option>
          {accounts.filter(account => account.account_category === (field === 'stock_account_id' ? 'security' : 'cash')).map(account => <option key={account.account_id} value={account.account_id}>{account.account_name} · {account.currency}</option>)}
        </select></label>)}
      {(['fees', 'taxes'] as const).map(field => <label key={field}><span>{field === 'fees' ? '交付费用' : '交付税费'}</span><input value={optionDelivery?.[field] ?? 0} readOnly={!onChange} onChange={event => onChange?.({ option_delivery: { ...optionDelivery!, [field]: event.target.value, ...(field === 'fees' && Number(event.target.value) === 0 ? { fee_category: 'unknown' as const } : {}) } })} /></label>)}
      <label><span>交付费用分类</span><select disabled={!onChange || !Number(optionDelivery.fees)} value={optionDelivery.fee_category ?? 'unknown'} onChange={event => onChange?.({ option_delivery: { ...optionDelivery!, fee_category: event.target.value as PortfolioFeeCategory } })}>
        {(['unknown', 'transaction_cost', 'management_fee', 'custody_fee', 'administration_fee', 'performance_fee', 'financing_interest', 'borrow_fee', 'payment_in_lieu', 'other'] as const).map(category => <option key={category} value={category}>{formatLabel(category)}</option>)}
      </select></label>
      <label><input type="checkbox" checked={Boolean(optionDelivery.allow_stock_short)} disabled={!onChange} onChange={event => onChange?.({ option_delivery: { ...optionDelivery!, allow_stock_short: event.target.checked } })} />券商已确认交付时形成证券空头</label>
    </div> : null}
    {record.lot_selections?.map((item, index) => onChange ? <div key={index} className="transaction-capture-review-grid">
      <label><span>批次 {index + 1} 开仓记录</span><input value={item.opening_transaction_id} onChange={event => onChange({ lot_selections: record.lot_selections?.map((lot, row) => row === index ? { ...lot, opening_transaction_id: event.target.value } : lot) })} /></label>
      <label><span>批次 {index + 1} 处置数量</span><input type="number" min="0" step="any" value={item.quantity} onChange={event => onChange({ lot_selections: record.lot_selections?.map((lot, row) => row === index ? { ...lot, quantity: event.target.value } : lot) })} /></label>
      <button type="button" onClick={() => onChange({ lot_selections: record.lot_selections?.filter((_, row) => row !== index) })}>移除批次 {index + 1}</button>
    </div> : <p key={index}>开仓记录 {item.opening_transaction_id}：本次处置 {item.quantity}</p>)}
    {onChange && canSelectLots && <button type="button" onClick={() => onChange({ lot_selections: [...(record.lot_selections ?? []), { opening_transaction_id: '', quantity: '' }] })}>添加指定批次</button>}
  </fieldset>
}
