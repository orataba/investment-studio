import { FormEvent, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, useParams, useSearchParams } from 'react-router'

import CalculationStatus from '../components/CalculationStatus'
import FundDistributionTasksPanel from '../components/FundDistributionTasksPanel'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  createPortfolioInternalTransfer,
  commitPortfolioTransactionImport,
  createPortfolioTransactionCaptureAnalysisRevision,
  createPortfolioTransactionCaptureBatch,
  createPortfolioTransaction,
  deletePortfolioTransaction,
  getPortfolioAccounts,
  getPortfolioDerivativeContracts,
  getPortfolioFxRates,
  getPortfolioInstruments,
  getPortfolioTransactionExecutionQuote,
  getPortfolioTransactionPositionPreview,
  getPortfolioTransactionCaptureBatches,
  getPortfolioTransactionsWorkspace,
  importPortfolioTransactionFile,
  materializePlatformSecurity,
  portfolioTransactionDownloadUrl,
  portfolioTransactionCaptureImageUrl,
  portfolioTransactionTemplateUrl,
  previewPortfolioTransactionFile,
  reviewPortfolioInstrumentEventTask,
  searchPlatformSecurityCatalog,
  startPortfolioTransactionCaptureAnalysis,
  uploadPortfolioTransactionCapture,
  type PortfolioAccountRecord,
  type PortfolioFeeCategory,
  type PortfolioDerivativeContractCreate,
  type PortfolioDerivativeContractRecord,
  type PortfolioPositionLotRecord,
  type PortfolioSharedFxRateRecord,
  type PortfolioTransactionPositionPreviewResponse,
  type SharedInstrumentRecord,
  type SecuritySearchOption,
  type PortfolioTransactionCreatePayload,
  type PortfolioTransactionCaptureAnalysisRevision,
  type PortfolioTransactionCaptureBatchRecord,
  type PortfolioTransactionCaptureBatchPurpose,
  type PortfolioTransactionFileFormat,
  type PortfolioTransactionFilePreviewResponse,
  type PortfolioTransactionFilters,
  type PortfolioTransactionImportAction,
  type PortfolioTransactionImportCommand,
  type PortfolioTransactionRecord,
  type PortfolioTransactionUpdatePayload,
  type PortfolioTransactionWorkspaceResponse,
  type PortfolioInstrumentEventTaskRecord,
  updatePortfolioTransaction,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatQuantity,
  formatSignedCurrency,
  formatUnitPrice,
  signedValueClass,
} from '../lib/format'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'
import DownloadFormatMenu from '../../../../../packages/ui/src/DownloadFormatMenu'
import {
  countActiveTransactionFilters,
  transactionActivityLabel,
  transactionChangedFields,
  transactionDateLabels,
} from '../lib/transactionPresentation'
import {
  requiredDerivativeContractType,
  supportsTransactionAssetType,
} from '../lib/transactionEligibility'
import {
  resolveWorkspaceTransactionSelection,
  stopTransactionRowSelection,
} from '../lib/transactionSelection'
import {
  calculateTransactionGrossAmount,
  calculateTransactionUnitPrice,
  resolveTransactionPriceContract,
  transactionPriceContractForDerivative,
  transactionPriceContractForInstrument,
  type TransactionPriceContract,
} from '../lib/transactionPricing'
import { executionQuoteUnavailableMessage } from '../lib/executionQuotePresentation'
import {
  buildHumanReviewedCaptureProposal,
  cloneTransactionImport,
  hasTransactionCaptureImport,
  transactionCaptureProposalReady,
  type TransactionCaptureDuplicateAssessment,
  type TransactionCaptureReviewDraft,
} from '../lib/transactionCaptureReview'
import {
  buildInitialDerivativeContractDraft,
  buildInitialFcnUnderlyingDraft,
  derivativeContractDraftFromRecord,
  derivativeContractFromDraft,
  type DerivativeContractDraft,
} from '../lib/derivativeContractDraft'
import {
  resolveTransactionAction,
  transactionActionGroups,
  transactionActionValue,
  type TransactionAssetDomain,
} from '../lib/transactionActions'

