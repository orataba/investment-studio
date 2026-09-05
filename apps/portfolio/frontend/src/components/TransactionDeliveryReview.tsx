import type { PortfolioAccountRecord, PortfolioFeeCategory, PortfolioTransactionCreatePayload, SharedInstrumentRecord } from '../lib/api'
import { formatLabel } from '../lib/format'
import RegistryInstrumentPicker from './RegistryInstrumentPicker'

type DeliveryFields = Pick<PortfolioTransactionCreatePayload, 'asset_deliveries' | 'option_delivery' | 'lot_selections'>

export function TransactionDeliveryReview({ record, accounts, instruments = [], canAddAssetDelivery = false, canSelectLots = false, requireOptionDelivery = false, onChange }: {
  record: DeliveryFields
  accounts: PortfolioAccountRecord[]
  instruments?: SharedInstrumentRecord[]
  canAddAssetDelivery?: boolean
  canSelectLots?: boolean
  requireOptionDelivery?: boolean
  onChange?: (changes: DeliveryFields) => void
}) {
  const optionDelivery = record.option_delivery ?? (requireOptionDelivery ? { stock_account_id: '', settlement_cash_account_id: '' } : undefined)
  const accountName = (id: string) => accounts.find(account => account.account_id === id)?.account_name ?? id
  if (!record.asset_deliveries?.length && !optionDelivery && !record.lot_selections?.length && !(onChange && (canAddAssetDelivery || canSelectLots))) return null
  return <fieldset className="transaction-capture-review-wide">
    <legend>关联交付与指定批次</legend>
    {(record.asset_deliveries ?? []).map((leg, index) => {
      const update = (changes: Partial<typeof leg>) => onChange?.({ asset_deliveries: record.asset_deliveries?.map((item, row) => row === index ? { ...item, ...changes } : item) })
      return <div key={index} className="transaction-capture-review-grid">
        {onChange ? <>
          <label><span>交付 {index + 1} 接收账户</span><select value={leg.account_id} onChange={event => update({ account_id: event.target.value })}>
            <option value="">选择账户</option>
            {accounts.filter(account => account.account_category === 'security').map(account => <option key={account.account_id} value={account.account_id}>{account.account_name} · {account.currency}</option>)}
          </select></label>
          <RegistryInstrumentPicker label={`交付 ${index + 1} 证券`} value={leg.instrument_id} instruments={instruments.filter(instrument => ['equity', 'etf'].includes(instrument.instrument_type))} onSelect={instrument_id => update({ instrument_id })} />
        </> : <strong>{accountName(leg.account_id)} · {leg.instrument_id}</strong>}
        {(['quantity', 'fair_value', 'currency', 'fx_rate_to_contract'] as const).map(field => <label key={field}>
          <span>{{ quantity: '收股数量', fair_value: '该腿总公允价值', currency: '证券币种', fx_rate_to_contract: '合约币种 / 证券币种汇率' }[field]}</span>
          <input value={leg[field]} readOnly={!onChange} onChange={event => update({ [field]: event.target.value })} />
        </label>)}
        {onChange && <button type="button" onClick={() => onChange({ asset_deliveries: record.asset_deliveries?.filter((_, row) => row !== index) })}>移除交付 {index + 1}</button>}
      </div>
    })}
    {onChange && canAddAssetDelivery && <button type="button" onClick={() => onChange({ asset_deliveries: [...(record.asset_deliveries ?? []), { account_id: '', instrument_id: '', quantity: '', fair_value: '', currency: '', fx_rate_to_contract: '' }] })}>添加交付证券</button>}
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
