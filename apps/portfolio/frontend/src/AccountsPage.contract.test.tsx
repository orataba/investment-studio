import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useLocation } from 'react-router'

import AccountsPage from './pages/AccountsPage'
import type {
  PortfolioAccountsWorkspaceResponse,
  PortfolioTransactionRecord,
} from './lib/api'
import { fcnContractFixture, instrumentFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  SUPPORTED_PORTFOLIO_CURRENCIES: ['CNY', 'USD'],
  createPortfolioAccount: vi.fn(),
  getPortfolioAccountsWorkspace: vi.fn(),
  updatePortfolioAccount: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))
vi.mock('./components/PortfolioAccessProvider', () => ({
  usePortfolioAccess: () => ({ can_edit: true }),
}))

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="account-location">{location.search}</output>
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const cashAccount = {
  account_id: 'cash-1',
  portfolio_id: '3',
  account_name: 'Settlement Cash',
  account_type: 'deposit_account',
  account_category: 'cash' as const,
  currency: 'CNY',
  institution: 'Custodian Bank',
  status: 'active',
}

const securitiesAccount = {
  account_id: 'broker-1',
  portfolio_id: '3',
  account_name: 'Private Fund Account',
  account_type: 'securities_account',
  account_category: 'security' as const,
  currency: 'CNY',
  institution: 'Custodian Bank',
  default_settlement_cash_account_id: 'cash-1',
  cost_basis_method: 'fifo' as const,
  opened_at: '2026-06-23',
  status: 'active',
}

const fundInstrument = instrumentFixture({
  instrument_id: 'fund-1',
  instrument_name: 'Index Enhanced Fund C',
  instrument_type: 'public_fund',
  currency: 'CNY',
  identifiers: [{ identifier_type: 'ticker', identifier_value: '017847', is_primary: true }],
})

const fundSubscription = {
  transaction_id: 'txn-subscription-1',
  transaction_sequence: 1,
  portfolio_id: '3',
  transaction_type: 'buy',
  asset_domain: 'security',
  asset_subtype: 'public_fund',
  flow_scope: 'internal',
  trade_date: '2026-07-24',
  trade_time: '15:30',
  trade_at: '2026-07-24T15:30:00+08:00',
  trade_timezone: 'Asia/Shanghai',
  trade_time_is_estimated: false,
  settlement_date: '2026-07-25',
  position_effective_date: '2026-07-25',
  economic_date: '2026-07-24',
  external_flow_date: null,
  entitlement_date: null,
  acquisition_date: '2026-07-25',
  account: securitiesAccount,
  settlement_cash_account: cashAccount,
  instrument_id: 'fund-1',
  instrument_ref: fundInstrument,
  derivative_contract_id: null,
  derivative_contract: null,
  quantity: 199_872.08,
  source_quantity: '199872.08',
  price: 1.2508,
  source_price: '1.2508',
  gross_amount: 250_000,
  source_gross_amount: '250000',
  counter_amount: null,
  source_counter_amount: null,
  fx_rate: null,
  source_fx_rate: null,
  fees: 0,
  source_fees: '0',
  fee_category: 'transaction_cost',
  taxes: 0,
  source_taxes: '0',
  currency: 'CNY',
  transfer_scope: null,
  transfer_object_type: null,
  transfer_group_id: null,
  counterparty_account_id: null,
  net_cash_effect: -250_000,
  note: 'Post-close subscription; position recognized on the next EOD.',
  created_at: '2026-07-25T09:00:00+08:00',
  row_version: 1,
} satisfies PortfolioTransactionRecord