const DEFAULT_FORM_TIME = (import.meta.env.VITE_PORTFOLIO_DEFAULT_TRADE_TIME || '12:00').slice(0, 5)
const DEFAULT_TRADE_TIMEZONE = import.meta.env.VITE_PORTFOLIO_DEFAULT_TRADE_TIMEZONE || 'Asia/Shanghai'
const TRANSACTION_FILTER_TYPE_GROUPS = [
  {
    label: 'Trades',
    types: ['buy', 'sell', 'option_write', 'option_buy_to_close'],
  },
  {
    label: 'Income and capital',
    types: ['dividend', 'dividend_reinvestment', 'coupon', 'interest', 'return_of_capital', 'maturity_redemption'],
  },
  {
    label: 'Derivative close outcomes',
    types: ['lifecycle_event'],
  },
  {
    label: 'Cash and charges',
    types: ['deposit', 'withdrawal', 'fx_conversion', 'fee', 'tax'],
  },
  {
    label: 'Transfers and setup',
    types: ['transfer_out', 'transfer_in', 'opening_balance'],
  },
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

type TransactionEntryKind = 'security' | 'fcn' | 'option' | 'cash'

const FUND_INSTRUMENT_TYPES = new Set(['public_fund', 'private_fund'])

function isFundInstrumentType(instrumentType?: string | null) {
  return FUND_INSTRUMENT_TYPES.has((instrumentType || '').trim().toLowerCase())
}

const TRANSACTION_ENTRY_KINDS: Array<{
  value: TransactionEntryKind
  label: string
  description: string
}> = [
  {
    value: 'security',
    label: 'Security',
    description: 'Equity, ETF, fund, and other Registry securities',
  },
  {
    value: 'fcn',
    label: 'FCN',
    description: 'Fixed coupon note entry, income, exit, and close outcomes',
  },
  {
    value: 'option',
    label: 'Option',
    description: 'Call or put positions with explicit open and close direction',
  },
  {
    value: 'cash',
    label: 'Cash & Operations',
    description: 'Cash movements, FX, fees, tax, and setup',
  },
]

function transactionEntryKind(
  assetDomain: TransactionAssetDomain,
  assetSubtype?: string | null,
): TransactionEntryKind {
  if (assetDomain === 'cash') return 'cash'
  if (assetDomain === 'derivative') return assetSubtype === 'fcn' ? 'fcn' : 'option'
  return 'security'
}

function entryKindAssetDomain(entryKind: TransactionEntryKind): TransactionAssetDomain {
  if (entryKind === 'cash') return 'cash'
  if (entryKind === 'security') return 'security'
  return 'derivative'
}

function accountAllowsEntryKind(
  account: PortfolioAccountRecord,
  entryKind: TransactionEntryKind,
) {
  return account.account_category === entryKind
}

function assetTypeAccountCategory(assetType: string): TransactionEntryKind | null {
  const normalized = assetType.trim().toLowerCase()
  if (normalized === 'fcn' || normalized === 'option') return normalized
  if (['equity', 'etf', 'public_fund', 'private_fund', 'other'].includes(normalized)) return 'security'
  return null
}

type TransactionInspectorTab = 'fact' | 'postings' | 'lots' | 'obligations' | 'history'

type TransactionCaptureAssistantView = 'new' | 'history'

type TransactionCaptureDraftFile = {
  id: string
  file: File
  previewUrl: string
}

const TRANSACTION_CAPTURE_PURPOSES: Array<{
  value: PortfolioTransactionCaptureBatchPurpose
  label: string
  description: string
}> = [
  {
    value: 'auto',
    label: 'Let AI decide',
    description: 'Mixed or uncertain broker screenshots',
  },
  {
    value: 'transaction_import',
    label: 'Record transactions',
    description: 'Trades, cash movements, fees, and income',
  },
  {
    value: 'portfolio_initialization',
    label: 'Initialize portfolio',
    description: 'Historical holdings and opening balances',
  },
  {
    value: 'position_reconciliation',
    label: 'Reconcile positions',
    description: 'Compare broker positions with the ledger',
  },
]

const TRANSACTION_CAPTURE_MEDIA_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp'])
const TRANSACTION_CAPTURE_MAX_FILES = 10
const TRANSACTION_CAPTURE_MAX_BYTES = 12 * 1024 * 1024

const TRANSACTION_CAPTURE_ACTIONS: Record<
  PortfolioTransactionImportCommand['asset_type'],
  Array<{ value: PortfolioTransactionImportAction; label: string }>
> = {
  security: [
    { value: 'buy', label: 'Buy' },
    { value: 'sell', label: 'Sell' },
    { value: 'dividend', label: 'Dividend' },
    { value: 'dividend_reinvestment', label: 'Dividend reinvestment' },
    { value: 'return_of_capital', label: 'Return of capital' },
    { value: 'fee', label: 'Fee' },
    { value: 'tax', label: 'Tax' },
    { value: 'opening_balance', label: 'Opening balance' },
    { value: 'transfer_in', label: 'Transfer in' },
    { value: 'transfer_out', label: 'Transfer out' },
  ],
  fcn: [
    { value: 'entry', label: 'Entry' },
    { value: 'early_exit', label: 'Early exit' },
    { value: 'coupon', label: 'Coupon' },
    { value: 'knock_in_close', label: 'Knock-in close' },
    { value: 'knock_out_close', label: 'Knock-out close' },
    { value: 'maturity_close', label: 'Maturity close' },
    { value: 'fee', label: 'Fee' },
    { value: 'tax', label: 'Tax' },
    { value: 'opening_balance', label: 'Opening balance' },
  ],
  option: [
    { value: 'buy_to_open', label: 'Buy to open' },
    { value: 'sell_to_close', label: 'Sell to close' },
    { value: 'sell_to_open', label: 'Sell to open' },
    { value: 'buy_to_close', label: 'Buy to close' },
    { value: 'expire_long', label: 'Expire long' },
    { value: 'cash_settle_long', label: 'Cash settle long' },
    { value: 'expire_written', label: 'Expire written' },
    { value: 'cash_settle_written', label: 'Cash settle written' },
    { value: 'fee', label: 'Fee' },
    { value: 'tax', label: 'Tax' },
    { value: 'opening_balance', label: 'Opening balance' },
  ],
  cash: [
    { value: 'deposit', label: 'Deposit' },
    { value: 'withdrawal', label: 'Withdrawal' },
    { value: 'interest', label: 'Interest' },
    { value: 'fx_conversion', label: 'FX conversion' },
    { value: 'fee', label: 'Fee' },
    { value: 'tax', label: 'Tax' },
    { value: 'opening_balance', label: 'Opening balance' },
    { value: 'transfer_in', label: 'Transfer in' },
    { value: 'transfer_out', label: 'Transfer out' },
  ],
}

let fallbackIdempotencySequence = 0

function transactionIdempotencyKey(operation: 'create' | 'transfer' | 'file-import') {
  const randomId = globalThis.crypto?.randomUUID?.()
  if (randomId) {
    return `transaction-${operation}-${randomId}`
  }
  fallbackIdempotencySequence += 1
  return `transaction-${operation}-${Date.now()}-${fallbackIdempotencySequence}`
}

function transactionCaptureIdempotencyKey(batchId: string, revision: number) {
  return `transaction-capture-${batchId}-revision-${revision}`
}

function transactionCaptureRecordTitle(
  record: PortfolioTransactionImportCommand,
  recordIndex: number,
) {
  const assetReference = record.instrument_id
    ?? record.derivative_contract?.contract_name
    ?? record.derivative_contract_id
    ?? record.currency
  return `Record ${recordIndex + 1} · ${formatLabel(record.asset_type)} · ${assetReference}`
}

function captureInputValue(value: string | number | null | undefined) {
  return value == null ? '' : String(value)
}

function primaryIdentifier(
  instrument:
    | SecuritySearchOption
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

function instrumentSearchLabel(instrument: SecuritySearchOption) {
  return `${primaryIdentifier(instrument)} · ${instrument.instrument_name}`
}

function RegistryInstrumentPicker({
  label,
  value,
  instruments,
  onSelect,
}: {
  label: string
  value: string
  instruments: SharedInstrumentRecord[]
  onSelect: (instrumentId: string) => void
}) {
  const [query, setQuery] = useState('')
  const deferredQuery = useDeferredValue(query)
  const selectedInstrument =
    instruments.find((instrument) => instrument.instrument_id === value) ?? null
  const inputValue = selectedInstrument
    ? instrumentSearchLabel(selectedInstrument)
    : query
  const normalizedQuery = deferredQuery.trim().toLowerCase()
  const results = useMemo(() => {
    if (!normalizedQuery) {
      return []
    }
    return instruments
      .filter((instrument) =>
        [
          primaryIdentifier(instrument),
          instrument.instrument_name,
          instrument.instrument_type,
          instrument.currency,
        ]
          .join(' ')
          .toLowerCase()
          .includes(normalizedQuery),
      )
      .slice(0, 12)
  }, [instruments, normalizedQuery])
  const showResults = !selectedInstrument && query.trim() !== ''

  function selectResult(instrument: SharedInstrumentRecord) {
    setQuery('')
    onSelect(instrument.instrument_id)
  }

  return (
    <div className="transaction-instrument-search transaction-registry-picker">
      <label className="transaction-picker-search">
        <span>{label}</span>
        <input
          type="search"
          value={inputValue}
          placeholder="Search ticker or name"
          onChange={(event) => {
            setQuery(event.target.value)
            if (selectedInstrument) {
              onSelect('')
            }
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || results.length === 0) {
              return
            }
            event.preventDefault()
            selectResult(results[0])
          }}
        />
      </label>
      {showResults ? (
        <div className="transaction-instrument-results">
          {results.map((instrument) => (
            <button
              type="button"
              key={instrument.instrument_id}
              className="transaction-instrument-result"
              onClick={() => selectResult(instrument)}
            >
              <div className="holding-name-stack">
                <span>{primaryIdentifier(instrument)}</span>
                <span className="holding-secondary">{instrument.instrument_name}</span>
              </div>
              <span className="transaction-picker-meta">{instrument.currency}</span>
            </button>
          ))}
          {!results.length ? (
            <div className="transaction-instrument-empty">No matching Registry security.</div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
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

function formatCaptureByteSize(byteSize: number) {
  if (byteSize < 1024) return `${byteSize} B`
  if (byteSize < 1024 * 1024) return `${Math.ceil(byteSize / 1024)} KB`
  return `${(byteSize / (1024 * 1024)).toFixed(1)} MB`
}

let transactionCaptureDraftSequence = 0

function transactionCaptureDraftId(file: File) {
  transactionCaptureDraftSequence += 1
  return `${file.name}:${file.size}:${file.lastModified}:${transactionCaptureDraftSequence}`
}

function transactionCapturePreviewUrl(file: File) {
  return typeof URL.createObjectURL === 'function' ? URL.createObjectURL(file) : ''
}

function revokeTransactionCapturePreview(previewUrl: string) {
  if (previewUrl && typeof URL.revokeObjectURL === 'function') {
    URL.revokeObjectURL(previewUrl)
  }
}

function transactionCapturePurposeLabel(purpose: PortfolioTransactionCaptureBatchPurpose) {
  return TRANSACTION_CAPTURE_PURPOSES.find((item) => item.value === purpose)?.label ?? formatLabel(purpose)
}

function transactionCaptureDisplayStatus(
  batch: PortfolioTransactionCaptureBatchRecord,
) {
  if (batch.ledger_status === 'recorded') {
    return 'recorded'
  }
  if (batch.ledger_status === 'partially_recorded') {
    return 'partially_recorded'
  }
  if (batch.analysis_run_status === 'queued' || batch.analysis_run_status === 'running') {
    return batch.analysis_run_status
  }
  if (batch.analysis_run_status === 'failed') {
    return 'failed'
  }
  return batch.status
}

function transactionCaptureStatusLabel(
  batch: PortfolioTransactionCaptureBatchRecord,
) {
  if (batch.ledger_status === 'recorded') return 'Recorded'
  if (batch.ledger_status === 'partially_recorded') return 'Partially recorded'
  if (batch.analysis_run_status === 'queued') return 'Queued'
  if (batch.analysis_run_status === 'running') return 'Analyzing'
  if (batch.analysis_run_status === 'failed') return 'Analysis failed'
  if (batch.status === 'review_required') {
    return hasTransactionCaptureImport(batch.latest_analysis) ? 'Needs review' : 'No draft'
  }
  return 'Ready'
}

function accountAllowsAssetType(
  account: PortfolioAccountRecord | null | undefined,
  assetType: string,
) {
  if (
    !account ||
    account.account_category === 'cash'
  ) {
    return true
  }

  return account.account_category === assetTypeAccountCategory(assetType)
}

function requiresSettlement(
  transactionType: string,
  accountType?: string | null,
  lifecycleEventType?: string | null,
) {
  if (isFxConversionTransaction(transactionType)) {
    return false
  }
  if (transactionType === 'lifecycle_event') {
    return lifecycleEventType === 'option_writer_cash_settlement'
  }
  if (
    transactionType === 'maturity_redemption' &&
    lifecycleEventType === 'option_long_expiry'
  ) {
    return false
  }
  if (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'option_write' ||
    transactionType === 'option_buy_to_close'
  ) {
    return true
  }

  if (
    transactionType === 'dividend' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption' ||
    transactionType === 'lifecycle_event'
  ) {
    return true
  }

  return (transactionType === 'fee' || transactionType === 'tax') && accountType === 'securities_account'
}

function eligibleAccounts(
  transactionType: string,
  accounts: PortfolioAccountRecord[],
  transferObjectType?: string | null,
  entryKind?: TransactionEntryKind,
) {
  if (entryKind) {
    return accounts.filter((account) => accountAllowsEntryKind(account, entryKind))
  }
  if (
    transactionType === 'deposit' ||
    transactionType === 'withdrawal' ||
    transactionType === 'interest' ||
    isFxConversionTransaction(transactionType)
  ) {
    return accounts.filter((account) => account.account_category === 'cash')
  }

  if (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'option_write' ||
    transactionType === 'option_buy_to_close' ||
    transactionType === 'dividend' ||
    transactionType === 'dividend_reinvestment' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption' ||
    transactionType === 'lifecycle_event'
  ) {
    return accounts.filter((account) => account.account_category !== 'cash')
  }

  if (isTransferTransaction(transactionType)) {
    if (transferObjectType === 'cash') {
      return accounts.filter((account) => account.account_category === 'cash')
    }
    if (transferObjectType === 'position') {
      return accounts.filter((account) => account.account_category !== 'cash')
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
        account.account_category === 'cash' &&
        account.account_id !== currentAccountId &&
        (!sourceCurrency || account.currency.toUpperCase() !== sourceCurrency),
    )
  }

  if (!isTransferTransaction(transactionType)) {
    return []
  }

  const sourceAccount = accounts.find((account) => account.account_id === currentAccountId)
  return eligibleAccounts(transactionType, accounts, transferObjectType).filter(
    (account) =>
      account.account_id !== currentAccountId &&
      (transferObjectType !== 'position' ||
        !sourceAccount ||
        account.account_category === sourceAccount.account_category),
  )
}

function isSelectableInstrument(
  transactionType: string,
  instrument: SecuritySearchOption,
  account?: PortfolioAccountRecord | null,
  accountCurrency?: string | null,
  transferObjectType?: string | null,
) {
  if (isFxConversionTransaction(transactionType)) {
    return false
  }
  const currencyIsVerified =
    !('catalog_symbol' in instrument) || instrument.currency_verified
  if (
    accountCurrency &&
    currencyIsVerified &&
    instrument.currency.toUpperCase() !== accountCurrency.toUpperCase()
  ) {
    return false
  }

  if (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'option_write' ||
    transactionType === 'option_buy_to_close' ||
    transactionType === 'dividend' ||
    transactionType === 'dividend_reinvestment' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    transactionType === 'maturity_redemption' ||
    transactionType === 'lifecycle_event' ||
    transactionType === 'fee' ||
    transactionType === 'tax' ||
    (isTransferTransaction(transactionType) && transferObjectType === 'position')
  ) {
    return (
      supportsTransactionAssetType(transactionType, instrument.instrument_type) &&
      accountAllowsAssetType(account, instrument.instrument_type)
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
    transactionType === 'option_write' ||
    transactionType === 'option_buy_to_close' ||
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
  return (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'option_write' ||
    transactionType === 'option_buy_to_close'
  )
}

function grossAmountLabel(transactionType: string, lifecycleEventType?: string | null) {
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

  if (transactionType === 'option_write') {
    return 'Premium Received'
  }

  if (transactionType === 'option_buy_to_close') {
    return 'Close Cost'
  }

  if (
    transactionType === 'maturity_redemption' &&
    lifecycleEventType === 'option_long_cash_settlement'
  ) {
    return 'Cash Settlement Received'
  }

  if (
    transactionType === 'maturity_redemption' &&
    lifecycleEventType === 'option_long_expiry'
  ) {
    return 'Cash Amount (zero)'
  }

  if (transactionType === 'lifecycle_event') {
    return lifecycleEventType === 'option_writer_cash_settlement'
      ? 'Cash Settlement Paid'
      : 'Cash Amount (zero)'
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

function showsFeeField(transactionType: string, lifecycleEventType?: string | null) {
  return (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'option_write' ||
    transactionType === 'option_buy_to_close' ||
    transactionType === 'dividend' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    (transactionType === 'maturity_redemption' &&
      lifecycleEventType !== 'option_long_expiry') ||
    (transactionType === 'lifecycle_event' &&
      lifecycleEventType === 'option_writer_cash_settlement')
  )
}

function showsTaxField(transactionType: string, lifecycleEventType?: string | null) {
  return (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'option_write' ||
    transactionType === 'option_buy_to_close' ||
    transactionType === 'dividend' ||
    transactionType === 'coupon' ||
    transactionType === 'return_of_capital' ||
    (transactionType === 'maturity_redemption' &&
      lifecycleEventType !== 'option_long_expiry') ||
    (transactionType === 'lifecycle_event' &&
      lifecycleEventType === 'option_writer_cash_settlement')
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
  lifecycleEventType?: string | null,
) {
  if (grossAmount == null || !Number.isFinite(grossAmount)) {
    return null
  }

  if (transactionType === 'buy') {
    return -(grossAmount + fees + taxes)
  }
  if (transactionType === 'option_write') {
    return grossAmount - fees - taxes
  }
  if (transactionType === 'option_buy_to_close') {
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
  if (transactionType === 'lifecycle_event') {
    return lifecycleEventType === 'option_writer_cash_settlement'
      ? -(grossAmount + fees + taxes)
      : 0
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
  asset_domain: TransactionAssetDomain
  transaction_type: string
  lifecycle_event_type: string
  trade_date: string
  trade_time: string
  settlement_date: string
  position_effective_date: string
  entitlement_date: string
  acquisition_date: string
  account_id: string
  counterparty_account_id: string
  settlement_cash_account_id: string
  transfer_object_type: string
  instrument_id: string
  derivative_contract_id: string
  quantity: string
  price: string
  gross_amount: string
  counter_amount: string
  fx_rate: string
  fees: string
  fee_category: PortfolioFeeCategory
  taxes: string
  note: string
  source_system: string
  external_reference: string
  instrument_search: string
}

type PricingAnchor = 'price' | 'gross_amount'

function buildInitialFormState(accounts: PortfolioAccountRecord[]): TransactionFormState {
  const defaultFormDate = localTodayIso()
  const defaultSecurityAccount = accounts.find((account) =>
    accountAllowsEntryKind(account, 'security'),
  )
  const defaultCashAccount =
    accounts.find((account) => account.account_id === defaultSecurityAccount?.default_settlement_cash_account_id) ??
    accounts.find((account) => account.account_type === 'deposit_account')

  return {
    asset_domain: 'security',
    transaction_type: 'buy',
    lifecycle_event_type: '',
    trade_date: defaultFormDate,
    trade_time: '',
    settlement_date: defaultFormDate,
    position_effective_date: defaultFormDate,
    entitlement_date: '',
    acquisition_date: '',
    account_id: defaultSecurityAccount?.account_id ?? '',
    counterparty_account_id: '',
    settlement_cash_account_id: defaultCashAccount?.account_id ?? '',
    transfer_object_type: 'cash',
    instrument_id: '',
    derivative_contract_id: '',
    quantity: '',
    price: '',
    gross_amount: '',
    counter_amount: '',
    fx_rate: '',
    fees: '0',
    fee_category: 'unknown',
    taxes: '0',
    note: '',
    source_system: '',
    external_reference: '',
    instrument_search: '',
  }
}

function buildFormStateFromTransaction(transaction: PortfolioTransactionRecord): TransactionFormState {
  return {
    asset_domain: transaction.asset_domain,
    transaction_type: transaction.transaction_type,
    lifecycle_event_type: transaction.lifecycle_event_type || '',
    trade_date: transaction.trade_date,
    trade_time: transaction.trade_time_is_estimated ? '' : transaction.trade_time,
    settlement_date: transaction.settlement_date,
    position_effective_date:
      transaction.position_effective_date || transaction.trade_date,
    entitlement_date: transaction.entitlement_date || '',
    acquisition_date: transaction.acquisition_date || '',
    account_id: transaction.account.account_id,
    counterparty_account_id: transaction.counterparty_account_id || '',
    settlement_cash_account_id: transaction.settlement_cash_account?.account_id || '',
    transfer_object_type: transaction.transfer_object_type || 'cash',
    instrument_id: transaction.instrument_id || '',
    derivative_contract_id: transaction.derivative_contract_id || '',
    quantity: formatFormNumber(transaction.quantity, { zeroAsEmpty: true }),
    price: formatFormNumber(transaction.price, { zeroAsEmpty: true }),
    gross_amount: formatFormNumber(transaction.gross_amount, { zeroAsEmpty: true }),
    counter_amount: formatFormNumber(transaction.counter_amount, { zeroAsEmpty: true }),
    fx_rate: formatFormNumber(transaction.fx_rate, { zeroAsEmpty: true }),
    fees: formatFormNumber(transaction.fees),
    fee_category: transaction.fee_category,
    taxes: formatFormNumber(transaction.taxes),
    note: transaction.note || '',
    source_system: transaction.source_system || '',
    external_reference: transaction.external_reference || '',
    instrument_search: '',
  }
}

function supportsEntitlementDate(transactionType: string, hasAssetReference: boolean) {
  return (
    transactionType === 'dividend' ||
    transactionType === 'dividend_reinvestment' ||
    transactionType === 'coupon' ||
    ((transactionType === 'fee' || transactionType === 'tax') && hasAssetReference)
  )
}

function supportsPositionEffectiveDate(transactionType: string) {
  return (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'dividend_reinvestment' ||
    transactionType === 'maturity_redemption'
  )
}

function supportsAcquisitionDate(transactionType: string, accountType?: string | null) {
  return transactionType === 'opening_balance' && accountType === 'securities_account'
}

function canEditTransaction(transaction: PortfolioTransactionRecord | null) {
  if (!transaction) {
    return false
  }
  return (
    !transaction.transfer_group_id &&
    !isTransferTransaction(transaction.transaction_type)
  )
}

function tradeTimeLabel(tradeTime: string, tradeTimezone: string, tradeTimeIsEstimated: boolean) {
  return {
    primary: tradeTime || DEFAULT_FORM_TIME,
    secondary: tradeTimeIsEstimated ? `${tradeTimezone} · estimated` : tradeTimezone,
  }
}

function auditTimestampLabel(value: string) {
  const parsed = new Date(value)
  if (!Number.isFinite(parsed.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}

function transactionAccountHref(portfolioId: string, accountId: string) {
  return `/portfolios/${portfolioId}/accounts?account_id=${encodeURIComponent(accountId)}`
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
  const entryKindControlRef = useRef<HTMLButtonElement | null>(null)
  const securitySearchRef = useRef<HTMLInputElement | null>(null)
  const accountSelectRef = useRef<HTMLSelectElement | null>(null)
  const transactionFileInputRef = useRef<HTMLInputElement | null>(null)
  const transactionCaptureInputRef = useRef<HTMLInputElement | null>(null)
  const transactionCaptureDraftFilesRef = useRef<TransactionCaptureDraftFile[]>([])
  const autoQuoteKeyRef = useRef<string | null>(null)
  const autoQuantityKeyRef = useRef<string | null>(null)
  const autoGrossDerivedRef = useRef(false)
  const submittingTransactionRef = useRef(false)
  const [accounts, setAccounts] = useState<PortfolioAccountRecord[]>([])
  const [instruments, setInstruments] = useState<SharedInstrumentRecord[]>([])
  const [derivativeContracts, setDerivativeContracts] = useState<PortfolioDerivativeContractRecord[]>([])
  const [fxRates, setFxRates] = useState<PortfolioSharedFxRateRecord[]>([])
  const [transactionsWorkspace, setTransactionsWorkspace] = useState<PortfolioTransactionWorkspaceResponse | null>(null)
  const [workspaceRequestedTransactionId, setWorkspaceRequestedTransactionId] = useState<string | null>(null)
  const [metaLoading, setMetaLoading] = useState(true)
  const [loadingTransactions, setLoadingTransactions] = useState(true)
  const [metadataError, setMetadataError] = useState<string | null>(null)
  const [ledgerError, setLedgerError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [activeEventTask, setActiveEventTask] = useState<PortfolioInstrumentEventTaskRecord | null>(null)
  const [activeEventTaskReviewer, setActiveEventTaskReviewer] = useState('')
  const [eventTasksRefreshKey, setEventTasksRefreshKey] = useState(0)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingTransactionId, setEditingTransactionId] = useState<string | null>(null)
  const [submittingTransaction, setSubmittingTransaction] = useState(false)
  const [importingFile, setImportingFile] = useState(false)
  const [uploadingCapture, setUploadingCapture] = useState(false)
  const [startingCaptureBatchId, setStartingCaptureBatchId] = useState<string | null>(null)
  const [captureAssistantOpen, setCaptureAssistantOpen] = useState(false)
  const [captureAssistantView, setCaptureAssistantView] =
    useState<TransactionCaptureAssistantView>('new')
  const [capturePurpose, setCapturePurpose] =
    useState<PortfolioTransactionCaptureBatchPurpose>('auto')
  const [captureDraftFiles, setCaptureDraftFiles] = useState<TransactionCaptureDraftFile[]>([])
  const [captureDragActive, setCaptureDragActive] = useState(false)
  const [selectedCaptureBatchId, setSelectedCaptureBatchId] = useState<string | null>(null)
  const [transactionCaptureBatches, setTransactionCaptureBatches] = useState<
    PortfolioTransactionCaptureBatchRecord[]
  >([])
  const [captureError, setCaptureError] = useState<string | null>(null)
  const [captureReviewDraft, setCaptureReviewDraft] =
    useState<TransactionCaptureReviewDraft | null>(null)
  const [savingCaptureReview, setSavingCaptureReview] = useState(false)
  const [committingCapture, setCommittingCapture] = useState(false)
  const [pendingFileImport, setPendingFileImport] = useState<{
    fileName: string
    file: File
    preview: PortfolioTransactionFilePreviewResponse
  } | null>(null)
  const [fileImportError, setFileImportError] = useState<string | null>(null)
  const [pendingDeleteTransaction, setPendingDeleteTransaction] = useState<PortfolioTransactionRecord | null>(null)
  const [deletingTransaction, setDeletingTransaction] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [inspectorTab, setInspectorTab] = useState<TransactionInspectorTab>('fact')
  const [form, setForm] = useState<TransactionFormState>(() => buildInitialFormState([]))
  const deferredInstrumentSearch = useDeferredValue(form.instrument_search)
  const [securityCatalogResults, setSecurityCatalogResults] = useState<SecuritySearchOption[]>([])
  const [securityCatalogLoading, setSecurityCatalogLoading] = useState(false)
  const [securityCatalogError, setSecurityCatalogError] = useState<string | null>(null)
  const [securityMaterializing, setSecurityMaterializing] = useState(false)
  const [derivativeDraft, setDerivativeDraft] = useState<DerivativeContractDraft>(() =>
    buildInitialDerivativeContractDraft(),
  )
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
    if (submittingTransaction) {
      return
    }
    setDrawerOpen(false)
    setEditingTransactionId(null)
    setActiveEventTask(null)
    setActiveEventTaskReviewer('')
  })
  const captureAssistantDialogRef = useModalDialog(captureAssistantOpen, () => {
    if (uploadingCapture || savingCaptureReview || committingCapture) {
      return
    }
    setCaptureAssistantOpen(false)
    setCaptureDragActive(false)
    setCaptureReviewDraft(null)
  })

  currentPortfolioIdRef.current = portfolioId

  const filters: PortfolioTransactionFilters = {
    account_id: searchParams.get('account_id') ?? '',
    asset_domain:
      (searchParams.get('asset_domain') as PortfolioTransactionFilters['asset_domain']) ?? '',
    asset_subtype:
      (searchParams.get('asset_subtype') as PortfolioTransactionFilters['asset_subtype']) ?? '',
    transaction_type: searchParams.get('transaction_type') ?? '',
    position_reference_id: searchParams.get('position_reference_id') ?? '',
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
    transactionCaptureDraftFilesRef.current = captureDraftFiles
  }, [captureDraftFiles])

  useEffect(() => () => {
    transactionCaptureDraftFilesRef.current.forEach((draft) => {
      revokeTransactionCapturePreview(draft.previewUrl)
    })
  }, [])

  useEffect(() => {
    let cancelled = false
    setPendingDeleteTransaction(null)
    setDeleteError(null)
    setDeletingTransaction(false)
    setInspectorTab('fact')

    if (!portfolioId) {
      setAccounts([])
      setInstruments([])
      setDerivativeContracts([])
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
    setDerivativeContracts([])
    setFxRates([])
    setForm(buildInitialFormState([]))
    setDerivativeDraft(buildInitialDerivativeContractDraft())
    setMetadataError(null)

    Promise.all([
      getPortfolioAccounts(portfolioId),
      getPortfolioInstruments(portfolioId),
      getPortfolioDerivativeContracts(portfolioId),
      getPortfolioFxRates(portfolioId),
    ])
      .then(([accountsResponse, instrumentsResponse, derivativeResponse, fxRatesResponse]) => {
        if (cancelled) {
          return
        }

        setAccounts(accountsResponse.accounts)
        setInstruments(instrumentsResponse.instruments)
        setDerivativeContracts(derivativeResponse.derivative_contracts)
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
    setCaptureAssistantOpen(false)
    setCaptureAssistantView('new')
    setCapturePurpose('auto')
    setSelectedCaptureBatchId(null)
    setCaptureDragActive(false)
    setCaptureReviewDraft(null)
    setSavingCaptureReview(false)
    setCommittingCapture(false)
    setCaptureDraftFiles((current) => {
      current.forEach((draft) => revokeTransactionCapturePreview(draft.previewUrl))
      return []
    })
    if (!portfolioId) {
      setTransactionCaptureBatches([])
      setCaptureError(null)
      return () => {
        cancelled = true
      }
    }

    setTransactionCaptureBatches([])
    setStartingCaptureBatchId(null)
    setCaptureError(null)
    getPortfolioTransactionCaptureBatches(portfolioId)
      .then((response) => {
        if (!cancelled) {
          setTransactionCaptureBatches(response.batches)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setCaptureError(
            error instanceof Error ? error.message : 'Failed to load screenshot evidence.',
          )
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  const captureAnalysisActiveKey = transactionCaptureBatches
    .filter(
      (batch) => batch.analysis_run_status === 'queued' || batch.analysis_run_status === 'running',
    )
    .map((batch) => batch.batch_id)
    .sort()
    .join('|')

  useEffect(() => {
    if (!portfolioId || !captureAnalysisActiveKey) {
      return undefined
    }
    let cancelled = false
    let timeoutId: number | undefined
    const activeBatchIds = new Set(captureAnalysisActiveKey.split('|'))

    const pollAnalysis = async () => {
      try {
        const response = await getPortfolioTransactionCaptureBatches(portfolioId)
        if (cancelled || currentPortfolioIdRef.current !== portfolioId) {
          return
        }
        setTransactionCaptureBatches(response.batches)
        const stillActive = response.batches.some(
          (batch) => batch.analysis_run_status === 'queued' || batch.analysis_run_status === 'running',
        )
        if (!stillActive) {
          const failedBatch = response.batches.find(
            (batch) => activeBatchIds.has(batch.batch_id) && batch.analysis_run_status === 'failed',
          )
          setCaptureError(
            failedBatch
              ? failedBatch.analysis_run_error ?? 'Screenshot analysis did not finish.'
              : null,
          )
          return
        }
      } catch (error) {
        if (!cancelled) {
          setCaptureError(
            error instanceof Error ? error.message : 'Failed to refresh screenshot analysis.',
          )
        }
      }
      if (!cancelled) {
        timeoutId = window.setTimeout(() => void pollAnalysis(), 2000)
      }
    }

    timeoutId = window.setTimeout(() => void pollAnalysis(), 1000)
    return () => {
      cancelled = true
      if (timeoutId !== undefined) {
        window.clearTimeout(timeoutId)
      }
    }
  }, [captureAnalysisActiveKey, portfolioId])

  useEffect(() => {
    const query = deferredInstrumentSearch.trim()
    if (!drawerOpen || form.asset_domain !== 'security' || !query) {
      setSecurityCatalogResults([])
      setSecurityCatalogLoading(false)
      setSecurityCatalogError(null)
      return undefined
    }

    let cancelled = false
    const timeoutId = window.setTimeout(() => {
      setSecurityCatalogLoading(true)
      setSecurityCatalogError(null)
      searchPlatformSecurityCatalog(query, 12)
        .then(({ results, catalogErrors }) => {
          if (!cancelled) {
            setSecurityCatalogResults(results)
            const unavailableCatalogs = Object.keys(catalogErrors)
            setSecurityCatalogError(
              unavailableCatalogs.length
                ? `${unavailableCatalogs.map((item) => item.toUpperCase()).join(' and ')} catalog unavailable.`
                : null,
            )
          }
        })
        .catch((error) => {
          if (!cancelled) {
            setSecurityCatalogResults([])
            setSecurityCatalogError(
              error instanceof Error ? error.message : 'Failed to search the local stock and ETF catalogs.',
            )
          }
        })
        .finally(() => {
          if (!cancelled) {
            setSecurityCatalogLoading(false)
          }
        })
    }, 250)

    return () => {
      cancelled = true
      window.clearTimeout(timeoutId)
    }
  }, [deferredInstrumentSearch, drawerOpen, form.asset_domain])

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
    filters.asset_domain,
    filters.asset_subtype,
    filters.transaction_type,
    filters.position_reference_id,
    filters.start_date,
    filters.end_date,
    selectedTransactionId,
  ])

  const formEntryKind = transactionEntryKind(
    form.asset_domain,
    derivativeContracts.find(
      (contract) => contract.derivative_contract_id === form.derivative_contract_id,
    )?.contract_type ?? derivativeDraft.contract_type,
  )
  const formEntryKindLabel =
    TRANSACTION_ENTRY_KINDS.find((entryKind) => entryKind.value === formEntryKind)?.label ??
    formEntryKind
  const formAccountLabel = formEntryKind === 'cash' ? 'Cash Account' : 'Holding Account'
  const formAccountOptions = eligibleAccounts(
    form.transaction_type,
    accounts,
    form.transfer_object_type,
    formEntryKind,
  )
  const selectedAccount =
    formAccountOptions.find((account) => account.account_id === form.account_id) ??
    formAccountOptions[0]
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
  const shouldRequireAsset = form.asset_domain !== 'cash'
  const shouldAllowRegistryInstrument = form.asset_domain === 'security'
  const shouldAllowDerivativeContract = form.asset_domain === 'derivative'
  const shouldRequireSettlement = requiresSettlement(
    form.transaction_type,
    selectedAccount?.account_type,
    form.lifecycle_event_type,
  )
  const shouldUseQuantity =
    usesQuantity(
      form.transaction_type,
      selectedAccount?.account_type,
      form.transfer_object_type,
    ) ||
    (form.transaction_type === 'lifecycle_event' &&
      (form.lifecycle_event_type === 'option_writer_expiry' ||
        form.lifecycle_event_type === 'option_writer_cash_settlement'))
  const shouldPreviewPosition =
    shouldUseQuantity &&
    form.transaction_type !== 'option_write' &&
    form.transaction_type !== 'option_buy_to_close' &&
    form.transaction_type !== 'lifecycle_event'

  if (!portfolioId) {
    return <Navigate replace to="/portfolios" />
  }
  const shouldUsePrice = usesPrice(form.transaction_type)
  const shouldShowFees = showsFeeField(form.transaction_type, form.lifecycle_event_type)
  const shouldShowTaxes = showsTaxField(form.transaction_type, form.lifecycle_event_type)
  const shouldShowFeeCategory =
    form.transaction_type === 'fee' ||
    (shouldShowFees && Number.isFinite(Number(form.fees)) && Number(form.fees) > 0)
  const selectedInstrument = instruments.find((instrument) => instrument.instrument_id === form.instrument_id) ?? null
  const selectedDerivativeContract =
    derivativeContracts.find(
      (contract) => contract.derivative_contract_id === form.derivative_contract_id,
    ) ?? null
  const requiredContractType = requiredDerivativeContractType(
    form.transaction_type,
    form.lifecycle_event_type,
  )
  const selectedDerivativeType =
    selectedDerivativeContract?.contract_type ?? derivativeDraft.contract_type
  const filteredDerivativeContracts = derivativeContracts.filter(
    (contract) =>
      contract.account_id === selectedAccount?.account_id &&
      contract.contract_type === (requiredContractType ?? selectedDerivativeType) &&
      supportsTransactionAssetType(
        form.transaction_type,
        contract.contract_type,
        form.lifecycle_event_type,
      ) &&
      accountAllowsAssetType(
        selectedAccount,
        contract.contract_type,
      ),
  )
  const isCreatingDerivativeContract =
    form.asset_domain === 'derivative' && !form.derivative_contract_id
  const draftDerivativeContract = (() => {
    if (!isCreatingDerivativeContract) {
      return null
    }
    try {
      return derivativeContractFromDraft(derivativeDraft)
    } catch {
      return null
    }
  })()
  const activeDerivativeContract =
    selectedDerivativeContract ?? draftDerivativeContract
  const hasAssetReference =
    form.asset_domain === 'security'
      ? Boolean(selectedInstrument)
      : form.asset_domain === 'derivative'
        ? Boolean(activeDerivativeContract)
        : false
  const resolvedAssetType =
    form.asset_domain === 'derivative'
      ? activeDerivativeContract?.contract_type ?? derivativeDraft.contract_type
      : form.asset_domain === 'security'
        ? selectedInstrument?.instrument_type ?? null
        : null
  const activeOptionType =
    activeDerivativeContract?.contract_type === 'option'
      ? activeDerivativeContract.terms.option_type
      : derivativeDraft.contract_type === 'option'
        ? derivativeDraft.option_type
        : null
  const formActionGroups = transactionActionGroups(
    form.asset_domain,
    resolvedAssetType,
    activeOptionType,
    { newDerivativeContract: isCreatingDerivativeContract },
  )
  const selectedActionValue = transactionActionValue(
    formActionGroups,
    form.transaction_type,
    form.lifecycle_event_type,
    form.transfer_object_type,
  )
  const isFundTrade =
    shouldUsePrice && isFundInstrumentType(selectedInstrument?.instrument_type)
  const transactionUnitPriceDecimals = isFundTrade ? 12 : 6
  const positionPreviewAccountRole: 'selected' | 'source' =
    form.transaction_type === 'transfer_in' && form.transfer_object_type === 'position' ? 'source' : 'selected'
  const positionPreviewAccountId =
    positionPreviewAccountRole === 'source'
      ? selectedCounterparty?.account_id ?? ''
      : selectedAccount?.account_id ?? form.account_id
  const resolvedTransactionCurrency = selectedAccount?.currency?.toUpperCase() ?? ''
  const activeDerivativeCurrency =
    selectedDerivativeContract?.currency?.toUpperCase() ?? resolvedTransactionCurrency
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
  const instrumentInputValue = selectedInstrument && !form.instrument_search
    ? instrumentSearchLabel(selectedInstrument)
    : form.instrument_search

  const filteredInstrumentOptions = useMemo(() => {
    const normalizedSearch = deferredInstrumentSearch.trim().toLowerCase()
    if (!normalizedSearch) {
      return []
    }
    const registryMatches = instruments
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
    const seenInstrumentIds = new Set(registryMatches.map((instrument) => instrument.instrument_id))
    const fmpMatches = securityCatalogResults.filter((instrument) => {
      const existingInstrumentId =
        'existing_instrument_id' in instrument ? instrument.existing_instrument_id : null
      if (existingInstrumentId && seenInstrumentIds.has(existingInstrumentId)) {
        return false
      }
      return isSelectableInstrument(
        form.transaction_type,
        instrument,
        selectedAccount,
        selectedAccount?.currency,
        form.transfer_object_type,
      )
    })
    return [...registryMatches, ...fmpMatches].slice(0, 12)
  }, [
    deferredInstrumentSearch,
    securityCatalogResults,
    form.transaction_type,
    form.transfer_object_type,
    instruments,
    selectedAccount,
    selectedAccount?.currency,
  ])
  const selectedInstrumentLabel = selectedInstrument ? instrumentSearchLabel(selectedInstrument) : ''
  const showInstrumentResults =
    shouldAllowRegistryInstrument &&
    form.instrument_search.trim() !== '' &&
    (!selectedInstrument || form.instrument_search.trim() !== selectedInstrumentLabel)
  const activeHistoricalQuote =
    historicalQuote?.instrumentId === selectedInstrument?.instrument_id &&
    historicalQuote?.requestedAsOfDate === form.trade_date
      ? historicalQuote
      : null
  const fallbackPriceContract =
    form.asset_domain === 'derivative'
      ? transactionPriceContractForDerivative(activeDerivativeContract)
      : transactionPriceContractForInstrument(selectedInstrument)
  const transactionPriceContract = resolveTransactionPriceContract(
    form.asset_domain === 'derivative'
      ? fallbackPriceContract
        ? [fallbackPriceContract]
        : []
      : [
          ...(activeHistoricalQuote ? [activeHistoricalQuote] : []),
          ...(selectedInstrument?.latest_market_data ?? []),
          ...(fallbackPriceContract ? [fallbackPriceContract] : []),
        ],
  )

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
      return resolved == null ? '' : formatCalculatedFormNumber(resolved, transactionUnitPriceDecimals)
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
    if (
      !drawerOpen ||
      !portfolioId ||
      !shouldUsePrice ||
      form.asset_domain !== 'security' ||
      !selectedInstrument ||
      !form.trade_date
    ) {
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
            isFundInstrumentType(selectedInstrument.instrument_type) ||
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
    form.asset_domain,
    form.trade_date,
    portfolioId,
    selectedInstrument?.instrument_id,
    shouldUsePrice,
  ])

  const selectedPositionReferenceId =
    form.asset_domain === 'derivative'
      ? form.derivative_contract_id
      : selectedInstrument?.instrument_id ?? ''

  useEffect(() => {
    if (
      !drawerOpen ||
      !portfolioId ||
      !shouldPreviewPosition ||
      !selectedPositionReferenceId ||
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
    const positionReferenceId = selectedPositionReferenceId
    const positionKind =
      form.asset_domain === 'derivative' ? 'derivative_contract' : 'instrument'
    const accountId = positionPreviewAccountId
    const tradeDate = form.trade_date
    setPositionPreviewLoading(true)
    setPositionPreviewError(null)

    getPortfolioTransactionPositionPreview(portfolioId, {
      account_id: accountId,
      position_kind: positionKind,
      position_reference_id: positionReferenceId,
      as_of_date: tradeDate,
      trade_time: form.trade_time || undefined,
      exclude_transaction_id: editingTransactionId || undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setPositionPreview(response)
          if (form.transaction_type === 'sell') {
            const availableQuantity = Math.max(0, response.quantity)
            const quantityKey = `sell:${response.account_id}:${response.position_reference_id}:${response.as_of_date}`
            setForm((current) => {
              const canApplyQuantity = !current.quantity.trim() || autoQuantityKeyRef.current !== null
              if (
                current.transaction_type !== 'sell' ||
                (current.asset_domain === 'derivative'
                  ? 'derivative_contract'
                  : 'instrument') !== response.position_kind ||
                (response.position_kind === 'instrument'
                  ? current.instrument_id
                  : current.derivative_contract_id) !== response.position_reference_id ||
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
                  next.price = formatCalculatedFormNumber(resolved, transactionUnitPriceDecimals)
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
    form.asset_domain,
    portfolioId,
    positionPreviewAccountId,
    selectedPositionReferenceId,
    shouldPreviewPosition,
    transactionUnitPriceDecimals,
    transactionPriceContract?.price_scale,
    transactionPriceContract?.price_unit,
  ])

  function commitSelectedInstrument(instrument: SharedInstrumentRecord) {
    setPricingAnchor(
      isFundInstrumentType(instrument.instrument_type) && usesPrice(form.transaction_type)
        ? 'gross_amount'
        : 'price',
    )
    setForm((current) => {
      const isChangingInstrument = Boolean(current.instrument_id && current.instrument_id !== instrument.instrument_id)
      if (isChangingInstrument) {
        autoQuoteKeyRef.current = null
        autoQuantityKeyRef.current = null
        autoGrossDerivedRef.current = false
      }
      return {
        ...current,
        asset_domain: 'security',
        instrument_id: instrument.instrument_id,
        derivative_contract_id: '',
        instrument_search: instrumentSearchLabel(instrument),
        price: isChangingInstrument ? '' : current.price,
        quantity: isChangingInstrument ? '' : current.quantity,
        gross_amount: isChangingInstrument ? '' : current.gross_amount,
      }
    })
    window.setTimeout(() => accountSelectRef.current?.focus(), 0)
  }

  async function selectInstrument(instrument: SecuritySearchOption) {
    if (!('catalog_symbol' in instrument)) {
      commitSelectedInstrument(instrument)
      return
    }
    if (!portfolioId || securityMaterializing) {
      return
    }
    setSecurityMaterializing(true)
    setSecurityCatalogError(null)
    try {
      const materialized = await materializePlatformSecurity(
        instrument.instrument_type,
        instrument.catalog_provider,
        instrument.catalog_symbol,
      )
      const refreshed = await getPortfolioInstruments(portfolioId)
      setInstruments(refreshed.instruments)
      const preparedInstrument = refreshed.instruments.find(
        (candidate) => candidate.instrument_id === materialized.instrument_id,
      ) ?? materialized
      if (
        selectedAccount &&
        preparedInstrument.currency.toUpperCase() !== selectedAccount.currency.toUpperCase()
      ) {
        setSecurityCatalogError(
          `FMP verified ${preparedInstrument.currency} as the quote currency. ` +
          `Select or create a ${preparedInstrument.currency} securities account to use it.`,
        )
        return
      }
      commitSelectedInstrument(preparedInstrument)
      setSecurityCatalogResults([])
    } catch (error) {
      setSecurityCatalogError(
        error instanceof Error ? error.message : 'Failed to prepare the FMP security data.',
      )
    } finally {
      setSecurityMaterializing(false)
    }
  }

  function selectDerivativeContract(contractId: string) {
    autoQuoteKeyRef.current = null
    autoQuantityKeyRef.current = null
    autoGrossDerivedRef.current = false
    setPricingAnchor('price')
    setForm((current) => ({
      ...current,
      asset_domain: 'derivative',
      transaction_type: contractId ? current.transaction_type : 'buy',
      lifecycle_event_type: contractId ? current.lifecycle_event_type : '',
      instrument_id: '',
      instrument_search: '',
      derivative_contract_id: contractId,
      quantity: '',
      price: '',
      gross_amount: '',
    }))
    const selected = derivativeContracts.find(
      (contract) => contract.derivative_contract_id === contractId,
    )
    setDerivativeDraft(
      selected
        ? derivativeContractDraftFromRecord(selected)
        : buildInitialDerivativeContractDraft(
            requiredContractType ?? derivativeDraft.contract_type,
          ),
    )
  }

  function updateEntryKind(nextEntryKind: TransactionEntryKind) {
    const nextAssetDomain = entryKindAssetDomain(nextEntryKind)
    const nextTransactionType = nextEntryKind === 'cash' ? 'deposit' : 'buy'
    const nextAccount = eligibleAccounts(
      nextTransactionType,
      accounts,
      'cash',
      nextEntryKind,
    )[0]
    autoQuoteKeyRef.current = null
    autoQuantityKeyRef.current = null
    autoGrossDerivedRef.current = false
    setPricingAnchor('price')
    setForm((current) => {
      const factDate =
        current.transaction_type === 'opening_balance'
          ? localTodayIso()
          : current.trade_date
      return {
        ...current,
        asset_domain: nextAssetDomain,
        transaction_type: nextTransactionType,
        lifecycle_event_type: '',
        account_id: nextAccount?.account_id ?? '',
        transfer_object_type: 'cash',
        instrument_id: '',
        instrument_search: '',
        derivative_contract_id: '',
        quantity: '',
        price: '',
        gross_amount: '',
        trade_date: factDate,
        settlement_date: factDate,
      }
    })
    if (nextEntryKind === 'option' || nextEntryKind === 'fcn') {
      setDerivativeDraft(buildInitialDerivativeContractDraft(nextEntryKind))
    }
  }

  function updateTransactionAction(actionValue: string) {
    const selectedAction = resolveTransactionAction(formActionGroups, actionValue)
    if (!selectedAction) {
      return
    }
    const nextTransactionType = selectedAction.transactionType
    const portfolioInceptionDate = transactionsWorkspace?.portfolio_inception_date
    if (nextTransactionType === 'opening_balance' && !portfolioInceptionDate) {
      setFormError('Portfolio inception date is unavailable. Reload after the database migration completes.')
      return
    }
    setPricingAnchor(
      isFundInstrumentType(selectedInstrument?.instrument_type) && usesPrice(nextTransactionType)
        ? 'gross_amount'
        : 'price',
    )
    setForm((current) => {
      const shouldClearAutoSellQuantity =
        autoQuantityKeyRef.current !== null &&
        current.transaction_type === 'sell' &&
        nextTransactionType !== 'sell'
      if (shouldClearAutoSellQuantity) {
        autoQuantityKeyRef.current = null
        autoGrossDerivedRef.current = false
      }
      const factDate =
        nextTransactionType === 'opening_balance'
          ? portfolioInceptionDate!
          : current.transaction_type === 'opening_balance' &&
              nextTransactionType !== 'opening_balance'
            ? localTodayIso()
            : current.trade_date
      return {
        ...current,
        transaction_type: nextTransactionType,
        lifecycle_event_type: selectedAction.lifecycleEventType ?? '',
        transfer_object_type:
          selectedAction.transferObjectType ?? current.transfer_object_type,
        quantity: shouldClearAutoSellQuantity ? '' : current.quantity,
        trade_date: factDate,
        settlement_date:
          nextTransactionType === 'opening_balance' ||
          current.transaction_type === 'opening_balance'
            ? factDate
            : current.settlement_date,
        gross_amount:
          selectedAction.lifecycleEventType === 'option_long_expiry' ||
          selectedAction.lifecycleEventType === 'option_writer_expiry'
            ? '0'
            : shouldClearAutoSellQuantity
              ? ''
              : current.gross_amount,
      }
    })
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
        positionPreview.position_kind ===
          (current.asset_domain === 'derivative'
            ? 'derivative_contract'
            : 'instrument') &&
        positionPreview.position_reference_id ===
          (current.asset_domain === 'security'
            ? current.instrument_id
            : current.derivative_contract_id) &&
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
          if (isFundTrade) {
            next.price = ''
          }
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
          next.price = formatCalculatedFormNumber(resolved, transactionUnitPriceDecimals)
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
        if ((pricingAnchor === 'gross_amount' || isFundTrade) && nextGrossAmount) {
          const resolved = calculateTransactionUnitPrice(
            transactionPriceContract,
            nextQuantity,
            nextGrossAmount,
          )
          if (resolved != null) {
            next.price = formatCalculatedFormNumber(resolved, transactionUnitPriceDecimals)
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
    const nextEligibleAccounts = eligibleAccounts(
      form.transaction_type,
      accounts,
      form.transfer_object_type,
      formEntryKind,
    )
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
  }, [
    accounts,
    form.account_id,
    formEntryKind,
    form.transaction_type,
    form.transfer_object_type,
  ])

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
    if (form.asset_domain !== 'security' || !selectedInstrument || !selectedAccount) {
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
    form.asset_domain,
    selectedAccount,
    selectedInstrument,
  ])

  useEffect(() => {
    if (form.asset_domain !== 'derivative' || !selectedDerivativeContract || !selectedAccount) {
      return
    }
    if (
      selectedDerivativeContract.account_id === selectedAccount.account_id &&
      (!requiredContractType || selectedDerivativeContract.contract_type === requiredContractType) &&
      supportsTransactionAssetType(
        form.transaction_type,
        selectedDerivativeContract.contract_type,
        form.lifecycle_event_type,
      )
    ) {
      return
    }
    setForm((current) => ({ ...current, derivative_contract_id: '' }))
    setDerivativeDraft(
      buildInitialDerivativeContractDraft(
        requiredContractType ?? selectedDerivativeContract.contract_type,
      ),
    )
  }, [
    form.asset_domain,
    form.transaction_type,
    requiredContractType,
    selectedAccount,
    selectedDerivativeContract,
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
    if (
      !supportsEntitlementDate(form.transaction_type, hasAssetReference) &&
      form.entitlement_date
    ) {
      setForm((current) => ({
        ...current,
        entitlement_date: '',
      }))
    }
  }, [form.entitlement_date, form.transaction_type, hasAssetReference])

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

  function handleTransactionFileDownload(
    kind: 'export' | 'template',
    format: PortfolioTransactionFileFormat,
  ) {
    const link = document.createElement('a')
    link.href = kind === 'export'
      ? portfolioTransactionDownloadUrl(portfolioId, format)
      : portfolioTransactionTemplateUrl(portfolioId, format)
    link.download = ''
    document.body.appendChild(link)
    link.click()
    link.remove()
  }

  function openCaptureAssistant(view: TransactionCaptureAssistantView, batchId?: string) {
    setCaptureAssistantView(view)
    setSelectedCaptureBatchId(batchId ?? null)
    setCaptureError(null)
    setCaptureReviewDraft(null)
    setCaptureAssistantOpen(true)
  }

  function addTransactionCaptureFiles(fileList: FileList | File[]) {
    const files = Array.from(fileList)
    if (!files.length || uploadingCapture) {
      return
    }

    const unsupported = files.filter((file) => !TRANSACTION_CAPTURE_MEDIA_TYPES.has(file.type))
    if (unsupported.length) {
      setCaptureError('Use PNG, JPEG, or WebP screenshots only.')
      return
    }

    const oversized = files.filter((file) => file.size > TRANSACTION_CAPTURE_MAX_BYTES)
    if (oversized.length) {
      setCaptureError('Each screenshot must be 12 MB or smaller.')
      return
    }

    if (captureDraftFiles.length + files.length > TRANSACTION_CAPTURE_MAX_FILES) {
      setCaptureError('Select at most 10 screenshots for one assistant analysis batch.')
      return
    }

    setCaptureDraftFiles((current) => [
      ...current,
      ...files.map((file) => ({
        id: transactionCaptureDraftId(file),
        file,
        previewUrl: transactionCapturePreviewUrl(file),
      })),
    ])
    setCaptureError(null)
  }

  function removeTransactionCaptureFile(id: string) {
    setCaptureDraftFiles((current) => current.filter((draft) => {
      if (draft.id !== id) {
        return true
      }
      revokeTransactionCapturePreview(draft.previewUrl)
      return false
    }))
    setCaptureError(null)
  }

  function clearTransactionCaptureDraft() {
    setCaptureDraftFiles((current) => {
      current.forEach((draft) => revokeTransactionCapturePreview(draft.previewUrl))
      return []
    })
    if (transactionCaptureInputRef.current) {
      transactionCaptureInputRef.current.value = ''
    }
  }

  async function handleTransactionCaptures() {
    const files = captureDraftFiles.map((draft) => draft.file)
    if (!files.length || uploadingCapture || !portfolioId) {
      return
    }
    const targetPortfolioId = portfolioId
    setUploadingCapture(true)
    setCaptureError(null)
    setNotice(null)
    try {
      const captures = await Promise.all(
        files.map((file) => uploadPortfolioTransactionCapture(targetPortfolioId, file)),
      )
      const batch = await createPortfolioTransactionCaptureBatch(
        targetPortfolioId,
        captures.map((capture) => capture.capture_id),
        capturePurpose,
      )
      if (currentPortfolioIdRef.current !== targetPortfolioId) {
        return
      }
      setSelectedCaptureBatchId(batch.batch_id)
      setCaptureAssistantView('history')
      clearTransactionCaptureDraft()
      setTransactionCaptureBatches((current) => [
        batch,
        ...current.filter((item) => item.batch_id !== batch.batch_id),
      ].slice(0, 10))
      try {
        const queued = await startPortfolioTransactionCaptureAnalysis(
          targetPortfolioId,
          batch.batch_id,
        )
        if (currentPortfolioIdRef.current !== targetPortfolioId) {
          return
        }
        setTransactionCaptureBatches((current) => current.map((item) => (
          item.batch_id === queued.batch_id ? queued : item
        )))
        setNotice('AI is analyzing the screenshots. You can close this panel.')
      } catch (analysisError) {
        if (currentPortfolioIdRef.current === targetPortfolioId) {
          setCaptureError(
            analysisError instanceof Error
              ? `Screenshots saved, but analysis did not start: ${analysisError.message}`
              : 'Screenshots saved, but analysis did not start. Try again.',
          )
        }
      }
    } catch (error) {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setCaptureError(
          error instanceof Error ? error.message : 'Failed to prepare screenshot analysis.',
        )
      }
    } finally {
      setUploadingCapture(false)
      if (transactionCaptureInputRef.current) {
        transactionCaptureInputRef.current.value = ''
      }
    }
  }

  async function handleScreenshotAnalysis(batch: PortfolioTransactionCaptureBatchRecord) {
    if (
      !portfolioId
      || startingCaptureBatchId
      || batch.analysis_run_status === 'queued'
      || batch.analysis_run_status === 'running'
    ) {
      return
    }
    const targetPortfolioId = portfolioId
    setStartingCaptureBatchId(batch.batch_id)
    setCaptureError(null)
    setNotice(null)
    try {
      const queued = await startPortfolioTransactionCaptureAnalysis(
        targetPortfolioId,
        batch.batch_id,
      )
      if (currentPortfolioIdRef.current !== targetPortfolioId) {
        return
      }
      setTransactionCaptureBatches((current) => current.map((item) => (
        item.batch_id === queued.batch_id ? queued : item
      )))
      setNotice('AI is analyzing the screenshots. You can close this panel.')
    } catch (error) {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setCaptureError(
          error instanceof Error ? error.message : 'Failed to start screenshot analysis.',
        )
      }
    } finally {
      setStartingCaptureBatchId(null)
    }
  }

  function beginTransactionCaptureReview(batch: PortfolioTransactionCaptureBatchRecord) {
    if (batch.ledger_status === 'recorded' || batch.ledger_status === 'partially_recorded') {
      setCaptureError('This screenshot batch already has ledger transactions. Review those records before trying again.')
      return
    }
    if (metaLoading) {
      setCaptureError('Wait for portfolio accounts and instruments to finish loading.')
      return
    }
    const latestAnalysis = batch.latest_analysis
    if (!hasTransactionCaptureImport(latestAnalysis)) {
      setCaptureError('This analysis does not contain a transaction proposal to review.')
      return
    }
    setCaptureReviewDraft({
      batchId: batch.batch_id,
      sourceRevision: latestAnalysis.revision,
      transactionImport: cloneTransactionImport(latestAnalysis.transaction_import),
      duplicateAssessments: Object.fromEntries(
        latestAnalysis.analysis.candidates
          .filter((candidate) => (
            candidate.possible_duplicate_of?.length
            || candidate.possible_existing_transaction_ids?.length
          ))
          .map((candidate) => [
            candidate.candidate_id,
            candidate.duplicate_assessment === 'same_record'
              || candidate.duplicate_assessment === 'uncertain'
              ? candidate.duplicate_assessment
              : 'distinct_records',
          ]),
      ),
    })
    setCaptureError(null)
  }

  function updateTransactionCaptureReviewRecord(
    recordIndex: number,
    update: (record: PortfolioTransactionImportCommand) => PortfolioTransactionImportCommand,
  ) {
    setCaptureReviewDraft((current) => {
      if (!current) {
        return current
      }
      return {
        ...current,
        transactionImport: {
          ...current.transactionImport,
          records: current.transactionImport.records.map((record, index) => (
            index === recordIndex ? update(record) : record
          )),
        },
      }
    })
    setCaptureError(null)
  }

  function updateTransactionCaptureDuplicateAssessment(
    candidateId: string,
    assessment: TransactionCaptureDuplicateAssessment,
  ) {
    setCaptureReviewDraft((current) => current
      ? {
          ...current,
          duplicateAssessments: {
            ...current.duplicateAssessments,
            [candidateId]: assessment,
          },
        }
      : current)
    setCaptureError(null)
  }

  async function recordTransactionCaptureProposal(
    batch: PortfolioTransactionCaptureBatchRecord,
    analysisOverride?: PortfolioTransactionCaptureAnalysisRevision,
  ) {
    const analysis = analysisOverride ?? batch.latest_analysis
    if (
      batch.ledger_status !== 'unrecorded'
      || !hasTransactionCaptureImport(analysis)
      || !transactionCaptureProposalReady(analysis)
      || !analysis.preview_digest
      || committingCapture
    ) {
      setCaptureError('Check the transaction draft before recording it.')
      return false
    }

    const targetPortfolioId = portfolioId
    setCommittingCapture(true)
    setCaptureError(null)
    setNotice(null)
    try {
      const committed = await commitPortfolioTransactionImport(
        targetPortfolioId,
        analysis.transaction_import,
        analysis.preview_digest,
        transactionCaptureIdempotencyKey(batch.batch_id, analysis.revision),
      )
      if (currentPortfolioIdRef.current !== targetPortfolioId) {
        return false
      }
      const recordedReferences = new Set(
        analysis.transaction_import.records.map((record) => record.external_reference),
      )
      setTransactionCaptureBatches((current) => current.map((item) => (
        item.batch_id === batch.batch_id
          ? {
              ...item,
              ledger_status: 'recorded',
              recorded_transaction_ids: committed.transactions
                .filter((transaction) => (
                  transaction.source_system === analysis.transaction_import.source_system
                  && recordedReferences.has(transaction.external_reference ?? '')
                ))
                .map((transaction) => transaction.transaction_id),
            }
          : item
      )))
      setCaptureAssistantOpen(false)
      setCaptureReviewDraft(null)
      setNotice(
        `Recorded ${committed.created_count} transaction${committed.created_count === 1 ? '' : 's'} from screenshots.`,
      )
      await refreshTransactions(
        filters,
        committed.transactions[0]?.transaction_id ?? null,
      )
      return true
    } catch (error) {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setCaptureError(
          error instanceof Error ? error.message : 'Failed to record the screenshot transactions.',
        )
      }
      return false
    } finally {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setCommittingCapture(false)
      }
    }
  }

  async function confirmAndRecordTransactionCaptureReview() {
    const draft = captureReviewDraft
    const batch = transactionCaptureBatches.find((item) => item.batch_id === draft?.batchId)
    const latestAnalysis = batch?.latest_analysis
    if (
      !draft
      || !batch
      || !latestAnalysis
      || draft.sourceRevision !== latestAnalysis.revision
      || savingCaptureReview
      || committingCapture
    ) {
      setCaptureError('The AI draft changed. Reopen the latest draft before reviewing it.')
      return
    }

    const reviewed = buildHumanReviewedCaptureProposal(
      batch.batch_id,
      latestAnalysis,
      draft,
    )

    const targetPortfolioId = portfolioId
    setSavingCaptureReview(true)
    setCaptureError(null)
    setNotice(null)
    try {
      const response = await createPortfolioTransactionCaptureAnalysisRevision(
        targetPortfolioId,
        batch.batch_id,
        {
          source: 'human',
          finish_reason: 'user_confirmed',
          schema_version: 'portfolio.transaction-capture-analysis.v2',
          analysis: {
            ...reviewed.analysis,
          },
          transaction_import: reviewed.transactionImport,
        },
      )
      if (currentPortfolioIdRef.current !== targetPortfolioId) {
        return
      }
      setTransactionCaptureBatches((current) => current.map((item) => (
        item.batch_id === response.batch.batch_id ? response.batch : item
      )))
      const preview = response.preview
      if (preview?.error_count) {
        const issues = [
          ...preview.batch_errors,
          ...preview.rows.flatMap((row) => row.errors.map((error) => `Record ${row.record_index}: ${error}`)),
        ]
        if (reviewed.transactionImport) {
          setCaptureReviewDraft({
            ...draft,
            sourceRevision: response.analysis_revision.revision,
            transactionImport: cloneTransactionImport(reviewed.transactionImport),
          })
        }
        setCaptureError(
          `Check ${preview.error_count} issue${preview.error_count === 1 ? '' : 's'}${issues.length ? `: ${issues.slice(0, 4).join(' ')}` : '.'}`,
        )
      } else if (reviewed.transactionImport) {
        setCaptureReviewDraft(null)
        await recordTransactionCaptureProposal(response.batch, response.analysis_revision)
      } else {
        setCaptureReviewDraft(null)
        setNotice('The possible duplicates were excluded. No transaction was recorded.')
      }
    } catch (error) {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setCaptureError(
          error instanceof Error ? error.message : 'Failed to check the transaction draft.',
        )
      }
    } finally {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setSavingCaptureReview(false)
      }
    }
  }

  async function handleTransactionFile(file: File | null) {
    if (!file || importingFile) {
      return
    }
    setImportingFile(true)
    setLedgerError(null)
    setFileImportError(null)
    setNotice(null)
    try {
      const preview = await previewPortfolioTransactionFile(portfolioId, file)
      setPendingFileImport({ fileName: file.name, file, preview })
    } catch (error) {
      setLedgerError(
        error instanceof Error ? error.message : 'Failed to read the transaction file.',
      )
    } finally {
      setImportingFile(false)
      if (transactionFileInputRef.current) {
        transactionFileInputRef.current.value = ''
      }
    }
  }

  async function confirmTransactionFileImport() {
    const pendingImport = pendingFileImport
    const targetPortfolioId = portfolioId
    if (
      !pendingImport ||
      pendingImport.preview.error_count > 0 ||
      !targetPortfolioId ||
      importingFile
    ) {
      return
    }
    setImportingFile(true)
    setFileImportError(null)
    setLedgerError(null)
    try {
      const imported = await importPortfolioTransactionFile(
        targetPortfolioId,
        pendingImport.file,
        pendingImport.preview.preview_digest,
        transactionIdempotencyKey('file-import'),
      )
      if (currentPortfolioIdRef.current !== targetPortfolioId) {
        return
      }
      setPendingFileImport(null)
      setNotice(`Imported ${imported.created_count} transaction facts from ${pendingImport.fileName}.`)
      await refreshTransactions(
        filters,
        imported.transactions[0]?.transaction_id ?? null,
      )
    } catch (error) {
      if (currentPortfolioIdRef.current === targetPortfolioId) {
        setFileImportError(
          error instanceof Error ? error.message : 'Failed to import the transaction file.',
        )
      }
    } finally {
      setImportingFile(false)
    }
  }

  const pageErrors = [metadataError, ledgerError].filter(
    (message, index, messages): message is string => Boolean(message) && messages.indexOf(message) === index,
  )
  const pageError = pageErrors.length ? pageErrors.join(' ') : null
  const latestCaptureBatch = transactionCaptureBatches[0] ?? null
  const selectedCaptureBatch =
    transactionCaptureBatches.find((batch) => batch.batch_id === selectedCaptureBatchId)
    ?? latestCaptureBatch

  function openCreateDrawer() {
    setEditingTransactionId(null)
    setActiveEventTask(null)
    setActiveEventTaskReviewer('')
    setForm(buildInitialFormState(accounts))
    setDerivativeDraft(buildInitialDerivativeContractDraft())
    setPricingAnchor('price')
    submittingTransactionRef.current = false
    setSubmittingTransaction(false)
    autoQuoteKeyRef.current = null
    autoQuantityKeyRef.current = null
    autoGrossDerivedRef.current = false
    setFormError(null)
    setNotice(null)
    setDrawerOpen(true)
  }

  function openEditDrawer(transaction: PortfolioTransactionRecord) {
    setEditingTransactionId(transaction.transaction_id)
    setActiveEventTask(null)
    setActiveEventTaskReviewer('')
    setForm(buildFormStateFromTransaction(transaction))
    setDerivativeDraft(
      derivativeContractDraftFromRecord(transaction.derivative_contract),
    )
    setPricingAnchor(
      isFundInstrumentType(transaction.instrument_ref?.instrument_type) &&
      usesPrice(transaction.transaction_type)
        ? 'gross_amount'
        : 'price',
    )
    submittingTransactionRef.current = false
    setSubmittingTransaction(false)
    autoQuoteKeyRef.current = null
    autoQuantityKeyRef.current = null
    autoGrossDerivedRef.current = false
    setFormError(null)
    setNotice(null)
    setDrawerOpen(true)
  }

  function openEventTaskDrawer(
    task: PortfolioInstrumentEventTaskRecord,
    transactionType: 'dividend' | 'dividend_reinvestment',
    reviewedBy: string,
  ) {
    const initial = buildInitialFormState(accounts)
    const account = accounts.find((item) => item.account_id === task.account_id)
    const settlementAccount = accounts.find(
      (item) => item.account_id === account?.default_settlement_cash_account_id,
    )
    const transactionDate = task.payable_date ?? task.effective_date
    const expectedGrossAmount = task.expected_gross_amount ?? 0
    const reinvestedQuantity =
      transactionType === 'dividend_reinvestment' &&
      task.reinvestment_nav != null &&
      task.reinvestment_nav > 0
        ? expectedGrossAmount / task.reinvestment_nav
        : null
    setEditingTransactionId(null)
    setActiveEventTask(task)
    setActiveEventTaskReviewer(reviewedBy)
    setForm({
      ...initial,
      transaction_type: transactionType,
      trade_date: transactionDate,
      settlement_date: transactionDate,
      entitlement_date: task.record_date ?? task.effective_date,
      account_id: task.account_id,
      settlement_cash_account_id:
        transactionType === 'dividend'
          ? settlementAccount?.account_id ?? account?.default_settlement_cash_account_id ?? ''
          : '',
      instrument_id: task.instrument_id,
      quantity:
        reinvestedQuantity != null
          ? formatCalculatedFormNumber(reinvestedQuantity, 12)
          : '',
      gross_amount: formatCalculatedFormNumber(expectedGrossAmount, 8),
      note: `Registry distribution ${task.event_action_id}`,
      instrument_search: '',
    })
    setPricingAnchor('gross_amount')
    submittingTransactionRef.current = false
    setSubmittingTransaction(false)
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
      if (submittingTransaction) {
        return
      }
      setDrawerOpen(false)
      setEditingTransactionId(null)
      setActiveEventTask(null)
      setActiveEventTaskReviewer('')
    }
    window.addEventListener('keydown', handleKeyDown)
    window.setTimeout(() => {
      const firstControl =
        entryKindControlRef.current ?? accountSelectRef.current ?? securitySearchRef.current
      firstControl?.focus()
    }, 0)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [drawerOpen, submittingTransaction])

  async function handleCreateTransaction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submittingTransactionRef.current) {
      return
    }
    setFormError(null)
    setNotice(null)
    const isEditingTransaction = editingTransactionId !== null
    const editingTransaction = isEditingTransaction
      ? transactionsWorkspace?.transactions.find(
          (transaction) => transaction.transaction_id === editingTransactionId,
        ) ?? null
      : null

    if (isEditingTransaction && !editingTransaction) {
      setFormError('This transaction changed or is no longer visible. Reload the ledger and retry.')
      return
    }

    if (
      activeEventTask &&
      form.transaction_type !== 'dividend' &&
      form.transaction_type !== 'dividend_reinvestment'
    ) {
      setFormError('A distribution review must create a dividend or dividend-reinvestment fact.')
      return
    }

    const resolvedAccount =
      accounts.find((account) => account.account_id === form.account_id) ?? selectedAccount ?? null
    if (!resolvedAccount) {
      setFormError('Select an account before saving.')
      return
    }

    let inlineDerivativeContract: PortfolioDerivativeContractCreate | null = null
    if (form.asset_domain === 'derivative' && !selectedDerivativeContract) {
      try {
        inlineDerivativeContract = derivativeContractFromDraft(derivativeDraft)
      } catch (error) {
        setFormError(
          error instanceof Error ? error.message : 'Complete the derivative contract terms.',
        )
        return
      }
    }
    const resolvedDerivativeContract =
      selectedDerivativeContract ?? inlineDerivativeContract
    const hasSelectedAsset =
      form.asset_domain === 'security'
        ? selectedInstrument !== null
        : form.asset_domain === 'derivative'
          ? resolvedDerivativeContract !== null
          : true
    if (shouldRequireAsset && !hasSelectedAsset) {
      setFormError('Select a Registry security or Portfolio derivative contract.')
      return
    }
    if (
      resolvedAssetType &&
      !supportsTransactionAssetType(
        form.transaction_type,
        resolvedAssetType,
        form.lifecycle_event_type,
      )
    ) {
      setFormError('The selected asset does not support this transaction action.')
      return
    }
    if (
      requiredContractType &&
      resolvedDerivativeContract?.contract_type !== requiredContractType
    ) {
      setFormError(`This event requires a ${requiredContractType.toUpperCase()} contract.`)
      return
    }

    if (form.transaction_type === 'lifecycle_event' && !form.lifecycle_event_type) {
      setFormError('Select the derivative close outcome represented by this transaction.')
      return
    }

    if (form.external_reference.trim() && !form.source_system.trim()) {
      setFormError('External reference requires a source system.')
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
        source_system: form.source_system.trim() || null,
        external_reference: form.external_reference.trim() || null,
        note: form.note.trim() || null,
      }

      submittingTransactionRef.current = true
      setSubmittingTransaction(true)
      try {
        const created = isEditingTransaction
          ? await updatePortfolioTransaction(portfolioId, editingTransactionId, {
              ...payload,
              expected_row_version: editingTransaction!.row_version,
            })
          : await createPortfolioTransaction(
              portfolioId,
              payload,
              transactionIdempotencyKey('create'),
            )
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
      } finally {
        submittingTransactionRef.current = false
        setSubmittingTransaction(false)
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
        source_system: form.source_system.trim() || null,
        external_reference: form.external_reference.trim() || null,
        note: form.note.trim() || null,
      } as const

      submittingTransactionRef.current = true
      setSubmittingTransaction(true)
      try {
        const created = await createPortfolioInternalTransfer(
          portfolioId,
          payload,
          transactionIdempotencyKey('transfer'),
        )
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
      } finally {
        submittingTransactionRef.current = false
        setSubmittingTransaction(false)
      }
      return
    }

    const zeroCashLifecycle =
      (form.transaction_type === 'lifecycle_event' &&
        form.lifecycle_event_type === 'option_writer_expiry') ||
      (form.transaction_type === 'maturity_redemption' &&
        form.lifecycle_event_type === 'option_long_expiry')
    const zeroCostAssetOpening =
      form.transaction_type === 'opening_balance' &&
      form.asset_domain !== 'cash' &&
      hasSelectedAsset
    const zeroGrossAllowed = zeroCashLifecycle || zeroCostAssetOpening
    const grossAmount = zeroCashLifecycle ? 0 : Number(computedGrossAmount)
    if (
      !Number.isFinite(grossAmount) ||
      grossAmount < 0 ||
      (!zeroGrossAllowed && grossAmount === 0)
    ) {
      setFormError(
        zeroCostAssetOpening
          ? 'Enter a non-negative gross amount.'
          : 'Enter a positive gross amount.',
      )
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
      lifecycle_event_type: form.lifecycle_event_type || null,
      trade_date: form.trade_date,
      trade_time: form.trade_time || null,
      settlement_date: form.settlement_date || form.trade_date,
      position_effective_date: supportsPositionEffectiveDate(form.transaction_type)
        ? form.position_effective_date || form.trade_date
        : null,
      entitlement_date: supportsEntitlementDate(
        form.transaction_type,
        form.asset_domain !== 'cash' && hasSelectedAsset,
      )
        ? form.entitlement_date || form.trade_date
        : null,
      acquisition_date: supportsAcquisitionDate(form.transaction_type, resolvedAccount.account_type)
        ? form.acquisition_date || null
        : null,
      account_id: resolvedAccount.account_id,
      settlement_cash_account_id: shouldRequireSettlement ? form.settlement_cash_account_id || null : null,
      instrument_id:
        form.asset_domain === 'security' && shouldAllowRegistryInstrument
          ? selectedInstrument?.instrument_id ?? null
          : null,
      derivative_contract_id:
        form.asset_domain === 'derivative'
          ? resolvedDerivativeContract?.derivative_contract_id ?? null
          : null,
      derivative_contract:
        form.asset_domain === 'derivative' && !selectedDerivativeContract
          ? inlineDerivativeContract
          : null,
      quantity: shouldUseQuantity && form.quantity ? Number(form.quantity) : null,
      price: shouldUsePrice && computedUnitPrice ? Number(computedUnitPrice) : null,
      gross_amount: grossAmount,
      counter_amount: null,
      fx_rate: null,
      fees: shouldShowFees && form.fees ? Number(form.fees) : 0,
      fee_category: shouldShowFeeCategory ? form.fee_category : 'unknown',
      taxes: shouldShowTaxes && form.taxes ? Number(form.taxes) : 0,
      currency: resolvedTransactionCurrency,
      source_system: form.source_system.trim() || null,
      external_reference: form.external_reference.trim() || null,
      note: form.note.trim() || null,
    }

    if (activeEventTask && !activeEventTaskReviewer.trim()) {
      setFormError('Operator identity is required to link this distribution transaction.')
      return
    }

    submittingTransactionRef.current = true
    setSubmittingTransaction(true)
    try {
      const created = isEditingTransaction
        ? await updatePortfolioTransaction(portfolioId, editingTransactionId, {
            ...payload,
            expected_row_version: editingTransaction!.row_version,
          } satisfies PortfolioTransactionUpdatePayload)
        : await createPortfolioTransaction(
            portfolioId,
            payload,
            transactionIdempotencyKey('create'),
          )
      let eventReviewError: string | null = null
      if (activeEventTask && !isEditingTransaction) {
        try {
          await reviewPortfolioInstrumentEventTask(
            portfolioId,
            activeEventTask.instrument_event_task_id,
            {
              decision: 'processed',
              transaction_ids: [created.transaction_id],
              note: `Recorded through Portfolio transaction ${created.transaction_id}.`,
              reviewed_by: activeEventTaskReviewer.trim(),
              expected_row_version: activeEventTask.row_version,
            },
          )
        } catch (reviewError) {
          eventReviewError =
            reviewError instanceof Error
              ? reviewError.message
              : 'The Registry event review could not be linked.'
        }
      }
      setDrawerOpen(false)
      setEditingTransactionId(null)
      setActiveEventTask(null)
      setActiveEventTaskReviewer('')
      setEventTasksRefreshKey((current) => current + 1)
      setNotice(
        eventReviewError
          ? `Added transaction ${created.transaction_id}; the distribution review remains pending: ${eventReviewError}`
          : isEditingTransaction
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
    } finally {
      submittingTransactionRef.current = false
      setSubmittingTransaction(false)
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
      setEventTasksRefreshKey((current) => current + 1)
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
  const relatedOptionObligations = transactionsWorkspace?.related_option_obligations ?? []
  const selectedTransactionChangeLog = transactionsWorkspace?.change_log ?? []
  const visibleTransactions = transactionsWorkspace?.transactions ?? []
  const selectedFilterEntryKind = filters.asset_domain
    ? transactionEntryKind(filters.asset_domain, filters.asset_subtype)
    : null
  const transactionPositionReferenceIds = useMemo(() => {
    const references = transactionsWorkspace?.position_reference_ids?.length
      ? [...transactionsWorkspace.position_reference_ids]
      : visibleTransactions.flatMap((transaction) => [
          transaction.instrument_id ?? transaction.derivative_contract_id ?? '',
        ])
    if (filters.position_reference_id) {
      references.push(filters.position_reference_id)
    }
    return new Set(references.filter(Boolean))
  }, [filters.position_reference_id, transactionsWorkspace?.position_reference_ids, visibleTransactions])
  const transactionFilterInstruments = instruments.filter(
    (instrument) => transactionPositionReferenceIds.has(instrument.instrument_id),
  )
  const transactionFilterContracts = derivativeContracts.filter(
    (contract) =>
      transactionPositionReferenceIds.has(contract.derivative_contract_id) &&
      (!selectedFilterEntryKind ||
        selectedFilterEntryKind === contract.contract_type),
  )
  const fcnTransactionCount =
    summary?.fcn_transactions ??
    visibleTransactions.filter(
      (transaction) =>
        transaction.asset_domain === 'derivative' && transaction.asset_subtype === 'fcn',
    ).length
  const optionTransactionCount =
    summary?.option_transactions ??
    visibleTransactions.filter(
      (transaction) =>
        transaction.asset_domain === 'derivative' && transaction.asset_subtype === 'option',
    ).length
  const transactionFilterTypeGroups = useMemo(() => {
    if (!selectedFilterEntryKind) {
      return TRANSACTION_FILTER_TYPE_GROUPS
    }
    const allowedTransactionTypes = new Set(
      transactionActionGroups(
        entryKindAssetDomain(selectedFilterEntryKind),
        selectedFilterEntryKind === 'fcn' || selectedFilterEntryKind === 'option'
          ? selectedFilterEntryKind
          : null,
      ).flatMap((group) => group.actions.map((action) => action.transactionType)),
    )
    return TRANSACTION_FILTER_TYPE_GROUPS.map((group) => ({
      ...group,
      types: group.types.filter((transactionType) =>
        allowedTransactionTypes.has(transactionType),
      ),
    })).filter((group) => group.types.length > 0)
  }, [selectedFilterEntryKind])
  const latestTradeDate = visibleTransactions.reduce(
    (latest, transaction) =>
      !latest || transaction.trade_date > latest ? transaction.trade_date : latest,
    '',
  )
  const activeFilterCount = countActiveTransactionFilters(filters)
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
    form.lifecycle_event_type,
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
  useEffect(() => {
    setInspectorTab('fact')
  }, [selectedTransactionId])

  const amountField = (
    <label className="transaction-ticket-field">
      <span>
        {isFundTrade
          ? 'Confirmed Amount'
          : grossAmountLabel(form.transaction_type, form.lifecycle_event_type)}
      </span>
      <input
        type="number"
        min="0"
        step="0.01"
        aria-label={
          isFundTrade
            ? 'Confirmed Amount'
            : grossAmountLabel(form.transaction_type, form.lifecycle_event_type)
        }
        value={form.gross_amount}
        placeholder={
          isTransferTransaction(form.transaction_type) && form.transfer_object_type === 'position'
            ? computedGrossAmount || 'Optional carrying cost'
            : computedGrossAmount || '0.00'
        }
        onChange={(event) => updatePricingField('gross_amount', event.target.value)}
      />
      {isFundTrade ? (
        <span className="transaction-ticket-hint">
          Enter the confirmed cash amount; unit price is derived from amount and shares.
        </span>
      ) : null}
    </label>
  )

  return (
    <PortfolioWorkspaceLayout
      activeSection="Transactions"
      busy={metaLoading || loadingTransactions}
    >
      <section className="portfolio-detail-surface">
        <FundDistributionTasksPanel
          portfolioId={portfolioId}
          accountNames={accountNameById}
          refreshKey={eventTasksRefreshKey}
          onRecord={openEventTaskDrawer}
        />
        <div className="portfolio-detail-toolbar transaction-activity-toolbar">
          <div>
            <div className="panel-title">Activity</div>
            <div className="portfolio-detail-meta">
              {summary
                ? `${summary.total_transactions} ${activeFilterCount ? 'matching ' : ''}facts`
                : `${visibleTransactions.length} facts`}
              {summary
                ? ` · ${summary.security_transactions} security · ${fcnTransactionCount} FCN · ${optionTransactionCount} option · ${summary.cash_transactions} cash`
                : ''}
              {latestTradeDate ? ` · latest trade ${latestTradeDate}` : ''}
              {summary?.external_cash_flows ? ` · ${summary.external_cash_flows} external flows` : ''}
            </div>
          </div>
          <div className="transaction-toolbar-actions">
            <div className="transaction-toolbar-file-actions" aria-label="Transaction files">
              <DownloadFormatMenu
                buttonLabel="Export"
                wrapperClassName="portfolio-download-menu"
                buttonClassName="toolbar-link transaction-toolbar-button"
                menuClassName="portfolio-download-menu-list"
                itemClassName="portfolio-download-menu-item"
                onSelect={(format) => handleTransactionFileDownload('export', format)}
              />
              <button
                type="button"
                className="toolbar-link transaction-toolbar-button"
                disabled={importingFile || metaLoading}
                title="Import and check a transaction CSV or Excel file"
                onClick={() => transactionFileInputRef.current?.click()}
              >
                {importingFile ? 'Checking…' : 'Import'}
              </button>
              <DownloadFormatMenu
                buttonLabel="Template"
                wrapperClassName="portfolio-download-menu"
                buttonClassName="toolbar-link transaction-toolbar-button"
                menuClassName="portfolio-download-menu-list"
                itemClassName="portfolio-download-menu-item"
                onSelect={(format) => handleTransactionFileDownload('template', format)}
              />
            </div>
            <input
              ref={transactionFileInputRef}
              type="file"
              accept=".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              hidden
              onChange={(event) =>
                void handleTransactionFile(event.target.files?.[0] ?? null)
              }
            />
            <input
              ref={transactionCaptureInputRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
              multiple
              hidden
              onChange={(event) => {
                addTransactionCaptureFiles(event.target.files ?? [])
                event.target.value = ''
              }}
            />
            <div className="transaction-toolbar-entry-actions">
              <button
                type="button"
                className="toolbar-link transaction-toolbar-button"
                disabled={metaLoading}
                title="Create an editable transaction draft from screenshots"
                onClick={() => openCaptureAssistant('new')}
              >
                From Screenshot
              </button>
              <button
                type="button"
                className="toolbar-link button-primary"
                disabled={metaLoading || accounts.length === 0}
                onClick={openCreateDrawer}
              >
                Record Transaction
              </button>
            </div>
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
              <span>Asset Type</span>
              <select
                className="toolbar-select transaction-filter-input"
                value={selectedFilterEntryKind ?? ''}
                onChange={(event) => {
                  const nextEntryKind = event.target.value as TransactionEntryKind | ''
                  patchSearchParams({
                    asset_domain: nextEntryKind
                      ? entryKindAssetDomain(nextEntryKind)
                      : null,
                    asset_subtype:
                      nextEntryKind === 'fcn' || nextEntryKind === 'option'
                        ? nextEntryKind
                        : null,
                    transaction_type: null,
                    position_reference_id: null,
                    transaction_id: null,
                  })
                }}
              >
                <option value="">All asset types</option>
                <option value="security">Security</option>
                <option value="fcn">FCN</option>
                <option value="option">Option</option>
                <option value="cash">Cash &amp; Operations</option>
              </select>
            </label>
            <label>
              <span>Action</span>
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
                <option value="">All actions</option>
                {transactionFilterTypeGroups.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.types.map((transactionType) => (
                      <option key={transactionType} value={transactionType}>
                        {transactionActivityLabel(transactionType)}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            <label>
              <span>Asset</span>
              <select
                className="toolbar-select transaction-filter-input"
                value={filters.position_reference_id ?? ''}
                disabled={metaLoading || selectedFilterEntryKind === 'cash'}
                onChange={(event) =>
                  patchSearchParams({
                    position_reference_id: event.target.value,
                    transaction_id: null,
                  })
                }
              >
                <option value="">All assets</option>
                {selectedFilterEntryKind !== 'fcn' && selectedFilterEntryKind !== 'option' ? (
                  <optgroup label="Registry securities">
                    {transactionFilterInstruments.map((instrument) => (
                      <option key={instrument.instrument_id} value={instrument.instrument_id}>
                        {instrumentSearchLabel(instrument)}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
                {selectedFilterEntryKind !== 'security' && selectedFilterEntryKind !== 'cash' ? (
                  <optgroup label="Portfolio derivative contracts">
                    {transactionFilterContracts.map((contract) => (
                      <option key={contract.derivative_contract_id} value={contract.derivative_contract_id}>
                        {contract.contract_name} · {formatLabel(contract.contract_type)}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
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

        </section>

        {activeFilterCount ? (
          <div className="transaction-active-filters" aria-label="Active transaction filters">
            {filters.account_id ? (
              <button type="button" onClick={() => patchSearchParams({ account_id: null, transaction_id: null })}>
                Account: {accountNameById[filters.account_id] || filters.account_id} <span aria-hidden="true">×</span>
              </button>
            ) : null}
            {selectedFilterEntryKind ? (
              <button type="button" onClick={() => patchSearchParams({ asset_domain: null, asset_subtype: null, position_reference_id: null, transaction_id: null })}>
                Asset Type: {TRANSACTION_ENTRY_KINDS.find((item) => item.value === selectedFilterEntryKind)?.label}{' '}
                <span aria-hidden="true">×</span>
              </button>
            ) : null}
            {filters.transaction_type ? (
              <button type="button" onClick={() => patchSearchParams({ transaction_type: null, transaction_id: null })}>
                Action: {transactionActivityLabel(filters.transaction_type)} <span aria-hidden="true">×</span>
              </button>
            ) : null}
            {filters.position_reference_id ? (
              <button type="button" onClick={() => patchSearchParams({ position_reference_id: null, transaction_id: null })}>
                Asset: {derivativeContracts.find((item) => item.derivative_contract_id === filters.position_reference_id)?.contract_name ?? primaryIdentifier(instruments.find((item) => item.instrument_id === filters.position_reference_id) ?? {
                  instrument_id: filters.position_reference_id,
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
            <button
              type="button"
              className="transaction-active-filter-clear"
              onClick={() =>
                patchSearchParams({
                  account_id: null,
                  asset_domain: null,
                  asset_subtype: null,
                  transaction_type: null,
                  position_reference_id: null,
                  start_date: null,
                  end_date: null,
                  transaction_id: null,
                })
              }
            >
              Clear all
            </button>
          </div>
        ) : null}

        {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
        {captureError && !captureAssistantOpen ? <div className="error-state">{captureError}</div> : null}
        {pageError ? <div className="error-state">{pageError}</div> : null}
        {deleteError ? <div className="error-state">{deleteError}</div> : null}
        {metaLoading || loadingTransactions ? (
          <CalculationStatus />
        ) : null}

        {!ledgerError && transactionsWorkspace ? (
          <div className="transaction-workbench-grid">
            <section className={`transaction-ledger-panel ${loadingTransactions ? 'transaction-ledger-panel-refreshing' : ''}`}>
              <div className="table-shell transaction-table-shell" aria-busy={loadingTransactions}>
                <table className="transactions-table transaction-ledger-table">
                  <thead>
                    <tr>
                      <th>Trade</th>
                      <th>Activity</th>
                      <th>Account / Settlement</th>
                      <th>Quantity / Unit Price</th>
                      <th>Gross / Net Cash</th>
                      <th>Recognition</th>
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
                          <td>
                            <div className="holding-name-stack">
                              <strong>{transaction.trade_date}</strong>
                              <span className="holding-secondary">
                                {timeMeta.primary} · {timeMeta.secondary}
                              </span>
                            </div>
                          </td>
                          <td className="holding-name-cell transaction-activity-cell">
                            <div className="holding-name-stack">
                              <span>
                                <span className="transaction-type-pill">
                                  {transactionActivityLabel(
                                    transaction.transaction_type,
                                    transaction.asset_subtype,
                                    transaction.option_action,
                                  )}
                                </span>
                              </span>
                              <span className="holding-secondary">
                                {transaction.asset_domain === 'cash'
                                  ? 'Cash & Operations'
                                  : transaction.asset_domain === 'derivative'
                                    ? formatLabel(transaction.asset_subtype || 'derivative')
                                    : `Security · ${formatLabel(transaction.asset_subtype || 'other')}`}
                              </span>
                              {transaction.instrument_ref ? (
                                <>
                                  <strong>{primaryIdentifier(transaction.instrument_ref)}</strong>
                                  <span className="holding-secondary">{transaction.instrument_ref.instrument_name}</span>
                                </>
                              ) : transaction.derivative_contract ? (
                                <>
                                  <strong>{transaction.derivative_contract.contract_name}</strong>
                                  <span className="holding-secondary">
                                    {transaction.derivative_contract.derivative_contract_id}
                                  </span>
                                </>
                              ) : isFxConversionTransaction(transaction.transaction_type) ? (
                                <span className="holding-secondary">
                                  {transaction.currency} → {accountCurrencyById[transaction.counterparty_account_id || ''] || '—'}
                                </span>
                              ) : (
                                <span className="holding-secondary">{formatLabel(transaction.flow_scope)} cash</span>
                              )}
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
                                  ? `Counterparty ${accountNameById[transaction.counterparty_account_id] || transaction.counterparty_account_id}`
                                  : transaction.settlement_cash_account
                                    ? `Settle via ${transaction.settlement_cash_account.account_name}`
                                    : formatLabel(transaction.account.account_category)}
                              </span>
                            </div>
                          </td>
                          <td className="transaction-number-cell">
                            <div className="holding-name-stack">
                              <span>{transaction.quantity == null ? '—' : formatQuantity(transaction.quantity)}</span>
                              <span className="holding-secondary">
                                {transaction.price != null ? formatUnitPrice(transaction.price, transaction.currency) : 'No unit price'}
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
                          <td>
                            <div className="holding-name-stack">
                              <span>Settle {transaction.settlement_date}</span>
                              <span className="holding-secondary">
                                {transaction.position_effective_date
                                  ? `Position EOD ${transaction.position_effective_date}`
                                  : `Economic ${transaction.economic_date}`}
                              </span>
                              <span className="holding-secondary transaction-id-caption">
                                {transaction.transaction_id}
                              </span>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                    {!visibleTransactions.length ? (
                      <tr>
                        <td colSpan={7} className="empty-state-cell">No transactions match these filters.</td>
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
                      <div className="panel-title">
                        {transactionActivityLabel(
                          selectedTransaction.transaction_type,
                          selectedTransaction.asset_subtype,
                          selectedTransaction.option_action,
                        )}
                      </div>
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
                        {selectedTransaction.transfer_group_id
                          ? 'Delete Pair'
                          : 'Delete'}
                      </button>
                    </div>
                  </div>
                  <div className="transaction-inspector-tabs" role="tablist" aria-label="Transaction detail views">
                    {([
                      ['fact', 'Fact'],
                      ['postings', `Postings ${transactionsWorkspace.ledger_summary.posting_count}`],
                      ['lots', `Lots ${relatedPositionLots.length}`],
                      ...(relatedOptionObligations.length
                        ? ([['obligations', `Obligations ${relatedOptionObligations.length}`]] as const)
                        : []),
                      [
                        'history',
                        `History ${transactionsWorkspace.change_log_summary?.change_count ?? selectedTransactionChangeLog.length}`,
                      ],
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
                      {transactionsWorkspace.accounting_impact ? (
                        <div
                          className="transaction-fact-highlight transaction-accounting-highlight"
                          title={`Released cost ${formatCurrency(transactionsWorkspace.accounting_impact.local_cost_basis_released, selectedTransaction.currency)}; historical base cost ${formatCurrency(transactionsWorkspace.accounting_impact.historical_cost_basis_base, transactionsWorkspace.accounting_impact.base_currency)}; recognition FX ${formatNumber(transactionsWorkspace.accounting_impact.recognition_fx_rate_to_base, 6)}; settlement monetary basis ${formatCurrency(transactionsWorkspace.accounting_impact.settlement_monetary_cost_basis_base, transactionsWorkspace.accounting_impact.base_currency)}.`}
                        >
                          <span>
                            Realized position P&amp;L · {transactionsWorkspace.accounting_impact.base_currency}
                            {transactionsWorkspace.accounting_impact.fx_coverage_status === 'complete'
                              ? ''
                              : ` · ${formatLabel(transactionsWorkspace.accounting_impact.fx_coverage_status)}`}
                          </span>
                          <strong className={signedValueClass(transactionsWorkspace.accounting_impact.realized_position_pnl_base)}>
                            {formatSignedCurrency(
                              transactionsWorkspace.accounting_impact.realized_position_pnl_base,
                              transactionsWorkspace.accounting_impact.base_currency,
                            )}
                          </strong>
                          <em>
                            Price {formatSignedCurrency(
                              transactionsWorkspace.accounting_impact.realized_price_pnl_base,
                              transactionsWorkspace.accounting_impact.base_currency,
                            )}{' · '}FX {formatSignedCurrency(
                              transactionsWorkspace.accounting_impact.realized_position_fx_pnl_base,
                              transactionsWorkspace.accounting_impact.base_currency,
                            )}
                          </em>
                        </div>
                      ) : null}
                      <dl className="transaction-fact-list">
                        <div>
                          <dt>
                            {selectedTransaction.derivative_contract
                              ? 'Contract'
                              : selectedTransaction.instrument_ref
                                ? 'Security'
                                : 'Ledger'}
                          </dt>
                          <dd>
                            {selectedTransaction.instrument_ref
                              ? `${primaryIdentifier(selectedTransaction.instrument_ref)} · ${selectedTransaction.instrument_ref.instrument_name}`
                              : selectedTransaction.derivative_contract
                                ? `${selectedTransaction.derivative_contract.contract_name} · ${formatLabel(selectedTransaction.derivative_contract.contract_type)}`
                                : 'Cash & Operations'}
                          </dd>
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
                          <dt>Trade / position effective</dt>
                          <dd>
                            {selectedTransaction.trade_date}{' '}
                            {tradeTimeLabel(
                              selectedTransaction.trade_time,
                              selectedTransaction.trade_timezone,
                              selectedTransaction.trade_time_is_estimated,
                            ).primary}
                            {selectedTransaction.trade_time_is_estimated ? ' (estimated)' : ''}
                            <br /><span>{selectedTransaction.position_effective_date ?? '—'}</span>
                          </dd>
                        </div>
                        <div>
                          <dt>Settlement / economic</dt>
                          <dd>{selectedTransaction.settlement_date}<br /><span>{selectedTransaction.economic_date}</span></dd>
                        </div>
                        {selectedTransaction.transaction_type === 'deposit' ||
                        selectedTransaction.transaction_type === 'withdrawal' ? (
                          <div>
                            <dt>External flow date</dt>
                            <dd>{selectedTransaction.external_flow_date ?? '—'}</dd>
                          </div>
                        ) : null}
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
                        {selectedTransaction.lifecycle_event_type ? (
                          <div>
                            <dt>Derivative outcome</dt>
                            <dd>{formatLabel(selectedTransaction.lifecycle_event_type)}</dd>
                          </div>
                        ) : null}
                        {selectedTransaction.source_system || selectedTransaction.external_reference ? (
                          <div>
                            <dt>External source</dt>
                            <dd>
                              {selectedTransaction.source_system ?? '—'}
                              <br /><span>{selectedTransaction.external_reference ?? '—'}</span>
                            </dd>
                          </div>
                        ) : null}
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
                      {!selectedTransaction.instrument_id &&
                      !selectedTransaction.derivative_contract_id ? (
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

                  {inspectorTab === 'obligations' ? (
                    <div className="transaction-inspector-list" role="tabpanel">
                      {relatedOptionObligations.map((obligation) => (
                        <article key={obligation.obligation_id} className="transaction-inspector-card">
                          <div className="transaction-inspector-card-head">
                            <div>
                              <strong>{obligation.obligation_id}</strong>
                              <span>Opened {obligation.opened_at ?? '—'}</span>
                            </div>
                            <span className="transaction-type-pill">
                              {formatLabel(obligation.status)}
                            </span>
                          </div>
                          <dl className="transaction-inspector-card-metrics">
                            <div>
                              <dt>Remaining</dt>
                              <dd>{formatQuantity(obligation.remaining_quantity)}</dd>
                            </div>
                            <div>
                              <dt>Premium basis</dt>
                              <dd>
                                {formatCurrency(
                                  obligation.premium_basis_remaining,
                                  obligation.contract_currency ?? selectedTransaction.currency,
                                )}
                              </dd>
                            </div>
                            <div>
                              <dt>Liability</dt>
                              <dd>
                                {formatCurrency(
                                  obligation.carrying_liability,
                                  obligation.contract_currency ?? selectedTransaction.currency,
                                )}
                              </dd>
                            </div>
                          </dl>
                        </article>
                      ))}
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
                            <span>
                              {posting.recognition_start_date
                                ? `${posting.recognition_start_date} → ${posting.effective_date}`
                                : posting.settlement_date}
                            </span>
                          </div>
                          <dl className="transaction-inspector-card-metrics">
                            <div><dt>Cash</dt><dd>{formatSignedCurrency(posting.cash_amount_delta, posting.currency)}</dd></div>
                            <div><dt>Pending</dt><dd>{formatSignedCurrency(posting.pending_amount_delta, posting.currency)}</dd></div>
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

                  {inspectorTab === 'history' ? (
                    <div className="transaction-inspector-list" role="tabpanel">
                      {[...selectedTransactionChangeLog].reverse().map((change) => {
                        const changedFields = transactionChangedFields(change)
                        return (
                          <article key={change.change_id} className="transaction-inspector-card">
                            <div className="transaction-inspector-card-head">
                              <div>
                                <strong>{formatLabel(change.change_type)}</strong>
                                <span>{auditTimestampLabel(change.changed_at)}</span>
                              </div>
                              <span>Version {change.row_version}</span>
                            </div>
                            {changedFields.length ? (
                              <div className="transaction-impact-tags">
                                {changedFields.map((field) => <span key={field}>{field}</span>)}
                              </div>
                            ) : null}
                            {change.request_idempotency_key ? (
                              <div className="transaction-audit-request">
                                Request {change.request_idempotency_key}
                              </div>
                            ) : null}
                          </article>
                        )
                      })}
                      {!selectedTransactionChangeLog.length ? (
                        <div className="empty-state">No audit history is available for this fact.</div>
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

      {captureAssistantOpen ? (
        <div
          className="transaction-drawer-backdrop"
          role="presentation"
          onClick={() => {
            if (!uploadingCapture && !savingCaptureReview && !committingCapture) {
              setCaptureAssistantOpen(false)
              setCaptureDragActive(false)
              setCaptureReviewDraft(null)
            }
          }}
        >
          <aside
            ref={captureAssistantDialogRef}
            className="transaction-drawer transaction-capture-assistant-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Screenshot assistant"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <header className="transaction-capture-assistant-header">
              <div>
                <span>AI-assisted entry</span>
                <div className="panel-title">Transactions from Screenshots</div>
                <p>AI prepares an editable draft. You decide what is recorded.</p>
              </div>
              <button
                type="button"
                className="toolbar-link"
                disabled={uploadingCapture || savingCaptureReview || committingCapture}
                onClick={() => {
                  setCaptureAssistantOpen(false)
                  setCaptureDragActive(false)
                  setCaptureReviewDraft(null)
                }}
              >
                Close
              </button>
            </header>

            <div className="transaction-capture-assistant-tabs" role="tablist" aria-label="Assistant workspace">
              <button
                type="button"
                role="tab"
                aria-selected={captureAssistantView === 'new'}
                className={captureAssistantView === 'new' ? 'active' : ''}
                onClick={() => {
                  setCaptureAssistantView('new')
                  setCaptureError(null)
                  setCaptureReviewDraft(null)
                }}
              >
                New analysis
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={captureAssistantView === 'history'}
                className={captureAssistantView === 'history' ? 'active' : ''}
                onClick={() => {
                  setCaptureAssistantView('history')
                  setCaptureError(null)
                  setCaptureReviewDraft(null)
                }}
              >
                History <span>{transactionCaptureBatches.length}</span>
              </button>
            </div>

            <div className="transaction-capture-assistant-body">
              {captureAssistantView === 'new' ? (
                <div className="transaction-capture-new" role="tabpanel">
                  <section className="transaction-capture-section">
                    <div className="transaction-capture-section-heading">
                      <div>
                        <strong>Add screenshots</strong>
                        <span>PNG, JPEG, or WebP · up to 10 files · 12 MB each</span>
                      </div>
                      {captureDraftFiles.length ? (
                        <button
                          type="button"
                          className="toolbar-link"
                          disabled={uploadingCapture}
                          onClick={clearTransactionCaptureDraft}
                        >
                          Clear
                        </button>
                      ) : null}
                    </div>
                    <label className="transaction-capture-purpose-field">
                      <span>Use</span>
                      <select
                        className="toolbar-select"
                        aria-label="Use"
                        value={capturePurpose}
                        onChange={(event) => setCapturePurpose(
                          event.target.value as PortfolioTransactionCaptureBatchPurpose,
                        )}
                      >
                        {TRANSACTION_CAPTURE_PURPOSES.map((purpose) => (
                          <option key={purpose.value} value={purpose.value}>
                            {purpose.label}
                          </option>
                        ))}
                      </select>
                      <small>
                        {TRANSACTION_CAPTURE_PURPOSES.find((purpose) => purpose.value === capturePurpose)?.description}
                      </small>
                    </label>
                    <div
                      className={`transaction-capture-dropzone${captureDragActive ? ' active' : ''}`}
                      onDragEnter={(event) => {
                        event.preventDefault()
                        setCaptureDragActive(true)
                      }}
                      onDragOver={(event) => {
                        event.preventDefault()
                        setCaptureDragActive(true)
                      }}
                      onDragLeave={(event) => {
                        if (event.currentTarget === event.target) {
                          setCaptureDragActive(false)
                        }
                      }}
                      onDrop={(event) => {
                        event.preventDefault()
                        setCaptureDragActive(false)
                        addTransactionCaptureFiles(event.dataTransfer.files)
                      }}
                    >
                      <span aria-hidden="true">+</span>
                      <strong>Drop screenshots here</strong>
                      <p>Related pages can be analyzed together.</p>
                      <button
                        type="button"
                        className="toolbar-link"
                        disabled={uploadingCapture || captureDraftFiles.length >= TRANSACTION_CAPTURE_MAX_FILES}
                        onClick={() => transactionCaptureInputRef.current?.click()}
                      >
                        Choose screenshots
                      </button>
                    </div>

                    {captureDraftFiles.length ? (
                      <div className="transaction-capture-draft-grid" aria-label="Selected screenshot evidence">
                        {captureDraftFiles.map((draft, index) => (
                          <article key={draft.id}>
                            {draft.previewUrl ? (
                              <img src={draft.previewUrl} alt="" />
                            ) : (
                              <div className="transaction-capture-file-placeholder">Image</div>
                            )}
                            <div>
                              <strong>{draft.file.name}</strong>
                              <span>{index + 1} of {captureDraftFiles.length} · {formatCaptureByteSize(draft.file.size)}</span>
                            </div>
                            <button
                              type="button"
                              aria-label={`Remove ${draft.file.name}`}
                              disabled={uploadingCapture}
                              onClick={() => removeTransactionCaptureFile(draft.id)}
                            >
                              ×
                            </button>
                          </article>
                        ))}
                      </div>
                    ) : null}
                  </section>

                  {captureError ? <div className="error-state transaction-capture-error">{captureError}</div> : null}

                  <footer className="transaction-capture-prepare-footer">
                    <div>
                      <strong>AI creates a draft only</strong>
                      <span>No transaction is recorded until you confirm it.</span>
                    </div>
                    <button
                      type="button"
                      className="button-primary"
                      disabled={!captureDraftFiles.length || uploadingCapture}
                      onClick={() => void handleTransactionCaptures()}
                    >
                      {uploadingCapture ? 'Starting analysis…' : 'Analyze screenshots'}
                    </button>
                  </footer>
                </div>
              ) : (
                <div className="transaction-capture-history" role="tabpanel">
                  {captureError ? <div className="error-state transaction-capture-error">{captureError}</div> : null}
                  {transactionCaptureBatches.length ? (
                    <div className="transaction-capture-history-layout">
                      <nav aria-label="Recent screenshot batches">
                        <div className="transaction-capture-section-heading">
                          <div>
                            <strong>Recent batches</strong>
                            <span>Newest first</span>
                          </div>
                        </div>
                        <div className="transaction-capture-batch-list">
                          {transactionCaptureBatches.map((batch) => (
                            <button
                              key={batch.batch_id}
                              type="button"
                              className={selectedCaptureBatch?.batch_id === batch.batch_id ? 'active' : ''}
                              onClick={() => {
                                setSelectedCaptureBatchId(batch.batch_id)
                                setCaptureReviewDraft(null)
                                setCaptureError(null)
                              }}
                            >
                              <span
                                className={`transaction-capture-batch-dot transaction-capture-batch-dot-${transactionCaptureDisplayStatus(batch)}`}
                              />
                              <span>
                                <strong>{transactionCapturePurposeLabel(batch.purpose)}</strong>
                                <small>
                                  {batch.captures[0]?.original_filename ?? `${batch.capture_count} screenshot${batch.capture_count === 1 ? '' : 's'}`}
                                  {batch.capture_count > 1 ? ` +${batch.capture_count - 1}` : ''} ·{' '}
                                  {new Date(batch.created_at).toLocaleDateString()}
                                </small>
                              </span>
                            </button>
                          ))}
                        </div>
                      </nav>

                      {selectedCaptureBatch ? (
                        <section className="transaction-capture-batch-detail" aria-label="Selected screenshot batch">
                          <div className="transaction-capture-batch-title">
                            <div>
                              <span>Screenshot batch</span>
                              <strong>{transactionCapturePurposeLabel(selectedCaptureBatch.purpose)}</strong>
                            </div>
                            <span
                              className={`transaction-capture-status transaction-capture-status-${transactionCaptureDisplayStatus(selectedCaptureBatch)}`}
                            >
                              {transactionCaptureStatusLabel(selectedCaptureBatch)}
                            </span>
                          </div>

                          <div className="transaction-capture-evidence-grid">
                            {selectedCaptureBatch.captures.map((capture) => (
                              <figure key={capture.capture_id}>
                                <img
                                  src={portfolioTransactionCaptureImageUrl(portfolioId, capture.capture_id)}
                                  alt={`Screenshot evidence ${capture.original_filename}`}
                                />
                                <figcaption>
                                  <strong>{capture.original_filename}</strong>
                                  <span>{formatCaptureByteSize(capture.byte_size)}</span>
                                </figcaption>
                              </figure>
                            ))}
                          </div>

                          {selectedCaptureBatch.latest_analysis
                          && selectedCaptureBatch.analysis_run_status !== 'queued'
                          && selectedCaptureBatch.analysis_run_status !== 'running' ? (
                            <div className="transaction-capture-analysis">
                              <details className="transaction-capture-ai-notes">
                                <summary>AI notes</summary>
                                <p>{selectedCaptureBatch.latest_analysis.analysis.summary}</p>
                                {selectedCaptureBatch.latest_analysis.analysis.questions.length ? (
                                  <ul>
                                    {selectedCaptureBatch.latest_analysis.analysis.questions.map((question, index) => (
                                      <li key={`${index}-${question}`}>{question}</li>
                                    ))}
                                  </ul>
                                ) : null}
                              </details>

                              {captureReviewDraft?.batchId === selectedCaptureBatch.batch_id ? (
                                <section className="transaction-capture-review" aria-label="Transaction draft">
                                  <div className="transaction-capture-review-heading">
                                    <div>
                                      <span>Editable draft</span>
                                      <strong>Check and record</strong>
                                    </div>
                                    <span>AI suggestions can be changed directly</span>
                                  </div>

                                  <div className="transaction-capture-review-records">
                                    {captureReviewDraft.transactionImport.records.map((record, recordIndex) => {
                                      const recordNumber = recordIndex + 1
                                      const eligibleHoldingAccounts = accounts.filter(
                                        (account) => (
                                          account.account_category === record.asset_type
                                          && (
                                            account.currency === record.currency
                                            || account.account_id === record.account_id
                                          )
                                        ),
                                      )
                                      const eligibleCashAccounts = accounts.filter(
                                        (account) => (
                                          account.account_category === 'cash'
                                          && (
                                            account.currency === record.currency
                                            || account.account_id === record.settlement_cash_account_id
                                          )
                                        ),
                                      )
                                      const fcnContract = record.derivative_contract?.contract_type === 'fcn'
                                        ? record.derivative_contract
                                        : null
                                      const optionContractDraft = record.derivative_contract?.contract_type === 'option'
                                        ? record.derivative_contract
                                        : null
                                      const isTransfer = record.transaction_action === 'transfer_in'
                                        || record.transaction_action === 'transfer_out'
                                      const isFxConversion = record.transaction_action === 'fx_conversion'
                                      const reviewCandidate = selectedCaptureBatch.latest_analysis?.analysis.candidates.find(
                                        (candidate) => candidate.proposed_transaction_record_index === recordNumber,
                                      )
                                      const reviewFieldsNeedingAttention = reviewCandidate?.fields.filter(
                                        (field) => field.status !== 'observed',
                                      ) ?? []
                                      const hasDuplicateReferences = Boolean(
                                        reviewCandidate?.possible_duplicate_of?.length
                                        || reviewCandidate?.possible_existing_transaction_ids?.length,
                                      )
                                      const eligibleCounterpartyAccounts = accounts.filter((account) => {
                                        if (account.account_id === record.account_id) return false
                                        if (isFxConversion) {
                                          return account.account_category === 'cash'
                                            && (
                                              account.currency !== record.currency
                                              || account.account_id === record.counterparty_account_id
                                            )
                                        }
                                        return account.account_category === record.asset_type
                                          && (
                                            account.currency === record.currency
                                            || account.account_id === record.counterparty_account_id
                                          )
                                      })
                                      const eligibleDerivativeContracts = derivativeContracts.filter((contract) => (
                                        contract.contract_type === record.asset_type
                                        && (
                                          (
                                            contract.account_id === record.account_id
                                            && contract.currency === record.currency
                                          )
                                          || contract.derivative_contract_id === record.derivative_contract_id
                                        )
                                      ))
                                      const selectedDerivativeContract = derivativeContracts.find(
                                        (contract) => contract.derivative_contract_id === record.derivative_contract_id,
                                      )
                                      const accountRoleLabel = record.transaction_action === 'transfer_out'
                                        ? 'Source account'
                                        : record.transaction_action === 'transfer_in'
                                          ? 'Destination account'
                                          : isFxConversion
                                            ? 'Source cash account'
                                            : record.asset_type === 'cash'
                                              ? 'Cash account'
                                              : 'Holding account'
                                      return (
                                        <article key={record.external_reference || recordNumber}>
                                          <header>
                                            <div>
                                              <strong>{transactionCaptureRecordTitle(record, recordIndex)}</strong>
                                              <span>{record.external_reference}</span>
                                            </div>
                                            <em>{formatLabel(record.asset_type)}</em>
                                          </header>

                                          {reviewFieldsNeedingAttention.length ? (
                                            <details className="transaction-capture-field-notes">
                                              <summary>
                                                AI notes for {reviewFieldsNeedingAttention.length} field{reviewFieldsNeedingAttention.length === 1 ? '' : 's'}
                                              </summary>
                                              <div>
                                                {reviewFieldsNeedingAttention.map((field) => (
                                                  <span key={field.name}>
                                                    <strong>{formatLabel(field.name)}</strong>
                                                    {' · '}{formatLabel(field.status)}
                                                    {field.note ? ` · ${field.note}` : ''}
                                                  </span>
                                                ))}
                                              </div>
                                            </details>
                                          ) : null}

                                          {reviewCandidate && hasDuplicateReferences ? (
                                            <div className="transaction-capture-review-duplicate">
                                              <div>
                                                <strong>Duplicate decision</strong>
                                                <span>
                                                  {[...(reviewCandidate.possible_existing_transaction_ids ?? []),
                                                    ...(reviewCandidate.possible_duplicate_of ?? [])].join(' · ')}
                                                </span>
                                              </div>
                                              <label>
                                                <span>Resolution</span>
                                                <select
                                                  aria-label={`Record ${recordNumber} duplicate resolution`}
                                                  value={captureReviewDraft.duplicateAssessments[reviewCandidate.candidate_id]}
                                                  onChange={(event) => updateTransactionCaptureDuplicateAssessment(
                                                    reviewCandidate.candidate_id,
                                                    event.target.value as 'same_record' | 'distinct_records' | 'uncertain',
                                                  )}
                                                >
                                                  <option value="distinct_records">Distinct — keep proposal</option>
                                                  <option value="same_record">Same fact — exclude proposal</option>
                                                  <option value="uncertain">Uncertain — exclude proposal</option>
                                                </select>
                                              </label>
                                            </div>
                                          ) : null}

                                          <div className="transaction-capture-review-grid">
                                            <label>
                                              <span>Action</span>
                                              <select
                                                aria-label={`Record ${recordNumber} action`}
                                                value={record.transaction_action}
                                                onChange={(event) => updateTransactionCaptureReviewRecord(
                                                  recordIndex,
                                                  (current) => ({
                                                    ...current,
                                                    transaction_action: event.target.value as PortfolioTransactionImportAction,
                                                  }),
                                                )}
                                              >
                                                {TRANSACTION_CAPTURE_ACTIONS[record.asset_type].map((action) => (
                                                  <option key={action.value} value={action.value}>{action.label}</option>
                                                ))}
                                              </select>
                                            </label>
                                            <label>
                                              <span>{accountRoleLabel}</span>
                                              <select
                                                aria-label={`Record ${recordNumber} ${accountRoleLabel.toLowerCase()}`}
                                                value={record.account_id}
                                                onChange={(event) => updateTransactionCaptureReviewRecord(
                                                  recordIndex,
                                                  (current) => ({ ...current, account_id: event.target.value }),
                                                )}
                                              >
                                                {eligibleHoldingAccounts.map((account) => (
                                                  <option key={account.account_id} value={account.account_id}>
                                                    {account.account_name} · {account.currency}
                                                  </option>
                                                ))}
                                              </select>
                                            </label>
                                            {isTransfer || isFxConversion ? (
                                              <label>
                                                <span>
                                                  {record.transaction_action === 'transfer_out'
                                                    ? 'Destination account'
                                                    : record.transaction_action === 'transfer_in'
                                                      ? 'Source account'
                                                      : 'Target cash account'}
                                                </span>
                                                <select
                                                  aria-label={`Record ${recordNumber} counterparty account`}
                                                  value={record.counterparty_account_id ?? ''}
                                                  onChange={(event) => updateTransactionCaptureReviewRecord(
                                                    recordIndex,
                                                    (current) => ({
                                                      ...current,
                                                      counterparty_account_id: event.target.value || null,
                                                    }),
                                                  )}
                                                >
                                                  <option value="">Select account</option>
                                                  {eligibleCounterpartyAccounts.map((account) => (
                                                    <option key={account.account_id} value={account.account_id}>
                                                      {account.account_name} · {account.currency}
                                                    </option>
                                                  ))}
                                                </select>
                                              </label>
                                            ) : null}
                                            {record.asset_type !== 'cash' && !isTransfer ? (
                                              <label>
                                                <span>Settlement cash</span>
                                                <select
                                                  aria-label={`Record ${recordNumber} settlement cash account`}
                                                  value={record.settlement_cash_account_id ?? ''}
                                                  onChange={(event) => updateTransactionCaptureReviewRecord(
                                                    recordIndex,
                                                    (current) => ({
                                                      ...current,
                                                      settlement_cash_account_id: event.target.value || null,
                                                    }),
                                                  )}
                                                >
                                                  <option value="">None</option>
                                                  {eligibleCashAccounts.map((account) => (
                                                    <option key={account.account_id} value={account.account_id}>
                                                      {account.account_name} · {account.currency}
                                                    </option>
                                                  ))}
                                                </select>
                                              </label>
                                            ) : null}
                                            <label>
                                              <span>Currency</span>
                                              <input
                                                aria-label={`Record ${recordNumber} currency`}
                                                value={record.currency}
                                                maxLength={3}
                                                onChange={(event) => updateTransactionCaptureReviewRecord(
                                                  recordIndex,
                                                  (current) => ({ ...current, currency: event.target.value.toUpperCase() }),
                                                )}
                                              />
                                            </label>
                                            <label>
                                              <span>Trade date</span>
                                              <input
                                                type="date"
                                                aria-label={`Record ${recordNumber} trade date`}
                                                value={record.trade_date}
                                                onChange={(event) => updateTransactionCaptureReviewRecord(
                                                  recordIndex,
                                                  (current) => ({ ...current, trade_date: event.target.value }),
                                                )}
                                              />
                                            </label>
                                            <label>
                                              <span>Trade time</span>
                                              <input
                                                type="time"
                                                step={60}
                                                aria-label={`Record ${recordNumber} trade time`}
                                                value={record.trade_time ?? ''}
                                                onChange={(event) => updateTransactionCaptureReviewRecord(
                                                  recordIndex,
                                                  (current) => ({ ...current, trade_time: event.target.value || null }),
                                                )}
                                              />
                                            </label>
                                            <label>
                                              <span>Settlement date</span>
                                              <input
                                                type="date"
                                                aria-label={`Record ${recordNumber} settlement date`}
                                                value={record.settlement_date ?? ''}
                                                onChange={(event) => updateTransactionCaptureReviewRecord(
                                                  recordIndex,
                                                  (current) => ({ ...current, settlement_date: event.target.value || null }),
                                                )}
                                              />
                                            </label>
                                            <label>
                                              <span>Position effective</span>
                                              <input
                                                type="date"
                                                aria-label={`Record ${recordNumber} position effective date`}
                                                value={record.position_effective_date ?? ''}
                                                onChange={(event) => updateTransactionCaptureReviewRecord(
                                                  recordIndex,
                                                  (current) => ({
                                                    ...current,
                                                    position_effective_date: event.target.value || null,
                                                  }),
                                                )}
                                              />
                                            </label>
                                            {[
                                              'dividend',
                                              'dividend_reinvestment',
                                              'coupon',
                                              'fee',
                                              'tax',
                                            ].includes(record.transaction_action) ? (
                                              <label>
                                                <span>Entitlement date</span>
                                                <input
                                                  type="date"
                                                  aria-label={`Record ${recordNumber} entitlement date`}
                                                  value={record.entitlement_date ?? ''}
                                                  onChange={(event) => updateTransactionCaptureReviewRecord(
                                                    recordIndex,
                                                    (current) => ({
                                                      ...current,
                                                      entitlement_date: event.target.value || null,
                                                    }),
                                                  )}
                                                />
                                              </label>
                                            ) : null}
                                            {record.transaction_action === 'opening_balance'
                                            && record.asset_type !== 'cash' ? (
                                              <label>
                                                <span>Acquisition date</span>
                                                <input
                                                  type="date"
                                                  aria-label={`Record ${recordNumber} acquisition date`}
                                                  value={record.acquisition_date ?? ''}
                                                  onChange={(event) => updateTransactionCaptureReviewRecord(
                                                    recordIndex,
                                                    (current) => ({
                                                      ...current,
                                                      acquisition_date: event.target.value || null,
                                                    }),
                                                  )}
                                                />
                                              </label>
                                            ) : null}
                                          </div>

                                          {record.asset_type === 'security' ? (
                                            <div className="transaction-capture-review-grid transaction-capture-review-asset-grid">
                                              <label className="transaction-capture-review-wide">
                                                <span>Registry security</span>
                                                <input
                                                  list={`capture-review-security-${recordNumber}`}
                                                  aria-label={`Record ${recordNumber} registry security`}
                                                  value={record.instrument_id ?? ''}
                                                  onChange={(event) => updateTransactionCaptureReviewRecord(
                                                    recordIndex,
                                                    (current) => ({ ...current, instrument_id: event.target.value || null }),
                                                  )}
                                                />
                                                <datalist id={`capture-review-security-${recordNumber}`}>
                                                  {instruments.map((instrument) => (
                                                    <option
                                                      key={instrument.instrument_id}
                                                      value={instrument.instrument_id}
                                                      label={instrumentSearchLabel(instrument)}
                                                    />
                                                  ))}
                                                </datalist>
                                              </label>
                                            </div>
                                          ) : null}

                                          {record.asset_type === 'fcn' || record.asset_type === 'option' ? (
                                            <div className="transaction-capture-review-contract-source">
                                              <label>
                                                <span>Contract source</span>
                                                <select
                                                  aria-label={`Record ${recordNumber} contract source`}
                                                  value={record.derivative_contract
                                                    ? '__new_from_screenshot__'
                                                    : record.derivative_contract_id ?? ''}
                                                  onChange={(event) => {
                                                    if (event.target.value === '__new_from_screenshot__') return
                                                    updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => ({
                                                        ...current,
                                                        derivative_contract_id: event.target.value || null,
                                                        derivative_contract: null,
                                                      }),
                                                    )
                                                  }}
                                                >
                                                  <option value="">Select existing contract</option>
                                                  {record.derivative_contract ? (
                                                    <option value="__new_from_screenshot__">
                                                      New contract from screenshot
                                                    </option>
                                                  ) : null}
                                                  {eligibleDerivativeContracts.map((contract) => (
                                                    <option
                                                      key={contract.derivative_contract_id}
                                                      value={contract.derivative_contract_id}
                                                    >
                                                      {contract.contract_name} · {contract.currency}
                                                    </option>
                                                  ))}
                                                </select>
                                              </label>
                                              <span>
                                                {record.derivative_contract
                                                  ? 'The contract below will be created only with the reviewed transaction.'
                                                  : selectedDerivativeContract
                                                    ? `${selectedDerivativeContract.contract_name} · ${selectedDerivativeContract.account_id}`
                                                    : 'Choose a contract already assigned to this portfolio account.'}
                                              </span>
                                            </div>
                                          ) : null}

                                          {fcnContract ? (
                                            <div className="transaction-capture-review-terms">
                                              <div className="transaction-capture-review-subheading">
                                                <strong>FCN contract terms</strong>
                                                <span>Confirm economic terms against the source document.</span>
                                              </div>
                                              <div className="transaction-capture-review-grid">
                                                <label>
                                                  <span>Contract ID</span>
                                                  <input
                                                    aria-label={`Record ${recordNumber} FCN contract ID`}
                                                    value={fcnContract.derivative_contract_id}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract_id: event.target.value,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              derivative_contract_id: event.target.value,
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Contract name</span>
                                                  <input
                                                    aria-label={`Record ${recordNumber} FCN contract name`}
                                                    value={fcnContract.contract_name}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              contract_name: event.target.value,
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>ISIN / external reference</span>
                                                  <input
                                                    aria-label={`Record ${recordNumber} FCN external reference`}
                                                    value={fcnContract.external_reference ?? ''}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              external_reference: event.target.value || null,
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>FCN notional</span>
                                                  <input
                                                    type="number"
                                                    min="0"
                                                    step="0.01"
                                                    aria-label={`Record ${recordNumber} FCN notional`}
                                                    value={captureInputValue(fcnContract.terms.notional)}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                notional: event.target.value,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Annual coupon (%)</span>
                                                  <input
                                                    type="number"
                                                    min="0"
                                                    step="0.0001"
                                                    aria-label={`Record ${recordNumber} annual coupon`}
                                                    value={captureInputValue(fcnContract.terms.annual_coupon_rate_pct)}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                annual_coupon_rate_pct: event.target.value || null,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Issue date</span>
                                                  <input
                                                    type="date"
                                                    aria-label={`Record ${recordNumber} FCN issue date`}
                                                    value={fcnContract.terms.issue_date}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                issue_date: event.target.value,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Final observation</span>
                                                  <input
                                                    type="date"
                                                    aria-label={`Record ${recordNumber} FCN final observation date`}
                                                    value={fcnContract.terms.final_observation_date ?? ''}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                final_observation_date: event.target.value || null,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Maturity date</span>
                                                  <input
                                                    type="date"
                                                    aria-label={`Record ${recordNumber} FCN maturity date`}
                                                    value={fcnContract.terms.maturity_date}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                maturity_date: event.target.value,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Issuer</span>
                                                  <input
                                                    aria-label={`Record ${recordNumber} FCN issuer`}
                                                    value={fcnContract.terms.issuer}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                issuer: event.target.value,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Counterparty</span>
                                                  <input
                                                    aria-label={`Record ${recordNumber} FCN counterparty`}
                                                    value={fcnContract.terms.counterparty}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'fcn'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                counterparty: event.target.value,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                              </div>
                                              {fcnContract.terms.underlyings.map((underlying, underlyingIndex) => (
                                                <div className="transaction-capture-review-underlying" key={`${underlying.instrument_id}-${underlyingIndex}`}>
                                                  <strong>Underlying {underlyingIndex + 1}</strong>
                                                  <div className="transaction-capture-review-grid">
                                                    <label>
                                                      <span>Instrument</span>
                                                      <input
                                                        list={`capture-review-fcn-${recordNumber}-${underlyingIndex + 1}`}
                                                        aria-label={`Record ${recordNumber} underlying ${underlyingIndex + 1} instrument`}
                                                        value={underlying.instrument_id}
                                                        onChange={(event) => updateTransactionCaptureReviewRecord(
                                                          recordIndex,
                                                          (current) => {
                                                            if (current.derivative_contract?.contract_type !== 'fcn') return current
                                                            return {
                                                              ...current,
                                                              derivative_contract: {
                                                                ...current.derivative_contract,
                                                                terms: {
                                                                  ...current.derivative_contract.terms,
                                                                  underlyings: current.derivative_contract.terms.underlyings.map(
                                                                    (item, index) => index === underlyingIndex
                                                                      ? { ...item, instrument_id: event.target.value }
                                                                      : item,
                                                                  ),
                                                                },
                                                              },
                                                            }
                                                          },
                                                        )}
                                                      />
                                                      <datalist id={`capture-review-fcn-${recordNumber}-${underlyingIndex + 1}`}>
                                                        {instruments.map((instrument) => (
                                                          <option
                                                            key={instrument.instrument_id}
                                                            value={instrument.instrument_id}
                                                            label={instrumentSearchLabel(instrument)}
                                                          />
                                                        ))}
                                                      </datalist>
                                                    </label>
                                                    {[
                                                      ['initial_reference_price', 'Initial reference price'],
                                                      ['strike_level_pct', 'Strike level (%)'],
                                                      ['knock_in_level_pct', 'Knock-in level (%)'],
                                                      ['knock_out_level_pct', 'Knock-out level (%)'],
                                                    ].map(([fieldName, fieldLabel]) => (
                                                      <label key={fieldName}>
                                                        <span>{fieldLabel}</span>
                                                        <input
                                                          type="number"
                                                          min="0"
                                                          step="0.0001"
                                                          aria-label={`Record ${recordNumber} underlying ${underlyingIndex + 1} ${fieldLabel}`}
                                                          value={captureInputValue(
                                                            underlying[fieldName as keyof typeof underlying] as string | number | null,
                                                          )}
                                                          onChange={(event) => updateTransactionCaptureReviewRecord(
                                                            recordIndex,
                                                            (current) => {
                                                              if (current.derivative_contract?.contract_type !== 'fcn') return current
                                                              return {
                                                                ...current,
                                                                derivative_contract: {
                                                                  ...current.derivative_contract,
                                                                  terms: {
                                                                    ...current.derivative_contract.terms,
                                                                    underlyings: current.derivative_contract.terms.underlyings.map(
                                                                      (item, index) => index === underlyingIndex
                                                                        ? { ...item, [fieldName]: event.target.value || null }
                                                                        : item,
                                                                    ),
                                                                  },
                                                                },
                                                              }
                                                            },
                                                          )}
                                                        />
                                                      </label>
                                                    ))}
                                                    <label className="transaction-capture-review-checkbox">
                                                      <input
                                                        type="checkbox"
                                                        checked={underlying.deliverable}
                                                        onChange={(event) => updateTransactionCaptureReviewRecord(
                                                          recordIndex,
                                                          (current) => {
                                                            if (current.derivative_contract?.contract_type !== 'fcn') return current
                                                            return {
                                                              ...current,
                                                              derivative_contract: {
                                                                ...current.derivative_contract,
                                                                terms: {
                                                                  ...current.derivative_contract.terms,
                                                                  underlyings: current.derivative_contract.terms.underlyings.map(
                                                                    (item, index) => index === underlyingIndex
                                                                      ? { ...item, deliverable: event.target.checked }
                                                                      : item,
                                                                  ),
                                                                },
                                                              },
                                                            }
                                                          },
                                                        )}
                                                      />
                                                      <span>Physical delivery applies</span>
                                                    </label>
                                                  </div>
                                                </div>
                                              ))}
                                            </div>
                                          ) : null}

                                          {optionContractDraft ? (
                                            <div className="transaction-capture-review-terms">
                                              <div className="transaction-capture-review-subheading">
                                                <strong>Option contract terms</strong>
                                                <span>Confirm the contract identity and multiplier.</span>
                                              </div>
                                              <div className="transaction-capture-review-grid">
                                                <label>
                                                  <span>Contract ID</span>
                                                  <input
                                                    aria-label={`Record ${recordNumber} option contract ID`}
                                                    value={optionContractDraft.derivative_contract_id}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'option'
                                                        ? {
                                                            ...current,
                                                            derivative_contract_id: event.target.value,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              derivative_contract_id: event.target.value,
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Contract name</span>
                                                  <input
                                                    aria-label={`Record ${recordNumber} option contract name`}
                                                    value={optionContractDraft.contract_name}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'option'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              contract_name: event.target.value,
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Broker / external reference</span>
                                                  <input
                                                    aria-label={`Record ${recordNumber} option external reference`}
                                                    value={optionContractDraft.external_reference ?? ''}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'option'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              external_reference: event.target.value || null,
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Underlying</span>
                                                  <input
                                                    list={`capture-review-option-${recordNumber}`}
                                                    aria-label={`Record ${recordNumber} option underlying`}
                                                    value={optionContractDraft.terms.underlying_instrument_id}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'option'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                underlying_instrument_id: event.target.value,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                  <datalist id={`capture-review-option-${recordNumber}`}>
                                                    {instruments.map((instrument) => (
                                                      <option
                                                        key={instrument.instrument_id}
                                                        value={instrument.instrument_id}
                                                        label={instrumentSearchLabel(instrument)}
                                                      />
                                                    ))}
                                                  </datalist>
                                                </label>
                                                <label>
                                                  <span>Call / put</span>
                                                  <select
                                                    aria-label={`Record ${recordNumber} option type`}
                                                    value={optionContractDraft.terms.option_type}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'option'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                option_type: event.target.value as 'call' | 'put',
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  >
                                                    <option value="call">Call</option>
                                                    <option value="put">Put</option>
                                                  </select>
                                                </label>
                                                <label>
                                                  <span>Expiry date</span>
                                                  <input
                                                    type="date"
                                                    aria-label={`Record ${recordNumber} option expiry date`}
                                                    value={optionContractDraft.terms.expiry_date}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'option'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                expiry_date: event.target.value,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Strike</span>
                                                  <input
                                                    type="number"
                                                    min="0"
                                                    step="0.0001"
                                                    aria-label={`Record ${recordNumber} option strike`}
                                                    value={captureInputValue(optionContractDraft.terms.strike)}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'option'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                strike: event.target.value,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>Contract multiplier</span>
                                                  <input
                                                    type="number"
                                                    min="0"
                                                    step="1"
                                                    aria-label={`Record ${recordNumber} option multiplier`}
                                                    value={captureInputValue(optionContractDraft.terms.contract_multiplier)}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => current.derivative_contract?.contract_type === 'option'
                                                        ? {
                                                            ...current,
                                                            derivative_contract: {
                                                              ...current.derivative_contract,
                                                              terms: {
                                                                ...current.derivative_contract.terms,
                                                                contract_multiplier: event.target.value,
                                                              },
                                                            },
                                                          }
                                                        : current,
                                                    )}
                                                  />
                                                </label>
                                              </div>
                                            </div>
                                          ) : null}

                                          <div className="transaction-capture-review-grid transaction-capture-review-amounts">
                                            {[
                                              ['quantity', 'Quantity'],
                                              ['price', 'Price'],
                                              ['gross_amount', 'Gross amount'],
                                              ['fees', 'Fees'],
                                              ['taxes', 'Taxes'],
                                            ].map(([fieldName, fieldLabel]) => (
                                              <label key={fieldName}>
                                                <span>{fieldLabel}</span>
                                                <input
                                                  type="number"
                                                  min="0"
                                                  step="any"
                                                  aria-label={`Record ${recordNumber} ${fieldLabel.toLowerCase()}`}
                                                  value={captureInputValue(
                                                    record[fieldName as keyof PortfolioTransactionImportCommand] as string | number | null,
                                                  )}
                                                  onChange={(event) => updateTransactionCaptureReviewRecord(
                                                    recordIndex,
                                                    (current) => ({
                                                      ...current,
                                                      [fieldName]: event.target.value || null,
                                                    }),
                                                  )}
                                                />
                                              </label>
                                            ))}
                                            {isFxConversion ? (
                                              <>
                                                <label>
                                                  <span>Target amount</span>
                                                  <input
                                                    type="number"
                                                    min="0"
                                                    step="any"
                                                    aria-label={`Record ${recordNumber} target amount`}
                                                    value={captureInputValue(record.counter_amount)}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => ({
                                                        ...current,
                                                        counter_amount: event.target.value || null,
                                                      }),
                                                    )}
                                                  />
                                                </label>
                                                <label>
                                                  <span>FX rate</span>
                                                  <input
                                                    type="number"
                                                    min="0"
                                                    step="any"
                                                    aria-label={`Record ${recordNumber} FX rate`}
                                                    value={captureInputValue(record.fx_rate)}
                                                    onChange={(event) => updateTransactionCaptureReviewRecord(
                                                      recordIndex,
                                                      (current) => ({
                                                        ...current,
                                                        fx_rate: event.target.value || null,
                                                      }),
                                                    )}
                                                  />
                                                </label>
                                              </>
                                            ) : null}
                                            {record.transaction_action === 'fee'
                                            || Number(record.fees ?? 0) > 0 ? (
                                              <label>
                                                <span>Fee category</span>
                                                <select
                                                  aria-label={`Record ${recordNumber} fee category`}
                                                  value={record.fee_category ?? 'unknown'}
                                                  onChange={(event) => updateTransactionCaptureReviewRecord(
                                                    recordIndex,
                                                    (current) => ({
                                                      ...current,
                                                      fee_category: event.target.value as PortfolioFeeCategory,
                                                    }),
                                                  )}
                                                >
                                                  {FEE_CATEGORIES.map((option) => (
                                                    <option key={option.value} value={option.value}>
                                                      {option.label}
                                                    </option>
                                                  ))}
                                                </select>
                                              </label>
                                            ) : null}
                                            <label className="transaction-capture-review-wide">
                                              <span>Review note</span>
                                              <textarea
                                                aria-label={`Record ${recordNumber} review note`}
                                                rows={2}
                                                value={record.note ?? ''}
                                                onChange={(event) => updateTransactionCaptureReviewRecord(
                                                  recordIndex,
                                                  (current) => ({ ...current, note: event.target.value || null }),
                                                )}
                                              />
                                            </label>
                                          </div>
                                        </article>
                                      )
                                    })}
                                  </div>

                                  {selectedCaptureBatch.latest_analysis.analysis.questions.length ? (
                                    <div className="transaction-capture-review-notes">
                                      <strong>AI notes to check</strong>
                                      {selectedCaptureBatch.latest_analysis.analysis.questions.map((question, index) => (
                                        <span key={`${index}-${question}`}>{question}</span>
                                      ))}
                                    </div>
                                  ) : null}

                                  <footer className="transaction-capture-review-footer">
                                    <span>Ledger rules are checked automatically before anything is recorded.</span>
                                    <div>
                                      <button
                                        type="button"
                                        className="toolbar-link"
                                        disabled={savingCaptureReview}
                                        onClick={() => {
                                          setCaptureReviewDraft(null)
                                          setCaptureError(null)
                                        }}
                                      >
                                        Back
                                      </button>
                                      <button
                                        type="button"
                                        className="button-primary"
                                        disabled={savingCaptureReview || committingCapture}
                                        onClick={() => void confirmAndRecordTransactionCaptureReview()}
                                      >
                                        {savingCaptureReview || committingCapture ? 'Checking & recording…' : 'Confirm & record'}
                                      </button>
                                    </div>
                                  </footer>
                                </section>
                              ) : selectedCaptureBatch.ledger_status === 'recorded' ? (
                                <div className="transaction-capture-recorded">
                                  <div>
                                    <span>Recorded</span>
                                    <strong>These screenshot transactions are in the ledger.</strong>
                                  </div>
                                  <span>
                                    {selectedCaptureBatch.recorded_transaction_ids.length
                                      ? `Ledger facts: ${selectedCaptureBatch.recorded_transaction_ids.join(' · ')}`
                                      : 'The screenshot source references match recorded transaction facts.'}
                                  </span>
                                </div>
                              ) : selectedCaptureBatch.ledger_status === 'partially_recorded' ? (
                                <div className="transaction-capture-partial">
                                  <div>
                                    <span>Partially recorded</span>
                                    <strong>Some transactions from this batch are already in the ledger.</strong>
                                  </div>
                                  <span>
                                    {selectedCaptureBatch.recorded_transaction_ids.length
                                      ? `Ledger facts: ${selectedCaptureBatch.recorded_transaction_ids.join(' · ')}`
                                      : 'Open the recorded transactions before trying this batch again.'}
                                  </span>
                                </div>
                              ) : hasTransactionCaptureImport(selectedCaptureBatch.latest_analysis) ? (
                                <div className="transaction-capture-review-action">
                                  <div>
                                    <span>
                                      {transactionCaptureProposalReady(selectedCaptureBatch.latest_analysis)
                                        ? 'Ready'
                                        : selectedCaptureBatch.latest_analysis.source === 'human'
                                          ? 'Needs changes'
                                          : 'AI draft'}
                                    </span>
                                    <strong>
                                      {transactionCaptureProposalReady(selectedCaptureBatch.latest_analysis)
                                        ? `${selectedCaptureBatch.latest_analysis.transaction_import.records.length} transaction${selectedCaptureBatch.latest_analysis.transaction_import.records.length === 1 ? '' : 's'} ready to record.`
                                        : selectedCaptureBatch.latest_analysis.source === 'human'
                                          ? 'Edit the highlighted values and try again.'
                                          : 'Check the draft and change anything that is not right.'}
                                    </strong>
                                  </div>
                                  {transactionCaptureProposalReady(selectedCaptureBatch.latest_analysis) ? (
                                    <button
                                      type="button"
                                      className="button-primary"
                                      disabled={committingCapture}
                                      onClick={() => void recordTransactionCaptureProposal(selectedCaptureBatch)}
                                    >
                                      {committingCapture ? 'Recording…' : 'Confirm & record'}
                                    </button>
                                  ) : (
                                    <button
                                      type="button"
                                      className="toolbar-link"
                                      disabled={metaLoading || savingCaptureReview}
                                      onClick={() => beginTransactionCaptureReview(selectedCaptureBatch)}
                                    >
                                      {selectedCaptureBatch.latest_analysis.source === 'human'
                                        ? 'Edit draft'
                                        : 'Review draft'}
                                    </button>
                                  )}
                                </div>
                              ) : (
                                <div className="transaction-capture-review-boundary">
                                  <div>
                                    <strong>No transaction draft</strong>
                                    <span>AI could not produce a complete draft from these screenshots.</span>
                                  </div>
                                  <div>
                                    <button
                                      type="button"
                                      className="toolbar-link"
                                      onClick={() => {
                                        setCaptureAssistantOpen(false)
                                        openCreateDrawer()
                                      }}
                                    >
                                      Record manually
                                    </button>
                                    <button
                                      type="button"
                                      className="toolbar-link"
                                      disabled={startingCaptureBatchId === selectedCaptureBatch.batch_id}
                                      onClick={() => void handleScreenshotAnalysis(selectedCaptureBatch)}
                                    >
                                      Analyze again
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          ) : (
                            <div className="transaction-capture-agent-ready">
                              <span>AI analysis</span>
                              <strong>
                                {selectedCaptureBatch.analysis_run_status === 'queued'
                                  ? 'Waiting to start.'
                                  : selectedCaptureBatch.analysis_run_status === 'running'
                                    ? 'Reading and checking the screenshots.'
                                    : selectedCaptureBatch.analysis_run_status === 'failed'
                                      ? 'The last analysis did not finish.'
                                      : 'Screenshots are ready to analyze.'}
                              </strong>
                              <p>
                                {selectedCaptureBatch.analysis_run_status === 'failed'
                                  ? selectedCaptureBatch.analysis_run_error
                                    ?? 'Try the analysis again.'
                                  : 'AI will create an editable draft. It cannot record transactions by itself.'}
                              </p>
                              <div className="transaction-capture-agent-ready-footer">
                                {selectedCaptureBatch.analysis_run_status === 'queued'
                                  || selectedCaptureBatch.analysis_run_status === 'running' ? (
                                    <span>Usually takes a few minutes. You can close this panel.</span>
                                  ) : (
                                    <button
                                      type="button"
                                      className="button-primary"
                                      disabled={startingCaptureBatchId === selectedCaptureBatch.batch_id}
                                      onClick={() => void handleScreenshotAnalysis(selectedCaptureBatch)}
                                    >
                                      {startingCaptureBatchId === selectedCaptureBatch.batch_id
                                        ? 'Starting…'
                                        : selectedCaptureBatch.analysis_run_status === 'failed'
                                          ? 'Retry analysis'
                                          : 'Analyze screenshots'}
                                    </button>
                                  )}
                              </div>
                            </div>
                          )}
                        </section>
                      ) : null}
                    </div>
                  ) : (
                    <div className="transaction-capture-empty-history">
                      <span>0</span>
                      <strong>No screenshot history yet</strong>
                      <p>Start a new analysis to create an editable transaction draft.</p>
                      <button type="button" className="toolbar-link" onClick={() => setCaptureAssistantView('new')}>
                        New analysis
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </aside>
        </div>
      ) : null}

      {drawerOpen ? (
        <div
          className="transaction-entry-backdrop"
          role="presentation"
          onClick={() => {
            if (submittingTransaction) {
              return
            }
            setDrawerOpen(false)
            setEditingTransactionId(null)
            setActiveEventTask(null)
            setActiveEventTaskReviewer('')
          }}
        >
          <aside
            ref={drawerDialogRef}
            className="transaction-entry-modal"
            role="dialog"
            aria-modal="true"
            aria-label={isEditingTransaction ? 'Correct transaction' : 'Record transaction'}
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="transaction-entry-modal-header">
              <div>
                <div className="panel-title">
                  {isEditingTransaction ? 'Correct Transaction' : 'Record Transaction'}
                </div>
              </div>
              <button
                type="button"
                className="toolbar-link"
                disabled={submittingTransaction}
                onClick={() => {
                  setDrawerOpen(false)
                  setEditingTransactionId(null)
                  setActiveEventTask(null)
                  setActiveEventTaskReviewer('')
                }}
              >
                Close
              </button>
            </div>

            <form className="transaction-form" onSubmit={(event) => void handleCreateTransaction(event)}>
              <div className="transaction-ticket-main">
                <div className="transaction-ticket-section-heading">
                  <div>
                    <strong>Choose what happened</strong>
                  </div>
                </div>
                <div
                  className="transaction-entry-kind-selector"
                  role="group"
                  aria-label="Entry Type"
                >
                  {TRANSACTION_ENTRY_KINDS.map((entryKind, index) => (
                    <button
                      key={entryKind.value}
                      ref={index === 0 ? entryKindControlRef : undefined}
                      type="button"
                      className={
                        formEntryKind === entryKind.value
                          ? 'transaction-entry-kind-option transaction-entry-kind-option-active'
                          : 'transaction-entry-kind-option'
                      }
                      aria-pressed={formEntryKind === entryKind.value}
                      title={entryKind.description}
                      disabled={Boolean(activeEventTask) || submittingTransaction}
                      onClick={() => updateEntryKind(entryKind.value)}
                    >
                      <strong>{entryKind.label}</strong>
                    </button>
                  ))}
                </div>

                <div className="transaction-entry-context-grid">
                  <label className="transaction-ticket-field">
                    <span>{formAccountLabel}</span>
                    <select
                      ref={accountSelectRef}
                      value={form.account_id}
                      disabled={submittingTransaction || formAccountOptions.length === 0}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          account_id: event.target.value,
                        }))
                      }
                    >
                      {!formAccountOptions.length ? (
                        <option value="">
                          {formEntryKind === 'cash'
                            ? 'No cash account'
                            : `No ${formEntryKindLabel} holding account`}
                        </option>
                      ) : null}
                      {formAccountOptions.map((account) => (
                        <option key={account.account_id} value={account.account_id}>
                          {account.account_name} · {account.currency}
                        </option>
                      ))}
                    </select>
                  </label>

                  {shouldAllowRegistryInstrument ? (
                    <section className="transaction-instrument-search">
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
                            void selectInstrument(filteredInstrumentOptions[0])
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
                              disabled={securityMaterializing}
                              onClick={() => void selectInstrument(instrument)}
                            >
                              <div className="holding-name-stack">
                                <span>{primaryIdentifier(instrument)}</span>
                                <span className="holding-secondary">{instrument.instrument_name}</span>
                              </div>
                              <span className="transaction-picker-meta">
                                {'currency_verified' in instrument && !instrument.currency_verified
                                  ? `${instrument.currency} · verify on add`
                                  : instrument.currency}
                              </span>
                            </button>
                          ))}
                          {!filteredInstrumentOptions.length ? (
                            <div className="transaction-instrument-empty">
                              {securityCatalogLoading
                                ? 'Searching Registry and the local stock and ETF catalogs…'
                                : 'No matching security.'}
                            </div>
                          ) : null}
                          {securityCatalogError ? (
                            <div className="transaction-instrument-empty">{securityCatalogError}</div>
                          ) : null}
                        </div>
                      ) : null}
                    </section>
                  ) : null}

                  {shouldAllowDerivativeContract ? (
                    <section className="transaction-instrument-search">
                      <label className="transaction-ticket-field">
                        <span>{formEntryKindLabel} Contract</span>
                        <select
                          value={form.derivative_contract_id}
                          onChange={(event) => selectDerivativeContract(event.target.value)}
                        >
                          <option value="">Create new contract</option>
                          {filteredDerivativeContracts.map((contract) => (
                            <option
                              key={contract.derivative_contract_id}
                              value={contract.derivative_contract_id}
                            >
                              {contract.contract_name} · {formatLabel(contract.contract_type)} · {contract.currency}
                            </option>
                          ))}
                        </select>
                      </label>
                    </section>
                  ) : null}

                  {formEntryKind === 'option' && isCreatingDerivativeContract ? (
                    <label className="transaction-ticket-field">
                      <span>Option Type</span>
                      <select
                        value={derivativeDraft.option_type}
                        onChange={(event) =>
                          setDerivativeDraft((current) => ({
                            ...current,
                            option_type: event.target.value as 'call' | 'put',
                          }))
                        }
                      >
                        <option value="call">Call</option>
                        <option value="put">Put</option>
                      </select>
                    </label>
                  ) : null}

                  <label className="transaction-ticket-field">
                    <span>Action</span>
                    <select
                      value={selectedActionValue}
                      disabled={Boolean(activeEventTask) || submittingTransaction}
                      onChange={(event) => updateTransactionAction(event.target.value)}
                    >
                      {formActionGroups.map((group) => (
                        <optgroup key={group.label} label={group.label}>
                          {group.actions.map((transactionAction) => (
                            <option key={transactionAction.value} value={transactionAction.value}>
                              {transactionAction.label}
                            </option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                  </label>
                </div>

                {!formAccountOptions.length ? (
                  <div className="transaction-entry-account-warning" role="status">
                    <span>
                      {formEntryKind === 'cash'
                        ? 'Set up a cash account before recording this entry.'
                        : `Enable ${formEntryKindLabel} on a holding account with a same-currency settlement account.`}
                    </span>
                    <Link to={`/portfolios/${portfolioId}/accounts`}>Open Accounts</Link>
                  </div>
                ) : null}

              {(form.transaction_type === 'lifecycle_event' ||
                form.transaction_type === 'maturity_redemption') &&
              resolvedAssetType === 'option' ? (
                <div className="portfolio-detail-meta">
                  This closes only the selected option contract. Physical delivery is recorded
                  as a separate Security transaction at the delivery-date reference price.
                </div>
              ) : null}

              {isCreatingDerivativeContract ? (
                <section className="transaction-contract-definition">
                  <div className="transaction-contract-definition-head">
                    <strong>New {formEntryKindLabel} contract</strong>
                  </div>
                  <div className="transaction-form-grid transaction-ticket-grid">
                  <label className="transaction-ticket-field">
                    <span>Contract ID</span>
                    <input
                      value={derivativeDraft.derivative_contract_id}
                      onChange={(event) =>
                        setDerivativeDraft((current) => ({
                          ...current,
                          derivative_contract_id: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <label className="transaction-ticket-field">
                    <span>Contract Name</span>
                    <input
                      value={derivativeDraft.contract_name}
                      onChange={(event) =>
                        setDerivativeDraft((current) => ({
                          ...current,
                          contract_name: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <label className="transaction-ticket-field">
                    <span>Contract Reference</span>
                    <input
                      value={derivativeDraft.external_reference}
                      onChange={(event) =>
                        setDerivativeDraft((current) => ({
                          ...current,
                          external_reference: event.target.value,
                        }))
                      }
                    />
                  </label>

                  {derivativeDraft.contract_type === 'option' ? (
                    <>
                      <RegistryInstrumentPicker
                        label="Underlying Security"
                        value={derivativeDraft.option_underlying_instrument_id}
                        instruments={instruments}
                        onSelect={(instrumentId) =>
                          setDerivativeDraft((current) => ({
                            ...current,
                            option_underlying_instrument_id: instrumentId,
                          }))
                        }
                      />
                      <label className="transaction-ticket-field">
                        <span>Expiry Date</span>
                        <input
                          type="date"
                          value={derivativeDraft.option_expiry_date}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({
                              ...current,
                              option_expiry_date: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="transaction-ticket-field">
                        <span>Strike</span>
                        <input
                          type="number"
                          min="0"
                          step="any"
                          value={derivativeDraft.option_strike}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({
                              ...current,
                              option_strike: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="transaction-ticket-field">
                        <span>Contract Multiplier</span>
                        <input
                          type="number"
                          min="0"
                          step="any"
                          value={derivativeDraft.option_contract_multiplier}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({
                              ...current,
                              option_contract_multiplier: event.target.value,
                            }))
                          }
                        />
                      </label>
                    </>
                  ) : (
                    <>
                      <label className="transaction-ticket-field">
                        <span>Notional</span>
                        <input
                          type="number"
                          min="0"
                          step="any"
                          value={derivativeDraft.fcn_notional}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({ ...current, fcn_notional: event.target.value }))
                          }
                        />
                      </label>
                      <label className="transaction-ticket-field">
                        <span>Annual Coupon Rate (%)</span>
                        <input
                          type="number"
                          min="0"
                          step="any"
                          value={derivativeDraft.fcn_annual_coupon_rate_pct}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({
                              ...current,
                              fcn_annual_coupon_rate_pct: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="transaction-ticket-field">
                        <span>Issue Date</span>
                        <input
                          type="date"
                          value={derivativeDraft.fcn_issue_date}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({ ...current, fcn_issue_date: event.target.value }))
                          }
                        />
                      </label>
                      <label className="transaction-ticket-field">
                        <span>Final Observation Date</span>
                        <input
                          type="date"
                          value={derivativeDraft.fcn_final_observation_date}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({
                              ...current,
                              fcn_final_observation_date: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="transaction-ticket-field">
                        <span>Maturity Date</span>
                        <input
                          type="date"
                          value={derivativeDraft.fcn_maturity_date}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({ ...current, fcn_maturity_date: event.target.value }))
                          }
                        />
                      </label>
                      <label className="transaction-ticket-field">
                        <span>Issuer</span>
                        <input
                          value={derivativeDraft.fcn_issuer}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({ ...current, fcn_issuer: event.target.value }))
                          }
                        />
                      </label>
                      <label className="transaction-ticket-field">
                        <span>Counterparty</span>
                        <input
                          value={derivativeDraft.fcn_counterparty}
                          onChange={(event) =>
                            setDerivativeDraft((current) => ({ ...current, fcn_counterparty: event.target.value }))
                          }
                        />
                      </label>
                      <div className="transaction-fcn-underlyings">
                        <div className="transaction-fcn-underlyings-head">
                          <strong>FCN Underlyings</strong>
                          <button
                            type="button"
                            className="toolbar-link"
                            onClick={() =>
                              setDerivativeDraft((current) => ({
                                ...current,
                                fcn_underlyings: [
                                  ...current.fcn_underlyings,
                                  buildInitialFcnUnderlyingDraft(),
                                ],
                              }))
                            }
                          >
                            Add Underlying
                          </button>
                        </div>
                        {derivativeDraft.fcn_underlyings.map((underlying, index) => (
                          <div className="transaction-fcn-underlying-row" key={index}>
                            <div className="transaction-fcn-security-field">
                              <RegistryInstrumentPicker
                                label={`Underlying ${index + 1}`}
                                value={underlying.instrument_id}
                                instruments={instruments}
                                onSelect={(instrumentId) =>
                                  setDerivativeDraft((current) => ({
                                    ...current,
                                    fcn_underlyings: current.fcn_underlyings.map((item, itemIndex) =>
                                      itemIndex === index
                                        ? { ...item, instrument_id: instrumentId }
                                        : item,
                                    ),
                                  }))
                                }
                              />
                            </div>
                            <div className="transaction-fcn-underlying-actions">
                              <label className="transaction-fcn-deliverable">
                                <input
                                  type="checkbox"
                                  checked={underlying.deliverable}
                                  onChange={(event) =>
                                    setDerivativeDraft((current) => ({
                                      ...current,
                                      fcn_underlyings: current.fcn_underlyings.map((item, itemIndex) =>
                                        itemIndex === index
                                          ? { ...item, deliverable: event.target.checked }
                                          : item,
                                      ),
                                    }))
                                  }
                                />
                                <span>Deliverable</span>
                              </label>
                              <button
                                type="button"
                                className="toolbar-link transaction-danger-action"
                                disabled={derivativeDraft.fcn_underlyings.length === 1}
                                onClick={() =>
                                  setDerivativeDraft((current) => ({
                                    ...current,
                                    fcn_underlyings: current.fcn_underlyings.filter(
                                      (_, itemIndex) => itemIndex !== index,
                                    ),
                                  }))
                                }
                              >
                                Remove
                              </button>
                            </div>
                            {([
                              ['initial_reference_price', 'Initial Price'],
                              ['strike_level_pct', 'Strike (%)'],
                              ['knock_in_level_pct', 'Knock-In (%)'],
                              ['knock_out_level_pct', 'Knock-Out (%)'],
                            ] as const).map(([field, label]) => (
                              <label className="transaction-ticket-field" key={field}>
                                <span>{label}</span>
                                <input
                                  type="number"
                                  min="0"
                                  step="any"
                                  value={underlying[field]}
                                  onChange={(event) =>
                                    setDerivativeDraft((current) => ({
                                      ...current,
                                      fcn_underlyings: current.fcn_underlyings.map((item, itemIndex) =>
                                        itemIndex === index
                                          ? { ...item, [field]: event.target.value }
                                          : item,
                                      ),
                                    }))
                                  }
                                />
                              </label>
                            ))}
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                  </div>
                </section>
              ) : null}

              {activeDerivativeContract?.contract_type === 'option' ? (
                <div className="portfolio-detail-meta">
                  {formatLabel(activeDerivativeContract.terms.option_type)} · underlying{' '}
                  {activeDerivativeContract.terms.underlying_instrument_id} · strike{' '}
                  {activeDerivativeContract.terms.strike} · expires{' '}
                  {activeDerivativeContract.terms.expiry_date} · multiplier{' '}
                  {activeDerivativeContract.terms.contract_multiplier}
                </div>
              ) : activeDerivativeContract?.contract_type === 'fcn' ? (
                <div className="portfolio-detail-meta">
                  FCN · notional {activeDerivativeContract.terms.notional}
                  {activeDerivativeCurrency ? ` ${activeDerivativeCurrency}` : ''} · matures{' '}
                  {activeDerivativeContract.terms.maturity_date} ·{' '}
                  {activeDerivativeContract.terms.issuer}
                </div>
              ) : null}

              <div className="transaction-ticket-section-heading transaction-ticket-section-heading-compact">
                <div>
                  <strong>Transaction facts</strong>
                </div>
                <em>{DEFAULT_TRADE_TIMEZONE}</em>
              </div>

              <div className="transaction-form-grid transaction-ticket-grid">
                {isFxConversion ? (
                  <label className="transaction-ticket-field">
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
                  <label className="transaction-ticket-field">
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
                  <label className="transaction-ticket-field">
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
                  <span>Trade Date</span>
                  <input
                    type="date"
                    value={form.trade_date}
                    disabled={form.transaction_type === 'opening_balance'}
                    onChange={(event) => {
                      const nextTradeDate = event.target.value
                      setForm((current) => {
                        const nextPositionEffectiveDate =
                          current.position_effective_date === current.trade_date ||
                          current.position_effective_date < nextTradeDate
                            ? nextTradeDate
                            : current.position_effective_date
                        const nextSettlementDate =
                          current.settlement_date === current.trade_date ||
                          current.settlement_date < nextTradeDate
                            ? nextTradeDate
                            : current.settlement_date
                        return {
                          ...current,
                          trade_date: nextTradeDate,
                          settlement_date: nextSettlementDate,
                          position_effective_date: nextPositionEffectiveDate,
                        }
                      })
                    }}
                  />
                </label>

                <label className="transaction-ticket-field">
                  <span>{transactionDateLabels(form.transaction_type).settlement}</span>
                  <input
                    type="date"
                    min={form.trade_date}
                    value={form.settlement_date}
                    disabled={form.transaction_type === 'opening_balance'}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        settlement_date: event.target.value,
                      }))
                    }
                  />
                </label>

                {form.transaction_type === 'opening_balance' ? (
                  <span className="transaction-ticket-hint">
                    Opening balances are fixed to portfolio inception{' '}
                    {transactionsWorkspace?.portfolio_inception_date}.
                  </span>
                ) : null}

                {supportsPositionEffectiveDate(form.transaction_type) ? (
                  <label className="transaction-ticket-field">
                    <span>Position Effective Date</span>
                    <input
                      type="date"
                      min={form.trade_date}
                      value={form.position_effective_date}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          position_effective_date: event.target.value,
                        }))
                      }
                      title="Trade Date for same-day holdings; confirmed share date for delayed funds"
                    />
                    {isFundTrade ? (
                      <span className="transaction-ticket-hint">
                        Use the confirmed share date when the fund position begins later.
                      </span>
                    ) : null}
                  </label>
                ) : null}

                <label className="transaction-ticket-field">
                  <span>Trade Time (optional)</span>
                  <input
                    type="time"
                    step={60}
                    value={form.trade_time}
                    title={`Blank stores an estimated ${DEFAULT_FORM_TIME}`}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        trade_time: event.target.value,
                      }))
                    }
                  />
                </label>

                {supportsEntitlementDate(form.transaction_type, hasAssetReference) ? (
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
                    <span>
                      {isFundTrade
                        ? 'Confirmed Shares'
                        : resolvedAssetType === 'option' ||
                            resolvedAssetType === 'fcn'
                          ? 'Contracts'
                          : 'Shares'}
                    </span>
                    <input
                      type="number"
                      min="0"
                      step="any"
                      aria-label={
                        isFundTrade
                          ? 'Confirmed Shares'
                          : resolvedAssetType === 'option' ||
                              resolvedAssetType === 'fcn'
                            ? 'Contracts'
                            : 'Shares'
                      }
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
                    <span>
                      {isFundTrade
                        ? 'Derived Unit Price'
                        : resolvedAssetType === 'option'
                          ? 'Premium per Underlying Unit'
                          : resolvedAssetType === 'fcn'
                            ? 'Contract Price'
                            : 'Execution Price'}
                    </span>
                    <input
                      type="number"
                      min="0"
                      step="any"
                      aria-label={isFundTrade ? 'Derived Unit Price' : 'Execution Price'}
                      value={form.price}
                      placeholder={computedUnitPrice || '0.0000'}
                      readOnly={isFundTrade}
                      onChange={(event) => updatePricingField('price', event.target.value)}
                    />
                    {historicalQuoteLoading ? (
                      <span className="transaction-ticket-hint">Loading reference quote</span>
                    ) : activeHistoricalQuote ? (
                      <span className="transaction-ticket-hint">
                        {isFundTrade ? 'Reference only · ' : ''}
                        {activeHistoricalQuote.stale ? 'Prior ' : ''}{formatLabel(activeHistoricalQuote.quoteBasis)}:{' '}
                        {formatUnitPrice(activeHistoricalQuote.price, activeHistoricalQuote.currency)} · {activeHistoricalQuote.asOfDate}
                      </span>
                    ) : historicalQuoteError ? (
                      <span className="transaction-ticket-hint">{historicalQuoteError}</span>
                    ) : resolvedAssetType === 'option' ? (
                      <span className="transaction-ticket-hint">
                        Amount = contracts × premium × multiplier.
                      </span>
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

              <details className="transaction-entry-additional-details">
                <summary>
                  <strong>Additional details</strong>
                </summary>
                <div className="transaction-entry-additional-body">
                  <div className="transaction-form-grid transaction-ticket-grid">
                    <label className="transaction-ticket-field">
                      <span>Source System</span>
                      <input
                        value={form.source_system}
                        placeholder="Optional; required with external reference"
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            source_system: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <label className="transaction-ticket-field">
                      <span>External Reference</span>
                      <input
                        value={form.external_reference}
                        placeholder="Unique within this portfolio and source"
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            external_reference: event.target.value,
                          }))
                        }
                      />
                    </label>
                  </div>

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
                </div>
              </details>
              </div>

              <aside className="transaction-ticket-review" aria-label="Transaction review">
                <div className="transaction-ticket-section-heading">
                  <div>
                    <strong>Accounting impact</strong>
                  </div>
                </div>
              <div className="transaction-ticket-summary" aria-live="polite">
                {isFxConversion ? (
                  <>
                    <div className="transaction-ticket-summary-row">
                      <span>Source Amount</span>
                      <strong>
                        {ticketGrossAmount == null || !resolvedTransactionCurrency
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
                        {computedCounterAmount && resolvedCounterpartyCurrency
                          ? formatCurrency(Number(computedCounterAmount), resolvedCounterpartyCurrency)
                          : '—'}
                      </strong>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="transaction-ticket-summary-row">
                      <span>Pre-fee Amount</span>
                      <strong>
                        {ticketGrossAmount == null || !resolvedTransactionCurrency
                          ? '—'
                          : formatCurrency(ticketGrossAmount, resolvedTransactionCurrency)}
                      </strong>
                    </div>
                    {shouldShowFees ? (
                      <div className="transaction-ticket-summary-row">
                        <span>Fee</span>
                        <strong>
                          {resolvedTransactionCurrency
                            ? formatCurrency(ticketFeeAmount, resolvedTransactionCurrency)
                            : '—'}
                        </strong>
                      </div>
                    ) : null}
                    {shouldShowTaxes ? (
                      <div className="transaction-ticket-summary-row">
                        <span>Tax</span>
                        <strong>
                          {resolvedTransactionCurrency
                            ? formatCurrency(ticketTaxAmount, resolvedTransactionCurrency)
                            : '—'}
                        </strong>
                      </div>
                    ) : null}
                    <div className="transaction-ticket-summary-row transaction-ticket-summary-total">
                      <span>Net Cash Effect</span>
                      <strong>
                        {ticketNetCashEffect == null || !resolvedTransactionCurrency
                          ? '—'
                          : formatSignedCurrency(ticketNetCashEffect, resolvedTransactionCurrency)}
                      </strong>
                    </div>
                  </>
                )}
              </div>

              {shouldRequireSettlement &&
              resolvedTransactionCurrency &&
              settlementAccountOptions.length === 0 ? (
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

              {formError ? <div className="error-state transaction-form-error">{formError}</div> : null}
              </aside>

              <div className="transaction-form-footer">
                <button
                  type="button"
                  className="toolbar-link"
                  disabled={submittingTransaction}
                  onClick={() => {
                    setDrawerOpen(false)
                    setEditingTransactionId(null)
                    setActiveEventTask(null)
                    setActiveEventTaskReviewer('')
                  }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="toolbar-link button-primary"
                  disabled={submittingTransaction || !selectedAccount}
                >
                  {submittingTransaction
                    ? 'Saving…'
                    : isEditingTransaction
                      ? 'Save Correction'
                      : 'Record Transaction'}
                </button>
              </div>
            </form>
          </aside>
        </div>
      ) : null}
      <ConfirmDialog
        open={Boolean(pendingFileImport)}
        title="Review Transaction File"
        description={
          pendingFileImport ? (
            <div className="transaction-file-preview">
              <div>
                <strong>{pendingFileImport.fileName}</strong>
                <span>
                  {pendingFileImport.preview.row_count} rows · {pendingFileImport.preview.valid_count} valid ·{' '}
                  {pendingFileImport.preview.error_count} issues
                </span>
              </div>
              <p>
                {pendingFileImport.preview.error_count
                  ? 'Nothing has been imported. Fix the issues below, then choose the file again.'
                  : `Import ${pendingFileImport.preview.valid_count} validated transaction fact(s)?`}
              </p>
              {pendingFileImport.preview.batch_errors.length ? (
                <div className="transaction-file-preview-issues">
                  <strong>File issues</strong>
                  <ul>
                    {pendingFileImport.preview.batch_errors.map((error) => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {pendingFileImport.preview.rows.some((row) => row.errors.length) ? (
                <div className="transaction-file-preview-issues">
                  <strong>Row issues</strong>
                  <ul>
                    {pendingFileImport.preview.rows
                      .filter((row) => row.errors.length)
                      .slice(0, 20)
                      .map((row) => (
                        <li key={row.row_number}>
                          Row {row.row_number}: {row.errors.join('; ')}
                        </li>
                      ))}
                  </ul>
                </div>
              ) : null}
              {pendingFileImport.preview.warnings.length ? (
                <div className="transaction-file-preview-warnings">
                  <strong>Warnings</strong>
                  <ul>
                    {pendingFileImport.preview.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null
        }
        confirmLabel={`Import ${pendingFileImport?.preview.valid_count ?? 0} Rows`}
        busyLabel="Importing…"
        error={fileImportError}
        busy={importingFile}
        confirmDisabled={Boolean(pendingFileImport?.preview.error_count)}
        confirmTone="primary"
        onCancel={() => {
          setPendingFileImport(null)
          setFileImportError(null)
        }}
        onConfirm={confirmTransactionFileImport}
      />
      <ConfirmDialog
        open={Boolean(pendingDeleteTransaction)}
        title={
          pendingDeleteTransaction?.transfer_group_id
            ? 'Delete Transfer Pair'
            : 'Delete Transaction'
        }
        description={
          pendingDeleteTransaction?.transfer_group_id
            ? `This permanently deletes both legs of transfer pair ${pendingDeleteTransaction.transfer_group_id}. This action cannot be undone.`
            : `This permanently deletes transaction ${pendingDeleteTransaction?.transaction_id ?? ''}. This action cannot be undone.`
        }
        confirmLabel={
          pendingDeleteTransaction?.transfer_group_id
            ? 'Delete Pair'
            : 'Delete Transaction'
        }
        confirmationText={
          pendingDeleteTransaction?.transfer_group_id ??
          pendingDeleteTransaction?.transaction_id
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
