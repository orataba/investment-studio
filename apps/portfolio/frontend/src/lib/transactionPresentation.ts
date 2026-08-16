import type { TableCell } from '../../../../../packages/ui/src/tableExport'

import type {
  PortfolioOptionAction,
  PortfolioTransactionChangeLogRecord,
  PortfolioTransactionRecord,
} from './api'
import { optionActionLabel, resolveOptionAction } from './optionActions'

export const TRANSACTION_EXPORT_HEADERS: TableCell[] = [
  'Transaction ID',
  'Trade Date',
  'Trade Time',
  'Timezone',
  'Settlement Date',
  'Position Effective Date',
  'Economic Date',
  'External Flow Date',
  'Type',
  'Flow Scope',
  'Account',
  'Asset Domain',
  'Asset Type',
  'Asset ID',
  'Asset Name',
  'Quantity',
  'Price',
  'Gross Amount',
  'Fees',
  'Fee Category',
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
      transaction.position_effective_date ?? '',
      transaction.economic_date,
      transaction.external_flow_date ?? '',
      transactionActivityLabel(
        transaction.transaction_type,
        transaction.asset_subtype,
        transaction.option_action,
      ),
      transaction.flow_scope,
      transaction.account.account_name,
      transaction.asset_domain,
      transaction.asset_subtype ?? '',
      transaction.instrument_id ?? transaction.derivative_contract_id ?? '',
      transaction.instrument_ref?.instrument_name ??
        transaction.derivative_contract?.contract_name ??
        '',
      transaction.quantity,
      transaction.price,
      transaction.gross_amount,
      transaction.fees,
      transaction.fee_category,
      transaction.taxes,
      transaction.net_cash_effect,
      transaction.currency,
      transaction.note ?? '',
    ]),
  ]
}

export function countActiveTransactionFilters(filters: {
  account_id?: string
  asset_domain?: string
  transaction_type?: string
  position_reference_id?: string
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

export function transactionActivityLabel(
  transactionType: string,
  instrumentType?: string | null,
  optionAction?: PortfolioOptionAction | null,
) {
  const resolvedOptionAction =
    optionAction ?? resolveOptionAction(transactionType, instrumentType)
  if (resolvedOptionAction) {
    return optionActionLabel(resolvedOptionAction)
  }
  if (instrumentType === 'fund') {
    if (transactionType === 'buy') {
      return 'Subscription'
    }
    if (transactionType === 'sell') {
      return 'Redemption'
    }
  }
  if (instrumentType === 'fcn') {
    if (transactionType === 'buy') {
      return 'FCN Contract Entry'
    }
    if (transactionType === 'coupon') {
      return 'FCN Income'
    }
    if (transactionType === 'maturity_redemption') {
      return 'FCN Contract Close'
    }
  }
  return transactionType
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

const AUDIT_FIELD_LABELS: Record<string, string> = {
  transaction_type: 'Type',
  trade_date: 'Trade date',
  trade_time: 'Trade time',
  settlement_date: 'Settlement date',
  position_effective_date: 'Position effective date',
  entitlement_date: 'Entitlement date',
  acquisition_date: 'Acquisition date',
  account_id: 'Account',
  settlement_cash_account_id: 'Settlement cash account',
  instrument_id: 'Instrument',
  quantity: 'Quantity',
  price: 'Unit price',
  gross_amount: 'Gross amount',
  counter_amount: 'Counter amount',
  fx_rate: 'FX rate',
  fees: 'Fees',
  fee_category: 'Fee category',
  taxes: 'Taxes',
  currency: 'Currency',
  counterparty_account_id: 'Counterparty account',
  note: 'Note',
}

const AUDIT_IGNORED_FIELDS = new Set([
  'transaction_id',
  'portfolio_id',
  'created_at',
  'row_version',
])

export function transactionChangedFields(
  change: Pick<PortfolioTransactionChangeLogRecord, 'change_type' | 'before' | 'after'>,
) {
  const before = change.before ?? {}
  const after = change.after ?? {}
  const keys =
    change.change_type === 'update'
      ? new Set([...Object.keys(before), ...Object.keys(after)])
      : new Set(Object.keys(change.change_type === 'delete' ? before : after))

  return Array.from(keys)
    .filter((key) => !AUDIT_IGNORED_FIELDS.has(key))
    .filter(
      (key) =>
        change.change_type !== 'update' ||
        JSON.stringify(before[key]) !== JSON.stringify(after[key]),
    )
    .map((key) => AUDIT_FIELD_LABELS[key] ?? transactionActivityLabel(key))
}
