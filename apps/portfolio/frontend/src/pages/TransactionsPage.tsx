import { FormEvent, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import InstrumentFilterCombobox from '../components/InstrumentFilterCombobox'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  createPortfolioInternalTransfer,
  createPortfolioTransaction,
  deletePortfolioTransaction,
  getPortfolioAccounts,
  getPortfolioFxRates,
  getPortfolioInstruments,
  getPortfolioTransactionExecutionQuote,
  getPortfolioTransactionPositionPreview,
  getPortfolioTransactionsWorkspace,
  type PortfolioAccountRecord,
  type PortfolioFeeCategory,
  type PortfolioPositionLotRecord,
  type PortfolioSharedFxRateRecord,
  type PortfolioTransactionPositionPreviewResponse,
  type SharedInstrumentRecord,
  type PortfolioTransactionCreatePayload,
  type PortfolioTransactionFilters,
  type PortfolioTransactionRecord,
  type PortfolioTransactionWorkspaceResponse,
  updatePortfolioTransaction,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatQuantity,
  formatSignedCurrency,
  formatUnitPrice,
} from '../lib/format'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'
import DownloadFormatMenu from '../../../../../packages/ui/src/DownloadFormatMenu'
import { downloadTable, type TableExportFormat } from '../../../../../packages/ui/src/tableExport'
import {
  buildTransactionExportRows,
  countActiveTransactionFilters,
  transactionDateLabels,
} from '../lib/transactionPresentation'
import { supportsTransactionInstrumentType } from '../lib/transactionEligibility'
import {
  resolveWorkspaceTransactionSelection,
  stopTransactionRowSelection,
} from '../lib/transactionSelection'
import {
  calculateTransactionGrossAmount,
  calculateTransactionUnitPrice,
  resolveTransactionPriceContract,
  type TransactionPriceContract,
} from '../lib/transactionPricing'
import { executionQuoteUnavailableMessage } from '../lib/executionQuotePresentation'

const DEFAULT_FORM_TIME = (import.meta.env.VITE_PORTFOLIO_DEFAULT_TRADE_TIME || '12:00').slice(0, 5)
const DEFAULT_TRADE_TIMEZONE = import.meta.env.VITE_PORTFOLIO_DEFAULT_TRADE_TIMEZONE || 'Asia/Shanghai'
const TRANSACTION_TYPES = [
  'buy',
  'sell',
  'dividend',
  'dividend_reinvestment',
  'coupon',
  'interest',
  'return_of_capital',
  'maturity_redemption',
  'fee',
  'tax',
  'deposit',
  'withdrawal',
  'fx_conversion',
  'transfer_out',
  'transfer_in',
  'opening_balance',
] as const

const FEE_CATEGORIES: Array<{ value: PortfolioFeeCategory; label: string }> = [
  { value: 'unknown', label: 'Unknown / unclassified' },
  { value: 'transaction_cost', label: 'Transaction cost' },
  { value: 'management_fee', label: 'Management fee' },
  { value: 'custody_fee', label: 'Custody fee' },
  { value: 'administration_fee', label: 'Administration fee' },
  { value: 'performance_fee', label: 'Performance fee' },
  { value: 'other', label: 'Other' },
]

const ACCOUNT_SCOPE_ENFORCED_TRANSACTION_TYPES = new Set(['buy', 'dividend_reinvestment', 'opening_balance'])

type TransactionInspectorTab = 'fact' | 'lots' | 'postings'

