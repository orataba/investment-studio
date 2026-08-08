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
  getPortfolioFxRates: vi.fn(),
  getPortfolioInstrumentEventTasks: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  getPortfolioTransactionExecutionQuote: vi.fn(),
  getPortfolioTransactionPositionPreview: vi.fn(),
  getPortfolioTransactionsWorkspace: vi.fn(),
  importPortfolioTransactionCsv: vi.fn(),
  portfolioTransactionCsvDownloadUrl: vi.fn(() => '/transactions.csv'),
  portfolioTransactionCsvTemplateUrl: vi.fn(() => '/transactions/csv-template'),
  previewPortfolioTransactionCsv: vi.fn(),
  reviewPortfolioInstrumentEventTask: vi.fn(),
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
  currency: 'USD',
  default_settlement_cash_account_id: 'cash-1',
  allowed_instrument_types: ['etf'],
  status: 'active',
}

const cashAccount = {
  account_id: 'cash-1',
  portfolio_id: '3',
  account_name: 'Settlement Cash',
  account_type: 'deposit_account',
  currency: 'USD',
  status: 'active',
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
  allowed_instrument_types: ['fund'],
}

const bondSecuritiesAccount = {
  ...securitiesAccount,
  account_id: 'bond-brokerage-1',
  account_name: 'Bond Brokerage',
  allowed_instrument_types: ['bond'],
}

