import type { TableCell } from '../../../../../packages/ui/src/tableExport'

import {
  restorePortfolioTransactionInputScale,
  type PortfolioTransactionRecord,
} from './api'

export const TRANSACTION_EXPORT_HEADERS: TableCell[] = [
  'Transaction ID',
  'Trade Date',
  'Trade Time',
  'Timezone',
  'Settlement Date',
  'Type',
  'Flow Scope',
  'Account',
  'Instrument ID',
  'Instrument Name',
  'Quantity',
  'Price',
  'Gross Amount',
  'Counter Amount',
  'Quoted FX Rate',
  'Fees',
  'Taxes',
  'Net Cash Effect',
  'Currency',
  'Note',
]

export function buildTransactionExportRows(transactions: PortfolioTransactionRecord[]): TableCell[][] {
  return [
    TRANSACTION_EXPORT_HEADERS,
    ...transactions.map((transaction) => [
      transaction.transaction_id,
      transaction.trade_date,
      transaction.trade_time,
      transaction.trade_timezone,
      transaction.settlement_date,
      transaction.transaction_type,
      transaction.flow_scope,
      transaction.account.account_name,
      transaction.instrument_id ?? '',
      transaction.instrument_ref?.instrument_name ?? '',
      restorePortfolioTransactionInputScale(
        transaction.quantity,
        transaction.quantity_input_scale,
      ),
      restorePortfolioTransactionInputScale(
        transaction.price,
        transaction.price_input_scale,
      ),
      restorePortfolioTransactionInputScale(
        transaction.gross_amount,
        transaction.gross_amount_input_scale,
      ),
      restorePortfolioTransactionInputScale(
        transaction.counter_amount,
        transaction.counter_amount_input_scale,
      ),
      restorePortfolioTransactionInputScale(
        transaction.quoted_fx_rate,
        transaction.quoted_fx_rate_input_scale,
      ),
      restorePortfolioTransactionInputScale(
        transaction.fees,
        transaction.fees_input_scale,
      ),
      restorePortfolioTransactionInputScale(
        transaction.taxes,
        transaction.taxes_input_scale,
      ),
      transaction.net_cash_effect ?? '',
      transaction.currency,
      transaction.note ?? '',
    ]),
  ]
}

const TRANSACTION_DECIMAL_DRAFT_FIELDS = new Set([
  'quantity',
  'price',
  'gross_amount',
  'counter_amount',
  'quoted_fx_rate',
  'fees',
  'taxes',
])

export function normalizeTransactionDecimalDraft(value: string): string {
  const normalized = value.trim()
  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(normalized)
  if (!match) {
    return normalized
  }
  const sign = match[1] === '-' ? '-' : ''
  const integer = match[2].replace(/^0+(?=\d)/, '')
  const fraction = match[3]
  const resolvedSign = integer === '0' && (!fraction || /^0*$/.test(fraction)) ? '' : sign
  return `${resolvedSign}${integer}${fraction ? `.${fraction}` : ''}`
}

export function transactionDraftHasChanges(
  current: Record<string, string>,
  base: Record<string, string>,
): boolean {
  const keys = new Set([...Object.keys(current), ...Object.keys(base)])
  keys.delete('instrument_search')
  for (const key of keys) {
    const currentValue = current[key] ?? ''
    const baseValue = base[key] ?? ''
    const normalize = TRANSACTION_DECIMAL_DRAFT_FIELDS.has(key)
      ? normalizeTransactionDecimalDraft
      : (value: string) => value.trim()
    if (normalize(currentValue) !== normalize(baseValue)) {
      return true
    }
  }
  return false
}

export function countActiveTransactionFilters(filters: {
  account_id?: string
  transaction_type?: string
  instrument_id?: string
  start_date?: string
  end_date?: string
}) {
  return Object.values(filters).filter((value) => Boolean(value?.trim())).length
}

export function transactionDateLabels(transactionType: string) {
  const recognizesInstrumentIncome = transactionType === 'dividend' || transactionType === 'coupon'
  return {
    settlement: recognizesInstrumentIncome ? 'Pay / Settlement Date' : 'Settlement Date',
    entitlement: recognizesInstrumentIncome ? 'Ex / Recognition Date' : 'Entitlement Date',
  }
}