function accountsWorkspaceFixture(): PortfolioAccountsWorkspaceResponse {
  return {
    portfolio_id: '3',
    base_currency: 'CNY',
    summary: {
      account_count: 2,
      deposit_account_count: 1,
      securities_account_count: 1,
      ledger_posting_count: 1,
      position_line_count: 1,
      valuation_coverage_state: 'complete',
      valued_account_count: 2,
      unvalued_account_count: 0,
      open_option_obligation_count: 0,
      derivative_liability_base: 0,
    },
    derivation_boundary: {
      ledger_postings: 'Derived from immutable source transactions.',
      positions: 'Derived from effective position postings.',
      holdings: 'Derived from account positions.',
      snapshot: 'Derived at the requested as-of date.',
    },
    selected_account_id: 'broker-1',
    accounts: [
      {
        account: cashAccount,
        linked_transaction_count: 1,
        linked_posting_count: 1,
        derived_cash_balance: 750_000,
        derivative_liability: 0,
        derivative_liability_base: 0,
        open_option_obligation_count: 0,
        pending_settlement: 0,
        valuation_coverage_state: 'complete',
        valuation_missing_components: [],
        account_value_base: 750_000,
        position_line_count: 0,
        position_market_value: 0,
        position_market_value_currency: 'CNY',
      },
      {
        account: securitiesAccount,
        default_settlement_cash_account_name: 'Settlement Cash',
        linked_transaction_count: 1,
        linked_posting_count: 1,
        derived_cash_balance: 0,
        settled_cash_cost_basis_base: 0,
        settled_cash_unrealized_fx_pnl_base: 0,
        derivative_liability: 0,
        derivative_liability_base: 0,
        open_option_obligation_count: 0,
        pending_settlement: -250_000,
        pending_settlement_cost_basis_base: -250_000,
        pending_settlement_unrealized_fx_pnl_base: 0,
        monetary_unrealized_fx_pnl_base: 0,
        monetary_fx_coverage_status: 'complete',
        valuation_coverage_state: 'complete',
        valuation_missing_components: [],
        account_value_base: 300_000,
        position_line_count: 1,
        position_market_value: 300_000,
        position_market_value_currency: 'CNY',
      },
    ],
    positions: [
      {
        position_id: 'position-1',
        account_id: 'broker-1',
        position_reference_id: 'fund-1',
        instrument_id: 'fund-1',
        instrument_ref: fundInstrument,
        derivative_contract_id: null,
        derivative_contract: null,
        quantity: 199_872.08,
        cost_basis: 250_000,
        last_price: 1.50096,
        market_value: 300_000,
        carrying_value: null,
        fair_value: 300_000,
        fair_value_coverage_status: 'complete',
        valuation_basis: 'market_quote',
        coverage_status: 'price-nav-fx',
        currency: 'CNY',
        cost_basis_method: 'fifo',
        open_position_lot_count: 1,
      },
    ],
    option_obligations: [],
    linked_transactions_summary: {
      total_transactions: 1,
      security_transactions: 1,
      derivative_transactions: 0,
      fcn_transactions: 0,
      option_transactions: 0,
      cash_transactions: 0,
      external_cash_flows: 0,
      opening_balance_records: 0,
    },
    linked_transactions: [fundSubscription],
    ledger_postings: [
      {
        posting_id: 'posting-1',
        transaction_id: 'txn-subscription-1',
        portfolio_id: '3',
        account_id: 'broker-1',
        settlement_cash_account_id: 'cash-1',
        posting_role: 'pending_settlement',
        source_transaction_type: 'buy',
        trade_date: '2026-07-24',
        settlement_date: '2026-07-25',
        effective_date: '2026-07-24',
        recognition_start_date: '2026-07-25',
        instrument_id: 'fund-1',
        instrument_ref: fundInstrument,
        cash_amount_delta: null,
        pending_amount_delta: -250_000,
        quantity_delta: null,
        cost_basis_delta: null,
        currency: 'CNY',
        note: 'Cash reserved after the order; fund units enter custody on the next EOD.',
      },
    ],
  }
}

