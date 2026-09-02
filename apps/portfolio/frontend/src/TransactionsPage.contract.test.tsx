import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TransactionsPage from './pages/TransactionsPage'
import type {
  PortfolioTransactionCaptureAnalysisRevision,
  PortfolioTransactionCaptureBatchRecord,
  PortfolioTransactionImportCommand,
  PortfolioTransactionRecord,
} from './lib/api'
import { instrumentFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  commitPortfolioTransactionImport: vi.fn(),
  createPortfolioInternalTransfer: vi.fn(),
  createPortfolioTransactionCaptureAnalysisRevision: vi.fn(),
  createPortfolioTransactionCaptureBatch: vi.fn(),
  createPortfolioTransaction: vi.fn(),
  deletePortfolioTransaction: vi.fn(),
  getPortfolioAccounts: vi.fn(),
  getPortfolioDerivativeContracts: vi.fn(),
  getPortfolioFxRates: vi.fn(),
  getPortfolioInstrumentEventTasks: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  getPortfolioTransactionExecutionQuote: vi.fn(),
  getPortfolioTransactionPositionPreview: vi.fn(),
  getPortfolioTransactionCaptureBatches: vi.fn(),
  getPortfolioTransactionsWorkspace: vi.fn(),
  importPortfolioTransactionFile: vi.fn(),
  materializePlatformSecurity: vi.fn(),
  portfolioTransactionDownloadUrl: vi.fn((_portfolioId: string, format: string) => `/transactions.${format}`),
  portfolioTransactionCaptureImageUrl: vi.fn((_portfolioId: string, captureId: string) => `/captures/${captureId}/image`),
  portfolioTransactionTemplateUrl: vi.fn((_portfolioId: string, format: string) => `/transactions/${format}-template`),
  previewPortfolioTransactionFile: vi.fn(),
  reviewPortfolioInstrumentEventTask: vi.fn(),
  searchPlatformSecurityCatalog: vi.fn(),
  startPortfolioTransactionCaptureAnalysis: vi.fn(),
  uploadPortfolioTransactionCapture: vi.fn(),
  updatePortfolioTransaction: vi.fn(),
}))
vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

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

const fmpEquityCandidate = {
  instrument_id: 'fmp:AAPL',
  instrument_name: 'Apple Inc.',
  instrument_type: 'equity' as const,
  currency: 'USD',
  exchange_code: 'XNAS',
  identifiers: [
    {
      identifier_type: 'exchange_ticker' as const,
      identifier_value: 'AAPL',
      is_primary: true,
    },
  ],
  broker_identifiers: [],
  catalog_provider: 'fmp' as const,
  catalog_symbol: 'AAPL',
  source: 'security_catalog' as const,
  existing_instrument_id: null,
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

const fmpEtfCandidate = {
  instrument_id: 'fmp:etf:MAGS',
  instrument_name: 'Roundhill Magnificent Seven ETF',
  instrument_type: 'etf' as const,
  currency: 'USD',
  exchange_code: null,
  identifiers: [
    {
      identifier_type: 'exchange_ticker' as const,
      identifier_value: 'MAGS',
      is_primary: true,
    },
  ],
  broker_identifiers: [],
  catalog_provider: 'fmp' as const,
  catalog_symbol: 'MAGS',
  source: 'security_catalog' as const,
  existing_instrument_id: null,
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
    vi.clearAllMocks()
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
    apiMocks.getPortfolioTransactionCaptureBatches.mockResolvedValue({ portfolio_id: '3', batches: [] })
    apiMocks.searchPlatformSecurityCatalog.mockResolvedValue({ results: [], catalogErrors: {} })
    apiMocks.materializePlatformSecurity.mockResolvedValue(materializedEquityInstrument)
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
    apiMocks.getPortfolioTransactionsWorkspace.mockResolvedValue({
      portfolio_id: '3',
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
    await waitFor(() =>
      expect(apiMocks.importPortfolioTransactionFile).toHaveBeenCalledWith(
        '3',
        file,
        'b'.repeat(64),
        expect.stringContaining('transaction-file-import-'),
      ),
    )
    expect(await screen.findByText('Imported 1 transaction facts from clean.xlsx.')).toBeInTheDocument()
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
    expect(await within(dialog).findByText('No matching security.')).toBeInTheDocument()

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

  it('searches the local FMP catalog and materializes a stock before selection', async () => {
    apiMocks.searchPlatformSecurityCatalog.mockResolvedValue({
      results: [fmpEquityCandidate],
      catalogErrors: {},
    })
    apiMocks.getPortfolioInstruments
      .mockResolvedValueOnce({
        portfolio_id: '3',
        instruments: [etfInstrument, alternateEtfInstrument, fundInstrument],
      })
      .mockResolvedValueOnce({
        portfolio_id: '3',
        instruments: [etfInstrument, alternateEtfInstrument, fundInstrument, materializedEquityInstrument],
      })

    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    const securitySearch = within(dialog).getByRole('searchbox', { name: 'Security' })
    await user.type(securitySearch, 'AAPL')
    await user.click(
      await within(dialog).findByRole(
        'button',
        { name: /AAPL.*Apple Inc\..*USD/ },
        { timeout: 3_000 },
      ),
    )

    await waitFor(() =>
      expect(apiMocks.materializePlatformSecurity).toHaveBeenCalledWith('equity', 'fmp', 'AAPL'),
    )
    expect(apiMocks.getPortfolioInstruments).toHaveBeenCalledTimes(2)
  })

  it('searches and materializes an ETF through the same transaction flow', async () => {
    apiMocks.searchPlatformSecurityCatalog.mockResolvedValue({
      results: [fmpEtfCandidate],
      catalogErrors: {},
    })
    apiMocks.materializePlatformSecurity.mockResolvedValue(materializedEtfInstrument)
    apiMocks.getPortfolioInstruments
      .mockResolvedValueOnce({
        portfolio_id: '3',
        instruments: [etfInstrument, alternateEtfInstrument, fundInstrument],
      })
      .mockResolvedValueOnce({
        portfolio_id: '3',
        instruments: [
          etfInstrument,
          alternateEtfInstrument,
          fundInstrument,
          materializedEtfInstrument,
        ],
      })

    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    const securitySearch = within(dialog).getByRole('searchbox', { name: 'Security' })
    await user.type(securitySearch, 'MAGS')
    await user.click(
      await within(dialog).findByRole(
        'button',
        { name: /MAGS.*Roundhill Magnificent Seven ETF.*USD/ },
        { timeout: 3_000 },
      ),
    )

    await waitFor(() =>
      expect(apiMocks.materializePlatformSecurity).toHaveBeenCalledWith('etf', 'fmp', 'MAGS'),
    )
    expect(apiMocks.getPortfolioInstruments).toHaveBeenCalledTimes(2)
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
