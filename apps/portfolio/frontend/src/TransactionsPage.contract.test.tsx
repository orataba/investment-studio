import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import TransactionsPage from './pages/TransactionsPage'
import type {
  PortfolioTransactionCaptureAnalysisRevision,
  PortfolioTransactionCaptureBatchRecord,
  PortfolioTransactionImportCommand,
  PortfolioTransactionRecord,
} from './lib/api'
import { instrumentFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const accessState = vi.hoisted(() => ({ can_edit: true }))
vi.mock('./components/PortfolioAccessProvider', () => ({ usePortfolioAccess: () => accessState }))

const apiMocks = vi.hoisted(() => ({
  commitPortfolioTransactionImport: vi.fn(),
  createPortfolioInternalTransfer: vi.fn(),
  createPortfolioTransactionCaptureAnalysisRevision: vi.fn(),
  createPortfolioTransactionCaptureBatch: vi.fn(),
  createPortfolioTransaction: vi.fn(),
  createPortfolioOptionOutcome: vi.fn(),
  deletePortfolioTransaction: vi.fn(),
  getPortfolioAccounts: vi.fn(),
  getPortfolioDerivativeContracts: vi.fn(),
  getPortfolioFxRates: vi.fn(),
  getPortfolioInstrumentEventTasks: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  searchPortfolioSecurities: vi.fn(),
  materializePortfolioSecurity: vi.fn(),
  getPortfolioOptionDeliveryLinks: vi.fn(),
  getPortfolioTransactionExecutionQuote: vi.fn(),
  getPortfolioTransactionPositionPreview: vi.fn(),
  getPortfolioTransactionCaptureBatches: vi.fn(),
  getPortfolioTransactionsWorkspace: vi.fn(),
  getPortfolioTransactions: vi.fn(),
  importPortfolioTransactionFile: vi.fn(),
  portfolioTransactionDownloadUrl: vi.fn((_portfolioId: string, format: string) => `/transactions.${format}`),
  portfolioTransactionCaptureImageUrl: vi.fn((_portfolioId: string, captureId: string) => `/captures/${captureId}/image`),
  portfolioTransactionTemplateUrl: vi.fn((_portfolioId: string, format: string) => `/transactions/${format}-template`),
  previewPortfolioTransactionFile: vi.fn(),
  reviewPortfolioInstrumentEventTask: vi.fn(),
  startPortfolioTransactionCaptureAnalysis: vi.fn(),
  uploadPortfolioTransactionCapture: vi.fn(),
  updatePortfolioTransaction: vi.fn(),
}))
vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

function SwitchableTransactionsPage() {
  const navigate = useNavigate()
  return (
    <>
      <button type="button" onClick={() => navigate('/portfolios/4/transactions')}>
        Switch portfolio
      </button>
      <TransactionsPage />
    </>
  )
}

const securitiesAccount = {
  account_id: 'brokerage-1',
  portfolio_id: '3',
  account_name: 'ETF Brokerage',
  account_type: 'securities_account',
  account_category: 'security' as const,
  currency: 'USD',
  default_settlement_cash_account_id: 'cash-1',
  status: 'active',
}

const cashAccount = {
  account_id: 'cash-1',
  portfolio_id: '3',
  account_name: 'Settlement Cash',
  account_type: 'deposit_account',
  account_category: 'cash' as const,
  currency: 'USD',
  status: 'active',
}

const optionAccount = {
  ...securitiesAccount,
  account_id: 'options-1',
  account_name: 'Options Account',
  account_category: 'option' as const,
}

const optionContract = {
  derivative_contract_id: 'option-call-1',
  portfolio_id: '3',
  account_id: 'options-1',
  contract_name: 'GETF Dec 30 Call',
  contract_type: 'option' as const,
  currency: 'USD',
  external_reference: 'GETF-20261218-C30',
  terms: {
    underlying_instrument_id: 'etf-1',
    option_type: 'call' as const,
    expiry_date: '2026-12-18',
    strike: 30,
    contract_multiplier: 100,
  },
  created_at: '2026-07-01T00:00:00Z',
}

const fundCashAccount = {
  ...cashAccount,
  account_id: 'cash-cny-1',
  account_name: 'CNY Settlement Cash',
  currency: 'CNY',
}

const fundSecuritiesAccount = {
  ...securitiesAccount,
  account_id: 'fund-brokerage-1',
  account_name: 'Fund Account',
  currency: 'CNY',
  default_settlement_cash_account_id: 'cash-cny-1',
  account_category: 'security' as const,
}

const fcnAccount = {
  ...fundSecuritiesAccount,
  account_id: 'fcn-cny-1',
  account_name: 'FCN CNY Account',
  account_category: 'fcn' as const,
}

const etfInstrument = {
  ...instrumentFixture({
    instrument_id: 'etf-1',
    instrument_name: 'Global Equity ETF',
    instrument_type: 'etf',
    identifiers: [{ identifier_type: 'ticker' as const, identifier_value: 'GETF', is_primary: true }],
  }),
  latest_market_data: [
    {
      metric_family: 'price' as const,
      quote_basis: 'close' as const,
      as_of_date: '2026-07-15',
      value: '25',
      currency: 'USD',
      price_unit: 'per_unit' as const,
      price_scale: '1',
      status: 'complete' as const,
    },
  ],
  coverage_state: 'complete' as const,
}

const alternateEtfInstrument = {
  ...instrumentFixture({
    instrument_id: 'etf-2',
    instrument_name: 'Alternate Equity ETF',
    instrument_type: 'etf',
    identifiers: [{ identifier_type: 'ticker' as const, identifier_value: 'AETF', is_primary: true }],
  }),
  latest_market_data: [
    {
      metric_family: 'price' as const,
      quote_basis: 'close' as const,
      as_of_date: '2026-07-15',
      value: '98.5',
      currency: 'USD',
      price_unit: 'per_unit' as const,
      price_scale: '1',
      status: 'complete' as const,
    },
  ],
  coverage_state: 'complete' as const,
}

const fundInstrument = {
  ...instrumentFixture({
    instrument_id: 'fund-1',
    instrument_name: 'Confirmed Allocation Fund',
    instrument_type: 'private_fund',
    currency: 'CNY',
    identifiers: [{ identifier_type: 'ticker' as const, identifier_value: 'FUND1', is_primary: true }],
  }),
  latest_market_data: [
    {
      metric_family: 'nav' as const,
      quote_basis: 'official_nav' as const,
      as_of_date: '2026-07-15',
      value: '1.2',
      currency: 'CNY',
      price_unit: 'per_unit' as const,
      price_scale: '1',
      status: 'complete' as const,
    },
  ],
  coverage_state: 'complete' as const,
}

const materializedEquityInstrument = {
  ...instrumentFixture({
    instrument_id: 'equity-aapl',
    instrument_name: 'Apple Inc.',
    instrument_type: 'equity',
    currency: 'USD',
    exchange_code: 'XNAS',
    identifiers: [
      {
        identifier_type: 'exchange_ticker' as const,
        identifier_value: 'AAPL',
        is_primary: true,
      },
    ],
  }),
  latest_market_data: [],
  coverage_state: 'complete' as const,
}

const materializedEtfInstrument = {
  ...instrumentFixture({
    instrument_id: 'etf-mags',
    instrument_name: 'Roundhill Magnificent Seven ETF',
    instrument_type: 'etf',
    currency: 'USD',
    identifiers: [
      {
        identifier_type: 'exchange_ticker' as const,
        identifier_value: 'MAGS',
        is_primary: true,
      },
    ],
  }),
  latest_market_data: [],
  coverage_state: 'complete' as const,
}

const selectedTransaction = {
  transaction_id: 'txn-1',
  transaction_sequence: 1,
  portfolio_id: '3',
  transaction_type: 'buy',
  asset_domain: 'security',
  asset_subtype: 'etf',
  flow_scope: 'internal',
  trade_date: '2026-07-14',
  trade_time: '12:00',
  trade_at: '2026-07-14T12:00:00+08:00',
  trade_timezone: 'Asia/Shanghai',
  trade_time_is_estimated: false,
  settlement_date: '2026-07-15',
  position_effective_date: '2026-07-14',
  economic_date: '2026-07-14',
  external_flow_date: null,
  entitlement_date: null,
  acquisition_date: null,
  account: securitiesAccount,
  settlement_cash_account: cashAccount,
  instrument_id: 'etf-1',
  instrument_ref: instrumentFixture({
    instrument_id: 'etf-1',
    instrument_name: 'Global Equity ETF',
    instrument_type: 'etf',
    identifiers: [{ identifier_type: 'ticker', identifier_value: 'GETF', is_primary: true }],
  }),
  derivative_contract_id: null,
  derivative_contract: null,
  quantity: 10,
  source_quantity: '10',
  price: 25,
  source_price: '25',
  gross_amount: 250,
  source_gross_amount: '250',
  counter_amount: null,
  source_counter_amount: null,
  fx_rate: null,
  source_fx_rate: null,
  fees: 1,
  source_fees: '1',
  fee_category: 'transaction_cost',
  taxes: 0,
  source_taxes: '0',
  currency: 'USD',
  transfer_scope: null,
  transfer_object_type: null,
  transfer_group_id: null,
  counterparty_account_id: null,
  net_cash_effect: -251,
  note: 'Opening ETF purchase',
  created_at: '2026-07-14T12:00:00+08:00',
  row_version: 3,
} satisfies PortfolioTransactionRecord

function screenshotBatchFixture({
  batchId,
  records,
  candidates,
  source = 'assistant',
  ledgerStatus = 'unrecorded',
  recordedTransactionIds = [],
}: {
  batchId: string
  records: PortfolioTransactionImportCommand[]
  candidates: PortfolioTransactionCaptureAnalysisRevision['analysis']['candidates']
  source?: 'assistant' | 'human'
  ledgerStatus?: PortfolioTransactionCaptureBatchRecord['ledger_status']
  recordedTransactionIds?: string[]
}): PortfolioTransactionCaptureBatchRecord {
  const capture = {
    capture_id: `${batchId}-capture`,
    portfolio_id: '3',
    original_filename: 'broker-activity.png',
    media_type: 'image/png' as const,
    byte_size: 4096,
    content_sha256: '9'.repeat(64),
    created_at: '2026-08-25T03:00:00Z',
  }
  const revision: PortfolioTransactionCaptureAnalysisRevision = {
    batch_id: batchId,
    revision: source === 'human' ? 2 : 1,
    source,
    harness: source === 'assistant' ? 'deepseek-harness' : null,
    provider: source === 'assistant' ? 'deepseek' : null,
    model_name: source === 'assistant' ? 'deepseek-v4-flash-vision-exp' : null,
    finish_reason: source === 'human' ? 'user_confirmed' : 'completed',
    schema_version: 'portfolio.transaction-capture-analysis.v2',
    analysis: {
      summary: 'Broker activity contains reviewable transaction candidates.',
      documents: [{ capture_id: capture.capture_id, document_kind: 'trade_activity' }],
      candidates,
      questions: [],
    },
    transaction_import: {
      source_system: 'portfolio_screenshot_assistant',
      records,
    },
    preview_digest: '8'.repeat(64),
    preview_error_count: 0,
    created_at: '2026-08-25T03:02:00Z',
  }
  return {
    batch_id: batchId,
    portfolio_id: '3',
    purpose: 'transaction_import',
    status: 'review_required',
    capture_count: 1,
    latest_analysis_revision: revision.revision,
    analysis_run_status: 'succeeded',
    analysis_run_attempt: 1,
    analysis_run_started_at: '2026-08-25T03:00:10Z',
    analysis_run_completed_at: '2026-08-25T03:02:00Z',
    analysis_run_error: null,
    captures: [capture],
    latest_analysis: revision,
    ledger_status: ledgerStatus,
    recorded_transaction_ids: recordedTransactionIds,
    created_at: '2026-08-25T03:00:00Z',
    updated_at: '2026-08-25T03:02:00Z',
  }
}

describe('Transactions rendered page contract', () => {
  beforeEach(() => {
    accessState.can_edit = true
    vi.clearAllMocks()
    apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [], catalog_errors: {} })
    apiMocks.materializePortfolioSecurity.mockReset()
    apiMocks.deletePortfolioTransaction.mockResolvedValue({
      portfolio_id: '3',
      deleted_count: 1,
      deleted_transaction_ids: ['txn-1'],
      transfer_group_id: null,
    })
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [securitiesAccount, cashAccount],
    })
    apiMocks.getPortfolioInstruments.mockResolvedValue({
      portfolio_id: '3',
      instruments: [etfInstrument, alternateEtfInstrument, fundInstrument],
    })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({
      portfolio_id: '3',
      derivative_contracts: [],
    })
    apiMocks.getPortfolioFxRates.mockResolvedValue({ portfolio_id: '3', rates: [] })
    apiMocks.getPortfolioOptionDeliveryLinks.mockResolvedValue({ portfolio_id: '3', links: [] })
    apiMocks.getPortfolioTransactionCaptureBatches.mockResolvedValue({ portfolio_id: '3', batches: [] })
    apiMocks.getPortfolioTransactionExecutionQuote.mockResolvedValue({
      portfolio_id: '3',
      instrument_id: '',
      requested_as_of_date: '2026-07-15',
      selection_role: null,
      value: null,
      quote_date: null,
      quote_basis: null,
      metric_family: null,
      currency: 'USD',
      provider: null,
      status: 'unavailable',
      stale: false,
      price_unit: null,
      price_scale: null,
      unavailable_reason: 'no_eligible_execution_quote',
    })
    apiMocks.getPortfolioInstrumentEventTasks.mockResolvedValue({
      portfolio_id: '3',
      accounting_policy: 'official_unit_nav_assume_no_unrecorded_distribution',
      attention_count: 0,
      tasks: [],
    })
    apiMocks.getPortfolioTransactionPositionPreview.mockImplementation(
      (_portfolioId, request) =>
        Promise.resolve({
          portfolio_id: '3',
          account_id: request.account_id,
          position_kind: request.position_kind,
          position_reference_id: request.position_reference_id,
          as_of_date: request.as_of_date,
          trade_at: `${request.as_of_date}T12:00:00+08:00`,
          quantity: 1_000,
        }),
    )
    apiMocks.getPortfolioTransactions.mockResolvedValue({ transactions: [] })
    apiMocks.getPortfolioTransactionsWorkspace.mockResolvedValue({
      portfolio_id: '3',
      base_currency: 'USD',
      portfolio_inception_date: '2026-01-02',
      summary: {
        total_transactions: 1,
        security_transactions: 1,
        derivative_transactions: 0,
        fcn_transactions: 0,
        option_transactions: 0,
        cash_transactions: 0,
        external_cash_flows: 0,
        opening_balance_records: 0,
      },
      derivation_boundary: {
        ledger_postings: 'fixture',
        positions: 'fixture',
        position_lots: 'fixture',
        holdings: 'fixture',
        snapshot: 'fixture',
      },
      selected_transaction_id: 'txn-1',
      position_reference_ids: ['etf-1'],
      delete_scope_row_versions: { 'txn-1': 3 },
      transactions: [selectedTransaction],
      selected_transaction: selectedTransaction,
      accounting_impact: null,
      cash_fx_impacts: [],
      ledger_summary: {
        posting_count: 1,
        cash_posting_count: 1,
        position_posting_count: 0,
      },
      ledger_postings: [
        {
          posting_id: 'posting-1',
          transaction_id: 'txn-1',
          portfolio_id: '3',
          account_id: 'cash-1',
          posting_role: 'settlement_cash',
          source_transaction_type: 'buy',
          trade_date: '2026-07-14',
          settlement_date: '2026-07-15',
          cash_amount_delta: -251,
          quantity_delta: 0,
          cost_basis_delta: null,
          currency: 'USD',
        },
      ],
      related_position_lot_summary: {
        position_lot_count: 0,
        open_position_lot_count: 0,
        closed_position_lot_count: 0,
        realized_pnl: 0,
      },
      related_position_lots: [],
      related_option_obligations: [],
      change_log_summary: {
        change_count: 1,
      },
      change_log: [
        {
          change_id: 'change-1',
          portfolio_id: '3',
          transaction_id: 'txn-1',
          change_type: 'create',
          row_version: 1,
          before: null,
          after: {
            transaction_id: 'txn-1',
            transaction_type: 'buy',
            trade_date: '2026-07-14',
          },
          request_idempotency_key: 'fixture-request',
          changed_at: '2026-07-14T04:00:00Z',
        },
      ],
    })
  })

  it('keeps reader exports and filters available without import or recording actions', async () => {
    accessState.can_edit = false
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    const reviewTrigger = await screen.findByRole('button', { name: /Fund distribution reviews: No distribution events/ })
    expect(reviewTrigger.closest('.transaction-activity-title')).toHaveTextContent('Activity')
    expect(screen.queryByText('No distribution events currently need attention.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'From Screenshot' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Record Transaction' })).toBeDisabled()
    await user.click(reviewTrigger)
    expect(screen.getByRole('tooltip')).toHaveTextContent('No distribution events currently need attention.')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Refresh Distribution Events' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Review distributions' }))
    expect(screen.getByRole('region', { name: 'Fund distribution reviews' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Refresh Distribution Events' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Close fund distribution reviews' }))
    await user.click(screen.getByRole('button', { name: 'Export' }))
    expect(screen.getByRole('menuitem', { name: 'CSV' })).toBeInTheDocument()
    expect(apiMocks.createPortfolioTransaction).not.toHaveBeenCalled()
    expect(apiMocks.commitPortfolioTransactionImport).not.toHaveBeenCalled()
  })

  it('keeps four transaction actions while exposing CSV and Excel formats', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    const exportButton = await screen.findByRole('button', { name: 'Export' })
    expect(screen.getByRole('button', { name: 'Import' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'From Screenshot' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Template' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record Transaction' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Transaction CSV' })).not.toBeInTheDocument()
    expect(screen.queryByText('Select visible')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Export View' })).not.toBeInTheDocument()

    await user.click(exportButton)
    expect(screen.getByRole('menuitem', { name: 'CSV' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Excel' })).toBeInTheDocument()
    expect(document.querySelector<HTMLInputElement>('input[type="file"]')?.accept).toContain('.xlsx')
  })

  it('shows realized cash FX separately from position P&L for a foreign-cash release', async () => {
    const workspace = await apiMocks.getPortfolioTransactionsWorkspace()
    apiMocks.getPortfolioTransactionsWorkspace.mockResolvedValue({
      ...workspace,
      base_currency: 'CNY',
      cash_fx_impacts: [
        {
          transaction_id: 'txn-1',
          posting_role: 'security_settlement_cash',
          account_id: cashAccount.account_id,
          currency: 'USD',
          recognition_date: '2026-07-15',
          recognition_fx_rate_to_base: 7.5,
          local_exposure_released: 251,
          historical_cost_basis_base: 1757,
          fair_value_base: 1882.5,
          realized_cash_fx_pnl_base: 125.5,
          fx_coverage_status: 'complete',
        },
      ],
    })

    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions?transaction_id=txn-1',
      '/portfolios/:portfolioId/transactions',
    )

    const inspector = await screen.findByRole('complementary', {
      name: 'Selected transaction details',
    })
    expect(within(inspector).getByText('Realized cash FX · CNY')).toBeInTheDocument()
    expect(within(inspector).getByText('+CN¥125.50')).toBeInTheDocument()
    expect(within(inspector).getByText(/\$251.00 released/)).toBeInTheDocument()
  })

  it('groups multiple screenshots for one agent analysis without creating a transaction fact', async () => {
    const user = userEvent.setup()
    const contentSha = 'c'.repeat(64)
    const overlapSha = 'd'.repeat(64)
    const firstCapture = {
      capture_id: 'capture-1',
      portfolio_id: '3',
      original_filename: 'broker-fill.png',
      media_type: 'image/png' as const,
      byte_size: 2048,
      content_sha256: contentSha,
      created_at: '2026-08-25T00:00:00Z',
    }
    const secondCapture = {
      ...firstCapture,
      capture_id: 'capture-2',
      original_filename: 'broker-fill-overlap.png',
      content_sha256: overlapSha,
    }
    apiMocks.uploadPortfolioTransactionCapture
      .mockResolvedValueOnce(firstCapture)
      .mockResolvedValueOnce(secondCapture)
    const readyBatch = {
      batch_id: 'capture-batch-1',
      portfolio_id: '3',
      purpose: 'transaction_import',
      status: 'ready',
      capture_count: 2,
      latest_analysis_revision: 0,
      analysis_run_status: 'idle',
      analysis_run_attempt: 0,
      analysis_run_started_at: null,
      analysis_run_completed_at: null,
      analysis_run_error: null,
      captures: [firstCapture, secondCapture],
      latest_analysis: null,
      ledger_status: 'no_proposal',
      recorded_transaction_ids: [],
      created_at: '2026-08-25T00:00:00Z',
      updated_at: '2026-08-25T00:00:00Z',
    } as const
    apiMocks.createPortfolioTransactionCaptureBatch.mockResolvedValue(readyBatch)
    apiMocks.startPortfolioTransactionCaptureAnalysis.mockResolvedValue({
      ...readyBatch,
      analysis_run_status: 'queued',
      analysis_run_attempt: 1,
    })
    const firstFile = new File(['screenshot-bytes'], 'broker-fill.png', {
      type: 'image/png',
    })
    const secondFile = new File(['overlap-bytes'], 'broker-fill-overlap.png', {
      type: 'image/png',
    })
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'From Screenshot' }))
    const assistant = screen.getByRole('dialog', { name: 'Screenshot assistant' })
    expect(within(assistant).getByRole('combobox', { name: 'Use' })).toHaveValue('auto')

    const screenshotInput = document.querySelector<HTMLInputElement>(
      'input[type="file"][accept*="image/png"]',
    )
    expect(screenshotInput).not.toBeNull()
    expect(screenshotInput).toHaveAttribute('multiple')
    fireEvent.change(screenshotInput!, { target: { files: [firstFile, secondFile] } })

    expect(within(assistant).getByText('broker-fill.png')).toBeInTheDocument()
    expect(within(assistant).getByText('broker-fill-overlap.png')).toBeInTheDocument()
    expect(apiMocks.uploadPortfolioTransactionCapture).not.toHaveBeenCalled()

    await user.selectOptions(
      within(assistant).getByRole('combobox', { name: 'Use' }),
      'transaction_import',
    )
    await user.click(within(assistant).getByRole('button', { name: 'Analyze screenshots' }))

    await waitFor(() =>
      expect(apiMocks.createPortfolioTransactionCaptureBatch).toHaveBeenCalledWith(
        '3',
        ['capture-1', 'capture-2'],
        'transaction_import',
      ),
    )
    const batchDetail = within(assistant).getByRole('region', { name: 'Selected screenshot batch' })
    expect(within(batchDetail).getByText('Record transactions')).toBeInTheDocument()
    expect(within(batchDetail).getAllByText('2 KB')).toHaveLength(2)
    await waitFor(() => expect(apiMocks.startPortfolioTransactionCaptureAnalysis).toHaveBeenCalledWith(
      '3',
      'capture-batch-1',
    ))
    expect(within(batchDetail).getByText('Waiting to start.')).toBeInTheDocument()
    expect(within(batchDetail).getByText(/AI will create an editable draft/)).toBeInTheDocument()
    expect(within(batchDetail).queryByRole('button', { name: /analyze/i })).not.toBeInTheDocument()
    expect(apiMocks.importPortfolioTransactionFile).not.toHaveBeenCalled()
    expect(apiMocks.createPortfolioTransaction).not.toHaveBeenCalled()
  })

  it('keeps AI notes secondary when no transaction draft was produced', async () => {
    const user = userEvent.setup()
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [securitiesAccount, cashAccount, optionAccount],
    })
    const capture = {
      capture_id: 'capture-review-1',
      portfolio_id: '3',
      original_filename: 'broker-position.png',
      media_type: 'image/png' as const,
      byte_size: 4096,
      content_sha256: 'e'.repeat(64),
      created_at: '2026-08-25T01:00:00Z',
    }
    apiMocks.getPortfolioTransactionCaptureBatches.mockResolvedValue({
      portfolio_id: '3',
      batches: [{
        batch_id: 'capture-batch-review-1',
        portfolio_id: '3',
        purpose: 'position_reconciliation',
        status: 'review_required',
        capture_count: 1,
        latest_analysis_revision: 2,
        analysis_run_status: 'succeeded',
        analysis_run_attempt: 1,
        analysis_run_started_at: '2026-08-25T01:00:10Z',
        analysis_run_completed_at: '2026-08-25T01:02:00Z',
        analysis_run_error: null,
        captures: [capture],
        latest_analysis: {
          batch_id: 'capture-batch-review-1',
          revision: 2,
          source: 'assistant',
          harness: 'deepseek-harness',
          provider: 'deepseek',
          model_name: 'deepseek-chat',
          schema_version: 'portfolio.transaction-capture-analysis.v2',
          analysis: {
            summary: 'One ETF position is visible; the settlement date still needs confirmation.',
            documents: [{ capture_id: capture.capture_id, document_kind: 'position_snapshot' }],
            candidates: [{
              candidate_id: 'position-1',
              candidate_kind: 'position_snapshot',
              account_resolution: {
                status: 'resolved',
                account_id: 'brokerage-1',
                candidate_account_ids: ['brokerage-1'],
                observed_account_hint: 'ETF brokerage account',
              },
              fields: [],
            }, {
              candidate_id: 'trade-possible-duplicate',
              candidate_kind: 'transaction',
              account_resolution: {
                status: 'resolved',
                account_id: 'options-1',
                candidate_account_ids: ['options-1'],
              },
              fields: [],
              possible_existing_transaction_ids: ['txn-0278'],
              duplicate_assessment: 'same_record',
            }],
            questions: ['Is this position as of trade date or settlement date?'],
          },
          transaction_import: {},
          preview_digest: 'f'.repeat(64),
          preview_error_count: 1,
          created_at: '2026-08-25T01:02:00Z',
        },
        ledger_status: 'unrecorded',
        recorded_transaction_ids: [],
        created_at: '2026-08-25T01:00:00Z',
        updated_at: '2026-08-25T01:02:00Z',
      }],
    })
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'From Screenshot' }))
    const assistant = screen.getByRole('dialog', { name: 'Screenshot assistant' })
    await user.click(within(assistant).getByRole('tab', { name: /History/ }))
    const detail = within(assistant).getByRole('region', { name: 'Selected screenshot batch' })
    expect(within(detail).getByText('AI notes')).toBeInTheDocument()
    expect(within(detail).getByText(/One ETF position is visible/)).toBeInTheDocument()
    expect(within(detail).getByText('Is this position as of trade date or settlement date?')).toBeInTheDocument()
    expect(within(detail).getByText('No transaction draft')).toBeInTheDocument()
    expect(within(detail).getByRole('button', { name: 'Record manually' })).toBeInTheDocument()
    expect(within(detail).getByRole('button', { name: 'Analyze again' })).toBeInTheDocument()
    expect(within(assistant).queryByRole('button', { name: 'Confirm & record' })).not.toBeInTheDocument()
  })

  it('lets a user edit an AI draft and confirm it in one action', async () => {
    const user = userEvent.setup()
    apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [{
      instrument_type: 'equity', symbol: 'PDD', catalog_provider: 'fmp', catalog_symbol: 'PDD',
      name: 'PDD Holdings', exchange_code: 'XNAS', exchange_label: 'NASDAQ', market: 'US',
      currency: 'USD', currency_verified: true, existing_instrument_id: null,
    }], catalog_errors: {} })
    apiMocks.materializePortfolioSecurity.mockResolvedValue({ ...materializedEquityInstrument,
      instrument_id: 'pdd', instrument_name: 'PDD Holdings',
      identifiers: [{ identifier_type: 'exchange_ticker', identifier_value: 'PDD', is_primary: true }],
    })
    const usdFcnAccount = {
      ...fcnAccount,
      account_id: 'fcn-usd-1',
      account_name: 'USD FCN Account',
      currency: 'USD',
      default_settlement_cash_account_id: 'cash-1',
    }
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [usdFcnAccount, cashAccount],
    })
    const capture = {
      capture_id: 'capture-fcn-1',
      portfolio_id: '3',
      original_filename: 'fcn-confirmation.png',
      media_type: 'image/png' as const,
      byte_size: 4096,
      content_sha256: 'a'.repeat(64),
      created_at: '2026-08-25T02:00:00Z',
    }
    const transactionImport = {
      source_system: 'portfolio_screenshot_assistant',
      records: [{
        external_reference: 'capture-batch-fcn-1#1',
        asset_type: 'fcn' as const,
        transaction_action: 'entry' as const,
        trade_date: '2026-08-11',
        trade_time: null,
        settlement_date: null,
        position_effective_date: null,
        entitlement_date: null,
        acquisition_date: null,
        account_id: 'fcn-usd-1',
        counterparty_account_id: null,
        settlement_cash_account_id: 'cash-1',
        instrument_id: null,
        derivative_contract_id: 'fcn-pdd-1',
        derivative_contract: {
          derivative_contract_id: 'fcn-pdd-1',
          contract_name: 'PDD FCN',
          contract_type: 'fcn' as const,
          external_reference: 'XS-PDD-1',
          terms: {
            notional: '600000',
            annual_coupon_rate_pct: '11.45',
            issue_date: '2026-08-11',
            final_observation_date: null,
            maturity_date: '2027-02-25',
            issuer: 'Goldman Sachs',
            counterparty: 'Goldman Sachs',
            underlyings: [{
              instrument_id: 'etf-1',
              initial_reference_price: '91.4',
              strike_level_pct: '80',
              knock_in_level_pct: null,
              knock_out_level_pct: '100',
              deliverable: false,
            }],
          },
        },
        quantity: '1',
        price: '600000',
        gross_amount: '600000',
        counter_amount: null,
        fx_rate: null,
        fees: null,
        fee_category: null,
        taxes: null,
        currency: 'USD',
        note: null,
      }],
    }
    const assistantAnalysis = {
      batch_id: 'capture-batch-fcn-1',
      revision: 1,
      source: 'assistant' as const,
      harness: 'deepseek-harness',
      provider: 'deepseek',
      model_name: 'deepseek-v4-flash-vision-exp',
      schema_version: 'portfolio.transaction-capture-analysis.v2',
      analysis: {
        summary: 'One PDD FCN entry was identified. Notional and barrier terms require review.',
        documents: [{ capture_id: capture.capture_id, document_kind: 'structured_product_confirmation' }],
        candidates: [{
          candidate_id: 'txn-fcn-1',
          candidate_kind: 'transaction' as const,
          account_resolution: {
            status: 'resolved' as const,
            account_id: 'fcn-usd-1',
            candidate_account_ids: ['fcn-usd-1'],
          },
          fields: [{ name: 'notional', value: '600000', status: 'ambiguous' }],
          proposed_transaction_record_index: 1,
          possible_duplicate_of: [],
          possible_existing_transaction_ids: [],
          duplicate_assessment: 'not_assessed' as const,
        }],
        questions: [
          'Confirm the FCN notional shown as 600.000.',
          'Confirm the strike and knock-out percentages.',
        ],
      },
      transaction_import: transactionImport,
      preview_digest: 'b'.repeat(64),
      preview_error_count: 0,
      created_at: '2026-08-25T02:02:00Z',
    }
    const readyBatch = {
      batch_id: 'capture-batch-fcn-1',
      portfolio_id: '3',
      purpose: 'transaction_import' as const,
      status: 'review_required' as const,
      capture_count: 1,
      latest_analysis_revision: 1,
      analysis_run_status: 'succeeded' as const,
      analysis_run_attempt: 1,
      analysis_run_started_at: '2026-08-25T02:00:10Z',
      analysis_run_completed_at: '2026-08-25T02:02:00Z',
      analysis_run_error: null,
      captures: [capture],
      latest_analysis: assistantAnalysis,
      ledger_status: 'unrecorded' as const,
      recorded_transaction_ids: [],
      created_at: '2026-08-25T02:00:00Z',
      updated_at: '2026-08-25T02:02:00Z',
    }
    apiMocks.getPortfolioTransactionCaptureBatches.mockResolvedValue({
      portfolio_id: '3',
      batches: [readyBatch],
    })

    const reviewedImport = {
      ...transactionImport,
      records: [{
        ...transactionImport.records[0],
        price: '650000',
        gross_amount: '650000',
        derivative_contract: {
          ...transactionImport.records[0].derivative_contract,
          terms: {
            ...transactionImport.records[0].derivative_contract.terms,
            notional: '650000',
            underlyings: [{ ...transactionImport.records[0].derivative_contract.terms.underlyings[0], instrument_id: 'pdd' }],
          },
        },
      }],
    }
    const humanAnalysis = {
      ...assistantAnalysis,
      revision: 2,
      source: 'human' as const,
      harness: null,
      provider: null,
      model_name: null,
      finish_reason: 'user_confirmed',
      analysis: {
        ...assistantAnalysis.analysis,
        questions: [],
      },
      transaction_import: reviewedImport,
      preview_digest: 'c'.repeat(64),
      preview_error_count: 0,
      created_at: '2026-08-25T02:05:00Z',
    }
    const humanBatch = {
      ...readyBatch,
      latest_analysis_revision: 2,
      latest_analysis: humanAnalysis,
      updated_at: '2026-08-25T02:05:00Z',
    }
    apiMocks.createPortfolioTransactionCaptureAnalysisRevision.mockResolvedValue({
      batch: humanBatch,
      analysis_revision: humanAnalysis,
      preview: {
        portfolio_id: '3',
        preview_digest: 'c'.repeat(64),
        row_count: 1,
        valid_count: 1,
        error_count: 0,
        warnings: [],
        batch_errors: [],
        rows: [],
      },
    })
    apiMocks.commitPortfolioTransactionImport.mockResolvedValue({
      portfolio_id: '3',
      preview_digest: 'c'.repeat(64),
      created_count: 1,
      transactions: [selectedTransaction],
    })

    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'From Screenshot' }))
    const assistant = screen.getByRole('dialog', { name: 'Screenshot assistant' })
    await user.click(within(assistant).getByRole('tab', { name: /History/ }))
    const detail = within(assistant).getByRole('region', { name: 'Selected screenshot batch' })
    expect(within(detail).queryByRole('button', { name: 'Confirm & record' })).not.toBeInTheDocument()

    await user.click(within(detail).getByRole('button', { name: 'Review draft' }))
    const review = within(detail).getByRole('region', { name: 'Transaction draft' })
    const confirmAndRecord = within(review).getByRole('button', { name: 'Confirm & record' })
    expect(confirmAndRecord).toBeEnabled()

    const underlyingInput = within(review).getByRole('searchbox', { name: 'Record 1 underlying 1 instrument' })
    fireEvent.change(underlyingInput, { target: { value: 'PDD' } })
    const underlyingResult = await within(review).findByRole('button', { name: /PDD.*NASDAQ/ })
    expect(apiMocks.materializePortfolioSecurity).not.toHaveBeenCalled()
    await user.click(underlyingResult)
    await waitFor(() => expect(underlyingInput).toHaveValue('PDD · PDD Holdings'))
    expect(apiMocks.commitPortfolioTransactionImport).not.toHaveBeenCalled()

    const notionalInput = within(review).getByLabelText('Record 1 FCN notional')
    const priceInput = within(review).getByLabelText('Record 1 price')
    const grossInput = within(review).getByLabelText('Record 1 gross amount')
    await user.clear(notionalInput)
    await user.type(notionalInput, '650000')
    await user.clear(priceInput)
    await user.type(priceInput, '650000')
    await user.clear(grossInput)
    await user.type(grossInput, '650000')
    expect(within(review).queryByRole('checkbox', { name: /Confirm the FCN notional/ })).not.toBeInTheDocument()
    expect(within(review).getByText('Confirm the FCN notional shown as 600.000.')).toBeInTheDocument()
    await user.click(confirmAndRecord)
    await waitFor(() => expect(
      apiMocks.createPortfolioTransactionCaptureAnalysisRevision,
    ).toHaveBeenCalledTimes(1))
    const reviewPayload = apiMocks.createPortfolioTransactionCaptureAnalysisRevision.mock.calls[0][2]
    expect(reviewPayload).toMatchObject({
      source: 'human',
      finish_reason: 'user_confirmed',
      analysis: {
        questions: [],
        candidates: [{
          account_resolution: {
            status: 'resolved',
            account_id: 'fcn-usd-1',
          },
        }],
      },
      transaction_import: reviewedImport,
    })

    await waitFor(() => expect(apiMocks.commitPortfolioTransactionImport).toHaveBeenCalledWith(
      '3',
      reviewedImport,
      'c'.repeat(64),
      'transaction-capture-capture-batch-fcn-1-revision-2',
    ))
    expect(await screen.findByText('Recorded 1 transaction from screenshots.')).toBeInTheDocument()
    expect(apiMocks.createPortfolioTransaction).not.toHaveBeenCalled()
  })

  it('uses server-derived ledger status after reload and never offers a second commit', async () => {
    const batchId = 'capture-batch-recorded-1'
    const batch = screenshotBatchFixture({
      batchId,
      source: 'human',
      ledgerStatus: 'recorded',
      recordedTransactionIds: ['txn-from-capture-1'],
      records: [{
        external_reference: `${batchId}#1`,
        asset_type: 'cash',
        transaction_action: 'deposit',
        trade_date: '2026-08-20',
        account_id: 'cash-1',
        gross_amount: '1000',
        currency: 'USD',
      }],
      candidates: [{
        candidate_id: 'cash-recorded-1',
        candidate_kind: 'transaction',
        account_resolution: {
          status: 'resolved',
          account_id: 'cash-1',
          candidate_account_ids: ['cash-1'],
        },
        fields: [{ name: 'gross_amount', value: '1000', status: 'observed' }],
        proposed_transaction_record_index: 1,
      }],
    })
    apiMocks.getPortfolioTransactionCaptureBatches.mockResolvedValue({
      portfolio_id: '3',
      batches: [batch],
    })
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'From Screenshot' }))
    const assistant = screen.getByRole('dialog', { name: 'Screenshot assistant' })
    await user.click(within(assistant).getByRole('tab', { name: /History/ }))
    const detail = within(assistant).getByRole('region', { name: 'Selected screenshot batch' })
    expect(within(detail).getByText('These screenshot transactions are in the ledger.')).toBeInTheDocument()
    expect(within(detail).getByText(/txn-from-capture-1/)).toBeInTheDocument()
    expect(within(detail).queryByRole('button', { name: 'Confirm & record' })).not.toBeInTheDocument()
    expect(within(detail).queryByRole('button', { name: /draft/i })).not.toBeInTheDocument()
    expect(apiMocks.commitPortfolioTransactionImport).not.toHaveBeenCalled()
  })

  it('reviews a multi-record FX and option batch and excludes a confirmed duplicate', async () => {
    const user = userEvent.setup()
    const hkdCashAccount = {
      ...cashAccount,
      account_id: 'cash-hkd-1',
      account_name: 'HKD Cash',
      currency: 'HKD',
    }
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [optionAccount, cashAccount, hkdCashAccount],
    })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({
      portfolio_id: '3',
      derivative_contracts: [optionContract],
    })
    const batchId = 'capture-batch-mixed-review-1'
    const records: PortfolioTransactionImportCommand[] = [{
      external_reference: `${batchId}#1`,
      asset_type: 'option',
      transaction_action: 'sell_to_close',
      trade_date: '2026-08-20',
      settlement_date: '2026-08-20',
      account_id: 'options-1',
      settlement_cash_account_id: 'cash-1',
      derivative_contract_id: 'option-call-1',
      derivative_contract: null,
      quantity: '1',
      price: '2',
      gross_amount: '200',
      currency: 'USD',
    }, {
      external_reference: `${batchId}#2`,
      asset_type: 'cash',
      transaction_action: 'fx_conversion',
      trade_date: '2026-08-20',
      account_id: 'cash-1',
      counterparty_account_id: 'cash-hkd-1',
      gross_amount: '100',
      counter_amount: '780',
      fx_rate: '7.8',
      currency: 'USD',
    }]
    const batch = screenshotBatchFixture({
      batchId,
      records,
      candidates: [{
        candidate_id: 'option-possible-duplicate',
        candidate_kind: 'transaction',
        account_resolution: {
          status: 'resolved',
          account_id: 'options-1',
          candidate_account_ids: ['options-1'],
        },
        fields: [{ name: 'contract', value: 'GETF Dec 30 Call', status: 'observed' }],
        proposed_transaction_record_index: 1,
        possible_existing_transaction_ids: ['txn-1'],
        duplicate_assessment: 'distinct_records',
      }, {
        candidate_id: 'fx-conversion-1',
        candidate_kind: 'transaction',
        account_resolution: {
          status: 'resolved',
          account_id: 'cash-1',
          candidate_account_ids: ['cash-1'],
        },
        fields: [{ name: 'fx_rate', value: '7.8', status: 'observed' }],
        proposed_transaction_record_index: 2,
      }],
    })
    apiMocks.getPortfolioTransactionCaptureBatches.mockResolvedValue({
      portfolio_id: '3',
      batches: [batch],
    })
    apiMocks.createPortfolioTransactionCaptureAnalysisRevision.mockImplementation(
      async (_portfolioId, _batchId, payload) => {
        const analysisRevision = {
          ...batch.latest_analysis!,
          revision: 2,
          source: 'human' as const,
          harness: null,
          provider: null,
          model_name: null,
          finish_reason: 'user_confirmed',
          analysis: payload.analysis,
          transaction_import: payload.transaction_import,
          preview_digest: '7'.repeat(64),
          preview_error_count: 0,
        }
        return {
          batch: {
            ...batch,
            latest_analysis_revision: 2,
            latest_analysis: analysisRevision,
          },
          analysis_revision: analysisRevision,
          preview: {
            portfolio_id: '3',
            preview_digest: '7'.repeat(64),
            row_count: 1,
            valid_count: 1,
            error_count: 0,
            warnings: [],
            batch_errors: [],
            rows: [],
          },
        }
      },
    )
    apiMocks.commitPortfolioTransactionImport.mockResolvedValue({
      portfolio_id: '3',
      preview_digest: '7'.repeat(64),
      created_count: 1,
      transactions: [selectedTransaction],
    })
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'From Screenshot' }))
    const assistant = screen.getByRole('dialog', { name: 'Screenshot assistant' })
    await user.click(within(assistant).getByRole('tab', { name: /History/ }))
    const detail = within(assistant).getByRole('region', { name: 'Selected screenshot batch' })
    await user.click(within(detail).getByRole('button', { name: 'Review draft' }))
    const review = within(detail).getByRole('region', { name: 'Transaction draft' })

    const optionAction = within(review).getByLabelText('Record 1 action')
    expect(within(optionAction).getByRole('option', { name: 'Fee' })).toBeInTheDocument()
    expect(within(optionAction).getByRole('option', { name: 'Tax' })).toBeInTheDocument()
    expect(within(optionAction).queryByRole('option', { name: 'Transfer in' })).not.toBeInTheDocument()
    expect(within(review).getByLabelText('Record 1 contract source')).toHaveValue('option-call-1')
    expect(within(review).getByText(/GETF Dec 30 Call · options-1/)).toBeInTheDocument()

    await user.selectOptions(
      within(review).getByLabelText('Record 1 duplicate resolution'),
      'same_record',
    )
    expect(within(review).getByLabelText('Record 2 counterparty account')).toHaveValue('cash-hkd-1')
    const targetAmount = within(review).getByLabelText('Record 2 target amount')
    const fxRate = within(review).getByLabelText('Record 2 FX rate')
    await user.clear(targetAmount)
    await user.type(targetAmount, '781')
    await user.clear(fxRate)
    await user.type(fxRate, '7.81')
    await user.click(within(review).getByRole('button', { name: 'Confirm & record' }))

    await waitFor(() => expect(
      apiMocks.createPortfolioTransactionCaptureAnalysisRevision,
    ).toHaveBeenCalledTimes(1))
    const submitted = apiMocks.createPortfolioTransactionCaptureAnalysisRevision.mock.calls[0][2]
    expect(submitted.transaction_import.records).toEqual([{
      ...records[1],
      external_reference: `${batchId}#1`,
      counter_amount: '781',
      fx_rate: '7.81',
    }])
    expect(submitted.analysis.candidates).toMatchObject([{
      candidate_id: 'option-possible-duplicate',
      proposed_transaction_record_index: null,
      duplicate_assessment: 'same_record',
    }, {
      candidate_id: 'fx-conversion-1',
      proposed_transaction_record_index: 1,
      account_resolution: {
        status: 'resolved',
        account_id: 'cash-1',
      },
    }])
    await waitFor(() => expect(apiMocks.commitPortfolioTransactionImport).toHaveBeenCalledWith(
      '3',
      {
        source_system: 'portfolio_screenshot_assistant',
        records: [{
          ...records[1],
          external_reference: `${batchId}#1`,
          counter_amount: '781',
          fx_rate: '7.81',
        }],
      },
      '7'.repeat(64),
      `transaction-capture-${batchId}-revision-2`,
    ))
  })

  it('shows file row and batch errors before allowing any import', async () => {
    apiMocks.previewPortfolioTransactionFile.mockResolvedValue({
      portfolio_id: '3',
      preview_digest: 'a'.repeat(64),
      headers: ['transaction_type', 'trade_date', 'account_id', 'gross_amount', 'currency'],
      row_count: 2,
      valid_count: 1,
      error_count: 2,
      warnings: [],
      batch_errors: ['Source identity already exists.'],
      rows: [
        { row_number: 2, transaction: null, errors: ['account_id: Account not found'] },
        { row_number: 3, transaction: {}, errors: [] },
      ],
    })
    const file = new File(['asset_type,transaction_action,trade_date'], 'candidate.csv', {
      type: 'text/csv',
    })
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    expect(fileInput).not.toBeNull()
    fireEvent.change(fileInput!, { target: { files: [file] } })

    const dialog = await screen.findByRole('alertdialog', {
      name: 'Review Transaction File',
    })
    expect(within(dialog).getByText('2 rows · 1 valid · 2 issues')).toBeInTheDocument()
    expect(within(dialog).getByText('Source identity already exists.')).toBeInTheDocument()
    expect(
      within(dialog).getByText('Row 2: account_id: Account not found'),
    ).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Import 1 Rows' })).toBeDisabled()
    expect(apiMocks.importPortfolioTransactionFile).not.toHaveBeenCalled()
  })

  it('imports an Excel batch only after a clean preview is confirmed', async () => {
    apiMocks.previewPortfolioTransactionFile.mockResolvedValue({
      portfolio_id: '3',
      preview_digest: 'b'.repeat(64),
      headers: ['transaction_type', 'trade_date', 'account_id', 'gross_amount', 'currency'],
      row_count: 1,
      valid_count: 1,
      error_count: 0,
      warnings: ['Source identity is incomplete.'],
      batch_errors: [],
      rows: [{ row_number: 2, transaction: {}, errors: [] }],
    })
    apiMocks.importPortfolioTransactionFile.mockResolvedValue({
      portfolio_id: '3',
      preview_digest: 'b'.repeat(64),
      created_count: 1,
      transactions: [selectedTransaction],
    })
    apiMocks.importPortfolioTransactionFile.mockRejectedValueOnce(new Error('Response lost after import'))
    const file = new File(['xlsx-bytes'], 'clean.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    fireEvent.change(fileInput!, { target: { files: [file] } })
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Review Transaction File',
    })
    const confirm = within(dialog).getByRole('button', { name: 'Import 1 Rows' })
    expect(confirm).toBeEnabled()
    expect(within(dialog).getByText('Source identity is incomplete.')).toBeInTheDocument()
    expect(apiMocks.importPortfolioTransactionFile).not.toHaveBeenCalled()

    await user.click(confirm)
    await within(dialog).findByText('Response lost after import')
    await user.click(confirm)
    await waitFor(() => expect(apiMocks.importPortfolioTransactionFile).toHaveBeenCalledTimes(2))
    expect(apiMocks.importPortfolioTransactionFile.mock.calls[0][3]).toBe(apiMocks.importPortfolioTransactionFile.mock.calls[1][3])
    await waitFor(() =>
      expect(apiMocks.importPortfolioTransactionFile).toHaveBeenCalledWith(
        '3',
        file,
        'b'.repeat(64),
        expect.stringContaining('transaction-file-import-'),
      ),
    )
    expect(await screen.findByText('Imported 1 transaction facts from clean.xlsx.')).toHaveClass('investment-studio-notice-toast')
  })

  it('discards a file preview when the active portfolio changes', async () => {
    let resolvePreview!: (value: {
      portfolio_id: string
      preview_digest: string
      headers: string[]
      row_count: number
      valid_count: number
      error_count: number
      warnings: string[]
      batch_errors: string[]
      rows: Array<{ row_number: number; transaction: object; errors: string[] }>
    }) => void
    apiMocks.previewPortfolioTransactionFile.mockReturnValueOnce(
      new Promise((resolve) => {
        resolvePreview = resolve
      }),
    )
    const file = new File(['candidate'], 'portfolio-3.csv', { type: 'text/csv' })
    const user = userEvent.setup()
    render(
      <LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={['/portfolios/3/transactions']}>
        <Routes>
          <Route
            path="/portfolios/:portfolioId/transactions"
            element={<SwitchableTransactionsPage />}
          />
        </Routes>
      </MemoryRouter></LanguageProvider>,
    )

    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    fireEvent.change(fileInput!, { target: { files: [file] } })
    await waitFor(() => {
      expect(apiMocks.previewPortfolioTransactionFile).toHaveBeenCalledWith('3', file)
    })
    await user.click(screen.getByRole('button', { name: 'Switch portfolio' }))
    await waitFor(() => {
      expect(apiMocks.getPortfolioAccounts).toHaveBeenCalledWith('4')
    })

    await act(async () => {
      resolvePreview({
        portfolio_id: '3',
        preview_digest: 'c'.repeat(64),
        headers: ['transaction_type'],
        row_count: 1,
        valid_count: 1,
        error_count: 0,
        warnings: [],
        batch_errors: [],
        rows: [{ row_number: 2, transaction: {}, errors: [] }],
      })
    })

    expect(
      screen.queryByRole('alertdialog', { name: 'Review Transaction File' }),
    ).not.toBeInTheDocument()
    expect(apiMocks.importPortfolioTransactionFile).not.toHaveBeenCalled()
  })

  it('enforces buy instrument eligibility and keeps transaction/accounting inspectors rendered', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions?transaction_id=txn-1',
      '/portfolios/:portfolioId/transactions',
    )

    const transactionInspector = await screen.findByRole('complementary', {
      name: 'Selected transaction details',
    })
    expect(within(transactionInspector).getByRole('tab', { name: 'Fact' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(within(transactionInspector).getByRole('tab', { name: 'Lots 0' })).toBeInTheDocument()
    expect(within(transactionInspector).getByRole('tab', { name: 'Postings 1' })).toBeInTheDocument()
    expect(within(transactionInspector).getByRole('tab', { name: 'History 1' })).toBeInTheDocument()
    expect(within(transactionInspector).getByText('Opening ETF purchase')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    expect(within(dialog).getByText('Accounting impact')).toBeInTheDocument()
    expect(within(dialog).queryByRole('textbox', { name: 'Currency' })).not.toBeInTheDocument()

    const securitySearch = within(dialog).getByRole('searchbox', { name: 'Security' })
    fireEvent.change(securitySearch, { target: { value: 'FUND1' } })
    expect(await within(dialog).findByText('No matching security for this account.')).toBeInTheDocument()

    fireEvent.change(securitySearch, { target: { value: 'GETF' } })
    expect(
      within(dialog).getByRole('button', { name: /GETF.*Global Equity ETF.*USD/ }),
    ).toBeInTheDocument()

    fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'Amount' }), {
      target: { value: '250' },
    })
    const review = within(dialog).getByRole('complementary', { name: 'Transaction review' })
    expect(within(review).getByText('Net Cash Effect')).toBeInTheDocument()
    expect(within(review).getByText('-$250.00')).toBeInTheDocument()
    expect(within(review).queryByRole('textbox')).not.toBeInTheDocument()

    await user.click(within(dialog).getByText('Additional details'))
    expect(within(dialog).getByRole('textbox', { name: 'Source System' })).toBeInTheDocument()
    expect(within(dialog).getByRole('textbox', { name: 'External Reference' })).toBeInTheDocument()
    expect(within(dialog).getByRole('textbox', { name: 'Note' })).toBeInTheDocument()
  })

  it('uses the shared modal shell for new entries, corrections and screenshot review while restoring launcher focus', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    const launcher = await screen.findByRole('button', { name: 'Record Transaction' })
    await waitFor(() => expect(launcher).toBeEnabled())
    await user.click(launcher)
    const entry = screen.getByRole('dialog', { name: 'Record transaction' })
    expect(entry).toHaveClass('portfolio-settings-modal', 'transaction-entry-modal')
    expect(entry.parentElement).toHaveClass('portfolio-settings-modal-backdrop', 'transaction-entry-backdrop')
    expect(entry).toHaveAttribute('aria-modal', 'true')
    const footer = entry.querySelector('footer')
    expect(footer).toHaveClass('portfolio-settings-modal-actions')
    expect(within(footer!).getByRole('button', { name: 'Cancel' })).toBeVisible()
    expect(within(footer!).getByRole('button', { name: 'Record Transaction' })).toBeVisible()
    await waitFor(() => expect(entry.contains(document.activeElement)).toBe(true))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(launcher).toHaveFocus())

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const correction = screen.getByRole('dialog', { name: 'Correct transaction' })
    expect(correction).toHaveClass('portfolio-settings-modal', 'transaction-entry-modal')
    expect(within(correction.querySelector('footer')!).getByRole('button', { name: 'Save Correction' })).toBeVisible()
    await user.click(within(correction).getByRole('button', { name: 'Close' }))

    const screenshotLauncher = screen.getByRole('button', { name: 'From Screenshot' })
    await user.click(screenshotLauncher)
    const screenshots = screen.getByRole('dialog', { name: 'Screenshot assistant' })
    expect(screenshots).toHaveClass('portfolio-settings-modal', 'transaction-capture-assistant-modal')
    expect(screenshots.parentElement).toHaveClass('portfolio-settings-modal-backdrop', 'transaction-entry-backdrop')
    expect(screenshots).not.toHaveClass('transaction-drawer')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(screenshotLauncher).toHaveFocus())
  })

  it('does not steal focus after the user starts searching in a new ticket', async () => {
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    const recordButton = await screen.findByRole('button', { name: 'Record Transaction' })
    vi.useFakeTimers()
    try {
      fireEvent.click(recordButton)
      const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
      const search = within(dialog).getByRole('searchbox', { name: 'Security' })
      act(() => search.focus())
      fireEvent.change(search, { target: { value: 'GETF' } })
      act(() => vi.runOnlyPendingTimers())
      expect(search).toHaveFocus()
      expect(search).toHaveValue('GETF')
    } finally {
      vi.useRealTimers()
    }
  })

  it.each([
    ['AAPL', materializedEquityInstrument],
    ['MAGS', materializedEtfInstrument],
  ])('selects an already registered %s without data maintenance', async (symbol, instrument) => {
    apiMocks.getPortfolioInstruments.mockResolvedValue({
      portfolio_id: '3', instruments: [etfInstrument, fundInstrument, instrument],
    })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.type(within(dialog).getByRole('searchbox', { name: 'Security' }), symbol)
    await user.click(await within(dialog).findByRole('button', { name: new RegExp(symbol + '.*USD') }))
    expect(apiMocks.getPortfolioInstruments).toHaveBeenCalledTimes(1)
    expect(apiMocks.materializePortfolioSecurity).not.toHaveBeenCalled()
  })

  it.each(['SHV', 'STIP', 'DBA', 'EMXC', 'GCC'])('finds %s in the catalog and registers only when selected', async (symbol) => {
    const candidate = { instrument_type: 'etf', symbol, catalog_provider: 'fmp', catalog_symbol: symbol,
      name: `${symbol} ETF`, exchange_code: 'ARCX', exchange_label: 'NYSE Arca', market: 'US',
      currency: 'USD', currency_verified: true, existing_instrument_id: null }
    const registered = { ...etfInstrument, instrument_id: symbol.toLowerCase(), instrument_name: candidate.name,
      identifiers: [{ identifier_type: 'exchange_ticker', identifier_value: symbol, is_primary: true }] }
    apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [candidate], catalog_errors: {} })
    apiMocks.materializePortfolioSecurity.mockResolvedValue(registered)
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    fireEvent.change(within(dialog).getByRole('searchbox', { name: 'Security' }), { target: { value: symbol } })
    const result = await within(dialog).findByRole('button', { name: new RegExp(`${symbol}.*USD`) })
    expect(apiMocks.searchPortfolioSecurities).toHaveBeenCalledWith('3', symbol)
    expect(apiMocks.materializePortfolioSecurity).not.toHaveBeenCalled()
    await user.click(result)
    await waitFor(() => expect(within(dialog).getByRole('searchbox', { name: 'Security' })).toHaveValue(`${symbol} · ${symbol} ETF`))
    expect(apiMocks.materializePortfolioSecurity).toHaveBeenCalledTimes(1)
    expect(apiMocks.createPortfolioTransaction).not.toHaveBeenCalled()
  })

  it('resolves a catalog name to an existing renamed instrument without registering again', async () => {
    apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [{ instrument_type: 'etf', symbol: 'GETF',
      catalog_provider: 'fmp', catalog_symbol: 'GETF', name: 'Provider Original Fund Name',
      exchange_code: 'ARCX', exchange_label: 'NYSE Arca', market: 'US', currency: 'USD',
      currency_verified: true, existing_instrument_id: etfInstrument.instrument_id }], catalog_errors: {} })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    const search = within(dialog).getByRole('searchbox', { name: 'Security' })
    fireEvent.change(search, { target: { value: 'Provider Original' } })
    await user.click(await within(dialog).findByRole('button', { name: /GETF.*Global Equity ETF/ }))
    expect(search).toHaveValue('GETF · Global Equity ETF')
    expect(apiMocks.materializePortfolioSecurity).not.toHaveBeenCalled()
  })

  it('hides catalog candidates with a verified currency that differs from the selected USD account', async () => {
    apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [{
      instrument_type: 'etf', symbol: 'HKETF', catalog_provider: 'fmp', catalog_symbol: 'HKETF.HK',
      name: 'Hong Kong ETF', exchange_code: 'XHKG', exchange_label: 'Hong Kong Exchange',
      market: 'HK', currency: 'HKD', currency_verified: true, existing_instrument_id: null,
    }], catalog_errors: {} })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    expect(within(dialog).getByRole('combobox', { name: 'Holding Account' })).toHaveValue(securitiesAccount.account_id)
    fireEvent.change(within(dialog).getByRole('searchbox', { name: 'Security' }), { target: { value: 'HKETF' } })

    expect(await within(dialog).findByText('No matching security for this account.')).toBeInTheDocument()
    expect(apiMocks.searchPortfolioSecurities).toHaveBeenCalledWith('3', 'HKETF')
    expect(within(dialog).queryByRole('button', { name: /HKETF.*Hong Kong ETF/ })).not.toBeInTheDocument()
    expect(apiMocks.materializePortfolioSecurity).not.toHaveBeenCalled()
  })

  it('shows an unverified catalog currency but rejects selection when registration confirms a non-USD currency', async () => {
    apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [{
      instrument_type: 'etf', symbol: 'LONETF', catalog_provider: 'fmp', catalog_symbol: 'LONETF.L',
      name: 'London ETF', exchange_code: 'XLON', exchange_label: 'London Stock Exchange',
      market: 'EU', currency: 'GBP', currency_verified: false, existing_instrument_id: null,
    }], catalog_errors: {} })
    apiMocks.materializePortfolioSecurity.mockResolvedValue({
      ...etfInstrument, instrument_id: 'etf-london', instrument_name: 'London ETF', currency: 'GBP',
      identifiers: [{ identifier_type: 'exchange_ticker', identifier_value: 'LONETF', is_primary: true }],
      latest_market_data: [],
    })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    expect(within(dialog).getByRole('combobox', { name: 'Holding Account' })).toHaveValue(securitiesAccount.account_id)
    const search = within(dialog).getByRole('searchbox', { name: 'Security' })
    fireEvent.change(search, { target: { value: 'LONETF' } })
    await user.click(await within(dialog).findByRole('button', { name: /LONETF.*Currency to be verified/ }))

    expect(await within(dialog).findByText('The security is not compatible with the selected account currency or transaction type.')).toBeInTheDocument()
    expect(search).toHaveValue('LONETF')
    expect(apiMocks.materializePortfolioSecurity).toHaveBeenCalledTimes(1)
    expect(apiMocks.createPortfolioTransaction).not.toHaveBeenCalled()
  })

  it('retries price preparation for an already registered security without usable prices', async () => {
    const registered = { ...etfInstrument, instrument_id: 'shv', instrument_name: 'Treasury ETF',
      identifiers: [
        { identifier_type: 'exchange_ticker', identifier_value: 'SHV', is_primary: true },
        { identifier_type: 'provider_symbol', identifier_value: 'fmp:SHV', is_primary: false },
      ], latest_market_data: [], coverage_state: 'unavailable' }
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [registered] })
    apiMocks.materializePortfolioSecurity.mockResolvedValue({ ...registered, latest_market_data: etfInstrument.latest_market_data, coverage_state: 'complete' })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    const search = within(dialog).getByRole('searchbox', { name: 'Security' })
    fireEvent.change(search, { target: { value: 'SHV' } })
    await user.click(await within(dialog).findByRole('button', { name: /SHV.*Treasury ETF/ }))
    await waitFor(() => expect(search).toHaveValue('SHV · Treasury ETF'))
    expect(apiMocks.materializePortfolioSecurity).toHaveBeenCalledWith('3', {
      instrument_type: 'etf', catalog_provider: 'fmp', catalog_symbol: 'SHV',
    })
    expect(apiMocks.createPortfolioTransaction).not.toHaveBeenCalled()
  })

  it('preserves the search after failed registration and permits an explicit retry', async () => {
    const candidate = { instrument_type: 'etf', symbol: 'SHV', catalog_provider: 'fmp', catalog_symbol: 'SHV',
      name: 'Treasury ETF', exchange_code: 'ARCX', exchange_label: 'NYSE Arca', market: 'US',
      currency: 'USD', currency_verified: true, existing_instrument_id: null }
    apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [candidate], catalog_errors: {} })
    apiMocks.materializePortfolioSecurity.mockRejectedValueOnce(new Error('Data preparation failed'))
    apiMocks.materializePortfolioSecurity.mockResolvedValueOnce({ ...etfInstrument, instrument_name: 'Treasury ETF',
      identifiers: [{ identifier_type: 'exchange_ticker', identifier_value: 'SHV', is_primary: true }] })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    const search = within(dialog).getByRole('searchbox', { name: 'Security' })
    fireEvent.change(search, { target: { value: 'SHV' } })
    await user.click(await within(dialog).findByRole('button', { name: /SHV.*Treasury ETF/ }))
    expect(await within(dialog).findByText('Data preparation failed')).toBeInTheDocument()
    expect(search).toHaveValue('SHV')
    await user.click(within(dialog).getByRole('button', { name: /SHV.*Treasury ETF/ }))
    await waitFor(() => expect(search).toHaveValue('SHV · Treasury ETF'))
  })

  it('does not select a late registration result after the user changes the search', async () => {
    const candidate = { instrument_type: 'etf', symbol: 'SHV', catalog_provider: 'fmp', catalog_symbol: 'SHV',
      name: 'Treasury ETF', exchange_code: 'ARCX', exchange_label: 'NYSE Arca', market: 'US',
      currency: 'USD', currency_verified: true, existing_instrument_id: null }
    apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [candidate], catalog_errors: {} })
    let finish!: (value: typeof etfInstrument) => void
    apiMocks.materializePortfolioSecurity.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    const search = within(dialog).getByRole('searchbox', { name: 'Security' })
    fireEvent.change(search, { target: { value: 'SHV' } })
    await user.click(await within(dialog).findByRole('button', { name: /SHV.*Treasury ETF/ }))
    fireEvent.change(search, { target: { value: 'GETF' } })
    await act(async () => finish(etfInstrument))
    expect(search).toHaveValue('GETF')
  })

  it.each([['FCN', 'Underlying 1'], ['Option', 'Underlying Security']])(
    'registers an external %s underlying only after selection without creating a transaction', async (kind, label) => {
      const candidate = { instrument_type: 'equity', symbol: 'PDD', catalog_provider: 'fmp', catalog_symbol: 'PDD',
        name: 'PDD Holdings', exchange_code: 'XNAS', exchange_label: 'NASDAQ', market: 'US',
        currency: 'USD', currency_verified: true, existing_instrument_id: null }
      const registered = { ...materializedEquityInstrument, instrument_id: 'pdd', instrument_name: 'PDD Holdings',
        identifiers: [{ identifier_type: 'exchange_ticker', identifier_value: 'PDD', is_primary: true }] }
      apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [candidate], catalog_errors: {} })
      apiMocks.materializePortfolioSecurity.mockResolvedValue(registered)
      const user = userEvent.setup()
      renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
      await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
      const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
      await user.click(within(dialog).getByRole('button', { name: new RegExp(`^${kind}`) }))
      const search = within(dialog).getByRole('searchbox', { name: label })
      fireEvent.change(search, { target: { value: 'PDD' } })
      const result = await within(dialog).findByRole('button', { name: /PDD.*NASDAQ/ })
      expect(apiMocks.materializePortfolioSecurity).not.toHaveBeenCalled()
      await user.click(result)
      await waitFor(() => expect(search).toHaveValue('PDD · PDD Holdings'))
      expect(apiMocks.materializePortfolioSecurity).toHaveBeenCalledTimes(1)
      expect(apiMocks.createPortfolioTransaction).not.toHaveBeenCalled()
    },
  )

  it.each([1, 2])('keeps a pending FCN selection attached to row %s when the first row is removed', async (pendingRow) => {
    const candidate = { instrument_type: 'equity', symbol: 'PDD', catalog_provider: 'fmp', catalog_symbol: 'PDD',
      name: 'PDD Holdings', exchange_code: 'XNAS', exchange_label: 'NASDAQ', market: 'US',
      currency: 'USD', currency_verified: true, existing_instrument_id: null }
    apiMocks.searchPortfolioSecurities.mockResolvedValue({ results: [candidate], catalog_errors: {} })
    let finish!: (record: typeof materializedEquityInstrument) => void
    apiMocks.materializePortfolioSecurity.mockReturnValue(new Promise(resolve => { finish = resolve }))
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.click(within(dialog).getByRole('button', { name: /^FCN/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Add Underlying' }))
    fireEvent.change(within(dialog).getByRole('searchbox', { name: `Underlying ${pendingRow}` }), { target: { value: 'PDD' } })
    await user.click(await within(dialog).findByRole('button', { name: /PDD.*NASDAQ/ }))
    await user.click(within(dialog).getAllByRole('button', { name: 'Remove' })[0])
    await act(async () => { finish({ ...materializedEquityInstrument, instrument_id: 'pdd', instrument_name: 'PDD Holdings',
      identifiers: [{ identifier_type: 'exchange_ticker', identifier_value: 'PDD', is_primary: true }] }) })
    expect(within(dialog).getByRole('searchbox', { name: 'Underlying 1' })).toHaveValue(pendingRow === 1 ? '' : 'PDD · PDD Holdings')
    expect(within(dialog).queryByRole('searchbox', { name: 'Underlying 2' })).not.toBeInTheDocument()
    expect(apiMocks.createPortfolioTransaction).not.toHaveBeenCalled()
  })

  it('uses one entry-type menu and keeps accounts and derivative actions contextual', async () => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [optionAccount, fcnAccount, securitiesAccount, cashAccount, fundCashAccount],
    })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({
      portfolio_id: '3',
      derivative_contracts: [optionContract],
    })
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    const entryType = within(dialog).getByRole('group', { name: 'Entry Type' })
    for (const label of ['Security', 'FCN', 'Option', 'Cash & Operations']) {
      expect(within(entryType).getByRole('button', { name: new RegExp(`^${label}`) })).toBeInTheDocument()
    }
    const account = within(dialog).getByRole('combobox', { name: 'Holding Account' })
    await waitFor(() => expect(account).toHaveValue(securitiesAccount.account_id))
    expect(within(account).queryByRole('option', { name: 'Options Account · USD' })).not.toBeInTheDocument()

    const securityAction = within(dialog).getByRole('combobox', { name: 'Action' })
    expect(
      within(securityAction).getByRole('option', { name: 'Buy' }),
    ).toBeInTheDocument()
    expect(
      within(securityAction).queryByRole('option', { name: 'Buy to Open Call' }),
    ).not.toBeInTheDocument()

    await user.click(within(entryType).getByRole('button', { name: /^FCN/ }))
    await waitFor(() => expect(account).toHaveValue(fcnAccount.account_id))
    expect(within(account).getByRole('option', { name: 'FCN CNY Account · CNY' })).toBeInTheDocument()
    expect(within(account).queryByRole('option', { name: 'Options Account · USD' })).not.toBeInTheDocument()
    await waitFor(() =>
      expect(within(dialog).getByRole('combobox', { name: 'Settlement Cash Account' })).toHaveValue(
        fundCashAccount.account_id,
      ),
    )

    await user.click(within(entryType).getByRole('button', { name: /^Option/ }))
    await waitFor(() => expect(account).toHaveValue(optionAccount.account_id))
    expect(within(account).getByRole('option', { name: 'Options Account · USD' })).toBeInTheDocument()
    expect(within(account).queryByRole('option', { name: 'ETF Brokerage · USD' })).not.toBeInTheDocument()
    await waitFor(() =>
      expect(within(dialog).getByRole('combobox', { name: 'Settlement Cash Account' })).toHaveValue(
        cashAccount.account_id,
      ),
    )

    const newContractAction = within(dialog).getByRole('combobox', { name: 'Action' })
    const optionType = within(dialog).getByRole('combobox', { name: 'Option Type' })
    expect(
      optionType.compareDocumentPosition(newContractAction) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(within(newContractAction).getByRole('option', { name: 'Buy to Open Call' })).toBeInTheDocument()
    expect(within(newContractAction).getByRole('option', { name: 'Sell to Open Call' })).toBeInTheDocument()
    expect(within(newContractAction).queryByRole('option', { name: 'Sell to Close Call' })).not.toBeInTheDocument()

    await user.selectOptions(optionType, 'put')
    expect(within(newContractAction).getByRole('option', { name: 'Buy to Open Put' })).toBeInTheDocument()
    expect(within(newContractAction).getByRole('option', { name: 'Sell to Open Put' })).toBeInTheDocument()

    await user.selectOptions(
      within(dialog).getByRole('combobox', { name: 'Option Contract' }),
      optionContract.derivative_contract_id,
    )
    const existingContractAction = within(dialog).getByRole('combobox', { name: 'Action' })
    for (const label of ['Buy to Open Call', 'Sell to Close Call', 'Sell to Open Call', 'Buy to Close Call']) {
      expect(within(existingContractAction).getByRole('option', { name: label })).toBeInTheDocument()
    }
    expect(
      within(existingContractAction).queryByRole('option', { name: 'Deposit' }),
    ).not.toBeInTheDocument()
  })

  it('records an existing written option at inception without a cash settlement leg', async () => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({ portfolio_id: '3', accounts: [optionAccount, securitiesAccount, cashAccount] })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({ portfolio_id: '3', derivative_contracts: [optionContract] })
    apiMocks.createPortfolioTransaction.mockResolvedValue({ ...selectedTransaction, transaction_id: 'opening-written', transaction_type: 'option_opening_balance' })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.click(within(dialog).getByRole('button', { name: /^Option/ }))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Option Contract' }), optionContract.derivative_contract_id)
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Action' }), 'opening_written')
    expect(within(dialog).getByLabelText('Trade Date')).toHaveValue('2026-01-02')
    expect(within(dialog).getByLabelText('Trade Date')).toBeDisabled()
    expect(within(dialog).queryByRole('combobox', { name: 'Settlement Cash Account' })).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Acquisition Date'), { target: { value: '2025-12-15' } })
    await user.type(within(dialog).getByRole('spinbutton', { name: /^Contracts/ }), '2')
    await user.type(within(dialog).getByRole('spinbutton', { name: 'Opening Premium Liability' }), '1000')
    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))
    await waitFor(() => expect(apiMocks.createPortfolioTransaction).toHaveBeenCalledWith('3', expect.objectContaining({
      transaction_type: 'option_opening_balance', trade_date: '2026-01-02', settlement_date: '2026-01-02',
      acquisition_date: '2025-12-15', derivative_contract_id: optionContract.derivative_contract_id,
      quantity: 2, gross_amount: 1000, settlement_cash_account_id: null,
    }), expect.any(String)))
  })

  it('limits known cash-settled contracts to compatible outcomes', async () => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({ portfolio_id: '3', accounts: [optionAccount, securitiesAccount, cashAccount] })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({ portfolio_id: '3', derivative_contracts: [
      { ...optionContract, terms: { ...optionContract.terms, settlement_type: 'cash', exercise_style: 'european' } },
    ] })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.click(within(dialog).getByRole('button', { name: /^Option/ }))
    expect(within(dialog).getByRole('combobox', { name: 'Contract Settlement' })).toHaveValue('')
    expect(within(dialog).getByRole('combobox', { name: 'Exercise Style' })).toHaveValue('')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Option Contract' }), optionContract.derivative_contract_id)
    const actions = within(dialog).getByRole('combobox', { name: 'Action' })
    expect(within(actions).queryByRole('option', { name: 'Exercise Long Call' })).not.toBeInTheDocument()
    expect(within(actions).queryByRole('option', { name: 'Assign Written Call' })).not.toBeInTheDocument()
    expect(within(actions).getByRole('option', { name: 'Cash-Settle Long Call' })).toBeInTheDocument()
  })

  it.each([false, true])('records an atomic option delivery with confirmed stock short = %s', async (allowShort) => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [optionAccount, securitiesAccount, cashAccount],
    })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({
      portfolio_id: '3',
      derivative_contracts: [optionContract],
    })
    apiMocks.createPortfolioOptionOutcome.mockResolvedValue({
      portfolio_id: '3',
      transactions: [
        {
          ...selectedTransaction,
          transaction_id: 'txn-option-exercise',
          transaction_type: 'maturity_redemption',
          lifecycle_event_type: 'option_long_exercise',
          asset_domain: 'derivative',
          asset_subtype: 'option',
          account: optionAccount,
          settlement_cash_account: null,
          instrument_id: null,
          instrument_ref: null,
          derivative_contract_id: optionContract.derivative_contract_id,
          derivative_contract: optionContract,
          quantity: 1,
          price: null,
          gross_amount: 0,
          net_cash_effect: 0,
        },
        {
          ...selectedTransaction,
          transaction_id: 'txn-stock-delivery',
          account: securitiesAccount,
          settlement_cash_account: cashAccount,
          instrument_id: 'etf-1',
          quantity: 100,
          price: 30,
          gross_amount: 3000,
        },
      ],
      option_delivery_link: {
        portfolio_id: '3',
        option_transaction_id: 'txn-option-exercise',
        stock_transaction_id: 'txn-stock-delivery',
        underlying_instrument_id: 'etf-1',
        created_at: '2026-09-02T00:00:00Z',
      },
    })
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.click(within(dialog).getByRole('button', { name: /^Option/ }))
    await user.selectOptions(
      within(dialog).getByRole('combobox', { name: 'Option Contract' }),
      optionContract.derivative_contract_id,
    )
    const action = within(dialog).getByRole('combobox', { name: 'Action' })
    expect(within(action).getByRole('option', { name: 'Exercise Long Call' })).toBeInTheDocument()
    expect(within(action).getByRole('option', { name: 'Assign Written Call' })).toBeInTheDocument()
    await user.selectOptions(action, allowShort ? 'assign_written' : 'exercise_long')
    if (allowShort) await user.click(within(dialog).getByRole('checkbox', { name: 'Broker confirmed a short stock position on delivery' }))

    expect(within(dialog).getByRole('combobox', { name: 'Security Account' })).toHaveValue(
      securitiesAccount.account_id,
    )
    expect(
      within(dialog).getByRole('combobox', { name: 'Settlement Cash Account' }),
    ).toHaveValue(cashAccount.account_id)
    expect(within(dialog).queryByRole('spinbutton', { name: /Amount/ })).not.toBeInTheDocument()
    await user.type(within(dialog).getByRole('spinbutton', { name: 'Contracts' }), '1')
    fireEvent.change(within(dialog).getByLabelText('Exercise / Assignment Date'), {
      target: { value: '2026-09-15' },
    })
    fireEvent.change(within(dialog).getByLabelText('Settlement Date'), {
      target: { value: '2026-09-17' },
    })
    await user.click(within(dialog).getByRole('button', { name: allowShort ? 'Record Assignment' : 'Record Exercise' }))

    await waitFor(() =>
      expect(apiMocks.createPortfolioOptionOutcome).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({
          derivative_contract_id: optionContract.derivative_contract_id,
          side: allowShort ? 'written' : 'long',
          ...(allowShort ? { allow_stock_short: true } : {}),
          outcome: 'physical',
          quantity: 1,
          event_date: '2026-09-15',
          settlement_date: '2026-09-17',
          stock_account_id: securitiesAccount.account_id,
          settlement_cash_account_id: cashAccount.account_id,
        }),
        expect.stringMatching(/^transaction-option-outcome-/),
      ),
    )
    expect(apiMocks.createPortfolioTransaction).not.toHaveBeenCalled()
  })

  it('retries the same unresolved cash submission with the same request identity', async () => {
    apiMocks.createPortfolioTransaction.mockRejectedValue(new Error('Simulated response lost'))
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.click(within(dialog).getByRole('button', { name: /^Cash & Operations/ }))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Action' }), 'deposit')
    fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'Amount' }), { target: { value: '1000' } })
    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))
    await within(dialog).findByText('Simulated response lost')
    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))
    await waitFor(() => expect(apiMocks.createPortfolioTransaction).toHaveBeenCalledTimes(2))
    const [first, second] = apiMocks.createPortfolioTransaction.mock.calls
    expect(first[1]).toEqual(second[1])
    expect(second[2]).toBe(first[2])
    fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'Amount' }), { target: { value: '2000' } })
    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))
    await waitFor(() => expect(apiMocks.createPortfolioTransaction).toHaveBeenCalledTimes(3))
    expect(apiMocks.createPortfolioTransaction.mock.calls[2][2]).not.toBe(first[2])
  })

  it('preserves selected lots and fee category entered for physical delivery', async () => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({ portfolio_id: '3', accounts: [{ ...optionAccount, cost_basis_method: 'fifo' }, securitiesAccount, cashAccount] })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({ portfolio_id: '3', derivative_contracts: [optionContract] })
    apiMocks.createPortfolioOptionOutcome.mockRejectedValue(new Error('Stop after request inspection'))
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.click(within(dialog).getByRole('button', { name: /^Option/ }))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Option Contract' }), optionContract.derivative_contract_id)
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Action' }), 'exercise_long')
    fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'Contracts' }), { target: { value: '1' } })
    fireEvent.change(within(dialog).getByLabelText('Exercise / Assignment Date'), { target: { value: '2026-09-15' } })
    fireEvent.change(within(dialog).getByLabelText('Trade Time (optional)'), { target: { value: '16:00' } })
    fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'Fee' }), { target: { value: '3' } })
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Fee category' }), 'transaction_cost')
    await user.click(within(dialog).getByText('Additional details'))
    await user.click(within(dialog).getByRole('button', { name: '添加指定批次' }))
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Lot 1 opening transaction' }), { target: { value: 'txn-later-lot' } })
    fireEvent.change(within(dialog).getByRole('spinbutton', { name: '处置数量' }), { target: { value: '1' } })
    await user.click(within(dialog).getByRole('button', { name: 'Record Exercise' }))
    await waitFor(() => expect(apiMocks.createPortfolioOptionOutcome).toHaveBeenCalledTimes(1))
    const payload = apiMocks.createPortfolioOptionOutcome.mock.calls[0][1]
    expect(payload.trade_time).toBe('16:00')
    expect({ lot_selections: payload.lot_selections, fee_category: payload.fee_category }).toEqual({
      lot_selections: [{ opening_transaction_id: 'txn-later-lot', quantity: '1' }], fee_category: 'transaction_cost',
    })
  })

  it.each([false, true])('exposes missing delivery fields in an AI physical outcome (already physical: %s)', async (initiallyPhysical) => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({ portfolio_id: '3', accounts: [optionAccount, securitiesAccount, cashAccount] })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({ portfolio_id: '3', derivative_contracts: [optionContract] })
    const batch = screenshotBatchFixture({
      batchId: 'audit-option-review', records: [{
        external_reference: 'audit-option-review#1', asset_type: 'option', transaction_action: initiallyPhysical ? 'physical_long' : 'cash_settle_long',
        trade_date: '2026-09-15', account_id: optionAccount.account_id, settlement_cash_account_id: cashAccount.account_id,
        derivative_contract_id: optionContract.derivative_contract_id, quantity: '1', gross_amount: '300', currency: 'USD',
      }], candidates: [],
    })
    apiMocks.getPortfolioTransactionCaptureBatches.mockResolvedValue({ portfolio_id: '3', batches: [batch] })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'From Screenshot' }))
    const assistant = screen.getByRole('dialog', { name: 'Screenshot assistant' })
    await user.click(within(assistant).getByRole('tab', { name: /History/ }))
    const detail = within(assistant).getByRole('region', { name: 'Selected screenshot batch' })
    await user.click(within(detail).getByRole('button', { name: 'Review draft' }))
    const action = await screen.findByRole('combobox', { name: 'Record 1 action' })
    if (!initiallyPhysical) await user.selectOptions(action, 'physical_long')
    expect(screen.queryByText('交付证券账户')).not.toBeNull()
  })

  it('records an FCN delivery with HKD acquisition tax, USD residual cash and a final coupon in one request', async () => {
    const usdFcnAccount = { ...fcnAccount, account_id: 'fcn-usd', currency: 'USD', default_settlement_cash_account_id: cashAccount.account_id }
    const hkdStockAccount = { ...securitiesAccount, account_id: 'stock-hkd', account_name: 'HK securities', currency: 'HKD' }
    const hkdCashAccount = { ...cashAccount, account_id: 'cash-hkd', account_name: 'HKD cash', currency: 'HKD' }
    const fcn = { derivative_contract_id: 'fcn-smic', portfolio_id: '3', account_id: 'fcn-usd', currency: 'USD', contract_name: 'SMIC FCN', contract_type: 'fcn', terms: {
      notional: 500000, annual_coupon_rate_pct: 8, issue_date: '2026-06-01', maturity_date: '2026-09-01', final_observation_date: '2026-08-31', issuer: 'Bank', counterparty: 'Broker',
      underlyings: [{ instrument_id: etfInstrument.instrument_id, initial_reference_price: 1000, strike_level_pct: 80, deliverable: true }],
    } }
    apiMocks.getPortfolioAccounts.mockResolvedValue({ portfolio_id: '3', accounts: [usdFcnAccount, hkdStockAccount, cashAccount, hkdCashAccount] })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({ portfolio_id: '3', derivative_contracts: [fcn] })
    apiMocks.getPortfolioTransactions.mockResolvedValue({ transactions: [{ ...selectedTransaction, transaction_id: 'coupon-old', derivative_contract_id: fcn.derivative_contract_id, transaction_type: 'coupon', trade_date: '2026-07-01', entitlement_date: '2026-07-01', gross_amount: 3333.33 }] })
    apiMocks.createPortfolioTransaction.mockRejectedValue(new Error('Stop after reviewed request'))
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.click(within(dialog).getByRole('button', { name: /^FCN/ }))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'FCN Contract' }), fcn.derivative_contract_id)
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Action' }), 'knock_in_close')
    await waitFor(() => expect(apiMocks.getPortfolioTransactions).toHaveBeenCalledWith('3', { position_reference_id: fcn.derivative_contract_id }))
    expect(await within(dialog).findByText(/2026-07-01.*3,333.33/)).toBeVisible()
    const addDelivery = within(dialog).getByRole('button', { name: 'Add delivered security' })
    expect(addDelivery.closest('details')).toBeNull()
    await user.click(addDelivery)
    await user.selectOptions(within(dialog).getByLabelText('Delivery 1 receiving account'), hkdStockAccount.account_id)
    fireEvent.change(within(dialog).getByLabelText('Delivered shares'), { target: { value: '4875' } })
    fireEvent.change(within(dialog).getByLabelText('Total confirmed fair value (HKD)'), { target: { value: '2925000' } })
    fireEvent.change(within(dialog).getByLabelText('Value translation FX (USD/HKD)'), { target: { value: '0.1282' } })
    fireEvent.change(within(dialog).getByLabelText('Share receipt date'), { target: { value: '2026-09-03' } })
    await user.click(within(dialog).getByText('Stock acquisition fees and taxes'))
    await user.selectOptions(within(dialog).getByLabelText('Stock-charge cash account'), hkdCashAccount.account_id)
    fireEvent.change(within(dialog).getByLabelText('Stock acquisition taxes (HKD)'), { target: { value: '3000' } })
    fireEvent.change(within(dialog).getByLabelText('Actual residual cash'), { target: { value: '19.23' } })
    fireEvent.change(within(dialog).getByLabelText('Contracts'), { target: { value: '1' } })
    await user.click(within(dialog).getByRole('button', { name: 'Add settlement cashflow' }))
    await user.selectOptions(within(dialog).getByLabelText('Cashflow 1 account'), cashAccount.account_id)
    fireEvent.change(within(dialog).getByLabelText('Cashflow 1 amount (USD)'), { target: { value: '3333.33' } })
    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))
    await waitFor(() => expect(apiMocks.createPortfolioTransaction).toHaveBeenCalledWith('3', expect.objectContaining({
      gross_amount: 19.23, settlement_cash_account_id: cashAccount.account_id,
      asset_deliveries: [expect.objectContaining({ quantity: '4875', fair_value: '2925000', currency: 'HKD', fx_rate_to_contract: '0.1282', delivery_date: '2026-09-03', taxes: '3000', settlement_cash_account_id: hkdCashAccount.account_id })],
      settlement_cashflows: [{ kind: 'coupon', cash_account_id: cashAccount.account_id, currency: 'USD', amount: '3333.33' }],
    }), expect.any(String)))
    expect(apiMocks.createPortfolioOptionOutcome).not.toHaveBeenCalled()
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Action' }), 'knock_out_close')
    expect(within(dialog).queryByRole('button', { name: 'Add delivered security' })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Add settlement cashflow' })).toBeVisible()
    fireEvent.change(within(dialog).getByLabelText('Redemption Amount'), { target: { value: '500000' } })
    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))
    await waitFor(() => expect(apiMocks.createPortfolioTransaction).toHaveBeenLastCalledWith('3', expect.objectContaining({
      lifecycle_event_type: 'fcn_knock_out', gross_amount: 500000, asset_deliveries: [],
      settlement_cashflows: [{ kind: 'coupon', cash_account_id: cashAccount.account_id, currency: 'USD', amount: '3333.33' }],
    }), expect.any(String)))
  }, 10_000) // Two complete settlement submissions share this rendered-page test.

  it('lets the reviewer correct FCN delivery accounts, instruments and selected lots before submission', async () => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({ portfolio_id: '3', accounts: [fcnAccount, securitiesAccount, fundSecuritiesAccount, cashAccount] })
    apiMocks.createPortfolioTransactionCaptureAnalysisRevision.mockRejectedValue(new Error('Stop after reviewed request'))
    const batch = screenshotBatchFixture({ batchId: 'review-fcn-delivery', candidates: [], records: [{
      external_reference: 'review-fcn-delivery#1', asset_type: 'fcn', transaction_action: 'knock_in_close',
      trade_date: '2026-09-01', account_id: fcnAccount.account_id, derivative_contract_id: 'fcn-review',
      quantity: '1', gross_amount: '0', currency: 'CNY',
      asset_deliveries: [{ account_id: fundSecuritiesAccount.account_id, instrument_id: etfInstrument.instrument_id, quantity: '100', fair_value: '7500', currency: 'USD', fx_rate_to_contract: '7' }],
      settlement_cashflows: [{ kind: 'tax', cash_account_id: cashAccount.account_id, currency: 'USD', amount: '15', recognition_date: '2026-09-01', settlement_date: '2026-09-02' }],
      lot_selections: [{ opening_transaction_id: 'wrong-lot', quantity: '1' }],
    }] })
    apiMocks.getPortfolioTransactionCaptureBatches.mockResolvedValue({ portfolio_id: '3', batches: [batch] })
    const user = userEvent.setup()
    renderPortfolioPage(<TransactionsPage />, '/portfolios/3/transactions', '/portfolios/:portfolioId/transactions')
    await user.click(await screen.findByRole('button', { name: 'From Screenshot' }))
    const assistant = screen.getByRole('dialog', { name: 'Screenshot assistant' })
    await user.click(within(assistant).getByRole('tab', { name: /History/ }))
    await user.click(within(assistant).getByRole('button', { name: 'Review draft' }))
    await user.selectOptions(await screen.findByLabelText('Delivery 1 receiving account'), securitiesAccount.account_id)
    const instrument = screen.getByRole('searchbox', { name: 'Delivery 1 security' })
    await user.clear(instrument)
    await user.type(instrument, 'AETF')
    await user.click(await screen.findByRole('button', { name: /AETF.*Alternate Equity ETF/ }))
    fireEvent.change(screen.getByLabelText('批次 1 开仓记录'), { target: { value: 'reviewed-lot' } })
    await user.click(screen.getByRole('button', { name: 'Add delivered security' }))
    expect(screen.getByLabelText('Delivery 2 receiving account')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove delivery 2' }))
    await user.click(within(assistant).getByRole('button', { name: 'Confirm & record' }))
    await waitFor(() => expect(apiMocks.createPortfolioTransactionCaptureAnalysisRevision).toHaveBeenCalledTimes(1))
    const record = apiMocks.createPortfolioTransactionCaptureAnalysisRevision.mock.calls[0][2].transaction_import.records[0]
    expect(record.asset_deliveries).toEqual([{ account_id: securitiesAccount.account_id, instrument_id: alternateEtfInstrument.instrument_id, quantity: '100', fair_value: '7500', currency: 'USD', fx_rate_to_contract: '7' }])
    expect(record.lot_selections).toEqual([{ opening_transaction_id: 'reviewed-lot', quantity: '1' }])
    expect(record.settlement_cashflows).toEqual([{ kind: 'tax', cash_account_id: cashAccount.account_id, currency: 'USD', amount: '15', recognition_date: '2026-09-01', settlement_date: '2026-09-02' }])
  })

  it('shows a linked option outcome and stock delivery as one ledger activity', async () => {
    const optionExerciseTransaction = {
      ...selectedTransaction,
      transaction_id: 'txn-option-exercise',
      transaction_type: 'maturity_redemption',
      lifecycle_event_type: 'option_long_exercise',
      asset_domain: 'derivative',
      asset_subtype: 'option',
      account: optionAccount,
      settlement_cash_account: null,
      instrument_id: null,
      instrument_ref: null,
      derivative_contract_id: optionContract.derivative_contract_id,
      derivative_contract: optionContract,
      quantity: 1,
      price: null,
      gross_amount: 0,
      net_cash_effect: 0,
    } satisfies PortfolioTransactionRecord
    const stockDeliveryTransaction = {
      ...selectedTransaction,
      transaction_id: 'txn-stock-delivery',
      transaction_type: 'buy',
      lifecycle_event_type: null,
      asset_domain: 'security',
      asset_subtype: 'etf',
      account: securitiesAccount,
      settlement_cash_account: cashAccount,
      instrument_id: 'etf-1',
      instrument_ref: etfInstrument,
      derivative_contract_id: null,
      derivative_contract: null,
      quantity: 100,
      price: 30,
      gross_amount: 3000,
      net_cash_effect: -3001,
    } satisfies PortfolioTransactionRecord
    apiMocks.getPortfolioTransactionsWorkspace.mockResolvedValue({
      portfolio_id: '3',
      base_currency: 'USD',
      portfolio_inception_date: '2026-01-02',
      summary: {
        total_transactions: 2,
        security_transactions: 1,
        derivative_transactions: 1,
        fcn_transactions: 0,
        option_transactions: 1,
        cash_transactions: 0,
        external_cash_flows: 0,
        opening_balance_records: 0,
      },
      derivation_boundary: {
        ledger_postings: 'fixture',
        positions: 'fixture',
        position_lots: 'fixture',
        holdings: 'fixture',
        snapshot: 'fixture',
      },
      selected_transaction_id: optionExerciseTransaction.transaction_id,
      position_reference_ids: [optionContract.derivative_contract_id, 'etf-1'],
      delete_scope_row_versions: {
        [optionExerciseTransaction.transaction_id]: optionExerciseTransaction.row_version,
        [stockDeliveryTransaction.transaction_id]: stockDeliveryTransaction.row_version,
      },
      transactions: [optionExerciseTransaction, stockDeliveryTransaction],
      selected_transaction: optionExerciseTransaction,
      accounting_impact: null,
      cash_fx_impacts: [],
      ledger_summary: { posting_count: 0, cash_posting_count: 0, position_posting_count: 0 },
      ledger_postings: [],
      related_position_lot_summary: {
        position_lot_count: 0,
        open_position_lot_count: 0,
        closed_position_lot_count: 0,
        realized_pnl: 0,
      },
      related_position_lots: [],
      related_option_obligations: [],
      change_log_summary: { change_count: 0 },
      change_log: [],
    })
    apiMocks.getPortfolioOptionDeliveryLinks.mockResolvedValue({
      portfolio_id: '3',
      links: [
        {
          portfolio_id: '3',
          option_transaction_id: optionExerciseTransaction.transaction_id,
          stock_transaction_id: stockDeliveryTransaction.transaction_id,
          underlying_instrument_id: 'etf-1',
          created_at: '2026-09-02T00:00:00Z',
        },
      ],
    })

    renderPortfolioPage(
      <TransactionsPage />,
      `/portfolios/3/transactions?transaction_id=${optionExerciseTransaction.transaction_id}`,
      '/portfolios/:portfolioId/transactions',
    )

    const table = await screen.findByRole('table')
    await waitFor(() => expect(table.querySelectorAll('tbody tr')).toHaveLength(1))
    expect(within(table).getByText('Stock delivery · Buy · GETF')).toBeInTheDocument()
    expect(within(table).getByText(optionExerciseTransaction.transaction_id)).toBeInTheDocument()
    expect(within(table).queryByText(stockDeliveryTransaction.transaction_id)).not.toBeInTheDocument()
    const inspector = screen.getByRole('complementary', { name: 'Selected transaction details' })
    expect(within(inspector).getByRole('button', { name: 'Delete' })).toBeInTheDocument()
    expect(within(inspector).getByRole('button', { name: 'Edit' })).toBeDisabled()
  })

  it('records cash fees without an asset entitlement date', async () => {
    apiMocks.createPortfolioTransaction.mockResolvedValue({
      ...selectedTransaction,
      transaction_id: 'txn-cash-fee',
      transaction_type: 'fee',
      asset_domain: 'cash',
      asset_subtype: null,
      account: cashAccount,
      settlement_cash_account: null,
      instrument_id: null,
      instrument_ref: null,
      gross_amount: 10,
      entitlement_date: null,
    })
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.click(
      within(dialog).getByRole('button', { name: /^Cash & Operations/ }),
    )
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Action' }), 'fee')

    expect(within(dialog).queryByLabelText('Entitlement Date')).not.toBeInTheDocument()
    await user.type(within(dialog).getByRole('spinbutton', { name: 'Amount' }), '10')
    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))

    await waitFor(() =>
      expect(apiMocks.createPortfolioTransaction).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({
          transaction_type: 'fee',
          account_id: cashAccount.account_id,
          instrument_id: null,
          derivative_contract_id: null,
          entitlement_date: null,
          gross_amount: 10,
        }),
        expect.stringMatching(/^transaction-create-/),
      ),
    )
  })

  it('accepts a zero-cost security opening only on portfolio inception', async () => {
    apiMocks.createPortfolioTransaction.mockResolvedValue({
      ...selectedTransaction,
      transaction_id: 'txn-zero-opening',
      transaction_type: 'opening_balance',
      trade_date: '2026-01-02',
      settlement_date: '2026-01-02',
      gross_amount: 0,
      quantity: 1,
    })
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await user.selectOptions(
      within(dialog).getByRole('combobox', { name: 'Action' }),
      'opening_balance',
    )
    expect(
      within(dialog).getByText('Opening balances are fixed to portfolio inception 2026-01-02.'),
    ).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Trade Date')).toBeDisabled()
    expect(within(dialog).getByLabelText('Settlement Date')).toBeDisabled()

    const securitySearch = within(dialog).getByRole('searchbox', { name: 'Security' })
    await user.type(securitySearch, 'GETF')
    await user.click(
      await within(dialog).findByRole('button', { name: /GETF.*Global Equity ETF.*USD/ }),
    )
    await user.type(within(dialog).getByRole('spinbutton', { name: /^Shares/ }), '1')
    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))

    await waitFor(() =>
      expect(apiMocks.createPortfolioTransaction).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({
          transaction_type: 'opening_balance',
          trade_date: '2026-01-02',
          settlement_date: '2026-01-02',
          instrument_id: 'etf-1',
          quantity: 1,
          gross_amount: 0,
        }),
        expect.stringMatching(/^transaction-create-/),
      ),
    )
  })

  it('carries the selected fact row version into a destructive delete', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions?transaction_id=txn-1',
      '/portfolios/:portfolioId/transactions',
    )

    const inspector = await screen.findByRole('complementary', {
      name: 'Selected transaction details',
    })
    await user.click(within(inspector).getByRole('button', { name: 'Delete' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Delete Transaction' })
    await user.type(within(dialog).getByRole('textbox'), 'txn-1')
    await user.click(within(dialog).getByRole('button', { name: 'Delete Transaction' }))

    await waitFor(() =>
      expect(apiMocks.deletePortfolioTransaction).toHaveBeenCalledWith('3', 'txn-1', {
        'txn-1': 3,
      }),
    )
  })

  it('clears prior economics when the selected instrument changes', async () => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [securitiesAccount, cashAccount],
    })
    apiMocks.getPortfolioTransactionExecutionQuote.mockResolvedValue({
      portfolio_id: '3',
      instrument_id: 'etf-2',
      requested_as_of_date: '2026-07-15',
      selection_role: null,
      value: null,
      quote_date: null,
      quote_basis: null,
      metric_family: null,
      currency: 'USD',
      provider: null,
      status: 'unavailable',
      stale: false,
      price_unit: null,
      price_scale: null,
      unavailable_reason: 'no_eligible_execution_quote',
    })

    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions?transaction_id=txn-1',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await waitFor(() =>
      expect(within(dialog).getByRole('combobox', { name: 'Holding Account' })).toHaveValue(
        'brokerage-1',
      ),
    )
    const securitySearch = within(dialog).getByRole('searchbox', { name: 'Security' })
    fireEvent.change(securitySearch, { target: { value: 'GETF' } })
    await user.click(
      await within(dialog).findByRole(
        'button',
        { name: /GETF.*Global Equity ETF.*USD/ },
        { timeout: 3_000 },
      ),
    )
    const quantityInput = within(dialog).getByRole('spinbutton', { name: /^Shares/ })
    const quoteInput = within(dialog).getByRole('spinbutton', { name: /^Execution Price/ })
    const amountInput = within(dialog).getByRole('spinbutton', { name: 'Amount' })
    await user.type(quantityInput, '1000')
    await user.type(quoteInput, '98.5')
    expect(amountInput).toHaveValue(98_500)

    fireEvent.change(securitySearch, { target: { value: 'AETF' } })
    await user.click(
      await within(dialog).findByRole(
        'button',
        { name: /AETF.*Alternate Equity ETF.*USD/ },
        { timeout: 3_000 },
      ),
    )

    expect(quantityInput).toHaveValue(null)
    expect(quoteInput).toHaveValue(null)
    expect(amountInput).toHaveValue(null)
  })

  it('keeps confirmed fund amount and shares authoritative and derives the unit price', async () => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [fundSecuritiesAccount, fundCashAccount],
    })
    apiMocks.getPortfolioInstruments.mockResolvedValue({
      portfolio_id: '3',
      instruments: [fundInstrument],
    })
    apiMocks.getPortfolioTransactionExecutionQuote.mockResolvedValue({
      portfolio_id: '3',
      instrument_id: 'fund-1',
      requested_as_of_date: '2026-07-15',
      selection_role: 'trading',
      value: 1.2,
      quote_date: '2026-07-15',
      quote_basis: 'official_nav',
      metric_family: 'nav',
      currency: 'CNY',
      provider: 'fixture',
      status: 'complete',
      stale: false,
      price_unit: 'per_unit',
      price_scale: 1,
      unavailable_reason: null,
    })
    apiMocks.createPortfolioTransaction.mockResolvedValue({
      ...selectedTransaction,
      transaction_id: 'txn-fund-subscription',
      account: fundSecuritiesAccount,
      settlement_cash_account: fundCashAccount,
      instrument_id: 'fund-1',
      instrument_ref: fundInstrument,
      quantity: 199_872.08,
      price: 1.250800011687,
      gross_amount: 250_000,
      currency: 'CNY',
    })

    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions?transaction_id=txn-1',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    const securitySearch = within(dialog).getByRole('searchbox', { name: 'Security' })
    await user.type(securitySearch, 'FUND1')
    await user.click(
      await within(dialog).findByRole('button', { name: /FUND1.*Confirmed Allocation Fund.*CNY/ }),
    )

    const sharesInput = within(dialog).getByRole('spinbutton', { name: 'Confirmed Shares' })
    const amountInput = within(dialog).getByRole('spinbutton', { name: 'Confirmed Amount' })
    const priceInput = within(dialog).getByRole('spinbutton', { name: 'Derived Unit Price' })
    await user.type(sharesInput, '199872.08')
    await user.type(amountInput, '250000')

    expect(priceInput).toHaveValue(1.250800011687)
    expect(priceInput).toHaveAttribute('readonly')
    expect(priceInput).not.toHaveValue(1.2)

    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))
    await waitFor(() =>
      expect(apiMocks.createPortfolioTransaction).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({
          transaction_type: 'buy',
          trade_time: null,
          instrument_id: 'fund-1',
          quantity: 199_872.08,
          price: 1.250800011687,
          gross_amount: 250_000,
          currency: 'CNY',
        }),
        expect.stringMatching(/^transaction-create-/),
      ),
    )
  })
})
