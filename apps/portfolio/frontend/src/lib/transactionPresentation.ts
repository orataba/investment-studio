import type { TableCell } from '../../../../../packages/ui/src/tableExport'

import type { PortfolioTransactionRecord } from './api'
import { toFiniteNumber } from './format'

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
      toFiniteNumber(transaction.quantity),
      toFiniteNumber(transaction.price),
      toFiniteNumber(transaction.gross_amount),
      toFiniteNumber(transaction.fees),
      toFiniteNumber(transaction.taxes),
      toFiniteNumber(transaction.net_cash_effect),
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
  'fx_rate',
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
  const fraction = (match[3] ?? '').replace(/0+$/, '')
  if (integer === '0' && !fraction) {
    return '0'
  }
  return `${sign}${integer}${fraction ? `.${fraction}` : ''}`
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