describe('Accounts rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getPortfolioAccountsWorkspace.mockResolvedValue(accountsWorkspaceFixture())
  })

  it('keeps account identity and balances persistent while separating setup, positions, transactions, and ledger', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <AccountsPage />,
      '/portfolios/3/accounts?account_id=broker-1',
      '/portfolios/:portfolioId/accounts',
    )

    expect(await screen.findByRole('heading', { name: 'Private Fund Account' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Account setup' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Transaction-derived' })).toBeInTheDocument()
    const accountingHint = screen.getByRole('button', { name: /Transaction-derived balances:/ })
    expect(accountingHint.closest('.account-section-heading')).toContainElement(screen.getByRole('heading', { name: 'Transaction-derived' }))
    expect(screen.queryByText('Custody accounts and their transaction-derived balances.')).not.toBeInTheDocument()
    expect(screen.getByText('Monetary FX P&L · CNY')).toHaveAttribute(
      'title',
      expect.stringContaining('Historical FX basis is preserved'),
    )
    expect(screen.queryByRole('heading', { name: 'Open positions' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Direct transactions' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Ledger entries' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Positions 1' }))
    expect(screen.getByRole('heading', { name: 'Open positions' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Unrealized P/L' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '017847' })).toHaveAttribute(
      'href',
      '/portfolios/3/holdings/fund-1?detail_tab=lots',
    )

    await user.click(screen.getByRole('tab', { name: 'Transactions 1' }))
    expect(screen.getByRole('heading', { name: 'Direct transactions' })).toBeInTheDocument()
    expect(screen.getByText('Subscription')).toBeInTheDocument()
    expect(screen.getByText('Position EOD 2026-07-25')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View' })).toHaveAttribute(
      'href',
      '/portfolios/3/transactions?account_id=broker-1&transaction_id=txn-subscription-1',
    )

    await user.click(screen.getByRole('tab', { name: 'Ledger 1' }))
    expect(screen.getByRole('heading', { name: 'Ledger entries' })).toBeInTheDocument()
    expect(screen.getByText('Pending')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'txn-subscription-1' })).toHaveAttribute(
      'href',
      '/portfolios/3/transactions?account_id=broker-1&transaction_id=txn-subscription-1',
    )
  })

  it('does not present carried account positions as zero unrealized P&L', async () => {
    const workspace = accountsWorkspaceFixture()
    workspace.positions = [
      {
        ...workspace.positions[0],
        position_id: 'position-fcn-1',
        position_reference_id: 'fcn-1',
        instrument_id: null,
        instrument_ref: null,
        derivative_contract_id: 'fcn-1',
        derivative_contract: fcnContractFixture({
          derivative_contract_id: 'fcn-1',
          contract_name: 'Carried FCN',
          currency: 'CNY',
        }),
        cost_basis: 250_000,
        last_price: null,
        market_value: 250_000,
        carrying_value: 250_000,
        fair_value: null,
        fair_value_coverage_status: 'unavailable',
        valuation_basis: 'carried_cost',
        coverage_status: 'event-cost',
      },
    ]
    apiMocks.getPortfolioAccountsWorkspace.mockResolvedValue(workspace)

    renderPortfolioPage(
      <AccountsPage />,
      '/portfolios/3/accounts?account_id=broker-1&account_tab=positions',
      '/portfolios/:portfolioId/accounts',
    )

    const positionLink = await screen.findByRole('link', { name: 'FCN-ALPHA-1' })
    const positionRow = positionLink.closest('tr')
    expect(positionRow).toHaveTextContent('Carried FCN')
    expect(within(positionRow!).getByText('N/A')).toBeInTheDocument()
    expect(positionRow).not.toHaveTextContent('$0.00')
  })

  it('restores a ledger deep link without rendering the other account detail sections', async () => {
    renderPortfolioPage(
      <AccountsPage />,
      '/portfolios/3/accounts?account_id=broker-1&account_tab=ledger',
      '/portfolios/:portfolioId/accounts',
    )

    expect(await screen.findByRole('heading', { name: 'Ledger entries' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Ledger 1' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('heading', { name: 'Account setup' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Open positions' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Direct transactions' })).not.toBeInTheDocument()
  })

  it('uses the same centered accessible modal for adding and editing an account', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(<AccountsPage />, '/portfolios/3/accounts?account_id=broker-1', '/portfolios/:portfolioId/accounts')
    await screen.findByRole('heading', { name: 'Private Fund Account' })
    const add = screen.getByRole('button', { name: 'Add Account' })
    await user.click(add)
    const createDialog = screen.getByRole('dialog', { name: 'Add account' })
    expect(createDialog).toHaveClass('transaction-entry-modal', 'account-editor-modal')
    expect(createDialog.parentElement).toHaveClass('transaction-entry-backdrop', 'account-editor-backdrop')
    expect(within(createDialog).getByLabelText('Account Name')).toHaveValue('')
    expect(within(createDialog).getByLabelText('Account Opening Date')).toHaveAttribute('type', 'date')
    expect(within(createDialog).getByRole('button', { name: 'Save Account' })).toBeInTheDocument()
    expect(within(createDialog).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(within(createDialog).queryByText(/借款与还款、抵押与释放/)).not.toBeInTheDocument()
    await user.selectOptions(within(createDialog).getByLabelText('现金用途'), 'margin')
    expect(within(createDialog).getByText(/借款与还款、抵押与释放/)).toHaveClass('account-cash-purpose-help')
    await user.selectOptions(within(createDialog).getByLabelText('现金用途'), 'operating')
    expect(within(createDialog).queryByText(/借款与还款、抵押与释放/)).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(add).toHaveFocus())
    await user.click(screen.getByRole('button', { name: 'Edit Account' }))
    const editDialog = screen.getByRole('dialog', { name: 'Edit account' })
    expect(editDialog).toHaveClass('transaction-entry-modal', 'account-editor-modal')
    expect(within(editDialog).getByLabelText('Account Name')).toHaveValue('Private Fund Account')
    expect(within(editDialog).getByRole('button', { name: 'Update Account' })).toBeInTheDocument()
    await user.click(within(editDialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('finishes loading an empty account directory and leaves account creation available', async () => {
    apiMocks.getPortfolioAccountsWorkspace.mockResolvedValue({ ...accountsWorkspaceFixture(), selected_account_id: null, accounts: [], positions: [], ledger_postings: [], linked_transactions: [] })
    renderPortfolioPage(<AccountsPage />, '/portfolios/3/accounts', '/portfolios/:portfolioId/accounts')
    expect(await screen.findByText('No account.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Selected account' })).toHaveAttribute('aria-busy', 'false')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add Account' })).toBeEnabled()
  })

  it('confirms an account update with a toast while keeping the directory mounted', async () => {
    apiMocks.updatePortfolioAccount.mockResolvedValue(securitiesAccount)
    const user = userEvent.setup()
    renderPortfolioPage(<AccountsPage />, '/portfolios/3/accounts?account_id=broker-1', '/portfolios/:portfolioId/accounts')
    await screen.findByRole('heading', { name: 'Private Fund Account' })
    const directory = screen.getByRole('navigation', { name: 'Accounts' })
    await user.click(screen.getByRole('button', { name: 'Edit Account' }))
    await user.click(screen.getByRole('button', { name: 'Update Account' }))
    expect(await screen.findByText('Updated Private Fund Account.')).toHaveClass('investment-studio-notice-toast-success')
    expect(screen.getByRole('navigation', { name: 'Accounts' })).toBe(directory)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps the directory and selected identity mounted while loading only the new account details', async () => {
    const user = userEvent.setup()
    const cashResponse = deferred<PortfolioAccountsWorkspaceResponse>()
    apiMocks.getPortfolioAccountsWorkspace.mockImplementation((_portfolioId, accountId) => accountId === 'cash-1' ? cashResponse.promise : Promise.resolve(accountsWorkspaceFixture()))
    renderPortfolioPage(<><AccountsPage /><LocationProbe /></>, '/portfolios/3/accounts?account_id=broker-1&account_tab=positions', '/portfolios/:portfolioId/accounts')
    await screen.findByRole('heading', { name: 'Open positions' })
    const directory = screen.getByRole('navigation', { name: 'Accounts' })
    const detail = screen.getByRole('region', { name: 'Selected account' })
    const cashButton = within(directory).getByRole('button', { name: /Settlement Cash/ })
    await user.click(cashButton)
    expect(screen.getByRole('navigation', { name: 'Accounts' })).toBe(directory)
    expect(screen.getByRole('region', { name: 'Selected account' })).toBe(detail)
    expect(cashButton).toHaveAttribute('aria-current', 'true')
    expect(detail).toHaveAttribute('aria-busy', 'true')
    expect(within(detail).getByRole('heading', { name: 'Settlement Cash' })).toBeInTheDocument()
    expect(detail).toHaveTextContent('750,000.00')
    expect(detail).not.toHaveTextContent('300,000.00')
    expect(within(detail).queryByRole('link', { name: '017847' })).not.toBeInTheDocument()
    expect(within(detail).getByRole('status')).toHaveTextContent('Loading')
    expect(within(detail).getByRole('status')).toHaveClass('account-detail-loading-status')
    expect(detail.querySelector('.calculation-status')).not.toBeInTheDocument()
    expect(screen.getByTestId('account-location')).toHaveTextContent('account_id=cash-1&account_tab=positions')
    await act(async () => cashResponse.resolve({ ...accountsWorkspaceFixture(), selected_account_id: 'cash-1', positions: [], ledger_postings: [], linked_transactions: [] }))
    await waitFor(() => expect(detail).toHaveAttribute('aria-busy', 'false'))
    expect(screen.getByRole('navigation', { name: 'Accounts' })).toBe(directory)
    expect(within(detail).getByRole('heading', { name: 'Open positions' })).toBeInTheDocument()
  })

  it('ignores a late account response after switching back and keeps the URL tab context', async () => {
    const user = userEvent.setup()
    const lateResponse = deferred<PortfolioAccountsWorkspaceResponse>()
    apiMocks.getPortfolioAccountsWorkspace.mockImplementation((_portfolioId, accountId) => accountId === 'cash-1' ? lateResponse.promise : Promise.resolve(accountsWorkspaceFixture()))
    renderPortfolioPage(<><AccountsPage /><LocationProbe /></>, '/portfolios/3/accounts?account_id=broker-1&account_tab=ledger', '/portfolios/:portfolioId/accounts')
    await screen.findByRole('heading', { name: 'Ledger entries' })
    const directory = screen.getByRole('navigation', { name: 'Accounts' })
    await user.click(within(directory).getByRole('button', { name: /Settlement Cash/ }))
    await user.click(within(directory).getByRole('button', { name: /Private Fund Account/ }))
    expect(await screen.findByRole('heading', { name: 'Ledger entries' })).toBeInTheDocument()
    await act(async () => lateResponse.resolve({ ...accountsWorkspaceFixture(), selected_account_id: 'cash-1', positions: [], ledger_postings: [] }))
    expect(screen.getByRole('heading', { name: 'Private Fund Account' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Ledger entries' })).toBeInTheDocument()
    expect(screen.getByTestId('account-location')).toHaveTextContent('account_id=broker-1&account_tab=ledger')
    expect(apiMocks.getPortfolioAccountsWorkspace).toHaveBeenCalledTimes(2)
  })

  it('keeps the directory usable after a detail request fails', async () => {
    const user = userEvent.setup()
    apiMocks.getPortfolioAccountsWorkspace.mockImplementation((_portfolioId, accountId) => accountId === 'cash-1' ? Promise.reject(new Error('Account unavailable')) : Promise.resolve(accountsWorkspaceFixture()))
    renderPortfolioPage(<AccountsPage />, '/portfolios/3/accounts?account_id=broker-1', '/portfolios/:portfolioId/accounts')
    await screen.findByRole('heading', { name: 'Private Fund Account' })
    const directory = screen.getByRole('navigation', { name: 'Accounts' })
    await user.click(within(directory).getByRole('button', { name: /Settlement Cash/ }))
    expect(await screen.findByText('Account unavailable')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Accounts' })).toBe(directory)
    expect(screen.queryByRole('heading', { name: 'Account setup' })).not.toBeInTheDocument()
    await user.click(within(directory).getByRole('button', { name: /Private Fund Account/ }))
    expect(await screen.findByRole('heading', { name: 'Account setup' })).toBeInTheDocument()
    expect(screen.queryByText('Account unavailable')).not.toBeInTheDocument()
  })
})
