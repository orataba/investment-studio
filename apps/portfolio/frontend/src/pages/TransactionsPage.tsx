import { FormEvent, useDeferredValue, useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  createPortfolioInternalTransfer,
  createPortfolioTransaction,
  getPlatformInstruments,
  getPortfolioAccounts,
  getPortfolioFxRates,
  getPortfolioTransactionsWorkspace,
  type PlatformInstrumentRecord,
  type PortfolioAccountRecord,
  type PortfolioPositionLotRecord,
  type PortfolioSharedFxRateRecord,
  type PortfolioTransactionCreatePayload,
  type PortfolioTransactionFilters,
  type PortfolioTransactionRecord,
  type PortfolioTransactionWorkspaceResponse,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatQuantity,
  formatSignedCurrency,
  formatUnitPrice,
} from '../lib/format'

const DEFAULT_FORM_DATE = '2026-04-15'
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

const POSITION_ASSET_TYPES = new Set(['fund', 'bond', 'equity', 'other'])
const ACCOUNT_SCOPE_ENFORCED_TRANSACTION_TYPES = new Set(['buy', 'dividend_reinvestment', 'opening_balance'])
const INCOME_ASSET_TYPES: Record<string, Set<string>> = {
  dividend: new Set(['fund', 'equity']),
  dividend_reinvestment: new Set(['fund', 'equity']),
  coupon: new Set(['bond']),
  return_of_capital: new Set(['fund', 'equity']),
  maturity_redemption: new Set(['bond']),
}

