import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getPortfolioTransactions } from '../lib/api'
import type { PortfolioTransactionRecord, PortfolioAccountRecord, PortfolioAssetDelivery, PortfolioFeeCategory, PortfolioFcnContractTerms, PortfolioSettlementCashflow, SharedInstrumentRecord } from '../lib/api'
import { formatCurrency, formatLabel, formatQuantity } from '../lib/format'
import SecurityInstrumentPicker, { primaryIdentifier } from './SecurityInstrumentPicker'

export type FcnSettlementFields = {
  asset_deliveries?: PortfolioAssetDelivery[]
  settlement_cashflows?: PortfolioSettlementCashflow[]
}

const feeCategories: PortfolioFeeCategory[] = ['unknown', 'transaction_cost', 'management_fee', 'custody_fee', 'administration_fee', 'performance_fee', 'financing_interest', 'borrow_fee', 'payment_in_lieu', 'other']

export function FcnSettlementReview({ record, accounts, instruments, currency = '', terms, economicDate, settlementDate, residualCash, residualAccountId, portfolioId, contractId, excludedTransactionId, allowAssetDelivery = true, onChange }: {
  record: FcnSettlementFields
  allowAssetDelivery?: boolean
  portfolioId?: string
  contractId?: string | null
  excludedTransactionId?: string | null
  accounts: PortfolioAccountRecord[]
  instruments: SharedInstrumentRecord[]
  currency?: string
  terms?: PortfolioFcnContractTerms
  economicDate?: string
  settlementDate?: string
  residualCash?: number | string
  residualAccountId?: string | null
  onChange?: (changes: FcnSettlementFields) => void
}) {
  const { language, t } = useLanguage()
  const label = (en: string, zh: string) => language === 'zh-Hans' ? zh : en
  const feeCategoryLabel = (category: PortfolioFeeCategory) => language === 'zh-Hans'
    ? ({ financing_interest: '融资利息', borrow_fee: '融券费用', payment_in_lieu: '空头股息补偿' } as Partial<Record<PortfolioFeeCategory, string>>)[category] ?? t(formatLabel(category))
    : formatLabel(category)
  const [coupons, setCoupons] = useState<PortfolioTransactionRecord[]>([])
  const [couponStatus, setCouponStatus] = useState<'loading' | 'ready' | 'error'>('ready')
  const showCouponHistory = Boolean(onChange && portfolioId && contractId)
  useEffect(() => {
    if (!showCouponHistory || !portfolioId || !contractId) return
    let cancelled = false
    setCouponStatus('loading')
    getPortfolioTransactions(portfolioId, { position_reference_id: contractId })
      .then(response => { if (!cancelled) { setCoupons(response.transactions.filter(transaction => transaction.transaction_id !== excludedTransactionId)); setCouponStatus('ready') } })
      .catch(() => { if (!cancelled) { setCoupons([]); setCouponStatus('error') } })
    return () => { cancelled = true }
  }, [showCouponHistory, portfolioId, contractId, excludedTransactionId])
  const recordedCoupons = coupons.flatMap(transaction => [
    ...(transaction.transaction_type === 'coupon' ? [{ date: transaction.entitlement_date || transaction.trade_date, amount: transaction.gross_amount, currency: transaction.currency, id: transaction.transaction_id }] : []),
    ...(transaction.settlement_cashflows ?? []).filter(flow => flow.kind === 'coupon').map((flow, index) => ({ date: flow.recognition_date || transaction.trade_date, amount: flow.amount, currency: flow.currency, id: `${transaction.transaction_id}:${index}` })),
  ])
  const deliveries = record.asset_deliveries ?? []
  const cashflows = record.settlement_cashflows ?? []
  const accountLabel = (id?: string | null) => accounts.find(account => account.account_id === id)?.account_name ?? id ?? '—'
  const cashAccounts = accounts.filter(account => account.account_category === 'cash')
  const money = (amount: string | number | undefined, unit: string) => amount === '' || amount == null || !unit ? '—' : formatCurrency(Number(amount), unit)
  const updateDelivery = (index: number, changes: Partial<PortfolioAssetDelivery>) => onChange?.({ asset_deliveries: deliveries.map((leg, row) => row === index ? { ...leg, ...changes } : leg) })
  const updateCashflow = (index: number, changes: Partial<PortfolioSettlementCashflow>) => onChange?.({ settlement_cashflows: cashflows.map((flow, row) => row === index ? { ...flow, ...changes } : flow) })
  return <fieldset className="fcn-settlement-review transaction-capture-review-wide" translate="no">
    <legend>{label('FCN settlement', 'FCN 结算单')}</legend>
    <p className="field-help">{label('Record the confirmed contract outcome. The original principal is not debited again when shares are delivered. A barrier observation alone does not close the FCN.', '按确认单记录合约结算。接票时不会再次扣除原本金；仅发生敲入观察不代表合约关闭。')}</p>
    {residualCash != null && <p><strong>{deliveries.length ? label('Actual residual cash', '实际现金尾差') : label('Cash redemption', '现金兑付')} · {money(residualCash, currency)}</strong> · {accountLabel(residualAccountId)} · {settlementDate || economicDate}</p>}
    {deliveries.map((leg, index) => {
      const instrument = instruments.find(item => item.instrument_id === leg.instrument_id)
      const underlying = terms?.underlyings.find(item => item.instrument_id === leg.instrument_id)
      const strike = underlying?.initial_reference_price != null && underlying.strike_level_pct != null ? underlying.initial_reference_price * underlying.strike_level_pct / 100 : null
      const unitValue = Number(leg.quantity) > 0 && leg.fair_value !== '' ? Number(leg.fair_value) / Number(leg.quantity) : null
      const chargeAccounts = cashAccounts.filter(account => account.currency === leg.currency)
      return <fieldset key={index}>
        <legend>{label(`Delivery ${index + 1}`, `交付 ${index + 1}`)}</legend>
        <div className="transaction-capture-review-grid">
          {onChange ? <>
            <label><span>{label(`Delivery ${index + 1} receiving account`, `交付 ${index + 1} 接收账户`)}</span><select value={leg.account_id} onChange={event => {
              const account = accounts.find(item => item.account_id === event.target.value)
              updateDelivery(index, { account_id: event.target.value, currency: account?.currency ?? '', ...(account?.currency !== leg.currency ? { fx_rate_to_contract: account?.currency === currency ? 1 : '', settlement_cash_account_id: null } : {}) })
            }}><option value="">{label('Select account', '选择账户')}</option>{accounts.filter(account => account.account_category === 'security').map(account => <option key={account.account_id} value={account.account_id}>{account.account_name} · {account.currency}</option>)}</select></label>
            <SecurityInstrumentPicker label={label(`Delivery ${index + 1} security`, `交付 ${index + 1} 证券`)} value={leg.instrument_id} instruments={instruments.filter(item => ['equity', 'etf'].includes(item.instrument_type))} onSelect={instrument_id => updateDelivery(index, { instrument_id })} />
          </> : <strong>{accountLabel(leg.account_id)} · {instrument ? `${primaryIdentifier(instrument)} · ${instrument.instrument_name}` : leg.instrument_id}</strong>}
          <label><span>{label('Delivered shares', '实际收到股数')}</span><input type="number" min="0" step="any" value={leg.quantity} readOnly={!onChange} onChange={event => updateDelivery(index, { quantity: event.target.value })} /></label>
          <label><span>{label('Total confirmed fair value', '证券总公允确认价值')} ({leg.currency || '—'})</span><input type="number" min="0" step="any" value={leg.fair_value} readOnly={!onChange} onChange={event => updateDelivery(index, { fair_value: event.target.value })} /></label>
          <label><span>{label('Share receipt date', '证券实际到账日')}</span><input type="date" aria-label={label('Share receipt date', '证券实际到账日')} value={leg.delivery_date ?? ''} readOnly={!onChange} onChange={event => updateDelivery(index, { delivery_date: event.target.value || null })} /><small>{!leg.delivery_date && <strong>{label('Receipt date not recorded. ', '实际到账日未记录。')}</strong>}{label('Enter a confirmed actual receipt date. If unrecorded, the shares are not marked as pending delivery; economic recognition follows the FCN outcome date.', '只填写已确认的实际到账日期。未填写时不标记待交付；经济确认仍按合约结算事实日。')}</small></label>
          <label><span>{label('Value translation FX', '确认价值折算汇率')} ({currency || 'contract'}/{leg.currency || 'security'})</span><input type="number" min="0" step="any" value={leg.fx_rate_to_contract} readOnly={!onChange} onChange={event => updateDelivery(index, { fx_rate_to_contract: event.target.value })} /></label>
        </div>
        <p>{label('Contract strike', '合同接票价')}: {strike == null ? label('Not confirmed', '未确认') : money(strike, leg.currency)} · {label('Confirmed value per share', '每股公允确认价值')}: {money(unitValue ?? undefined, leg.currency)}</p>
        <p className="field-help">{label('Confirmed fair value establishes the received stock basis; it is not the contractual strike or a fabricated market purchase. Value FX translates this value into the FCN currency and does not create a cash exchange.', '公允确认价值用于建立收到股票的成本，不是合同接票价或虚构的市场买入成交价。价值汇率只将确认价值折算到合约币种，不产生现金换汇。')}</p>
        <details open={Boolean(leg.quantity_fx_rate || leg.fractional_quantity || leg.fractional_reference_price)}>
          <summary>{label('Confirmed quantity and fractional-share terms (optional)', '股数与碎股的确认依据（可选）')}</summary>
          <div className="transaction-capture-review-grid">
            <label><span>{label('Quantity conversion FX', '股数计算的合同换算汇率')} ({leg.currency || 'security'}/{currency || 'contract'})</span><input type="number" min="0" step="any" value={leg.quantity_fx_rate ?? ''} readOnly={!onChange} onChange={event => updateDelivery(index, { quantity_fx_rate: event.target.value || null })} /></label>
            <label><span>{label('Fractional shares settled in cash', '现金替代的碎股数量')}</span><input type="number" min="0" step="any" value={leg.fractional_quantity ?? ''} readOnly={!onChange} onChange={event => updateDelivery(index, { fractional_quantity: event.target.value || null })} /></label>
            <label><span>{label('Fractional-share reference price', '碎股现金补偿参考价')} ({leg.currency || '—'})</span><input type="number" min="0" step="any" value={leg.fractional_reference_price ?? ''} readOnly={!onChange} onChange={event => updateDelivery(index, { fractional_reference_price: event.target.value || null })} /></label>
          </div>
          <p className="field-help">{label('These fields explain the broker-confirmed quantity and cash in lieu; they do not generate a stock purchase, FX trade, or additional cash. Enter the actual residual cash separately.', '这些字段解释券商确认的股数及碎股补偿，不另生成买股、换汇或现金。实际收到的现金尾差单独填写。')}</p>
        </details>
        <details open={Boolean(Number(leg.fees) || Number(leg.taxes))}>
          <summary>{label('Stock acquisition fees and taxes', '取得股票的费用与税费')}</summary>
          <p className="field-help">{label('Debited from the stock-currency cash account and included in the stock cost. Contract expenses belong in settlement cashflows below.', '从证券同币种现金账户扣款，计入股票取得成本。合约本身的费用填写在下方结算现金明细。')}</p>
          <div className="transaction-capture-review-grid">
            <label><span>{label('Stock-charge cash account', '股票费用扣款账户')}</span><select disabled={!onChange} value={leg.settlement_cash_account_id ?? ''} onChange={event => updateDelivery(index, { settlement_cash_account_id: event.target.value || null })}><option value="">{label('Select account', '选择账户')}</option>{chargeAccounts.map(account => <option key={account.account_id} value={account.account_id}>{account.account_name} · {account.currency}</option>)}</select></label>
            {(['fees', 'taxes'] as const).map(field => <label key={field}><span>{field === 'fees' ? label('Stock acquisition fees', '股票取得费用') : label('Stock acquisition taxes', '股票取得税费')} ({leg.currency || '—'})</span><input type="number" min="0" step="any" value={leg[field] ?? ''} readOnly={!onChange} onChange={event => updateDelivery(index, { [field]: event.target.value || 0 })} /></label>)}
            <label><span>{label('Fee category', '费用分类')}</span><select disabled={!onChange || !Number(leg.fees)} value={leg.fee_category ?? 'unknown'} onChange={event => updateDelivery(index, { fee_category: event.target.value as PortfolioFeeCategory })}>{feeCategories.map(category => <option key={category} value={category}>{feeCategoryLabel(category)}</option>)}</select></label>
            <label><span>{label('Stock-charge debit date', '股票费用扣款日')}</span><input type="date" aria-label={label('Stock-charge debit date', '股票费用扣款日')} value={leg.fee_settlement_date ?? (onChange ? '' : economicDate || '')} readOnly={!onChange} onChange={event => updateDelivery(index, { fee_settlement_date: event.target.value || null })} /><small>{label('Blank uses the FCN economic date.', '留空按 FCN 经济确认日。')}</small></label>
          </div>
        </details>
        <p><strong>{label('Stock received', '收到证券')}: {formatQuantity(Number(leg.quantity) || 0)} · {money(leg.fair_value, leg.currency)}</strong>{leg.fx_rate_to_contract !== '' && leg.fair_value !== '' && currency ? ` → ${money(Number(leg.fair_value) * Number(leg.fx_rate_to_contract), currency)}` : ''}{Number(leg.fees) || Number(leg.taxes) ? ` · ${label('Stock cost', '股票入账成本')}: ${money(Number(leg.fair_value) + Number(leg.fees || 0) + Number(leg.taxes || 0), leg.currency)}` : ''}</p>
        {onChange && <button type="button" onClick={() => onChange({ asset_deliveries: deliveries.filter((_, row) => row !== index) })}>{label(`Remove delivery ${index + 1}`, `移除交付 ${index + 1}`)}</button>}
      </fieldset>
    })}
    {onChange && allowAssetDelivery && <button type="button" onClick={() => onChange({ asset_deliveries: [...deliveries, { account_id: '', instrument_id: terms?.underlyings.filter(item => item.deliverable).length === 1 ? terms.underlyings.find(item => item.deliverable)!.instrument_id : '', quantity: '', fair_value: '', currency: '', fx_rate_to_contract: '' }] })}>{label('Add delivered security', '添加交付证券')}</button>}
    {showCouponHistory && <div className="transaction-capture-review-wide">
      <strong>{label('Previously recorded coupons', '已录票息')}</strong>
      {couponStatus === 'loading' ? <p>{label('Loading contract payment history…', '正在读取合约收息记录…')}</p> : couponStatus === 'error' ? <p role="status">{label('Coupon history could not be loaded. Check recorded transactions before adding a final coupon.', '未能读取票息历史；添加末期票息前请核对已有交易。')}</p> : <>
        {recordedCoupons.map(coupon => <p key={coupon.id}>{coupon.date} · {formatCurrency(Number(coupon.amount), coupon.currency)}</p>)}
        {!recordedCoupons.length && <p>{label('No recorded coupons for this contract.', '该合约暂无已录票息。')}</p>}
      </>}
    </div>}
    {(onChange || cashflows.length > 0) && <fieldset>
      <legend>{label('Settlement cashflows', '结算现金明细')}</legend>
      <p className="field-help">{label('Add only the final coupon or contract charges included in this settlement. Check recorded coupons first; do not enter the same payment twice.', '仅添加本次结算的末期票息或合约费用。先核对已录票息，同笔付款只记录一次。')}</p>
      {cashflows.map((flow, index) => <div className="transaction-capture-review-grid" key={index}>
        <label><span>{label(`Cashflow ${index + 1} type`, `现金明细 ${index + 1} 类型`)}</span><select value={flow.kind} disabled={!onChange} onChange={event => updateCashflow(index, { kind: event.target.value as PortfolioSettlementCashflow['kind'], fee_category: 'unknown', ...(event.target.value === 'coupon' && flow.currency !== currency ? { cash_account_id: '', currency: '' } : {}) })}><option value="coupon">{label('Final coupon', '末期票息')}</option><option value="fee">{label('Contract fee', '合约费用')}</option><option value="tax">{label('Contract tax', '合约税费')}</option></select></label>
        <label><span>{label(`Cashflow ${index + 1} account`, `现金明细 ${index + 1} 账户`)}</span><select value={flow.cash_account_id} disabled={!onChange} onChange={event => updateCashflow(index, { cash_account_id: event.target.value, currency: cashAccounts.find(account => account.account_id === event.target.value)?.currency ?? '' })}><option value="">{label('Select account', '选择账户')}</option>{cashAccounts.filter(account => flow.kind !== 'coupon' || !currency || account.currency === currency).map(account => <option key={account.account_id} value={account.account_id}>{account.account_name} · {account.currency}</option>)}</select></label>
        <label><span>{label(`Cashflow ${index + 1} amount`, `现金明细 ${index + 1} 金额`)} ({flow.currency || '—'})</span><input type="number" min="0" step="any" value={flow.amount} readOnly={!onChange} onChange={event => updateCashflow(index, { amount: event.target.value })} /></label>
        <label><span>{label('Income / expense recognition date', '收入／费用确认日')}</span><input type="date" value={flow.recognition_date ?? ''} readOnly={!onChange} onChange={event => updateCashflow(index, { recognition_date: event.target.value || null })} /></label>
        <label><span>{label('Cash settlement date', '现金收付日')}</span><input type="date" value={flow.settlement_date ?? ''} readOnly={!onChange} onChange={event => updateCashflow(index, { settlement_date: event.target.value || null })} /></label>
        {flow.kind === 'fee' && <label><span>{label('Fee category', '费用分类')}</span><select disabled={!onChange} value={flow.fee_category ?? 'unknown'} onChange={event => updateCashflow(index, { fee_category: event.target.value as PortfolioFeeCategory })}>{feeCategories.map(category => <option key={category} value={category}>{feeCategoryLabel(category)}</option>)}</select></label>}
        <label><span>{label('Cashflow note', '现金明细备注')}</span><input value={flow.note ?? ''} readOnly={!onChange} onChange={event => updateCashflow(index, { note: event.target.value || null })} /></label>
        <p><strong>{flow.kind === 'coupon' ? '+' : '−'}{money(flow.amount, flow.currency)}</strong> · {accountLabel(flow.cash_account_id)} · {label('Recognized', '确认')} {flow.recognition_date || economicDate || '—'} · {label('Cash', '收付')} {flow.settlement_date || flow.recognition_date || economicDate || '—'}</p>
        {onChange && <button type="button" onClick={() => onChange({ settlement_cashflows: cashflows.filter((_, row) => row !== index) })}>{label(`Remove cashflow ${index + 1}`, `移除现金明细 ${index + 1}`)}</button>}
      </div>)}
      {onChange && <button type="button" onClick={() => onChange({ settlement_cashflows: [...cashflows, { kind: 'coupon', cash_account_id: '', currency: '', amount: '' }] })}>{label('Add settlement cashflow', '添加结算现金明细')}</button>}
    </fieldset>}
  </fieldset>
}
