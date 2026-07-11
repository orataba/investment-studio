import type { TableCell } from '../../../../../packages/ui/src/tableExport'

import type { PortfolioTransactionRecord } from './api'

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
      transaction.quantity,
      transaction.price,
      transaction.gross_amount,
      transaction.fees,
      transaction.taxes,
      transaction.net_cash_effect,
      transaction.currency,
      transaction.note ?? '',
    ]),
  ]
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