const multiAssetSecuritiesAccount = {
  ...securitiesAccount,
  account_id: 'multi-asset-brokerage-1',
  account_name: 'Multi-Asset Brokerage',
  allowed_instrument_types: ['etf', 'bond'],
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

const bondInstrument = {
  ...instrumentFixture({
    instrument_id: 'bond-1',
    instrument_name: 'Treasury Bond',
    instrument_type: 'bond',
    identifiers: [{ identifier_type: 'ticker' as const, identifier_value: 'TBOND', is_primary: true }],
  }),
  latest_market_data: [
    {
      metric_family: 'price' as const,
      quote_basis: 'dirty_price' as const,
      as_of_date: '2026-07-15',
      value: '98.5',
      currency: 'USD',
      price_unit: 'percent_of_par' as const,
      price_scale: '0.01',
      status: 'complete' as const,
    },
  ],
  coverage_state: 'complete' as const,
}

const fundInstrument = {
  ...instrumentFixture({
    instrument_id: 'fund-1',
    instrument_name: 'Confirmed Allocation Fund',
    instrument_type: 'fund',
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

const selectedTransaction = {
  transaction_id: 'txn-1',
  transaction_sequence: 1,
  portfolio_id: '3',
  transaction_type: 'buy',
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
      instruments: [etfInstrument, bondInstrument],
    })
    apiMocks.getPortfolioFxRates.mockResolvedValue({ portfolio_id: '3', rates: [] })
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
          instrument_id: request.instrument_id,
          as_of_date: request.as_of_date,
          trade_at: `${request.as_of_date}T12:00:00+08:00`,
          quantity: 1_000,
        }),
    )
    apiMocks.getPortfolioTransactionsWorkspace.mockResolvedValue({
      portfolio_id: '3',
      summary: {
        total_transactions: 1,
        instrument_transactions: 1,
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

    const securitySearch = within(dialog).getByRole('searchbox', { name: 'Security' })
    fireEvent.change(securitySearch, { target: { value: 'TBOND' } })
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
  })

  it('limits lifecycle events to securities accounts before submission', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    const transactionType = within(dialog).getByRole('combobox', {
      name: 'Transaction Type',
    })
    expect(
      within(transactionType).getByRole('option', {
        name: 'Buy / Option Buy to Open',
      }),
    ).toBeInTheDocument()
    await user.selectOptions(transactionType, 'lifecycle_event')

    const account = within(dialog).getByRole('combobox', { name: 'Account' })
    await waitFor(() => expect(account).toHaveValue(securitiesAccount.account_id))
    expect(within(account).getByRole('option', { name: 'ETF Brokerage · USD' })).toBeInTheDocument()
    expect(
      within(account).queryByRole('option', { name: 'Settlement Cash · USD' }),
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
      accounts: [multiAssetSecuritiesAccount, cashAccount],
    })
    apiMocks.getPortfolioTransactionExecutionQuote.mockResolvedValue({
      portfolio_id: '3',
      instrument_id: 'bond-1',
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
      expect(within(dialog).getByRole('combobox', { name: 'Account' })).toHaveValue(
        'multi-asset-brokerage-1',
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

    fireEvent.change(securitySearch, { target: { value: 'TBOND' } })
    await user.click(
      await within(dialog).findByRole(
        'button',
        { name: /TBOND.*Treasury Bond.*USD/ },
        { timeout: 3_000 },
      ),
    )

    expect(quantityInput).toHaveValue(null)
    expect(quoteInput).toHaveValue(null)
    expect(amountInput).toHaveValue(null)
  })

  it('recalculates an auto-derived bond gross amount when the execution quote date changes', async () => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [bondSecuritiesAccount, cashAccount],
    })
    apiMocks.getPortfolioTransactionExecutionQuote.mockImplementation(
      (_portfolioId, _instrumentId, asOfDate) =>
        Promise.resolve({
          portfolio_id: '3',
          instrument_id: 'bond-1',
          requested_as_of_date: asOfDate,
          selection_role: 'trading',
          value: asOfDate === '2026-07-16' ? 99 : 98.5,
          quote_date: asOfDate,
          quote_basis: 'dirty_price',
          metric_family: 'price',
          currency: 'USD',
          provider: 'fixture',
          status: 'complete',
          stale: false,
          price_unit: 'percent_of_par',
          price_scale: 0.01,
          unavailable_reason: null,
        }),
    )

    const user = userEvent.setup()
    renderPortfolioPage(
      <TransactionsPage />,
      '/portfolios/3/transactions?transaction_id=txn-1',
      '/portfolios/:portfolioId/transactions',
    )

    await user.click(await screen.findByRole('button', { name: 'Record Transaction' }))
    const dialog = screen.getByRole('dialog', { name: 'Record transaction' })
    await waitFor(() =>
      expect(within(dialog).getByRole('combobox', { name: 'Account' })).toHaveValue(
        'bond-brokerage-1',
      ),
    )
    fireEvent.change(within(dialog).getByLabelText('Trade Date'), {
      target: { value: '2026-07-15' },
    })
    const securitySearch = within(dialog).getByRole('searchbox', { name: 'Security' })
    await user.type(securitySearch, 'TBOND')
    await user.click(
      await within(dialog).findByRole('button', { name: /TBOND.*Treasury Bond.*USD/ }),
    )

    const quantityInput = within(dialog).getByRole('spinbutton', { name: /^Shares/ })
    const quoteInput = within(dialog).getByRole('spinbutton', { name: /^Execution Price/ })
    const amountInput = within(dialog).getByRole('spinbutton', { name: 'Amount' })
    await waitFor(() => expect(quoteInput).toHaveValue(98.5))
    await user.type(quantityInput, '1000')
    expect(amountInput).toHaveValue(985)
    expect(
      within(within(dialog).getByRole('complementary', { name: 'Transaction review' })).getByText('-$985.00'),
    ).toBeInTheDocument()

    fireEvent.change(within(dialog).getByLabelText('Trade Date'), {
      target: { value: '2026-07-16' },
    })

    await waitFor(() => {
      expect(quoteInput).toHaveValue(99)
      expect(amountInput).toHaveValue(990)
    })
  })

  it('submits true sell percent-of-par economics and exposes a readable unavailable quote reason', async () => {
    apiMocks.getPortfolioAccounts.mockResolvedValue({
      portfolio_id: '3',
      accounts: [bondSecuritiesAccount, cashAccount],
    })
    apiMocks.getPortfolioTransactionExecutionQuote.mockResolvedValue({
      portfolio_id: '3',
      instrument_id: 'bond-1',
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
      unavailable_reason: 'clean_price_requires_matching_accrued_interest',
    })
    apiMocks.createPortfolioTransaction.mockResolvedValue({
      ...selectedTransaction,
      transaction_id: 'txn-bond-sell',
      transaction_type: 'sell',
      account: bondSecuritiesAccount,
      instrument_id: 'bond-1',
      instrument_ref: bondInstrument,
      quantity: 1_000,
      price: 98.5,
      gross_amount: 985,
      net_cash_effect: 985,
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
      expect(within(dialog).getByRole('combobox', { name: 'Account' })).toHaveValue(
        'bond-brokerage-1',
      ),
    )
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Transaction Type' }), 'sell')
    const securitySearch = within(dialog).getByRole('searchbox', { name: 'Security' })
    await user.type(securitySearch, 'TBOND')
    await user.click(
      await within(dialog).findByRole('button', { name: /TBOND.*Treasury Bond.*USD/ }),
    )

    expect(
      await within(dialog).findByText(
        'Clean bond price requires matching same-date accrued interest, so no execution quote was applied.',
      ),
    ).toBeInTheDocument()
    const quoteInput = within(dialog).getByRole('spinbutton', { name: /^Execution Price/ })
    const amountInput = within(dialog).getByRole('spinbutton', { name: 'Amount' })
    expect(quoteInput).toHaveValue(null)
    expect(amountInput).toHaveValue(null)

    const quantityInput = within(dialog).getByRole('spinbutton', { name: /^Shares/ })
    await waitFor(() => expect(quantityInput).toHaveValue(1_000))
    await user.type(quoteInput, '98.5')
    expect(amountInput).toHaveValue(985)
    expect(
      within(within(dialog).getByRole('complementary', { name: 'Transaction review' })).getByText('+$985.00'),
    ).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Record Transaction' }))
    await waitFor(() =>
      expect(apiMocks.createPortfolioTransaction).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({
          transaction_type: 'sell',
          trade_time: null,
          instrument_id: 'bond-1',
          quantity: 1_000,
          price: 98.5,
          gross_amount: 985,
        }),
        expect.stringMatching(/^transaction-create-/),
      ),
    )
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
        }),
        expect.stringMatching(/^transaction-create-/),
      ),
    )
  })
})
