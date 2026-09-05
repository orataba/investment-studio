import type {
  PortfolioOptionAction,
  PortfolioTransactionChangeLogRecord,
} from './api'
import { optionActionLabel, resolveOptionAction } from './optionActions'

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
  lifecycleEventType?: string | null,
) {
  const resolvedOptionAction =
    optionAction ?? resolveOptionAction(transactionType, instrumentType)
  if (resolvedOptionAction) {
    return optionActionLabel(resolvedOptionAction)
  }
  if (transactionType === 'option_opening_balance') return 'Written Option Opening Balance'
  if (transactionType === 'short_sell') return 'Short Sell'
  if (transactionType === 'buy_to_cover') return 'Buy to Cover'
  if (transactionType === 'short_opening_balance') return 'Short Stock Opening Balance'
  if (transactionType === 'lifecycle_event' && lifecycleEventType === 'fcn_knock_in') return 'FCN Knock-In Observation'
  const lifecycleLabels: Record<string, string> = {
    option_long_expiry: 'Long Option Expired',
    option_long_cash_settlement: 'Long Option Cash Settlement',
    option_long_exercise: 'Option Exercised',
    option_writer_expiry: 'Written Option Expired',
    option_writer_cash_settlement: 'Written Option Cash Settlement',
    option_writer_assignment: 'Option Assigned',
  }
  if (lifecycleEventType && lifecycleLabels[lifecycleEventType]) {
    return lifecycleLabels[lifecycleEventType]
  }
  if (instrumentType === 'public_fund' || instrumentType === 'private_fund') {
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