function primaryIdentifier(
  instrument:
    | SharedInstrumentRecord
    | {
        instrument_id: string
        identifiers: Array<{ identifier_value: string; is_primary: boolean }>
      },
) {
  return (
    instrument.identifiers.find((item) => item.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.instrument_id
  )
}

function instrumentSearchLabel(instrument: SharedInstrumentRecord) {
  return `${primaryIdentifier(instrument)} · ${instrument.instrument_name}`
}

function isTransferTransaction(transactionType: string) {
  return transactionType === 'transfer_in' || transactionType === 'transfer_out'
}

function isFxConversionTransaction(transactionType: string) {
  return transactionType === 'fx_conversion'
}

function localTodayIso() {
  const now = new Date()
  const timezoneOffsetMs = now.getTimezoneOffset() * 60 * 1000
  return new Date(now.getTime() - timezoneOffsetMs).toISOString().slice(0, 10)
}

function formatFormNumber(
  value: number | null | undefined,
  options?: { zeroAsEmpty?: boolean },
) {
  const zeroAsEmpty = options?.zeroAsEmpty ?? false
  if (value == null) {
    return ''
  }
  if (zeroAsEmpty && Math.abs(value) < 1e-9) {
    return ''
  }
  return String(value)
}

function parsePositiveFormNumber(value: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function formatCalculatedFormNumber(value: number, decimals: number) {
  if (!Number.isFinite(value)) {
    return ''
  }
  return value.toFixed(decimals).replace(/\.?0+$/, '')
}

function accountAllowsInstrumentType(
  account: PortfolioAccountRecord | null | undefined,
  instrumentType: string,
  transactionType: string,
) {
  if (
    !account ||
    account.account_type !== 'securities_account' ||
    !ACCOUNT_SCOPE_ENFORCED_TRANSACTION_TYPES.has(transactionType)
  ) {
    return true
  }

  if (!account.allowed_instrument_types?.length) {
    return true
  }

  return account.allowed_instrument_types.includes(instrumentType.trim().toLowerCase())
}

function requiresSettlement(transactionType: string, accountType?: string | null) {
  if (isFxConversionTransaction(transactionType)) {
    return false
  }
  if (transactionType === 'buy' || transactionType === 'sell') {
    return true
  }

  if (
    transactionType === 'dividend' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption'
  ) {
    return true
  }

  return (transactionType === 'fee' || transactionType === 'tax') && accountType === 'securities_account'
}

function requiresInstrument(
  transactionType: string,
  accountType?: string | null,
  transferObjectType?: string | null,
) {
  if (isFxConversionTransaction(transactionType)) {
    return false
  }
  if (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'dividend' ||
    transactionType === 'dividend_reinvestment' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption'
  ) {
    return true
  }

  if (isTransferTransaction(transactionType) && transferObjectType === 'position') {
    return true
  }

  return transactionType === 'opening_balance' && accountType === 'securities_account'
}

function allowsInstrument(
  transactionType: string,
  accountType?: string | null,
  transferObjectType?: string | null,
) {
  if (isFxConversionTransaction(transactionType)) {
    return false
  }
  if (requiresInstrument(transactionType, accountType, transferObjectType)) {
    return true
  }

  return (
    (transactionType === 'fee' || transactionType === 'tax') &&
    accountType === 'securities_account'
  )
}

function eligibleAccounts(
  transactionType: string,
  accounts: PortfolioAccountRecord[],
  transferObjectType?: string | null,
) {
  if (
    transactionType === 'deposit' ||
    transactionType === 'withdrawal' ||
    transactionType === 'interest' ||
    isFxConversionTransaction(transactionType)
  ) {
    return accounts.filter((account) => account.account_type === 'deposit_account')
  }

  if (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'dividend' ||
    transactionType === 'dividend_reinvestment' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption'
  ) {
    return accounts.filter((account) => account.account_type === 'securities_account')
  }

  if (isTransferTransaction(transactionType)) {
    if (transferObjectType === 'cash') {
      return accounts.filter((account) => account.account_type === 'deposit_account')
    }
    if (transferObjectType === 'position') {
      return accounts.filter((account) => account.account_type === 'securities_account')
    }
  }

  return accounts
}

function eligibleCounterpartyAccounts(
  transactionType: string,
  accounts: PortfolioAccountRecord[],
  currentAccountId: string,
  transferObjectType?: string | null,
) {
  if (isFxConversionTransaction(transactionType)) {
    const sourceAccount = accounts.find((account) => account.account_id === currentAccountId)
    const sourceCurrency = sourceAccount?.currency?.toUpperCase()
    return accounts.filter(
      (account) =>
        account.account_type === 'deposit_account' &&
        account.account_id !== currentAccountId &&
        (!sourceCurrency || account.currency.toUpperCase() !== sourceCurrency),
    )
  }

  if (!isTransferTransaction(transactionType)) {
    return []
  }

  return eligibleAccounts(transactionType, accounts, transferObjectType).filter(
    (account) => account.account_id !== currentAccountId,
  )
}

function isSelectableInstrument(
  transactionType: string,
  instrument: SharedInstrumentRecord,
  account?: PortfolioAccountRecord | null,
  accountCurrency?: string | null,
  transferObjectType?: string | null,
) {
  if (isFxConversionTransaction(transactionType)) {
    return false
  }
  if (accountCurrency && instrument.currency.toUpperCase() !== accountCurrency.toUpperCase()) {
    return false
  }

  if (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'dividend' ||
    transactionType === 'dividend_reinvestment' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption' ||
    transactionType === 'fee' ||
    transactionType === 'tax' ||
    (isTransferTransaction(transactionType) && transferObjectType === 'position')
  ) {
    return (
      supportsTransactionInstrumentType(transactionType, instrument.instrument_type) &&
      accountAllowsInstrumentType(account, instrument.instrument_type, transactionType)
    )
  }

  return true
}

function usesQuantity(
  transactionType: string,
  accountType?: string | null,
  transferObjectType?: string | null,
) {
  if (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'dividend_reinvestment' ||
    transactionType === 'maturity_redemption'
  ) {
    return true
  }

  if (isTransferTransaction(transactionType) && transferObjectType === 'position') {
    return true
  }

  return transactionType === 'opening_balance' && accountType === 'securities_account'
}

function usesPrice(transactionType: string) {
  return transactionType === 'buy' || transactionType === 'sell'
}

function grossAmountLabel(transactionType: string) {
  if (isFxConversionTransaction(transactionType)) {
    return 'Source Amount'
  }

  if (transactionType === 'fee' || transactionType === 'tax') {
    return 'Amount'
  }

  if (transactionType === 'interest') {
    return 'Interest Amount'
  }

  if (transactionType === 'dividend' || transactionType === 'coupon') {
    return 'Cash Amount'
  }

  if (transactionType === 'dividend_reinvestment') {
    return 'Reinvested Amount'
  }

  if (transactionType === 'return_of_capital') {
    return 'Return Amount'
  }

  if (transactionType === 'maturity_redemption') {
    return 'Redemption Amount'
  }

  if (transactionType === 'transfer_in' || transactionType === 'transfer_out') {
    return 'Transferred Cost / Amount'
  }

  if (transactionType === 'opening_balance') {
    return 'Opening Amount'
  }

  return 'Amount'
}

function showsFeeField(transactionType: string) {
  return (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'dividend' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption'
  )
}

function showsTaxField(transactionType: string) {
  return (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'dividend' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption'
  )
}

function parseNonNegativeFormNumber(value: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0
}

function previewNetCashEffect(
  transactionType: string,
  grossAmount: number | null,
  fees: number,
  taxes: number,
  transferObjectType?: string | null,
) {
  if (grossAmount == null || !Number.isFinite(grossAmount)) {
    return null
  }

  if (transactionType === 'buy') {
    return -(grossAmount + fees + taxes)
  }
  if (
    transactionType === 'sell' ||
    transactionType === 'dividend' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption'
  ) {
    return grossAmount - fees - taxes
  }
  if (transactionType === 'interest' || transactionType === 'deposit') {
    return grossAmount
  }
  if (transactionType === 'dividend_reinvestment') {
    return 0
  }
  if (transactionType === 'fee' || transactionType === 'tax' || transactionType === 'withdrawal') {
    return -grossAmount
  }
  if (transactionType === 'transfer_out' && transferObjectType === 'cash') {
    return -grossAmount
  }
  if (transactionType === 'transfer_in' && transferObjectType === 'cash') {
    return grossAmount
  }
  return null
}

function quantityDeltaForPreview(
  transactionType: string,
  transferObjectType: string | null | undefined,
  quantity: number | null,
  previewAccountRole: 'selected' | 'source' = 'selected',
) {
  if (quantity == null || !Number.isFinite(quantity)) {
    return null
  }
  if (previewAccountRole === 'source' && transactionType === 'transfer_in' && transferObjectType === 'position') {
    return -quantity
  }
  if (
    transactionType === 'buy' ||
    transactionType === 'dividend_reinvestment' ||
    (transactionType === 'transfer_in' && transferObjectType === 'position') ||
    transactionType === 'opening_balance'
  ) {
    return quantity
  }
  if (
    transactionType === 'sell' ||
    transactionType === 'maturity_redemption' ||
    (transactionType === 'transfer_out' && transferObjectType === 'position')
  ) {
    return -quantity
  }
  return 0
}

type TransactionFormState = {
  transaction_type: string
  trade_date: string
  trade_time: string
  settlement_date: string
  entitlement_date: string
  acquisition_date: string
  account_id: string
  counterparty_account_id: string
  settlement_cash_account_id: string
  transfer_object_type: string
  instrument_id: string
  quantity: string
  price: string
  gross_amount: string
  counter_amount: string
  fx_rate: string
  fees: string
  fee_category: PortfolioFeeCategory
  taxes: string
  note: string
  instrument_search: string
}

type PricingAnchor = 'price' | 'gross_amount'

function buildInitialFormState(accounts: PortfolioAccountRecord[]): TransactionFormState {
  const defaultFormDate = localTodayIso()
  const defaultSecurityAccount = accounts.find((account) => account.account_type === 'securities_account')
  const defaultCashAccount =
    accounts.find((account) => account.account_id === defaultSecurityAccount?.default_settlement_cash_account_id) ??
    accounts.find((account) => account.account_type === 'deposit_account')

  return {
    transaction_type: 'buy',
    trade_date: defaultFormDate,
    trade_time: DEFAULT_FORM_TIME,
    settlement_date: defaultFormDate,
    entitlement_date: '',
    acquisition_date: '',
    account_id: defaultSecurityAccount?.account_id ?? accounts[0]?.account_id ?? '',
    counterparty_account_id: '',
    settlement_cash_account_id: defaultCashAccount?.account_id ?? '',
    transfer_object_type: 'cash',
    instrument_id: '',
    quantity: '',
    price: '',
    gross_amount: '',
    counter_amount: '',
    fx_rate: '',
    fees: '0',
    fee_category: 'unknown',
    taxes: '0',
    note: '',
    instrument_search: '',
  }
}

function buildFormStateFromTransaction(transaction: PortfolioTransactionRecord): TransactionFormState {
  return {
    transaction_type: transaction.transaction_type,
    trade_date: transaction.trade_date,
    trade_time: transaction.trade_time || DEFAULT_FORM_TIME,
    settlement_date: transaction.settlement_date,
    entitlement_date: transaction.entitlement_date || '',
    acquisition_date: transaction.acquisition_date || '',
    account_id: transaction.account.account_id,
    counterparty_account_id: transaction.counterparty_account_id || '',
    settlement_cash_account_id: transaction.settlement_cash_account?.account_id || '',
    transfer_object_type: transaction.transfer_object_type || 'cash',
    instrument_id: transaction.instrument_id || '',
    quantity: formatFormNumber(transaction.quantity, { zeroAsEmpty: true }),
    price: formatFormNumber(transaction.price, { zeroAsEmpty: true }),
    gross_amount: formatFormNumber(transaction.gross_amount, { zeroAsEmpty: true }),
    counter_amount: formatFormNumber(transaction.counter_amount, { zeroAsEmpty: true }),
    fx_rate: formatFormNumber(transaction.fx_rate, { zeroAsEmpty: true }),
    fees: formatFormNumber(transaction.fees),
    fee_category: transaction.fee_category,
    taxes: formatFormNumber(transaction.taxes),
    note: transaction.note || '',
    instrument_search: '',
  }
}

function supportsEntitlementDate(transactionType: string) {
  return transactionType === 'dividend' || transactionType === 'coupon' || transactionType === 'fee' || transactionType === 'tax'
}

function supportsAcquisitionDate(transactionType: string, accountType?: string | null) {
  return transactionType === 'opening_balance' && accountType === 'securities_account'
}

function canEditTransaction(transaction: PortfolioTransactionRecord | null) {
  if (!transaction) {
    return false
  }
  return !transaction.transfer_group_id && !isTransferTransaction(transaction.transaction_type)
}

function tradeTimeLabel(tradeTime: string, tradeTimezone: string, tradeTimeIsEstimated: boolean) {
  return {
    primary: tradeTime || DEFAULT_FORM_TIME,
    secondary: tradeTimeIsEstimated ? `${tradeTimezone} · default` : tradeTimezone,
  }
}

function transactionAccountHref(portfolioId: string, accountId: string) {
  return `/portfolios/${portfolioId}/accounts?account_id=${encodeURIComponent(accountId)}`
}

function transactionInspectorHref(
  portfolioId: string,
  accountId: string,
  transactionId: string,
) {
  const params = new URLSearchParams({
    account_id: accountId,
    transaction_id: transactionId,
  })
  return `/portfolios/${portfolioId}/transactions?${params.toString()}`
}

function resolvePositionLotImpactKinds(
  positionLot: PortfolioPositionLotRecord,
  transactionId: string,
): string[] {
  const impactKinds: string[] = []
  if (positionLot.opened_by_transaction_id === transactionId) {
    impactKinds.push('opened')
  }
  if (positionLot.realizations.some((realization) => realization.transaction_id === transactionId)) {
    impactKinds.push('realized')
  }
  if (!impactKinds.length) {
    impactKinds.push('related')
  }
  return impactKinds
}

export default function TransactionsPage() {
  const { portfolioId = '' } = useParams()
  const currentPortfolioIdRef = useRef(portfolioId)
  const [searchParams, setSearchParams] = useSearchParams()
  const securitySearchRef = useRef<HTMLInputElement | null>(null)
  const accountSelectRef = useRef<HTMLSelectElement | null>(null)
  const autoQuoteKeyRef = useRef<string | null>(null)
  const autoQuantityKeyRef = useRef<string | null>(null)
  const autoGrossDerivedRef = useRef(false)
  const [accounts, setAccounts] = useState<PortfolioAccountRecord[]>([])
  const [instruments, setInstruments] = useState<SharedInstrumentRecord[]>([])
  const [fxRates, setFxRates] = useState<PortfolioSharedFxRateRecord[]>([])
  const [transactionsWorkspace, setTransactionsWorkspace] = useState<PortfolioTransactionWorkspaceResponse | null>(null)
  const [workspaceRequestedTransactionId, setWorkspaceRequestedTransactionId] = useState<string | null>(null)
  const [metaLoading, setMetaLoading] = useState(true)
  const [loadingTransactions, setLoadingTransactions] = useState(true)
  const [metadataError, setMetadataError] = useState<string | null>(null)
  const [ledgerError, setLedgerError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingTransactionId, setEditingTransactionId] = useState<string | null>(null)
  const [pendingDeleteTransaction, setPendingDeleteTransaction] = useState<PortfolioTransactionRecord | null>(null)
  const [deletingTransaction, setDeletingTransaction] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [selectedTransactionIds, setSelectedTransactionIds] = useState<Set<string>>(() => new Set())
  const [inspectorTab, setInspectorTab] = useState<TransactionInspectorTab>('fact')
  const [form, setForm] = useState<TransactionFormState>(() => buildInitialFormState([]))
  const [pricingAnchor, setPricingAnchor] = useState<PricingAnchor>('price')
  const [historicalQuote, setHistoricalQuote] = useState<{
    instrumentId: string
    requestedAsOfDate: string
    price: number
    asOfDate: string
    currency: string
    quoteBasis: string
    stale: boolean
    price_unit: TransactionPriceContract['price_unit']
    price_scale: number
  } | null>(null)
  const [historicalQuoteLoading, setHistoricalQuoteLoading] = useState(false)
  const [historicalQuoteError, setHistoricalQuoteError] = useState<string | null>(null)
  const [positionPreview, setPositionPreview] = useState<PortfolioTransactionPositionPreviewResponse | null>(null)
  const [positionPreviewLoading, setPositionPreviewLoading] = useState(false)
  const [positionPreviewError, setPositionPreviewError] = useState<string | null>(null)
  const drawerDialogRef = useModalDialog(drawerOpen, () => {
    setDrawerOpen(false)
    setEditingTransactionId(null)
  })

  currentPortfolioIdRef.current = portfolioId

  const filters: PortfolioTransactionFilters = {
    account_id: searchParams.get('account_id') ?? '',
    transaction_type: searchParams.get('transaction_type') ?? '',
    instrument_id: searchParams.get('instrument_id') ?? '',
    start_date: searchParams.get('start_date') ?? '',
    end_date: searchParams.get('end_date') ?? '',
  }
  const selectedTransactionId = searchParams.get('transaction_id') ?? ''

  function patchSearchParams(
    patch: Record<string, string | null | undefined>,
  ) {
    const next = new URLSearchParams(searchParams)
    for (const [key, value] of Object.entries(patch)) {
      if (value == null || value === '') {
        next.delete(key)
      } else {
        next.set(key, value)
      }
    }
    setSearchParams(next, { replace: true })
  }

  useEffect(() => {
    if (!notice) {
      return undefined
    }
    const timeoutId = window.setTimeout(() => setNotice(null), 2800)
    return () => window.clearTimeout(timeoutId)
  }, [notice])

  useEffect(() => {
    let cancelled = false
    setPendingDeleteTransaction(null)
    setDeleteError(null)
    setDeletingTransaction(false)
    setSelectedTransactionIds(new Set())
    setInspectorTab('fact')

    if (!portfolioId) {
      setAccounts([])
      setInstruments([])
      setFxRates([])
      setMetadataError('Portfolio id is required.')
      setMetaLoading(false)
      return () => {
        cancelled = true
      }
    }

    setMetaLoading(true)
    setAccounts([])
    setInstruments([])
    setFxRates([])
    setForm(buildInitialFormState([]))
    setMetadataError(null)

    Promise.all([
      getPortfolioAccounts(portfolioId),
      getPortfolioInstruments(portfolioId),
      getPortfolioFxRates(portfolioId),
    ])
      .then(([accountsResponse, instrumentsResponse, fxRatesResponse]) => {
        if (cancelled) {
          return
        }

        setAccounts(accountsResponse.accounts)
        setInstruments(instrumentsResponse.instruments)
        setFxRates(fxRatesResponse.rates)
        setForm(buildInitialFormState(accountsResponse.accounts))
        setMetadataError(null)
      })
      .catch((error) => {
        if (!cancelled) {
          setMetadataError(error instanceof Error ? error.message : 'Failed to load transaction metadata.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setMetaLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    let cancelled = false

    if (!portfolioId) {
      setTransactionsWorkspace(null)
      setWorkspaceRequestedTransactionId(null)
      setLedgerError('Portfolio id is required.')
      setLoadingTransactions(false)
      return () => {
        cancelled = true
      }
    }

    setLoadingTransactions(true)
    setLedgerError(null)

    getPortfolioTransactionsWorkspace(portfolioId, {
      ...filters,
      transaction_id: selectedTransactionId || undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setTransactionsWorkspace(response)
          setWorkspaceRequestedTransactionId(selectedTransactionId)
          setLedgerError(null)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setLedgerError(error instanceof Error ? error.message : 'Failed to load transaction ledger.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingTransactions(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [
    portfolioId,
    filters.account_id,
    filters.transaction_type,
    filters.instrument_id,
    filters.start_date,
    filters.end_date,
    selectedTransactionId,
  ])

  const selectedAccount =
    accounts.find((account) => account.account_id === form.account_id) ??
    eligibleAccounts(form.transaction_type, accounts, form.transfer_object_type)[0]
  const cashAccounts = accounts.filter((account) => account.account_type === 'deposit_account')
  const counterpartyAccounts = eligibleCounterpartyAccounts(
    form.transaction_type,
    accounts,
    form.account_id,
    form.transfer_object_type,
  )
  const selectedCounterparty =
    accounts.find((account) => account.account_id === form.counterparty_account_id) ??
    counterpartyAccounts[0] ??
    null
  const isFxConversion = isFxConversionTransaction(form.transaction_type)
  const shouldRequireInstrument = requiresInstrument(
    form.transaction_type,
    selectedAccount?.account_type,
    form.transfer_object_type,
  )
  const shouldAllowInstrument = allowsInstrument(
    form.transaction_type,
    selectedAccount?.account_type,
    form.transfer_object_type,
  )
  const shouldRequireSettlement = requiresSettlement(form.transaction_type, selectedAccount?.account_type)
  const shouldUseQuantity = usesQuantity(
    form.transaction_type,
    selectedAccount?.account_type,
    form.transfer_object_type,
  )

  if (!portfolioId) {
    return <Navigate replace to="/portfolios" />
  }
  const shouldUsePrice = usesPrice(form.transaction_type)
  const shouldShowFees = showsFeeField(form.transaction_type)
  const shouldShowTaxes = showsTaxField(form.transaction_type)
  const shouldShowFeeCategory =
    form.transaction_type === 'fee' ||
    (shouldShowFees && Number.isFinite(Number(form.fees)) && Number(form.fees) > 0)
  const selectedInstrument = instruments.find((instrument) => instrument.instrument_id === form.instrument_id) ?? null
  const positionPreviewAccountRole: 'selected' | 'source' =
    form.transaction_type === 'transfer_in' && form.transfer_object_type === 'position' ? 'source' : 'selected'
  const positionPreviewAccountId =
    positionPreviewAccountRole === 'source'
      ? selectedCounterparty?.account_id ?? ''
      : selectedAccount?.account_id ?? form.account_id
  const resolvedTransactionCurrency =
    selectedInstrument?.currency?.toUpperCase() ?? selectedAccount?.currency?.toUpperCase() ?? 'USD'
  const resolvedCounterpartyCurrency = selectedCounterparty?.currency?.toUpperCase() ?? ''
  const sharedFxRate = useMemo(() => {
    if (!isFxConversion || !resolvedTransactionCurrency || !resolvedCounterpartyCurrency) {
      return null
    }

    return (
      fxRates.find(
        (rate) =>
          rate.base_currency === resolvedTransactionCurrency &&
          rate.quote_currency === resolvedCounterpartyCurrency,
      ) ?? null
    )
  }, [fxRates, isFxConversion, resolvedCounterpartyCurrency, resolvedTransactionCurrency])
  const settlementAccountOptions = useMemo(() => {
    return cashAccounts
      .filter((account) => account.currency.toUpperCase() === resolvedTransactionCurrency)
      .sort((left, right) => left.account_name.localeCompare(right.account_name))
  }, [cashAccounts, resolvedTransactionCurrency])
  const deferredInstrumentSearch = useDeferredValue(form.instrument_search)
  const instrumentInputValue = selectedInstrument && !form.instrument_search
    ? instrumentSearchLabel(selectedInstrument)
    : form.instrument_search

  const filteredInstrumentOptions = useMemo(() => {
    const normalizedSearch = deferredInstrumentSearch.trim().toLowerCase()
    if (!normalizedSearch) {
      return []
    }
    return instruments
      .filter((instrument) =>
        isSelectableInstrument(
          form.transaction_type,
          instrument,
          selectedAccount,
          selectedAccount?.currency,
          form.transfer_object_type,
        ),
      )
      .filter((instrument) => {
        if (!normalizedSearch) {
          return true
        }

        const haystack = [
          instrument.instrument_name,
          instrument.instrument_type,
          instrument.currency,
          primaryIdentifier(instrument),
          instrumentSearchLabel(instrument),
        ]
          .join(' ')
          .toLowerCase()
        return haystack.includes(normalizedSearch)
      })
      .slice(0, 12)
  }, [
    deferredInstrumentSearch,
    form.transaction_type,
    form.transfer_object_type,
    instruments,
    selectedAccount,
    selectedAccount?.currency,
  ])
  const selectedInstrumentLabel = selectedInstrument ? instrumentSearchLabel(selectedInstrument) : ''
  const showInstrumentResults =
    shouldAllowInstrument &&
    form.instrument_search.trim() !== '' &&
    (!selectedInstrument || form.instrument_search.trim() !== selectedInstrumentLabel)
  const activeHistoricalQuote =
    historicalQuote?.instrumentId === selectedInstrument?.instrument_id &&
    historicalQuote?.requestedAsOfDate === form.trade_date
      ? historicalQuote
      : null
  const transactionPriceContract = resolveTransactionPriceContract([
    ...(activeHistoricalQuote ? [activeHistoricalQuote] : []),
    ...(selectedInstrument?.latest_market_data ?? []),
  ])

  const computedUnitPrice =
    form.price.trim() ||
    (() => {
      if (!shouldUseQuantity || !shouldUsePrice) {
        return ''
      }
      const quantity = Number(form.quantity)
      const grossAmount = Number(form.gross_amount)
      if (!Number.isFinite(quantity) || !Number.isFinite(grossAmount) || quantity <= 0 || grossAmount <= 0) {
        return ''
      }
      const resolved = calculateTransactionUnitPrice(
        transactionPriceContract,
        quantity,
        grossAmount,
      )
      return resolved == null ? '' : formatCalculatedFormNumber(resolved, 6)
    })()
  const computedGrossAmount =
    form.gross_amount.trim() ||
    (() => {
      if (!shouldUseQuantity || !shouldUsePrice) {
        return ''
      }
      const quantity = Number(form.quantity)
      const price = Number(form.price)
      if (!Number.isFinite(quantity) || !Number.isFinite(price) || quantity <= 0 || price <= 0) {
        return ''
      }
      const resolved = calculateTransactionGrossAmount(
        transactionPriceContract,
        quantity,
        price,
      )
      return resolved == null ? '' : formatCalculatedFormNumber(resolved, 2)
    })()
  const resolvedFxRate = form.fx_rate.trim() || (sharedFxRate?.rate ? sharedFxRate.rate.toFixed(6) : '')
  const computedCounterAmount =
    form.counter_amount.trim() ||
    (() => {
      if (!isFxConversion) {
        return ''
      }
      const sourceAmount = Number(computedGrossAmount)
      const fxRate = Number(resolvedFxRate)
      if (!Number.isFinite(sourceAmount) || !Number.isFinite(fxRate) || sourceAmount <= 0 || fxRate <= 0) {
        return ''
      }
      return (sourceAmount * fxRate).toFixed(2)
    })()

  useEffect(() => {
    if (!drawerOpen || !portfolioId || !shouldUsePrice || !selectedInstrument || !form.trade_date) {
      autoQuoteKeyRef.current = null
      autoGrossDerivedRef.current = false
      setHistoricalQuote(null)
      setHistoricalQuoteError(null)
      setHistoricalQuoteLoading(false)
      return
    }

    let cancelled = false
    const instrumentId = selectedInstrument.instrument_id
    const tradeDate = form.trade_date
    const quoteKey = `${instrumentId}:${tradeDate}`
    setHistoricalQuoteLoading(true)
    setHistoricalQuoteError(null)

    getPortfolioTransactionExecutionQuote(portfolioId, instrumentId, tradeDate)
      .then((response) => {
        if (cancelled) {
          return
        }

        const unavailableReason = response.unavailable_reason?.trim() || null
        if (
          response.status === 'unavailable' ||
          unavailableReason ||
          response.value == null ||
          !response.quote_date ||
          !response.quote_basis ||
          response.price_unit == null ||
          response.price_scale == null
        ) {
          const shouldClearAutoQuote = autoQuoteKeyRef.current !== null
          if (shouldClearAutoQuote) {
            autoGrossDerivedRef.current = false
            setForm((current) =>
              current.instrument_id === instrumentId && current.trade_date === tradeDate
                ? {
                    ...current,
                    price: '',
                    gross_amount: '',
                  }
                : current,
            )
          }
          autoQuoteKeyRef.current = null
          setHistoricalQuote(null)
          setHistoricalQuoteError(
            executionQuoteUnavailableMessage(unavailableReason) ||
              'Execution quote unavailable: no eligible unadjusted quote on or before this trade date.',
          )
          return
        }

        const quoteValue = response.value
        setHistoricalQuote({
          instrumentId,
          requestedAsOfDate: tradeDate,
          price: quoteValue,
          asOfDate: response.quote_date,
          currency: response.currency,
          quoteBasis: response.quote_basis,
          stale: response.stale,
          price_unit: response.price_unit,
          price_scale: response.price_scale,
        })
        setForm((current) => {
          const grossCanFollowQuote = !current.gross_amount.trim() || autoGrossDerivedRef.current
          const canApplyQuote =
            grossCanFollowQuote && (!current.price.trim() || autoQuoteKeyRef.current !== null)
          if (
            current.instrument_id !== instrumentId ||
            current.trade_date !== tradeDate ||
            !canApplyQuote ||
            !usesPrice(current.transaction_type)
          ) {
            return current
          }

          const next = {
            ...current,
            price: formatCalculatedFormNumber(quoteValue, 6),
          }
          const quantity = parsePositiveFormNumber(next.quantity)
          if (quantity && grossCanFollowQuote) {
            const resolved = calculateTransactionGrossAmount(
              response,
              quantity,
              quoteValue,
            )
            if (resolved != null) {
              next.gross_amount = formatCalculatedFormNumber(resolved, 2)
              autoGrossDerivedRef.current = true
            }
          }
          autoQuoteKeyRef.current = quoteKey
          return next
        })
      })
      .catch((error) => {
        if (!cancelled) {
          const shouldClearAutoQuote = autoQuoteKeyRef.current !== null
          if (shouldClearAutoQuote) {
            autoGrossDerivedRef.current = false
            setForm((current) =>
              current.instrument_id === instrumentId && current.trade_date === tradeDate
                ? {
                    ...current,
                    price: '',
                    gross_amount: '',
                  }
                : current,
            )
          }
          autoQuoteKeyRef.current = null
          setHistoricalQuote(null)
          setHistoricalQuoteError(error instanceof Error ? error.message : 'Failed to load historical quote.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setHistoricalQuoteLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [
    drawerOpen,
    form.trade_date,
    portfolioId,
    selectedInstrument?.instrument_id,
    shouldUsePrice,
  ])

  useEffect(() => {
    if (
      !drawerOpen ||
      !portfolioId ||
      !shouldUseQuantity ||
      !selectedInstrument ||
      !positionPreviewAccountId ||
      !form.trade_date
    ) {
      autoQuantityKeyRef.current = null
      setPositionPreview(null)
      setPositionPreviewError(null)
      setPositionPreviewLoading(false)
      return
    }

    let cancelled = false
    const instrumentId = selectedInstrument.instrument_id
    const accountId = positionPreviewAccountId
    const tradeDate = form.trade_date
    setPositionPreviewLoading(true)
    setPositionPreviewError(null)

    getPortfolioTransactionPositionPreview(portfolioId, {
      account_id: accountId,
      instrument_id: instrumentId,
      as_of_date: tradeDate,
      trade_time: form.trade_time || undefined,
      exclude_transaction_id: editingTransactionId || undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setPositionPreview(response)
          if (form.transaction_type === 'sell') {
            const availableQuantity = Math.max(0, response.quantity)
            const quantityKey = `sell:${response.account_id}:${response.instrument_id}:${response.as_of_date}`
            setForm((current) => {
              const canApplyQuantity = !current.quantity.trim() || autoQuantityKeyRef.current !== null
              if (
                current.transaction_type !== 'sell' ||
                current.instrument_id !== response.instrument_id ||
                current.account_id !== response.account_id ||
                current.trade_date !== response.as_of_date ||
                !canApplyQuantity
              ) {
                return current
              }

              const next = {
                ...current,
                quantity: formatCalculatedFormNumber(availableQuantity, 6),
              }
              if (availableQuantity <= 0) {
                next.gross_amount = ''
                autoGrossDerivedRef.current = false
                autoQuantityKeyRef.current = quantityKey
                return next
              }
              const nextQuantity = parsePositiveFormNumber(next.quantity)
              const nextPrice = parsePositiveFormNumber(next.price)
              const nextGrossAmount = parsePositiveFormNumber(next.gross_amount)
              if (nextQuantity && nextPrice) {
                const resolved = calculateTransactionGrossAmount(
                  transactionPriceContract,
                  nextQuantity,
                  nextPrice,
                )
                if (resolved != null) {
                  next.gross_amount = formatCalculatedFormNumber(resolved, 2)
                  autoGrossDerivedRef.current = true
                }
              } else if (nextQuantity && nextGrossAmount) {
                const resolved = calculateTransactionUnitPrice(
                  transactionPriceContract,
                  nextQuantity,
                  nextGrossAmount,
                )
                if (resolved != null) {
                  next.price = formatCalculatedFormNumber(resolved, 6)
                }
              }
              autoQuantityKeyRef.current = quantityKey
              return next
            })
          }
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setPositionPreview(null)
          setPositionPreviewError(error instanceof Error ? error.message : 'Failed to load account holding.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPositionPreviewLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [
    drawerOpen,
    editingTransactionId,
    form.trade_date,
    form.trade_time,
    form.transaction_type,
    portfolioId,
    positionPreviewAccountId,
    selectedInstrument?.instrument_id,
    shouldUseQuantity,
    transactionPriceContract?.price_scale,
    transactionPriceContract?.price_unit,
  ])

  function selectInstrument(instrument: SharedInstrumentRecord) {
    setForm((current) => {
      const isChangingInstrument = Boolean(current.instrument_id && current.instrument_id !== instrument.instrument_id)
      if (isChangingInstrument) {
        autoQuoteKeyRef.current = null
        autoQuantityKeyRef.current = null
        autoGrossDerivedRef.current = false
      }
      return {
        ...current,
        instrument_id: instrument.instrument_id,
        instrument_search: instrumentSearchLabel(instrument),
        price: isChangingInstrument ? '' : current.price,
        quantity: isChangingInstrument ? '' : current.quantity,
        gross_amount: isChangingInstrument ? '' : current.gross_amount,
      }
    })
    window.setTimeout(() => accountSelectRef.current?.focus(), 0)
  }

  function updatePricingField(
    field: 'quantity' | 'price' | 'gross_amount',
    value: string,
  ) {
    if (field === 'price') {
      autoQuoteKeyRef.current = null
      setPricingAnchor('price')
    } else if (field === 'gross_amount') {
      autoQuoteKeyRef.current = null
      autoGrossDerivedRef.current = false
      setPricingAnchor('gross_amount')
    } else if (field === 'quantity') {
      autoQuantityKeyRef.current = null
    }

    setForm((current) => {
      let nextValue = value
      if (
        field === 'quantity' &&
        current.transaction_type === 'sell' &&
        positionPreview &&
        positionPreview.account_id === current.account_id &&
        positionPreview.instrument_id === current.instrument_id &&
        positionPreview.as_of_date === current.trade_date
      ) {
        const requestedQuantity = parsePositiveFormNumber(value)
        const availableQuantity = Math.max(0, positionPreview.quantity)
        if (requestedQuantity && requestedQuantity > availableQuantity) {
          nextValue = formatCalculatedFormNumber(availableQuantity, 6)
        }
      }
      const next = {
        ...current,
        [field]: nextValue,
      }

      if (field === 'quantity') {
        const parsedQuantity = Number(next.quantity)
        if (Number.isFinite(parsedQuantity) && parsedQuantity <= 0) {
          next.gross_amount = ''
          autoGrossDerivedRef.current = false
          return next
        }
      }

      if (!shouldUseQuantity || !shouldUsePrice) {
        return next
      }

      const nextQuantity = parsePositiveFormNumber(next.quantity)
      const nextPrice = parsePositiveFormNumber(next.price)
      const nextGrossAmount = parsePositiveFormNumber(next.gross_amount)

      if (field === 'gross_amount' && nextQuantity && nextGrossAmount) {
        const resolved = calculateTransactionUnitPrice(
          transactionPriceContract,
          nextQuantity,
          nextGrossAmount,
        )
        if (resolved != null) {
          next.price = formatCalculatedFormNumber(resolved, 6)
        }
        return next
      }

      if (field === 'price' && nextQuantity && nextPrice) {
        const resolved = calculateTransactionGrossAmount(
          transactionPriceContract,
          nextQuantity,
          nextPrice,
        )
        if (resolved != null) {
          next.gross_amount = formatCalculatedFormNumber(resolved, 2)
          autoGrossDerivedRef.current = true
        }
        return next
      }

      if (field === 'quantity' && nextQuantity) {
        if (pricingAnchor === 'gross_amount' && nextGrossAmount) {
          const resolved = calculateTransactionUnitPrice(
            transactionPriceContract,
            nextQuantity,
            nextGrossAmount,
          )
          if (resolved != null) {
            next.price = formatCalculatedFormNumber(resolved, 6)
            autoGrossDerivedRef.current = false
          }
          return next
        }
        if (nextPrice) {
          const resolved = calculateTransactionGrossAmount(
            transactionPriceContract,
            nextQuantity,
            nextPrice,
          )
          if (resolved != null) {
            next.gross_amount = formatCalculatedFormNumber(resolved, 2)
            autoGrossDerivedRef.current = true
          }
        }
      }

      return next
    })
  }

  useEffect(() => {
    const nextEligibleAccounts = eligibleAccounts(form.transaction_type, accounts, form.transfer_object_type)
    if (nextEligibleAccounts.length === 0) {
      return
    }

    if (!nextEligibleAccounts.some((account) => account.account_id === form.account_id)) {
      const nextAccount = nextEligibleAccounts[0]
      setForm((current) => ({
        ...current,
        account_id: nextAccount.account_id,
      }))
    }
  }, [accounts, form.account_id, form.transaction_type, form.transfer_object_type])

  useEffect(() => {
    if (shouldRequireSettlement) {
      const defaultSettlementId =
        settlementAccountOptions.find(
          (account) =>
            account.account_id === selectedAccount?.default_settlement_cash_account_id &&
            account.currency.toUpperCase() === resolvedTransactionCurrency,
        )?.account_id ??
        settlementAccountOptions.find((account) => account.currency.toUpperCase() === resolvedTransactionCurrency)
          ?.account_id ??
        settlementAccountOptions[0]?.account_id ??
        ''
      if (
        defaultSettlementId &&
        !settlementAccountOptions.some((account) => account.account_id === form.settlement_cash_account_id)
      ) {
        setForm((current) => ({
          ...current,
          settlement_cash_account_id: defaultSettlementId,
        }))
      }
      return
    }

    if (form.settlement_cash_account_id) {
      setForm((current) => ({
        ...current,
        settlement_cash_account_id: '',
      }))
    }
  }, [
    resolvedTransactionCurrency,
    form.settlement_cash_account_id,
    selectedAccount?.default_settlement_cash_account_id,
    settlementAccountOptions,
    shouldRequireSettlement,
  ])

  useEffect(() => {
    if (!isFxConversion) {
      if (form.counter_amount || form.fx_rate) {
        setForm((current) => ({
          ...current,
          counter_amount: '',
          fx_rate: '',
        }))
      }
      return
    }

    if (!form.fx_rate && sharedFxRate?.rate) {
      setForm((current) => ({
        ...current,
        fx_rate: sharedFxRate.rate.toFixed(6),
      }))
    }
  }, [form.counter_amount, form.fx_rate, isFxConversion, sharedFxRate?.rate])

  useEffect(() => {
    if (!shouldAllowInstrument && form.instrument_id) {
      setForm((current) => ({
        ...current,
        instrument_id: '',
        instrument_search: '',
      }))
    }
  }, [form.instrument_id, shouldAllowInstrument])

  useEffect(() => {
    if (!selectedInstrument || !selectedAccount) {
      return
    }
    if (selectedInstrument.currency.toUpperCase() === selectedAccount.currency.toUpperCase()) {
      if (
        isSelectableInstrument(
          form.transaction_type,
          selectedInstrument,
          selectedAccount,
          selectedAccount.currency,
          form.transfer_object_type,
        )
      ) {
        return
      }
    }
    autoQuoteKeyRef.current = null
    autoQuantityKeyRef.current = null
    autoGrossDerivedRef.current = false
    setForm((current) => ({
      ...current,
      instrument_id: '',
      instrument_search: '',
      quantity: '',
      price: '',
      gross_amount: '',
    }))
  }, [
    form.transaction_type,
    form.transfer_object_type,
    selectedAccount,
    selectedInstrument,
  ])

  useEffect(() => {
    if (isFxConversion) {
      if (!counterpartyAccounts.some((account) => account.account_id === form.counterparty_account_id)) {
        setForm((current) => ({
          ...current,
          counterparty_account_id: counterpartyAccounts[0]?.account_id ?? '',
        }))
      }
      return
    }

    if (!isTransferTransaction(form.transaction_type)) {
      if (form.counterparty_account_id || form.transfer_object_type !== 'cash') {
        setForm((current) => ({
          ...current,
          counterparty_account_id: '',
          transfer_object_type: 'cash',
        }))
      }
      return
    }

    if (!counterpartyAccounts.some((account) => account.account_id === form.counterparty_account_id)) {
      setForm((current) => ({
        ...current,
        counterparty_account_id: counterpartyAccounts[0]?.account_id ?? '',
      }))
    }
  }, [
    counterpartyAccounts,
    form.counterparty_account_id,
    form.transaction_type,
    form.transfer_object_type,
    isFxConversion,
  ])

  useEffect(() => {
    if (!shouldUseQuantity && form.quantity) {
      setForm((current) => ({
        ...current,
        quantity: '',
      }))
    }
  }, [form.quantity, shouldUseQuantity])

  useEffect(() => {
    if (!shouldUsePrice && form.price) {
      setForm((current) => ({
        ...current,
        price: '',
      }))
    }
  }, [form.price, shouldUsePrice])

  useEffect(() => {
    if (!shouldShowFees && form.fees !== '0') {
      setForm((current) => ({
        ...current,
        fees: '0',
      }))
    }
  }, [form.fees, shouldShowFees])

  useEffect(() => {
    if (!shouldShowFeeCategory && form.fee_category !== 'unknown') {
      setForm((current) => ({
        ...current,
        fee_category: 'unknown',
      }))
    }
  }, [form.fee_category, shouldShowFeeCategory])

  useEffect(() => {
    if (!shouldShowTaxes && form.taxes !== '0') {
      setForm((current) => ({
        ...current,
        taxes: '0',
      }))
    }
  }, [form.taxes, shouldShowTaxes])

  useEffect(() => {
    if (!supportsEntitlementDate(form.transaction_type) && form.entitlement_date) {
      setForm((current) => ({
        ...current,
        entitlement_date: '',
      }))
    }
  }, [form.entitlement_date, form.transaction_type])

  useEffect(() => {
    if (!supportsAcquisitionDate(form.transaction_type, selectedAccount?.account_type) && form.acquisition_date) {
      setForm((current) => ({
        ...current,
        acquisition_date: '',
      }))
    }
  }, [form.acquisition_date, form.transaction_type, selectedAccount?.account_type])

  async function refreshTransactions(
    activeFilters: PortfolioTransactionFilters,
    selectedTransactionOverride?: string | null,
  ) {
    setLoadingTransactions(true)
    setLedgerError(null)
    try {
      const resolvedTransactionId =
        selectedTransactionOverride === undefined
          ? selectedTransactionId || undefined
          : selectedTransactionOverride || undefined
      const response = await getPortfolioTransactionsWorkspace(portfolioId, {
        ...activeFilters,
        transaction_id: resolvedTransactionId,
      })
      setTransactionsWorkspace(response)
      setWorkspaceRequestedTransactionId(resolvedTransactionId ?? '')
      setLedgerError(null)
    } catch (error) {
      setLedgerError(error instanceof Error ? error.message : 'Failed to load transaction ledger.')
    } finally {
      setLoadingTransactions(false)
    }
  }

  const pageErrors = [metadataError, ledgerError].filter(
    (message, index, messages): message is string => Boolean(message) && messages.indexOf(message) === index,
  )
  const pageError = pageErrors.length ? pageErrors.join(' ') : null

  function openCreateDrawer() {
    setEditingTransactionId(null)
    setForm(buildInitialFormState(accounts))
    setPricingAnchor('price')
    autoQuoteKeyRef.current = null
    autoQuantityKeyRef.current = null
    autoGrossDerivedRef.current = false
    setFormError(null)
    setNotice(null)
    setDrawerOpen(true)
  }

  function openEditDrawer(transaction: PortfolioTransactionRecord) {
    setEditingTransactionId(transaction.transaction_id)
    setForm(buildFormStateFromTransaction(transaction))
    setPricingAnchor('price')
    autoQuoteKeyRef.current = null
    autoQuantityKeyRef.current = null
    autoGrossDerivedRef.current = false
    setFormError(null)
    setNotice(null)
    setDrawerOpen(true)
  }

  useEffect(() => {
    if (!drawerOpen) {
      return
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }
      setDrawerOpen(false)
      setEditingTransactionId(null)
    }
    window.addEventListener('keydown', handleKeyDown)
    window.setTimeout(() => {
      const firstControl = securitySearchRef.current ?? accountSelectRef.current
      firstControl?.focus()
    }, 0)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [drawerOpen])

  async function handleCreateTransaction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    setNotice(null)
    const isEditingTransaction = editingTransactionId !== null
    const editingTransaction = isEditingTransaction
      ? transactionsWorkspace?.transactions.find(
          (transaction) => transaction.transaction_id === editingTransactionId,
        ) ?? null
      : null

    const resolvedAccount =
      accounts.find((account) => account.account_id === form.account_id) ?? selectedAccount ?? null
    if (!resolvedAccount) {
      setFormError('Select an account before saving.')
      return
    }

    if (shouldRequireInstrument && !selectedInstrument) {
      setFormError('Select an instrument from the shared registry.')
      return
    }

    if (isTransferTransaction(form.transaction_type) && !selectedCounterparty) {
      setFormError('Select the paired counterparty account for the internal transfer.')
      return
    }

    if (isFxConversion && !selectedCounterparty) {
      setFormError('Select the target cash account for the FX conversion.')
      return
    }

    if (shouldRequireSettlement && !form.settlement_cash_account_id) {
      setFormError('Select a settlement cash account before saving.')
      return
    }

    if (shouldUseQuantity) {
      const quantity = Number(form.quantity)
      if (!Number.isFinite(quantity) || quantity <= 0) {
        setFormError('Enter a positive quantity.')
        return
      }
      if (enteredQuantityExceedsPosition) {
        setFormError('Entered shares exceed the account holding as of the trade date.')
        return
      }
    }

    if (shouldUsePrice) {
      if (!transactionPriceContract) {
        setFormError('Instrument price contract is unavailable or inconsistent.')
        return
      }
      const price = Number(computedUnitPrice)
      if (!Number.isFinite(price) || price <= 0) {
        setFormError('Enter a positive price.')
        return
      }
    }

    if (isFxConversion) {
      const sourceAmount = Number(computedGrossAmount)
      const targetAmount = Number(computedCounterAmount)
      const fxRate = Number(resolvedFxRate)
      if (!Number.isFinite(sourceAmount) || sourceAmount <= 0) {
        setFormError('Enter a positive source amount.')
        return
      }
      if (!Number.isFinite(targetAmount) || targetAmount <= 0) {
        setFormError('Enter a positive received amount.')
        return
      }
      if (!Number.isFinite(fxRate) || fxRate <= 0) {
        setFormError('Enter a positive FX rate.')
        return
      }
      if (Math.abs(targetAmount - sourceAmount * fxRate) > 0.01) {
        setFormError('Received amount must match source amount multiplied by FX rate.')
        return
      }

      const payload: PortfolioTransactionCreatePayload = {
        transaction_type: form.transaction_type,
        trade_date: form.trade_date,
        trade_time: form.trade_time || null,
        settlement_date: form.settlement_date || form.trade_date,
        entitlement_date: null,
        acquisition_date: null,
        account_id: resolvedAccount.account_id,
        settlement_cash_account_id: null,
        instrument_id: null,
        quantity: null,
        price: null,
        gross_amount: sourceAmount,
        counter_amount: targetAmount,
        fx_rate: fxRate,
        fees: 0,
        taxes: 0,
        currency: resolvedTransactionCurrency,
        counterparty_account_id: selectedCounterparty.account_id,
        note: form.note.trim() || null,
        expected_row_version: editingTransaction?.row_version,
      }

      try {
        const created = isEditingTransaction
          ? await updatePortfolioTransaction(portfolioId, editingTransactionId, payload)
          : await createPortfolioTransaction(portfolioId, payload)
        setDrawerOpen(false)
        setEditingTransactionId(null)
        setNotice(
          isEditingTransaction
            ? `Updated ${formatLabel(created.transaction_type)} transaction ${created.transaction_id}.`
            : `Added ${formatLabel(created.transaction_type)} transaction ${created.transaction_id}.`,
        )
        setForm(buildInitialFormState(accounts))
        patchSearchParams({
          account_id: filters.account_id || created.account.account_id,
          transaction_id: created.transaction_id,
        })
        await refreshTransactions(filters, created.transaction_id)
      } catch (error) {
        setFormError(
          error instanceof Error
            ? error.message
            : isEditingTransaction
              ? 'Failed to update FX conversion.'
              : 'Failed to create FX conversion.',
        )
      }
      return
    }

    if (isTransferTransaction(form.transaction_type)) {
      if (isEditingTransaction) {
        setFormError('Paired internal transfers must be deleted and recreated as a batch.')
        return
      }
      const isTransferOut = form.transaction_type === 'transfer_out'
      const transferObjectType = form.transfer_object_type === 'position' ? 'position' : 'cash'
      const rawGrossAmount = computedGrossAmount.trim()
      const parsedGrossAmount = rawGrossAmount ? Number(rawGrossAmount) : NaN
      if (transferObjectType === 'cash') {
        if (!Number.isFinite(parsedGrossAmount) || parsedGrossAmount <= 0) {
          setFormError('Enter a positive cash amount for the transfer.')
          return
        }
      } else if (rawGrossAmount && (!Number.isFinite(parsedGrossAmount) || parsedGrossAmount <= 0)) {
        setFormError('Transferred cost basis must be positive when entered.')
        return
      }
      const payload = {
        trade_date: form.trade_date,
        trade_time: form.trade_time || null,
        settlement_date: form.settlement_date || form.trade_date,
        transfer_object_type: transferObjectType,
        from_account_id: isTransferOut ? resolvedAccount.account_id : selectedCounterparty!.account_id,
        to_account_id: isTransferOut ? selectedCounterparty!.account_id : resolvedAccount.account_id,
        instrument_id: transferObjectType === 'position' ? selectedInstrument?.instrument_id ?? null : null,
        quantity: shouldUseQuantity && form.quantity ? Number(form.quantity) : null,
        gross_amount:
          transferObjectType === 'position'
            ? rawGrossAmount
              ? parsedGrossAmount
              : null
            : parsedGrossAmount,
        note: form.note.trim() || null,
      } as const

      try {
        const created = await createPortfolioInternalTransfer(portfolioId, payload)
        setDrawerOpen(false)
        setNotice(`Added paired ${formatLabel(transferObjectType)} transfer ${created.transfer_group_id}.`)
        setForm(buildInitialFormState(accounts))
        patchSearchParams({
          account_id: filters.account_id || payload.from_account_id,
          transaction_id: created.transactions[0]?.transaction_id ?? null,
        })
        await refreshTransactions(filters, created.transactions[0]?.transaction_id ?? null)
      } catch (error) {
        setFormError(error instanceof Error ? error.message : 'Failed to create internal transfer.')
      }
      return
    }

    const grossAmount = Number(computedGrossAmount)
    if (!Number.isFinite(grossAmount) || grossAmount <= 0) {
      setFormError('Enter a positive gross amount.')
      return
    }

    if (
      shouldRequireSettlement &&
      settlementAccountOptions.find((account) => account.account_id === form.settlement_cash_account_id)?.currency
        .toUpperCase() !== resolvedTransactionCurrency
    ) {
      setFormError('Settlement cash account currency must match the transaction currency.')
      return
    }

    const payload: PortfolioTransactionCreatePayload = {
      transaction_type: form.transaction_type,
      trade_date: form.trade_date,
      trade_time: form.trade_time || null,
      settlement_date: form.settlement_date || form.trade_date,
      entitlement_date: supportsEntitlementDate(form.transaction_type)
        ? form.entitlement_date || form.trade_date
        : null,
      acquisition_date: supportsAcquisitionDate(form.transaction_type, resolvedAccount.account_type)
        ? form.acquisition_date || null
        : null,
      account_id: resolvedAccount.account_id,
      settlement_cash_account_id: shouldRequireSettlement ? form.settlement_cash_account_id || null : null,
      instrument_id: shouldAllowInstrument ? selectedInstrument?.instrument_id ?? null : null,
      quantity: shouldUseQuantity && form.quantity ? Number(form.quantity) : null,
      price: shouldUsePrice && computedUnitPrice ? Number(computedUnitPrice) : null,
      gross_amount: grossAmount,
      counter_amount: null,
      fx_rate: null,
      fees: shouldShowFees && form.fees ? Number(form.fees) : 0,
      fee_category: shouldShowFeeCategory ? form.fee_category : 'unknown',
      taxes: shouldShowTaxes && form.taxes ? Number(form.taxes) : 0,
      currency: resolvedTransactionCurrency,
      note: form.note.trim() || null,
      expected_row_version: editingTransaction?.row_version,
    }

    try {
      const created = isEditingTransaction
        ? await updatePortfolioTransaction(portfolioId, editingTransactionId, payload)
        : await createPortfolioTransaction(portfolioId, payload)
      setDrawerOpen(false)
      setEditingTransactionId(null)
      setNotice(
        isEditingTransaction
          ? `Updated ${formatLabel(created.transaction_type)} transaction ${created.transaction_id}.`
          : `Added ${formatLabel(created.transaction_type)} transaction ${created.transaction_id}.`,
      )
      setForm(buildInitialFormState(accounts))
      patchSearchParams({
        account_id: filters.account_id || created.account.account_id,
        transaction_id: created.transaction_id,
      })
      await refreshTransactions(filters, created.transaction_id)
    } catch (error) {
      setFormError(
        error instanceof Error
          ? error.message
          : isEditingTransaction
            ? 'Failed to update transaction.'
            : 'Failed to create transaction.',
      )
    }
  }

  async function handleDeleteTransaction() {
    const transaction = pendingDeleteTransaction
    const targetPortfolioId = portfolioId
    if (!transaction || !targetPortfolioId || deletingTransaction) {
      return
    }
    const deletingTransferPair = Boolean(transaction.transfer_group_id)
    const expectedRowVersions = transactionsWorkspace?.delete_scope_row_versions
    if (!expectedRowVersions || expectedRowVersions[transaction.transaction_id] !== transaction.row_version) {
      setDeleteError('Transaction delete scope is stale. Reload the ledger and retry.')
      return
    }
    setFormError(null)
    setDeleteError(null)
    setNotice(null)
    setDeletingTransaction(true)

    try {
      const deleted = await deletePortfolioTransaction(
        targetPortfolioId,
        transaction.transaction_id,
        expectedRowVersions,
      )
      if (currentPortfolioIdRef.current !== targetPortfolioId) {
        return
      }
      setDrawerOpen(false)
      setEditingTransactionId(null)
      setForm(buildInitialFormState(accounts))
      setPendingDeleteTransaction(null)
      patchSearchParams({ transaction_id: null })
      await refreshTransactions(filters, null)
      setNotice(
        deletingTransferPair
          ? `Deleted transfer pair ${deleted.transfer_group_id}.`
          : `Deleted transaction ${transaction.transaction_id}.`,
      )
    } catch (error) {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setDeleteError(error instanceof Error ? error.message : 'Failed to delete transaction.')
      }
    } finally {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setDeletingTransaction(false)
      }
    }
  }

  const summary = transactionsWorkspace?.summary
  const selectedTransaction = transactionsWorkspace?.selected_transaction ?? null
  const isEditingTransaction = editingTransactionId !== null

  useEffect(() => {
    const nextTransactionId = resolveWorkspaceTransactionSelection({
      currentTransactionId: selectedTransactionId,
      workspaceRequestedTransactionId,
      workspaceSelectedTransactionId: transactionsWorkspace?.selected_transaction_id,
    })
    if (nextTransactionId !== undefined) {
      patchSearchParams({ transaction_id: nextTransactionId })
    }
  }, [selectedTransactionId, transactionsWorkspace, workspaceRequestedTransactionId])

  const accountNameById = useMemo(
    () => Object.fromEntries(accounts.map((account) => [account.account_id, account.account_name])),
    [accounts],
  )
  const accountCurrencyById = useMemo(
    () => Object.fromEntries(accounts.map((account) => [account.account_id, account.currency])),
    [accounts],
  )
  const relatedPositionLots = transactionsWorkspace?.related_position_lots ?? []
  const visibleTransactions = transactionsWorkspace?.transactions ?? []
  const activeFilterCount = countActiveTransactionFilters(filters)
  const selectedVisibleTransactions = useMemo(
    () => visibleTransactions.filter((transaction) => selectedTransactionIds.has(transaction.transaction_id)),
    [selectedTransactionIds, visibleTransactions],
  )
  const allVisibleTransactionsSelected =
    visibleTransactions.length > 0 && selectedVisibleTransactions.length === visibleTransactions.length
  const ticketGrossNumber = Number(computedGrossAmount)
  const ticketGrossAmount =
    computedGrossAmount.trim() && Number.isFinite(ticketGrossNumber) && ticketGrossNumber >= 0
      ? ticketGrossNumber
      : null
  const ticketFeeAmount = shouldShowFees ? parseNonNegativeFormNumber(form.fees) : 0
  const ticketTaxAmount = shouldShowTaxes ? parseNonNegativeFormNumber(form.taxes) : 0
  const ticketNetCashEffect = previewNetCashEffect(
    form.transaction_type,
    ticketGrossAmount,
    ticketFeeAmount,
    ticketTaxAmount,
    form.transfer_object_type,
  )
  const ticketQuantity = parsePositiveFormNumber(form.quantity)
  const ticketQuantityDelta = quantityDeltaForPreview(
    form.transaction_type,
    form.transfer_object_type,
    ticketQuantity,
    positionPreviewAccountRole,
  )
  const projectedPositionQuantity =
    positionPreview && ticketQuantityDelta != null ? positionPreview.quantity + ticketQuantityDelta : null
  const enteredQuantityExceedsPosition =
    Boolean(positionPreview) &&
    ticketQuantityDelta != null &&
    ticketQuantityDelta < 0 &&
    projectedPositionQuantity != null &&
    projectedPositionQuantity < -1e-9
  const previewCurrency = resolvedTransactionCurrency || selectedAccount?.currency || 'USD'

  useEffect(() => {
    const visibleIds = new Set(visibleTransactions.map((transaction) => transaction.transaction_id))
    setSelectedTransactionIds((current) => {
      const retained = new Set(Array.from(current).filter((transactionId) => visibleIds.has(transactionId)))
      if (retained.size === current.size) {
        return current
      }
      return retained
    })
  }, [visibleTransactions])

  useEffect(() => {
    setInspectorTab('fact')
  }, [selectedTransactionId])

  function toggleTransactionSelection(transactionId: string) {
    setSelectedTransactionIds((current) => {
      const next = new Set(current)
      if (next.has(transactionId)) {
        next.delete(transactionId)
      } else {
        next.add(transactionId)
      }
      return next
    })
  }

  function toggleAllVisibleTransactions() {
    setSelectedTransactionIds(
      allVisibleTransactionsSelected
        ? new Set()
        : new Set(visibleTransactions.map((transaction) => transaction.transaction_id)),
    )
  }

  function exportTransactions(format: TableExportFormat) {
    const exportRows = selectedVisibleTransactions.length
      ? selectedVisibleTransactions
      : visibleTransactions
    downloadTable(
      `transactions-${portfolioId}-${new Date().toISOString().slice(0, 10)}`,
      buildTransactionExportRows(exportRows),
      format,
      'Transactions',
    )
  }
  const amountField = (
    <label className="transaction-ticket-field">
      <span>{grossAmountLabel(form.transaction_type)}</span>
      <input
        type="number"
        min="0"
        step="0.01"
        value={form.gross_amount}
        placeholder={
          isTransferTransaction(form.transaction_type) && form.transfer_object_type === 'position'
            ? computedGrossAmount || 'Optional carrying cost'
            : computedGrossAmount || '0.00'
        }
        onChange={(event) => updatePricingField('gross_amount', event.target.value)}
      />
    </label>
  )

  return (
    <PortfolioWorkspaceLayout
      activeSection="Transactions"
      toolbarLabel="View: Transaction Ledger"
      busy={metaLoading || loadingTransactions}
      controls={
        summary ? (
          <div className="portfolio-summary-strip">
            <article className="summary-card">
              <span className="summary-card-label">Facts</span>
              <strong className="summary-card-value">{summary.total_transactions}</strong>
            </article>
            <article className="summary-card">
              <span className="summary-card-label">Instrument Facts</span>
              <strong className="summary-card-value">{summary.instrument_transactions}</strong>
            </article>
            <article className="summary-card">
              <span className="summary-card-label">External Cash Flows</span>
              <strong className="summary-card-value">{summary.external_cash_flows}</strong>
            </article>
            <article className="summary-card">
              <span className="summary-card-label">Opening Balances</span>
              <strong className="summary-card-value">{summary.opening_balance_records}</strong>
            </article>
          </div>
        ) : undefined
      }
    >
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div>
            <div className="panel-title">Transaction Ledger</div>
            <div className="portfolio-detail-meta">
              {visibleTransactions.length} visible facts{activeFilterCount ? ` · ${activeFilterCount} active filters` : ''}
            </div>
          </div>
          <div className="transaction-filter-actions">
            {selectedVisibleTransactions.length ? (
              <span className="transaction-selection-count">{selectedVisibleTransactions.length} selected</span>
            ) : null}
            <DownloadFormatMenu
              wrapperClassName="portfolio-download-menu"
              buttonClassName="holdings-toolbar-button"
              menuClassName="portfolio-download-menu-list"
              itemClassName="portfolio-download-menu-item"
              buttonLabel={selectedVisibleTransactions.length ? 'Export Selected' : 'Export Ledger'}
              disabled={!visibleTransactions.length}
              onSelect={exportTransactions}
            />
            <button
              type="button"
              className="toolbar-link button-primary"
              disabled={metaLoading || accounts.length === 0}
              onClick={openCreateDrawer}
            >
              Add Transaction
            </button>
          </div>
        </div>

        <section className="transaction-filter-bar">
          <div className="transaction-filter-group">
            <label>
              <span>Account</span>
              <select
                className="toolbar-select transaction-filter-input"
                value={filters.account_id ?? ''}
                onChange={(event) =>
                  patchSearchParams({
                    account_id: event.target.value,
                    transaction_id: null,
                  })
                }
              >
                <option value="">All accounts</option>
                {accounts.map((account) => (
                  <option key={account.account_id} value={account.account_id}>
                    {account.account_name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Type</span>
              <select
                className="toolbar-select transaction-filter-input"
                value={filters.transaction_type ?? ''}
                onChange={(event) =>
                  patchSearchParams({
                    transaction_type: event.target.value,
                    transaction_id: null,
                  })
                }
              >
                <option value="">All types</option>
                {TRANSACTION_TYPES.map((transactionType) => (
                  <option key={transactionType} value={transactionType}>
                    {formatLabel(transactionType)}
                  </option>
                ))}
              </select>
            </label>
            <div className="transaction-filter-label">
              <span>Instrument</span>
              <InstrumentFilterCombobox
                instruments={instruments}
                value={filters.instrument_id ?? ''}
                disabled={metaLoading}
                onChange={(instrumentId) =>
                  patchSearchParams({
                    instrument_id: instrumentId,
                    transaction_id: null,
                  })
                }
              />
            </div>
            <label>
              <span>From</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={filters.start_date ?? ''}
                onChange={(event) =>
                  patchSearchParams({
                    start_date: event.target.value,
                    transaction_id: null,
                  })
                }
              />
            </label>
            <label>
              <span>To</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={filters.end_date ?? ''}
                onChange={(event) =>
                  patchSearchParams({
                    end_date: event.target.value,
                    transaction_id: null,
                  })
                }
              />
            </label>
          </div>

          <div className="transaction-filter-actions">
            <button
              type="button"
              className="toolbar-link"
              disabled={!activeFilterCount}
              onClick={() =>
                patchSearchParams({
                  account_id: null,
                  transaction_type: null,
                  instrument_id: null,
                  start_date: null,
                  end_date: null,
                  transaction_id: null,
                })
              }
            >
              Clear Filters
            </button>
          </div>
        </section>

        {activeFilterCount ? (
          <div className="transaction-active-filters" aria-label="Active transaction filters">
            {filters.account_id ? (
              <button type="button" onClick={() => patchSearchParams({ account_id: null, transaction_id: null })}>
                Account: {accountNameById[filters.account_id] || filters.account_id} <span aria-hidden="true">×</span>
              </button>
            ) : null}
            {filters.transaction_type ? (
              <button type="button" onClick={() => patchSearchParams({ transaction_type: null, transaction_id: null })}>
                Type: {formatLabel(filters.transaction_type)} <span aria-hidden="true">×</span>
              </button>
            ) : null}
            {filters.instrument_id ? (
              <button type="button" onClick={() => patchSearchParams({ instrument_id: null, transaction_id: null })}>
                Instrument: {primaryIdentifier(instruments.find((item) => item.instrument_id === filters.instrument_id) ?? {
                  instrument_id: filters.instrument_id,
                  identifiers: [],
                })} <span aria-hidden="true">×</span>
              </button>
            ) : null}
            {filters.start_date ? (
              <button type="button" onClick={() => patchSearchParams({ start_date: null, transaction_id: null })}>
                From: {filters.start_date} <span aria-hidden="true">×</span>
              </button>
            ) : null}
            {filters.end_date ? (
              <button type="button" onClick={() => patchSearchParams({ end_date: null, transaction_id: null })}>
                To: {filters.end_date} <span aria-hidden="true">×</span>
              </button>
            ) : null}
          </div>
        ) : null}

        {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
        {pageError ? <div className="error-state">{pageError}</div> : null}
        {deleteError ? <div className="error-state">{deleteError}</div> : null}
        {metaLoading || loadingTransactions ? (
          <CalculationStatus />
        ) : null}

        {!ledgerError && transactionsWorkspace ? (
          <div className="transaction-workbench-grid">
            <section className={`transaction-ledger-panel ${loadingTransactions ? 'transaction-ledger-panel-refreshing' : ''}`}>
              <div className="transaction-bulk-bar">
                <label className="transaction-select-all">
                  <input
                    type="checkbox"
                    checked={allVisibleTransactionsSelected}
                    onChange={toggleAllVisibleTransactions}
                  />
                  <span>Select visible</span>
                </label>
                <span>{visibleTransactions.length} ledger facts</span>
                {selectedVisibleTransactions.length ? (
                  <button type="button" onClick={() => setSelectedTransactionIds(new Set())}>
                    Clear selection
                  </button>
                ) : null}
              </div>
              <div className="table-shell transaction-table-shell" aria-busy={loadingTransactions}>
                <table className="transactions-table transaction-ledger-table">
                  <thead>
                    <tr>
                      <th className="transaction-select-column"><span className="sr-only">Select</span></th>
                      <th>Trade / Settle</th>
                      <th>Type / Scope</th>
                      <th>Account</th>
                      <th>Instrument</th>
                      <th>Units / Price</th>
                      <th>Gross / Net Cash</th>
                      <th>Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleTransactions.map((transaction) => {
                      const timeMeta = tradeTimeLabel(
                        transaction.trade_time,
                        transaction.trade_timezone,
                        transaction.trade_time_is_estimated,
                      )
                      const isSelected = transaction.transaction_id === selectedTransactionId
                      const isChecked = selectedTransactionIds.has(transaction.transaction_id)
                      return (
                        <tr
                          key={transaction.transaction_id}
                          className={isSelected ? 'transaction-row-active' : ''}
                          tabIndex={0}
                          aria-selected={isSelected}
                          onClick={() => patchSearchParams({ transaction_id: transaction.transaction_id })}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault()
                              patchSearchParams({ transaction_id: transaction.transaction_id })
                            }
                          }}
                        >
                          <td className="transaction-select-column">
                            <input
                              type="checkbox"
                              aria-label={`Select transaction ${transaction.transaction_id}`}
                              checked={isChecked}
                              onClick={(event) => event.stopPropagation()}
                              onChange={() => toggleTransactionSelection(transaction.transaction_id)}
                            />
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <strong>{transaction.trade_date}</strong>
                              <span className="holding-secondary">
                                {timeMeta.primary} · settle {transaction.settlement_date}
                              </span>
                            </div>
                          </td>
                          <td>
                            <div className="holding-name-stack">
                              <span><span className="transaction-type-pill">{formatLabel(transaction.transaction_type)}</span></span>
                              <span className="holding-secondary">{formatLabel(transaction.flow_scope)}</span>
                            </div>
                          </td>
                          <td className="transaction-account-cell">
                            <div className="holding-name-stack">
                              <Link
                                className="table-inline-link"
                                to={transactionAccountHref(portfolioId, transaction.account.account_id)}
                                onClick={stopTransactionRowSelection}
                              >
                                {transaction.account.account_name}
                              </Link>
                              <span className="holding-secondary">
                                {transaction.counterparty_account_id
                                  ? `To ${accountNameById[transaction.counterparty_account_id] || transaction.counterparty_account_id}`
                                  : formatLabel(transaction.account.account_type)}
                              </span>
                            </div>
                          </td>
                          <td className="holding-name-cell">
                            {transaction.instrument_ref ? (
                              <div className="holding-name-stack">
                                <strong>{primaryIdentifier(transaction.instrument_ref)}</strong>
                                <span className="holding-secondary">{transaction.instrument_ref.instrument_name}</span>
                              </div>
                            ) : isFxConversionTransaction(transaction.transaction_type) ? (
                              <div className="holding-name-stack">
                                <strong>FX Conversion</strong>
                                <span className="holding-secondary">
                                  {transaction.currency} → {accountCurrencyById[transaction.counterparty_account_id || ''] || '—'}
                                </span>
                              </div>
                            ) : (
                              <span className="holding-secondary">Cash ledger</span>
                            )}
                          </td>
                          <td className="transaction-number-cell">
                            <div className="holding-name-stack">
                              <span>{formatQuantity(transaction.quantity)}</span>
                              <span className="holding-secondary">
                                {transaction.price != null ? `@ ${formatUnitPrice(transaction.price, transaction.currency)}` : 'No unit price'}
                              </span>
                            </div>
                          </td>
                          <td className="transaction-number-cell">
                            <div className="holding-name-stack">
                              <span>{formatCurrency(transaction.gross_amount, transaction.currency)}</span>
                              <span className={transaction.net_cash_effect != null && transaction.net_cash_effect < 0 ? 'holding-secondary negative-cell' : 'holding-secondary'}>
                                Net {formatSignedCurrency(transaction.net_cash_effect, transaction.currency)}
                              </span>
                              {transaction.fees || transaction.taxes ? (
                                <span className="holding-secondary">
                                  Fee / tax {formatCurrency(transaction.fees + transaction.taxes, transaction.currency)}
                                </span>
                              ) : null}
                            </div>
                          </td>
                          <td className="transaction-note-cell">
                            <div className="holding-name-stack">
                              <span>{transaction.note || '—'}</span>
                              <span className="holding-secondary">
                                {transaction.transfer_group_id
                                  ? `${transaction.transfer_group_id} · ${formatLabel(transaction.transfer_object_type || 'transfer')}`
                                  : transaction.transaction_id}
                              </span>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                    {!visibleTransactions.length ? (
                      <tr>
                        <td colSpan={8} className="empty-state-cell">No transactions match these filters.</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>

            <aside className="transaction-inspector" aria-label="Selected transaction details">
              {selectedTransaction ? (
                <>
                  <div className="transaction-inspector-head">
                    <div>
                      <span className="portfolio-detail-meta">Selected fact</span>
                      <div className="panel-title">{formatLabel(selectedTransaction.transaction_type)}</div>
                      <div className="portfolio-detail-meta">{selectedTransaction.transaction_id}</div>
                    </div>
                    <div className="transaction-inspector-actions">
                      <button
                        type="button"
                        className="toolbar-link"
                        disabled={!canEditTransaction(selectedTransaction)}
                        onClick={() => openEditDrawer(selectedTransaction)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="toolbar-link transaction-danger-action"
                        onClick={() => {
                          setDeleteError(null)
                          setPendingDeleteTransaction(selectedTransaction)
                        }}
                      >
                        {selectedTransaction.transfer_group_id ? 'Delete Pair' : 'Delete'}
                      </button>
                    </div>
                  </div>
                  <div className="transaction-inspector-tabs" role="tablist" aria-label="Transaction detail views">
                    {([
                      ['fact', 'Fact'],
                      ['lots', `Lots ${relatedPositionLots.length}`],
                      ['postings', `Postings ${transactionsWorkspace.ledger_summary.posting_count}`],
                    ] as const).map(([tabKey, label]) => (
                      <button
                        key={tabKey}
                        type="button"
                        role="tab"
                        aria-selected={inspectorTab === tabKey}
                        className={inspectorTab === tabKey ? 'transaction-inspector-tab-active' : ''}
                        onClick={() => setInspectorTab(tabKey)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>

                  {inspectorTab === 'fact' ? (
                    <div className="transaction-fact-sheet" role="tabpanel">
                      <div className="transaction-fact-highlight">
                        <span>Gross amount</span>
                        <strong>{formatCurrency(selectedTransaction.gross_amount, selectedTransaction.currency)}</strong>
                        <em className={selectedTransaction.net_cash_effect != null && selectedTransaction.net_cash_effect < 0 ? 'negative-cell' : ''}>
                          Net cash {formatSignedCurrency(selectedTransaction.net_cash_effect, selectedTransaction.currency)}
                        </em>
                      </div>
                      <dl className="transaction-fact-list">
                        <div>
                          <dt>Instrument</dt>
                          <dd>{selectedTransaction.instrument_ref ? `${primaryIdentifier(selectedTransaction.instrument_ref)} · ${selectedTransaction.instrument_ref.instrument_name}` : 'Cash ledger'}</dd>
                        </div>
                        <div>
                          <dt>Account</dt>
                          <dd><Link className="table-inline-link" to={transactionAccountHref(portfolioId, selectedTransaction.account.account_id)}>{selectedTransaction.account.account_name}</Link></dd>
                        </div>
                        <div>
                          <dt>Settlement</dt>
                          <dd>{selectedTransaction.settlement_cash_account?.account_name ?? (selectedTransaction.counterparty_account_id ? accountNameById[selectedTransaction.counterparty_account_id] || selectedTransaction.counterparty_account_id : '—')}</dd>
                        </div>
                        <div>
                          <dt>Trade / settle</dt>
                          <dd>{selectedTransaction.trade_date} {selectedTransaction.trade_time}<br /><span>{selectedTransaction.settlement_date}</span></dd>
                        </div>
                        <div>
                          <dt>Economic / external flow</dt>
                          <dd>{selectedTransaction.economic_date}<br /><span>{selectedTransaction.external_flow_date ?? '—'}</span></dd>
                        </div>
                        <div>
                          <dt>Quantity / price</dt>
                          <dd>{formatQuantity(selectedTransaction.quantity)}<br /><span>{formatUnitPrice(selectedTransaction.price, selectedTransaction.currency)}</span></dd>
                        </div>
                        <div>
                          <dt>Fees / taxes</dt>
                          <dd>
                            {formatCurrency(selectedTransaction.fees, selectedTransaction.currency)} / {formatCurrency(selectedTransaction.taxes, selectedTransaction.currency)}
                            <br /><span>{formatLabel(selectedTransaction.fee_category)}</span>
                          </dd>
                        </div>
                      </dl>
                      {selectedTransaction.note ? (
                        <div className="transaction-inspector-note">
                          <span>Note</span>
                          <p>{selectedTransaction.note}</p>
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  {inspectorTab === 'lots' ? (
                    <div className="transaction-inspector-list" role="tabpanel">
                      {!selectedTransaction.instrument_id ? (
                        <div className="empty-state">Cash-only facts do not create position lots.</div>
                      ) : relatedPositionLots.length ? (
                        relatedPositionLots.map((positionLot) => (
                          <article key={positionLot.position_lot_id} className="transaction-inspector-card">
                            <div className="transaction-inspector-card-head">
                              <div>
                                <strong>{positionLot.position_lot_id}</strong>
                                <span>Opened {positionLot.opened_at}</span>
                              </div>
                              <span className="transaction-type-pill">{formatLabel(positionLot.status)}</span>
                            </div>
                            <div className="transaction-impact-tags">
                              {resolvePositionLotImpactKinds(positionLot, selectedTransaction.transaction_id).map((impactKind) => (
                                <span key={impactKind}>{formatLabel(impactKind)}</span>
                              ))}
                            </div>
                            <dl className="transaction-inspector-card-metrics">
                              <div><dt>Remaining</dt><dd>{formatQuantity(positionLot.remaining_quantity)}</dd></div>
                              <div><dt>Cost</dt><dd>{formatCurrency(positionLot.remaining_cost_basis, positionLot.currency)}</dd></div>
                              <div><dt>Realized P/L</dt><dd>{formatSignedCurrency(positionLot.realized_pnl, positionLot.currency)}</dd></div>
                            </dl>
                          </article>
                        ))
                      ) : (
                        <div className="empty-state">No position lots linked to this fact.</div>
                      )}
                    </div>
                  ) : null}

                  {inspectorTab === 'postings' ? (
                    <div className="transaction-inspector-list" role="tabpanel">
                      {transactionsWorkspace.ledger_postings.map((posting) => (
                        <article key={posting.posting_id} className="transaction-inspector-card">
                          <div className="transaction-inspector-card-head">
                            <div>
                              <strong>{formatLabel(posting.posting_role)}</strong>
                              <span>{accountNameById[posting.account_id] || posting.account_id}</span>
                            </div>
                            <span>{posting.settlement_date}</span>
                          </div>
                          <dl className="transaction-inspector-card-metrics">
                            <div><dt>Cash</dt><dd>{formatSignedCurrency(posting.cash_amount_delta, posting.currency)}</dd></div>
                            <div><dt>Quantity</dt><dd>{formatNumber(posting.quantity_delta, 2)}</dd></div>
                            <div><dt>Cost basis</dt><dd>{posting.cost_basis_delta != null ? formatSignedCurrency(posting.cost_basis_delta, posting.currency) : '—'}</dd></div>
                          </dl>
                        </article>
                      ))}
                      {!transactionsWorkspace.ledger_postings.length ? (
                        <div className="empty-state">No ledger postings.</div>
                      ) : null}
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="transaction-inspector-empty">
                  <span>Transaction details</span>
                  <p>Select a ledger row to inspect the fact, position lots, and accounting postings.</p>
                </div>
              )}
            </aside>
          </div>
        ) : null}
      </section>

      {drawerOpen ? (
        <div
          className="transaction-entry-backdrop"
          role="presentation"
          onClick={() => {
            setDrawerOpen(false)
            setEditingTransactionId(null)
          }}
        >
          <aside
            ref={drawerDialogRef}
            className="transaction-entry-modal"
            role="dialog"
            aria-modal="true"
            aria-label={isEditingTransaction ? 'Edit transaction' : 'Add transaction'}
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="transaction-entry-modal-header">
              <div>
                <div className="panel-title">{isEditingTransaction ? 'Edit Transaction' : 'Add Transaction'}</div>
              </div>
              <button
                type="button"
                className="toolbar-link"
                onClick={() => {
                  setDrawerOpen(false)
                  setEditingTransactionId(null)
                }}
              >
                Close
              </button>
            </div>

            <form className="transaction-form" onSubmit={(event) => void handleCreateTransaction(event)}>
              <div className="transaction-ticket-main">
                <div className="transaction-ticket-section-heading">
                  <div>
                    <span>Trade details</span>
                    <strong>Instrument, account and economics</strong>
                  </div>
                  <em>All dates use {DEFAULT_TRADE_TIMEZONE}</em>
                </div>
              {shouldAllowInstrument ? (
                <section className="transaction-instrument-search transaction-instrument-search-top">
                  <label className="transaction-picker-search">
                    <span>Security</span>
                    <input
                      ref={securitySearchRef}
                      type="search"
                      value={instrumentInputValue}
                      placeholder="Search ticker or name"
                      onChange={(event) => {
                        const instrumentSearch = event.target.value
                        autoQuoteKeyRef.current = null
                        autoQuantityKeyRef.current = null
                        autoGrossDerivedRef.current = false
                        setPricingAnchor('price')
                        setForm((current) => {
                          const clearsSelectedInstrument = Boolean(current.instrument_id)
                          return {
                            ...current,
                            instrument_id: '',
                            instrument_search: instrumentSearch,
                            quantity: clearsSelectedInstrument ? '' : current.quantity,
                            price: clearsSelectedInstrument ? '' : current.price,
                            gross_amount: clearsSelectedInstrument ? '' : current.gross_amount,
                          }
                        })
                      }}
                      onKeyDown={(event) => {
                        if (event.key !== 'Enter') {
                          return
                        }
                        if (filteredInstrumentOptions.length === 0) {
                          return
                        }
                        event.preventDefault()
                        selectInstrument(filteredInstrumentOptions[0])
                      }}
                    />
                  </label>

                  {showInstrumentResults ? (
                    <div className="transaction-instrument-results">
                      {filteredInstrumentOptions.map((instrument) => (
                        <button
                          type="button"
                          key={instrument.instrument_id}
                          className="transaction-instrument-result"
                          onClick={() => selectInstrument(instrument)}
                        >
                          <div className="holding-name-stack">
                            <span>{primaryIdentifier(instrument)}</span>
                            <span className="holding-secondary">{instrument.instrument_name}</span>
                          </div>
                          <span className="transaction-picker-meta">{instrument.currency}</span>
                        </button>
                      ))}
                      {!filteredInstrumentOptions.length ? (
                        <div className="transaction-instrument-empty">No matching security.</div>
                      ) : null}
                    </div>
                  ) : null}
                </section>
              ) : null}

              <div className="transaction-form-grid transaction-ticket-grid">
                <label className="transaction-ticket-field transaction-ticket-field-wide">
                  <span>Account</span>
                  <select
                    ref={accountSelectRef}
                    value={form.account_id}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        account_id: event.target.value,
                      }))
                    }
                  >
                    {eligibleAccounts(form.transaction_type, accounts, form.transfer_object_type).map((account) => (
                      <option key={account.account_id} value={account.account_id}>
                        {account.account_name} · {account.currency}
                      </option>
                    ))}
                  </select>
                </label>

                {isFxConversion ? (
                  <label className="transaction-ticket-field transaction-ticket-field-wide">
                    <span>Target Cash Account</span>
                    <select
                      value={form.counterparty_account_id}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          counterparty_account_id: event.target.value,
                        }))
                      }
                      disabled={counterpartyAccounts.length === 0}
                    >
                      {counterpartyAccounts.map((account) => (
                        <option key={account.account_id} value={account.account_id}>
                          {account.account_name} · {account.currency}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : isTransferTransaction(form.transaction_type) ? (
                  <label className="transaction-ticket-field transaction-ticket-field-wide">
                    <span>Counterparty Account</span>
                    <select
                      value={form.counterparty_account_id}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          counterparty_account_id: event.target.value,
                        }))
                      }
                    >
                      {counterpartyAccounts.map((account) => (
                        <option key={account.account_id} value={account.account_id}>
                          {account.account_name} · {account.currency}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : shouldRequireSettlement ? (
                  <label className="transaction-ticket-field transaction-ticket-field-wide">
                    <span>Settlement Cash Account</span>
                    <select
                      value={form.settlement_cash_account_id}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          settlement_cash_account_id: event.target.value,
                        }))
                      }
                      disabled={settlementAccountOptions.length === 0}
                    >
                      {settlementAccountOptions.map((account) => (
                        <option key={account.account_id} value={account.account_id}>
                          {account.account_name} · {account.currency}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}

                <label className="transaction-ticket-field">
                  <span>Currency</span>
                  <input value={resolvedTransactionCurrency} readOnly />
                </label>

                <label className="transaction-ticket-field">
                  <span>Type</span>
                  <select
                    value={form.transaction_type}
                    onChange={(event) => {
                      const nextTransactionType = event.target.value
                      setForm((current) => {
                        const shouldClearAutoSellQuantity =
                          autoQuantityKeyRef.current !== null &&
                          current.transaction_type === 'sell' &&
                          nextTransactionType !== 'sell'
                        if (shouldClearAutoSellQuantity) {
                          autoQuantityKeyRef.current = null
                          autoGrossDerivedRef.current = false
                        }
                        return {
                          ...current,
                          transaction_type: nextTransactionType,
                          quantity: shouldClearAutoSellQuantity ? '' : current.quantity,
                          gross_amount: shouldClearAutoSellQuantity ? '' : current.gross_amount,
                        }
                      })
                    }}
                  >
                    {TRANSACTION_TYPES.map((transactionType) => (
                      <option key={transactionType} value={transactionType}>
                        {formatLabel(transactionType)}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="transaction-ticket-field">
                  <span>Trade Date</span>
                  <input
                    type="date"
                    value={form.trade_date}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        trade_date: event.target.value,
                      }))
                    }
                  />
                </label>

                <label className="transaction-ticket-field">
                  <span>{transactionDateLabels(form.transaction_type).settlement}</span>
                  <input
                    type="date"
                    value={form.settlement_date}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        settlement_date: event.target.value,
                      }))
                    }
                  />
                </label>

                <label className="transaction-ticket-field">
                  <span>Trade Time</span>
                  <input
                    type="time"
                    step={60}
                    value={form.trade_time}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        trade_time: event.target.value,
                      }))
                    }
                  />
                </label>

                {supportsEntitlementDate(form.transaction_type) ? (
                  <label className="transaction-ticket-field">
                    <span>{transactionDateLabels(form.transaction_type).entitlement}</span>
                    <input
                      type="date"
                      value={form.entitlement_date}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          entitlement_date: event.target.value,
                        }))
                      }
                    />
                  </label>
                ) : null}

                {supportsAcquisitionDate(form.transaction_type, selectedAccount?.account_type) ? (
                  <label className="transaction-ticket-field">
                    <span>Acquisition Date</span>
                    <input
                      type="date"
                      value={form.acquisition_date}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          acquisition_date: event.target.value,
                        }))
                      }
                    />
                  </label>
                ) : null}

                {isTransferTransaction(form.transaction_type) ? (
                  <label className="transaction-ticket-field">
                    <span>Transfer Object</span>
                    <select
                      value={form.transfer_object_type}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          transfer_object_type: event.target.value,
                        }))
                      }
                    >
                      <option value="cash">Cash</option>
                      <option value="position">Position</option>
                    </select>
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {isFxConversion || isTransferTransaction(form.transaction_type) || !shouldUsePrice ? amountField : null}

                {isFxConversion ? (
                  <label className="transaction-ticket-field">
                    <span>Received Amount ({resolvedCounterpartyCurrency || 'Target'})</span>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={form.counter_amount}
                      placeholder={computedCounterAmount || '0.00'}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          counter_amount: event.target.value,
                        }))
                      }
                    />
                  </label>
                ) : shouldUseQuantity ? (
                  <label className="transaction-ticket-field">
                    <span>Shares</span>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      max={ticketQuantityDelta != null && ticketQuantityDelta < 0 ? positionPreview?.quantity : undefined}
                      value={form.quantity}
                      onChange={(event) => updatePricingField('quantity', event.target.value)}
                    />
                    {positionPreviewLoading ? (
                      <span className="transaction-ticket-hint">Loading</span>
                    ) : positionPreview ? (
                      <span
                        className={
                          enteredQuantityExceedsPosition
                            ? 'transaction-ticket-hint transaction-ticket-hint-warning'
                            : 'transaction-ticket-hint'
                        }
                      >
                        {positionPreviewAccountRole === 'source' ? 'Source holding' : 'Holding'}:{' '}
                        {formatQuantity(positionPreview.quantity)}
                        {projectedPositionQuantity != null ? ` · After: ${formatQuantity(projectedPositionQuantity)}` : ''}
                        {enteredQuantityExceedsPosition ? ' · Exceeds available shares' : ''}
                      </span>
                    ) : positionPreviewError ? (
                      <span className="transaction-ticket-hint">{positionPreviewError}</span>
                    ) : null}
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {isFxConversion ? (
                  <label className="transaction-ticket-field">
                    <span>FX Rate ({resolvedTransactionCurrency}/{resolvedCounterpartyCurrency || 'Target'})</span>
                    <input
                      type="number"
                      min="0"
                      step="0.000001"
                      value={form.fx_rate}
                      placeholder={resolvedFxRate || '0.000000'}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          fx_rate: event.target.value,
                        }))
                      }
                    />
                  </label>
                ) : shouldUsePrice ? (
                  <label className="transaction-ticket-field">
                    <span>Quote</span>
                    <input
                      type="number"
                      min="0"
                      step="0.0001"
                      value={form.price}
                      placeholder={computedUnitPrice || '0.0000'}
                      onChange={(event) => updatePricingField('price', event.target.value)}
                    />
                    {historicalQuoteLoading ? (
                      <span className="transaction-ticket-hint">Loading</span>
                    ) : activeHistoricalQuote ? (
                      <span className="transaction-ticket-hint">
                        {activeHistoricalQuote.stale ? 'Prior ' : ''}{formatLabel(activeHistoricalQuote.quoteBasis)}:{' '}
                        {formatUnitPrice(activeHistoricalQuote.price, activeHistoricalQuote.currency)} · {activeHistoricalQuote.asOfDate}
                      </span>
                    ) : historicalQuoteError ? (
                      <span className="transaction-ticket-hint">{historicalQuoteError}</span>
                    ) : null}
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {!isFxConversion && !isTransferTransaction(form.transaction_type) && shouldUsePrice ? amountField : null}

                {shouldShowFees ? (
                  <label className="transaction-ticket-field">
                    <span>Fee</span>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={form.fees}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          fees: event.target.value,
                        }))
                      }
                    />
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {shouldShowFeeCategory ? (
                  <label className="transaction-ticket-field">
                    <span>Fee category</span>
                    <select
                      value={form.fee_category}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          fee_category: event.target.value as PortfolioFeeCategory,
                        }))
                      }
                    >
                      {FEE_CATEGORIES.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {shouldShowTaxes ? (
                  <label className="transaction-ticket-field">
                    <span>Tax</span>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={form.taxes}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          taxes: event.target.value,
                        }))
                      }
                    />
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}
              </div>
              </div>

              <aside className="transaction-ticket-review" aria-label="Transaction review">
                <div className="transaction-ticket-section-heading">
                  <div>
                    <span>Review</span>
                    <strong>Accounting impact</strong>
                  </div>
                </div>
              <div className="transaction-ticket-summary" aria-live="polite">
                {isFxConversion ? (
                  <>
                    <div className="transaction-ticket-summary-row">
                      <span>Source Amount</span>
                      <strong>
                        {ticketGrossAmount == null
                          ? '—'
                          : formatCurrency(ticketGrossAmount, resolvedTransactionCurrency)}
                      </strong>
                    </div>
                    <div className="transaction-ticket-summary-row">
                      <span>FX Rate</span>
                      <strong>{resolvedFxRate ? formatNumber(Number(resolvedFxRate), 6) : '—'}</strong>
                    </div>
                    <div className="transaction-ticket-summary-row transaction-ticket-summary-total">
                      <span>Received Amount</span>
                      <strong>
                        {computedCounterAmount
                          ? formatCurrency(Number(computedCounterAmount), resolvedCounterpartyCurrency || 'USD')
                          : '—'}
                      </strong>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="transaction-ticket-summary-row">
                      <span>Pre-fee Amount</span>
                      <strong>
                        {ticketGrossAmount == null ? '—' : formatCurrency(ticketGrossAmount, previewCurrency)}
                      </strong>
                    </div>
                    {shouldShowFees ? (
                      <div className="transaction-ticket-summary-row">
                        <span>Fee</span>
                        <strong>{formatCurrency(ticketFeeAmount, previewCurrency)}</strong>
                      </div>
                    ) : null}
                    {shouldShowTaxes ? (
                      <div className="transaction-ticket-summary-row">
                        <span>Tax</span>
                        <strong>{formatCurrency(ticketTaxAmount, previewCurrency)}</strong>
                      </div>
                    ) : null}
                    <div className="transaction-ticket-summary-row transaction-ticket-summary-total">
                      <span>Net Cash Effect</span>
                      <strong>
                        {ticketNetCashEffect == null
                          ? '—'
                          : formatSignedCurrency(ticketNetCashEffect, previewCurrency)}
                      </strong>
                    </div>
                  </>
                )}
              </div>

              {shouldRequireSettlement && settlementAccountOptions.length === 0 ? (
                <div className="portfolio-detail-meta">
                  Settlement cash account required for {resolvedTransactionCurrency}.
                </div>
              ) : null}

              {isFxConversion && counterpartyAccounts.length === 0 ? (
                <div className="portfolio-detail-meta">
                  Counterparty deposit account required.
                </div>
              ) : null}

              {isFxConversion && sharedFxRate ? (
                <div className="portfolio-detail-meta">
                  Shared spot reference: {sharedFxRate.base_currency}/{sharedFxRate.quote_currency}{' '}
                  {formatNumber(sharedFxRate.rate, 6)} as of {sharedFxRate.as_of_date}.
                </div>
              ) : null}

              <label className="transaction-notes-field">
                <span>Note</span>
                <textarea
                  rows={3}
                  value={form.note}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      note: event.target.value,
                    }))
                  }
                />
              </label>

              {formError ? <div className="error-state transaction-form-error">{formError}</div> : null}
              </aside>

              <div className="transaction-form-footer">
                <button
                  type="button"
                  className="toolbar-link"
                  onClick={() => {
                    setDrawerOpen(false)
                    setEditingTransactionId(null)
                  }}
                >
                  Cancel
                </button>
                <button type="submit" className="toolbar-link button-primary">
                  {isEditingTransaction ? 'Save Changes' : 'Save Transaction'}
                </button>
              </div>
            </form>
          </aside>
        </div>
      ) : null}
      <ConfirmDialog
        open={Boolean(pendingDeleteTransaction)}
        title={pendingDeleteTransaction?.transfer_group_id ? 'Delete Transfer Pair' : 'Delete Transaction'}
        description={
          pendingDeleteTransaction?.transfer_group_id
            ? `This permanently deletes both legs of transfer pair ${pendingDeleteTransaction.transfer_group_id}. This action cannot be undone.`
            : `This permanently deletes transaction ${pendingDeleteTransaction?.transaction_id ?? ''}. This action cannot be undone.`
        }
        confirmLabel={pendingDeleteTransaction?.transfer_group_id ? 'Delete Pair' : 'Delete Transaction'}
        confirmationText={
          pendingDeleteTransaction?.transfer_group_id ?? pendingDeleteTransaction?.transaction_id
        }
        error={deleteError}
        busy={deletingTransaction}
        onCancel={() => {
          setPendingDeleteTransaction(null)
          setDeleteError(null)
        }}
        onConfirm={handleDeleteTransaction}
      />
    </PortfolioWorkspaceLayout>
  )
}