function primaryIdentifier(
  instrument:
    | PlatformInstrumentRecord
    | {
        asset_id: string
        identifiers: Array<{ identifier_value: string; is_primary: boolean }>
      },
) {
  return (
    instrument.identifiers.find((item) => item.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.asset_id
  )
}

function isTransferTransaction(transactionType: string) {
  return transactionType === 'transfer_in' || transactionType === 'transfer_out'
}

function isFxConversionTransaction(transactionType: string) {
  return transactionType === 'fx_conversion'
}

function accountAllowsAssetType(
  account: PortfolioAccountRecord | null | undefined,
  assetType: string,
  transactionType: string,
) {
  if (
    !account ||
    account.account_type !== 'securities_account' ||
    !ACCOUNT_SCOPE_ENFORCED_TRANSACTION_TYPES.has(transactionType)
  ) {
    return true
  }

  if (!account.allowed_asset_types?.length) {
    return true
  }

  return account.allowed_asset_types.includes(assetType.trim().toLowerCase())
}

function supportsTransactionAssetType(transactionType: string, assetType: string) {
  const normalizedAssetType = assetType.trim().toLowerCase()
  if (!normalizedAssetType) {
    return false
  }

  if (transactionType === 'buy' || transactionType === 'sell' || transactionType === 'opening_balance') {
    return POSITION_ASSET_TYPES.has(normalizedAssetType)
  }

  if (transactionType === 'fee' || transactionType === 'tax') {
    return POSITION_ASSET_TYPES.has(normalizedAssetType)
  }

  const allowedAssetTypes = INCOME_ASSET_TYPES[transactionType]
  if (!allowedAssetTypes) {
    return true
  }
  return allowedAssetTypes.has(normalizedAssetType)
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
  instrument: PlatformInstrumentRecord,
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
      supportsTransactionAssetType(transactionType, instrument.asset_type) &&
      accountAllowsAssetType(account, instrument.asset_type, transactionType)
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

function autoGrossAmountFromTrade(
  transactionType: string,
  assetType: string | null | undefined,
  quantity: number,
  price: number,
) {
  if (transactionType !== 'buy' && transactionType !== 'sell') {
    return quantity * price
  }
  if (String(assetType || '').trim().toLowerCase() === 'bond') {
    return null
  }
  return quantity * price
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

  return 'Gross Amount'
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

type TransactionFormState = {
  transaction_type: string
  trade_date: string
  trade_time: string
  settlement_date: string
  account_id: string
  counterparty_account_id: string
  settlement_cash_account_id: string
  transfer_object_type: string
  asset_id: string
  quantity: string
  price: string
  gross_amount: string
  counter_amount: string
  fx_rate: string
  fees: string
  taxes: string
  note: string
  instrument_search: string
}

function buildInitialFormState(accounts: PortfolioAccountRecord[]): TransactionFormState {
  const defaultSecurityAccount = accounts.find((account) => account.account_type === 'securities_account')
  const defaultCashAccount =
    accounts.find((account) => account.account_id === defaultSecurityAccount?.default_settlement_cash_account_id) ??
    accounts.find((account) => account.account_type === 'deposit_account')

  return {
    transaction_type: 'buy',
    trade_date: DEFAULT_FORM_DATE,
    trade_time: DEFAULT_FORM_TIME,
    settlement_date: DEFAULT_FORM_DATE,
    account_id: defaultSecurityAccount?.account_id ?? accounts[0]?.account_id ?? '',
    counterparty_account_id: '',
    settlement_cash_account_id: defaultCashAccount?.account_id ?? '',
    transfer_object_type: 'cash',
    asset_id: '',
    quantity: '',
    price: '',
    gross_amount: '',
    counter_amount: '',
    fx_rate: '',
    fees: '0',
    taxes: '0',
    note: '',
    instrument_search: '',
  }
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
  const { portfolioId = 'yungu' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [accounts, setAccounts] = useState<PortfolioAccountRecord[]>([])
  const [instruments, setInstruments] = useState<PlatformInstrumentRecord[]>([])
  const [fxRates, setFxRates] = useState<PortfolioSharedFxRateRecord[]>([])
  const [transactionsWorkspace, setTransactionsWorkspace] = useState<PortfolioTransactionWorkspaceResponse | null>(null)
  const [metaLoading, setMetaLoading] = useState(true)
  const [loadingTransactions, setLoadingTransactions] = useState(true)
  const [pageError, setPageError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [form, setForm] = useState<TransactionFormState>(() => buildInitialFormState([]))

  const filters: PortfolioTransactionFilters = {
    account_id: searchParams.get('account_id') ?? '',
    transaction_type: searchParams.get('transaction_type') ?? '',
    asset_id: searchParams.get('asset_id') ?? '',
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
    let cancelled = false
    setMetaLoading(true)

    Promise.all([getPortfolioAccounts(portfolioId), getPlatformInstruments(), getPortfolioFxRates(portfolioId)])
      .then(([accountsResponse, instrumentsResponse, fxRatesResponse]) => {
        if (cancelled) {
          return
        }

        setAccounts(accountsResponse.accounts)
        setInstruments(instrumentsResponse.instruments)
        setFxRates(fxRatesResponse.rates)
        setForm(buildInitialFormState(accountsResponse.accounts))
        setPageError(null)
      })
      .catch((error) => {
        if (!cancelled) {
          setPageError(error instanceof Error ? error.message : 'Failed to load transaction metadata.')
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
    setLoadingTransactions(true)

    getPortfolioTransactionsWorkspace(portfolioId, {
      ...filters,
      transaction_id: selectedTransactionId || undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setTransactionsWorkspace(response)
          setPageError(null)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setPageError(error instanceof Error ? error.message : 'Failed to load transaction ledger.')
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
    filters.asset_id,
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
  const shouldUsePrice = usesPrice(form.transaction_type)
  const shouldShowFees = showsFeeField(form.transaction_type)
  const shouldShowTaxes = showsTaxField(form.transaction_type)
  const selectedInstrument = instruments.find((instrument) => instrument.asset_id === form.asset_id) ?? null
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

  const filteredInstrumentOptions = useMemo(() => {
    const normalizedSearch = deferredInstrumentSearch.trim().toLowerCase()
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
          instrument.asset_name,
          instrument.asset_type,
          instrument.currency,
          primaryIdentifier(instrument),
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
      const resolved = autoGrossAmountFromTrade(
        form.transaction_type,
        selectedInstrument?.asset_type,
        quantity,
        price,
      )
      return resolved == null ? '' : resolved.toFixed(2)
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
    if (!shouldAllowInstrument && form.asset_id) {
      setForm((current) => ({
        ...current,
        asset_id: '',
      }))
    }
  }, [form.asset_id, shouldAllowInstrument])

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
    setForm((current) => ({
      ...current,
      asset_id: '',
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
    if (!shouldShowTaxes && form.taxes !== '0') {
      setForm((current) => ({
        ...current,
        taxes: '0',
      }))
    }
  }, [form.taxes, shouldShowTaxes])

  async function refreshTransactions(
    activeFilters: PortfolioTransactionFilters,
    selectedTransactionOverride?: string | null,
  ) {
    setLoadingTransactions(true)
    try {
      const response = await getPortfolioTransactionsWorkspace(portfolioId, {
        ...activeFilters,
        transaction_id: selectedTransactionOverride || selectedTransactionId || undefined,
      })
      setTransactionsWorkspace(response)
      setPageError(null)
    } catch (error) {
      setPageError(error instanceof Error ? error.message : 'Failed to load transaction ledger.')
    } finally {
      setLoadingTransactions(false)
    }
  }

  async function handleCreateTransaction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    setNotice(null)

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
    }

    if (shouldUsePrice) {
      const price = Number(form.price)
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
        account_id: resolvedAccount.account_id,
        settlement_cash_account_id: null,
        asset_id: null,
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
      }

      try {
        const created = await createPortfolioTransaction(portfolioId, payload)
        setDrawerOpen(false)
        setNotice(`Added ${formatLabel(created.transaction_type)} transaction ${created.transaction_id}.`)
        setForm(buildInitialFormState(accounts))
        patchSearchParams({
          account_id: filters.account_id || created.account.account_id,
          transaction_id: created.transaction_id,
        })
        await refreshTransactions(filters, created.transaction_id)
      } catch (error) {
        setFormError(error instanceof Error ? error.message : 'Failed to create FX conversion.')
      }
      return
    }

    if (isTransferTransaction(form.transaction_type)) {
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
        asset_id: transferObjectType === 'position' ? selectedInstrument?.asset_id ?? null : null,
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
      account_id: resolvedAccount.account_id,
      settlement_cash_account_id: shouldRequireSettlement ? form.settlement_cash_account_id || null : null,
      asset_id: shouldAllowInstrument ? selectedInstrument?.asset_id ?? null : null,
      quantity: shouldUseQuantity && form.quantity ? Number(form.quantity) : null,
      price: shouldUsePrice && form.price ? Number(form.price) : null,
      gross_amount: grossAmount,
      counter_amount: null,
      fx_rate: null,
      fees: shouldShowFees && form.fees ? Number(form.fees) : 0,
      taxes: shouldShowTaxes && form.taxes ? Number(form.taxes) : 0,
      currency: resolvedTransactionCurrency,
      note: form.note.trim() || null,
    }

    try {
      const created = await createPortfolioTransaction(portfolioId, payload)
      setDrawerOpen(false)
      setNotice(`Added ${formatLabel(created.transaction_type)} transaction ${created.transaction_id}.`)
      setForm(buildInitialFormState(accounts))
      patchSearchParams({
        account_id: filters.account_id || created.account.account_id,
        transaction_id: created.transaction_id,
      })
      await refreshTransactions(filters, created.transaction_id)
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Failed to create transaction.')
    }
  }

  const summary = transactionsWorkspace?.summary
  const derivationBoundary = transactionsWorkspace?.derivation_boundary
  const selectedTransaction = transactionsWorkspace?.selected_transaction ?? null

  useEffect(() => {
    const nextTransactionId = transactionsWorkspace?.selected_transaction_id ?? ''
    if (!nextTransactionId) {
      patchSearchParams({ transaction_id: null })
      return
    }
    if (selectedTransactionId !== nextTransactionId) {
      patchSearchParams({ transaction_id: nextTransactionId })
    }
  }, [selectedTransactionId, transactionsWorkspace])

  const accountNameById = useMemo(
    () => Object.fromEntries(accounts.map((account) => [account.account_id, account.account_name])),
    [accounts],
  )
  const accountCurrencyById = useMemo(
    () => Object.fromEntries(accounts.map((account) => [account.account_id, account.currency])),
    [accounts],
  )
  const relatedPositionLots = transactionsWorkspace?.related_position_lots ?? []

  return (
    <PortfolioWorkspaceLayout
      activeSection="Transactions"
      toolbarLabel="View: Transaction Ledger"
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
            <article className="summary-card summary-card-warning">
              <span className="summary-card-label">Next Layer</span>
              <strong className="summary-card-value">{formatLabel(derivationBoundary?.ledger_postings ?? 'next_layer')}</strong>
            </article>
          </div>
        ) : undefined
      }
    >
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Transactions</div>
          <div className="portfolio-detail-meta">Portfolio fact ledger</div>
        </div>

        <div className="holdings-meta-row">
          <p className="coverage-note">
            Transaction entry now uses portfolio-private accounts and facts, while instrument selection reads
            directly from the platform-owned registry. `ledger_postings`, `positions`, and `snapshot` stay as
            the next derivation layers.
          </p>
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
            <label>
              <span>Instrument</span>
              <select
                className="toolbar-select transaction-filter-input"
                value={filters.asset_id ?? ''}
                onChange={(event) =>
                  patchSearchParams({
                    asset_id: event.target.value,
                    transaction_id: null,
                  })
                }
              >
                <option value="">All instruments</option>
                {instruments.map((instrument) => (
                  <option key={instrument.asset_id} value={instrument.asset_id}>
                    {primaryIdentifier(instrument)} · {instrument.asset_name}
                  </option>
                ))}
              </select>
            </label>
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
              onClick={() =>
                patchSearchParams({
                  account_id: null,
                  transaction_type: null,
                  asset_id: null,
                  start_date: null,
                  end_date: null,
                  transaction_id: null,
                })
              }
            >
              Reset
            </button>
            <button
              type="button"
              className="toolbar-link button-primary"
              disabled={metaLoading || accounts.length === 0}
              onClick={() => {
                setDrawerOpen(true)
                setFormError(null)
              }}
            >
              Add Transaction
            </button>
          </div>
        </section>

        {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
        {pageError ? <div className="error-state">{pageError}</div> : null}
        {metaLoading || loadingTransactions ? (
          <CalculationStatus label={transactionsWorkspace ? 'Recalculating…' : 'Loading…'} />
        ) : null}

        {!metaLoading && !loadingTransactions && !pageError && transactionsWorkspace ? (
          <div className="table-shell transaction-table-shell">
            <table className="transactions-table">
              <thead>
                <tr>
                  <th>Trade Date</th>
                  <th>Time</th>
                  <th>Settle</th>
                  <th>Type</th>
                  <th>Scope</th>
                  <th>Account</th>
                  <th>Instrument</th>
                  <th>Quantity</th>
                  <th>Price</th>
                  <th>Gross</th>
                  <th>Fees / Taxes</th>
                  <th>Net Cash</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {transactionsWorkspace.transactions.map((transaction) => {
                  const timeMeta = tradeTimeLabel(
                    transaction.trade_time,
                    transaction.trade_timezone,
                    transaction.trade_time_is_estimated,
                  )
                  return (
                  <tr
                    key={transaction.transaction_id}
                    className={transaction.transaction_id === selectedTransactionId ? 'transaction-row-active' : ''}
                    onClick={() => patchSearchParams({ transaction_id: transaction.transaction_id })}
                  >
                    <td>{transaction.trade_date}</td>
                    <td className="holding-name-cell">
                      <div className="holding-name-stack">
                        <span>{timeMeta.primary}</span>
                        <span className="holding-secondary">{timeMeta.secondary}</span>
                      </div>
                    </td>
                    <td>{transaction.settlement_date}</td>
                    <td>
                      <span className="transaction-type-pill">{formatLabel(transaction.transaction_type)}</span>
                    </td>
                    <td>
                      <span className={`coverage-pill ${transaction.flow_scope === 'external_cash_flow' ? 'coverage-pill-live' : 'coverage-pill-warning'}`}>
                        {formatLabel(transaction.flow_scope)}
                      </span>
                    </td>
                    <td className="transaction-account-cell">
                      <div className="holding-name-stack">
                        <Link className="table-inline-link" to={transactionAccountHref(portfolioId, transaction.account.account_id)}>
                          {transaction.account.account_name}
                        </Link>
                        <span className="holding-secondary">
                          {transaction.counterparty_account_id
                            ? `${formatLabel(transaction.account.account_type)} → ${
                                accountNameById[transaction.counterparty_account_id] || transaction.counterparty_account_id
                              }`
                            : formatLabel(transaction.account.account_type)}
                        </span>
                      </div>
                    </td>
                    <td className="holding-name-cell">
                      {transaction.instrument_ref ? (
                        <div className="holding-name-stack">
                          <span>{primaryIdentifier(transaction.instrument_ref)}</span>
                          <span className="holding-secondary">{transaction.instrument_ref.asset_name}</span>
                        </div>
                      ) : isFxConversionTransaction(transaction.transaction_type) ? (
                        <div className="holding-name-stack">
                          <span>FX Cash Conversion</span>
                          <span className="holding-secondary">
                            {transaction.currency} → {accountCurrencyById[transaction.counterparty_account_id || ''] || '—'}
                          </span>
                        </div>
                      ) : (
                        <span className="holding-secondary">Cash ledger</span>
                      )}
                    </td>
                    <td>{formatQuantity(transaction.quantity)}</td>
                    <td>
                      {transaction.price != null ? formatUnitPrice(transaction.price, transaction.currency) : '—'}
                    </td>
                    <td>{formatCurrency(transaction.gross_amount, transaction.currency)}</td>
                    <td>
                      {transaction.fees || transaction.taxes
                        ? `${formatCurrency(transaction.fees, transaction.currency)} / ${formatCurrency(transaction.taxes, transaction.currency)}`
                        : '—'}
                    </td>
                    <td
                      className={
                        transaction.net_cash_effect != null && transaction.net_cash_effect < 0
                          ? 'negative-cell'
                          : ''
                      }
                    >
                      {formatSignedCurrency(transaction.net_cash_effect, transaction.currency)}
                    </td>
                    <td className="transaction-note-cell">
                      <div className="holding-name-stack">
                        <span>{transaction.note || '—'}</span>
                        {transaction.transfer_group_id ? (
                          <span className="holding-secondary">
                            {transaction.transfer_group_id}
                            {transaction.transfer_object_type
                              ? ` · ${formatLabel(transaction.transfer_object_type)}`
                              : ''}
                          </span>
                        ) : isFxConversionTransaction(transaction.transaction_type) &&
                          transaction.counter_amount != null &&
                          transaction.fx_rate != null ? (
                          <span className="holding-secondary">
                            Receive {formatCurrency(
                              transaction.counter_amount,
                              accountCurrencyById[transaction.counterparty_account_id || ''] || 'USD',
                            )}{' '}
                            · FX {formatNumber(transaction.fx_rate, 6)}
                          </span>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : null}

        {!metaLoading && !pageError && selectedTransaction ? (
          <section className="transaction-inspector-grid">
            <article className="panel">
              <div className="panel-header">
                <div className="panel-title">Selected Transaction Fact</div>
                <div className="portfolio-detail-meta">{selectedTransaction.transaction_id}</div>
              </div>
              <div className="account-summary-list">
                <div className="account-summary-row">
                  <span>Account</span>
                  <strong>
                    <Link
                      className="table-inline-link"
                      to={transactionAccountHref(portfolioId, selectedTransaction.account.account_id)}
                    >
                      {selectedTransaction.account.account_name}
                    </Link>
                  </strong>
                </div>
                <div className="account-summary-row">
                  <span>Settlement / Counterparty</span>
                  <strong>
                    {selectedTransaction.settlement_cash_account ? (
                      <Link
                        className="table-inline-link"
                        to={transactionAccountHref(
                          portfolioId,
                          selectedTransaction.settlement_cash_account.account_id,
                        )}
                      >
                        {selectedTransaction.settlement_cash_account.account_name}
                      </Link>
                    ) : selectedTransaction.counterparty_account_id ? (
                      <Link
                        className="table-inline-link"
                        to={transactionAccountHref(portfolioId, selectedTransaction.counterparty_account_id)}
                      >
                        {accountNameById[selectedTransaction.counterparty_account_id] ||
                          selectedTransaction.counterparty_account_id}
                      </Link>
                    ) : (
                      '—'
                    )}
                  </strong>
                </div>
                <div className="account-summary-row">
                  <span>Instrument</span>
                  <strong>
                    {selectedTransaction.instrument_ref
                      ? `${primaryIdentifier(selectedTransaction.instrument_ref)} · ${selectedTransaction.instrument_ref.asset_name}`
                      : 'Cash ledger'}
                  </strong>
                </div>
                <div className="account-summary-row">
                  <span>Trade / Settle</span>
                  <strong>
                    {selectedTransaction.trade_date} {selectedTransaction.trade_time} / {selectedTransaction.settlement_date}
                  </strong>
                </div>
                <div className="account-summary-row">
                  <span>Gross / Net Cash</span>
                  <strong>
                    {formatCurrency(selectedTransaction.gross_amount, selectedTransaction.currency)} /{' '}
                    {formatSignedCurrency(selectedTransaction.net_cash_effect, selectedTransaction.currency)}
                  </strong>
                </div>
                <div className="account-summary-row">
                  <span>Created</span>
                  <strong>{selectedTransaction.created_at || '—'}</strong>
                </div>
              </div>
            </article>

            <article className="panel">
                <div className="panel-header">
                  <div className="panel-title">Related PositionLots</div>
                  <div className="portfolio-detail-meta">
                  {selectedTransaction.asset_id
                    ? transactionsWorkspace?.related_position_lot_summary.position_lot_count ?? 0
                    : 0}{' '}
                  lots
                  </div>
                </div>
              {!selectedTransaction.asset_id ? (
                <div className="empty-state">Cash-only facts do not create PositionLot context.</div>
              ) : relatedPositionLots.length ? (
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th>Impact</th>
                        <th>Status</th>
                        <th>Opened</th>
                        <th>Entry / Remaining Qty</th>
                        <th>Entry / Remaining Cost</th>
                        <th>Realized P&L</th>
                        <th>Income / Expense</th>
                      </tr>
                    </thead>
                    <tbody>
                      {relatedPositionLots.map((positionLot) => {
                        const impactKinds = resolvePositionLotImpactKinds(
                          positionLot,
                          selectedTransaction.transaction_id,
                        )
                        return (
                          <tr key={positionLot.position_lot_id}>
                            <td>
                              <div className="transaction-impact-tags">
                                {impactKinds.map((impactKind) => (
                                  <span key={impactKind} className="transaction-type-pill">
                                    {formatLabel(impactKind)}
                                  </span>
                                ))}
                              </div>
                            </td>
                            <td>{formatLabel(positionLot.status)}</td>
                            <td>{positionLot.opened_at}</td>
                            <td>
                              {formatQuantity(positionLot.entry_quantity)} /{' '}
                              {formatQuantity(positionLot.remaining_quantity)}
                            </td>
                            <td>
                              {formatCurrency(positionLot.entry_cost_basis, positionLot.currency)} /{' '}
                              {formatCurrency(positionLot.remaining_cost_basis, positionLot.currency)}
                            </td>
                            <td
                              className={positionLot.realized_pnl < 0 ? 'negative-cell' : ''}
                            >
                              {formatSignedCurrency(positionLot.realized_pnl, positionLot.currency)}
                            </td>
                            <td>
                              {formatCurrency(positionLot.income_cash_amount, positionLot.currency)} /{' '}
                              {formatCurrency(positionLot.expense_cash_amount, positionLot.currency)}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="empty-state">No PositionLots were linked back to this fact.</div>
              )}
            </article>
          </section>
        ) : null}

        {!metaLoading && !pageError && selectedTransactionId ? (
          <section className="panel transaction-inspector-panel">
            <div className="panel-header">
              <div className="panel-title">Ledger Posting Inspector</div>
              <div className="portfolio-detail-meta">{selectedTransactionId}</div>
            </div>
            {transactionsWorkspace ? (
              <>
                <div className="transaction-inspector-summary">
                  <span className="portfolio-subhead-meta">
                    {transactionsWorkspace.ledger_summary.posting_count} postings
                  </span>
                  <span className="portfolio-subhead-meta">
                    {transactionsWorkspace.ledger_summary.cash_posting_count} cash
                  </span>
                  <span className="portfolio-subhead-meta">
                    {transactionsWorkspace.ledger_summary.position_posting_count} position
                  </span>
                </div>
                <div className="table-shell">
                  <table className="transactions-table">
                    <thead>
                      <tr>
                        <th>Posting Role</th>
                        <th>Account</th>
                        <th>Instrument</th>
                        <th>Cash Delta</th>
                        <th>Quantity Delta</th>
                        <th>Cost Basis Delta</th>
                        <th>Settle</th>
                      </tr>
                    </thead>
                    <tbody>
                      {transactionsWorkspace.ledger_postings.map((posting) => (
                        <tr key={posting.posting_id}>
                          <td>
                            <span className="transaction-type-pill">{formatLabel(posting.posting_role)}</span>
                          </td>
                          <td>{accountNameById[posting.account_id] || posting.account_id}</td>
                          <td className="holding-name-cell">
                            {posting.instrument_ref ? (
                              <div className="holding-name-stack">
                                <span>{primaryIdentifier(posting.instrument_ref)}</span>
                                <span className="holding-secondary">{posting.instrument_ref.asset_name}</span>
                              </div>
                            ) : (
                              <span className="holding-secondary">Cash ledger</span>
                            )}
                          </td>
                          <td
                            className={
                              posting.cash_amount_delta != null && posting.cash_amount_delta < 0 ? 'negative-cell' : ''
                            }
                          >
                            {formatSignedCurrency(posting.cash_amount_delta, posting.currency)}
                          </td>
                          <td>{formatNumber(posting.quantity_delta, 2)}</td>
                          <td
                            className={
                              posting.cost_basis_delta != null && posting.cost_basis_delta < 0 ? 'negative-cell' : ''
                            }
                          >
                            {posting.cost_basis_delta != null
                              ? formatSignedCurrency(posting.cost_basis_delta, posting.currency)
                              : '—'}
                          </td>
                          <td>{posting.settlement_date}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}
          </section>
        ) : null}
      </section>

      {drawerOpen ? (
        <div className="transaction-drawer-backdrop" role="presentation" onClick={() => setDrawerOpen(false)}>
          <aside
            className="transaction-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Add transaction"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="transaction-drawer-header">
              <div>
                <div className="panel-title">Add Transaction</div>
                <div className="portfolio-detail-meta">Portfolio-private fact entry with platform instrument picker</div>
              </div>
              <button type="button" className="toolbar-link" onClick={() => setDrawerOpen(false)}>
                Close
              </button>
            </div>

            <form className="transaction-form" onSubmit={(event) => void handleCreateTransaction(event)}>
              <div className="transaction-form-grid">
                <label>
                  <span>Transaction Type</span>
                  <select
                    value={form.transaction_type}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        transaction_type: event.target.value,
                      }))
                    }
                  >
                    {TRANSACTION_TYPES.map((transactionType) => (
                      <option key={transactionType} value={transactionType}>
                        {formatLabel(transactionType)}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  <span>Account</span>
                  <select
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
                          {account.account_name} · {formatLabel(account.account_type)}
                          {account.cost_basis_method ? ` · ${formatLabel(account.cost_basis_method)}` : ''}
                          {account.account_type === 'securities_account' && account.allowed_asset_types?.length
                            ? ` · ${account.allowed_asset_types.map((assetType) => formatLabel(assetType)).join('/')}`
                            : ''}
                        </option>
                      ))}
                    </select>
                  </label>

                <label>
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

                <label>
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

                <label>
                  <span>Settlement Date</span>
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

                <label>
                  <span>Transaction Currency</span>
                  <input value={resolvedTransactionCurrency} readOnly />
                </label>

                {isTransferTransaction(form.transaction_type) ? (
                  <label>
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
                ) : isFxConversion ? (
                  <label>
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
                ) : shouldRequireSettlement ? (
                  <label>
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
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                <label>
                  <span>{grossAmountLabel(form.transaction_type)}</span>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={form.gross_amount}
                    placeholder={
                      isTransferTransaction(form.transaction_type) && form.transfer_object_type === 'position'
                        ? computedGrossAmount || 'Optional: derive from current carrying cost'
                        : computedGrossAmount || '0.00'
                    }
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        gross_amount: event.target.value,
                      }))
                    }
                  />
                </label>

                {isFxConversion ? (
                  <label>
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
                  <label>
                    <span>Quantity</span>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={form.quantity}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          quantity: event.target.value,
                        }))
                      }
                    />
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {isFxConversion ? (
                  <label>
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
                ) : isTransferTransaction(form.transaction_type) ? (
                  <label>
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
                          {account.account_name} · {formatLabel(account.account_type)}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : shouldUsePrice ? (
                  <label>
                    <span>Price</span>
                    <input
                      type="number"
                      min="0"
                      step="0.0001"
                      value={form.price}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          price: event.target.value,
                        }))
                      }
                    />
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {shouldShowFees ? (
                  <label>
                    <span>Fees</span>
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

                {shouldShowTaxes ? (
                  <label>
                    <span>Taxes</span>
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

              {shouldRequireSettlement && settlementAccountOptions.length === 0 ? (
                <div className="portfolio-detail-meta">
                  No same-currency settlement cash account is available for {resolvedTransactionCurrency}. Add one in
                  `Accounts` first.
                </div>
              ) : null}

              {isFxConversion && counterpartyAccounts.length === 0 ? (
                <div className="portfolio-detail-meta">
                  Add another deposit account in a different currency before recording an FX conversion.
                </div>
              ) : null}

              {isFxConversion && sharedFxRate ? (
                <div className="portfolio-detail-meta">
                  Shared spot reference: {sharedFxRate.base_currency}/{sharedFxRate.quote_currency}{' '}
                  {formatNumber(sharedFxRate.rate, 6)} as of {sharedFxRate.as_of_date}.
                </div>
              ) : null}

              <div className="portfolio-detail-meta">
                Trade time uses portfolio default timezone {DEFAULT_TRADE_TIMEZONE}. Leave the default if exact
                intraday sequencing is not material.
              </div>

              {shouldAllowInstrument ? (
                <section className="transaction-picker">
                  <div className="transaction-picker-header">
                    <div className="panel-title">Instrument Picker</div>
                    <div className="portfolio-detail-meta">
                      Source: platform `/api/instruments` · filtered to {selectedAccount?.currency || resolvedTransactionCurrency}
                    </div>
                  </div>

                  <label className="transaction-picker-search">
                    <span>Search Registry</span>
                    <input
                      type="search"
                      value={form.instrument_search}
                      placeholder="Ticker, name, or asset type"
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          instrument_search: event.target.value,
                        }))
                      }
                    />
                  </label>

                  {selectedInstrument ? (
                    <div className="transaction-selected-instrument">
                      <span className="ticker-pill">{primaryIdentifier(selectedInstrument)}</span>
                      <span>{selectedInstrument.asset_name}</span>
                      <span className="holding-secondary">
                        {selectedInstrument.currency} · {formatLabel(selectedInstrument.asset_type)}
                      </span>
                    </div>
                  ) : null}

                  <div className="transaction-picker-list">
                    {filteredInstrumentOptions.map((instrument) => (
                      <button
                        type="button"
                        key={instrument.asset_id}
                        className={`transaction-picker-row ${
                          form.asset_id === instrument.asset_id ? 'transaction-picker-row-active' : ''
                        }`}
                        onClick={() =>
                          setForm((current) => ({
                            ...current,
                            asset_id: instrument.asset_id,
                          }))
                        }
                      >
                        <div className="holding-name-stack">
                          <span>{primaryIdentifier(instrument)}</span>
                          <span className="holding-secondary">{instrument.asset_name}</span>
                        </div>
                        <div className="transaction-picker-meta">
                          <span>{formatLabel(instrument.asset_type)}</span>
                          <span>{instrument.coverage_state}</span>
                        </div>
                      </button>
                    ))}
                    {!filteredInstrumentOptions.length ? (
                      <div className="empty-state">
                        No registry instruments match the current search. Manage master data in Platform / Instruments.
                      </div>
                    ) : null}
                  </div>
                </section>
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

              <div className="transaction-form-footer">
                <div className="portfolio-detail-meta">
                  {isFxConversion
                    ? `Source ${
                        computedGrossAmount
                          ? formatCurrency(Number(computedGrossAmount), resolvedTransactionCurrency)
                          : '—'
                      } · Receive ${
                        computedCounterAmount
                          ? formatCurrency(Number(computedCounterAmount), resolvedCounterpartyCurrency || 'USD')
                          : '—'
                      }`
                    : `Gross ${
                        computedGrossAmount
                          ? formatCurrency(
                              Number(computedGrossAmount),
                              selectedInstrument?.currency ?? selectedAccount?.currency ?? 'USD',
                            )
                          : '—'
                      }`}
                </div>
                <button type="submit" className="toolbar-link button-primary">
                  Save Transaction
                </button>
              </div>
            </form>
          </aside>
        </div>
      ) : null}
    </PortfolioWorkspaceLayout>
  )
}
