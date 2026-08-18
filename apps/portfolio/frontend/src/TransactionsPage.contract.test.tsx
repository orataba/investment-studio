import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TransactionsPage from './pages/TransactionsPage'
import type { PortfolioTransactionRecord } from './lib/api'
import { instrumentFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  createPortfolioInternalTransfer: vi.fn(),
  createPortfolioTransaction: vi.fn(),
  deletePortfolioTransaction: vi.fn(),
  getPortfolioAccounts: vi.fn(),
  getPortfolioDerivativeContracts: vi.fn(),
  getPortfolioFxRates: vi.fn(),
  getPortfolioInstrumentEventTasks: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  getPortfolioTransactionExecutionQuote: vi.fn(),
  getPortfolioTransactionPositionPreview: vi.fn(),
  getPortfolioTransactionsWorkspace: vi.fn(),
  importPortfolioTransactionFile: vi.fn(),
  materializePlatformSecurity: vi.fn(),
  portfolioTransactionDownloadUrl: vi.fn((_portfolioId: string, format: string) => `/transactions.${format}`),
  portfolioTransactionTemplateUrl: vi.fn((_portfolioId: string, format: string) => `/transactions/${format}-template`),
  previewPortfolioTransactionFile: vi.fn(),
  reviewPortfolioInstrumentEventTask: vi.fn(),
  searchPlatformSecurityCatalog: vi.fn(),
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
  fmp_symbol: 'AAPL',
  source: 'fmp_catalog' as const,
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
  fmp_symbol: 'MAGS',
  source: 'fmp_catalog' as const,
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
    const file = new File(['transaction_type,trade_date'], 'candidate.csv', {
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
      expect(apiMocks.materializePlatformSecurity).toHaveBeenCalledWith('equity', 'AAPL'),
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
      expect(apiMocks.materializePlatformSecurity).toHaveBeenCalledWith('etf', 'MAGS'),
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
