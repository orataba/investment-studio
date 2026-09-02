import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfolioSecurityDetailPage from './pages/PortfolioSecurityDetailPage'
import {
  holdingFixture,
  instrumentFixture,
  optionContractFixture,
} from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  getHoldingsWorkspace: vi.fn(),
  getPortfolioDerivativeContracts: vi.fn(),
  getPortfolioInstrumentPriceChart: vi.fn(),
  getPortfolioOptionDeliveryLinks: vi.fn(),
  getPortfolioOptionObligations: vi.fn(),
  getPortfolioPositionLots: vi.fn(),
  getPortfolioTransactions: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))
vi.mock('./components/InstrumentPriceChart', () => ({
  default: ({
    chart,
    loading,
    onRangeChange,
  }: {
    chart: { points: unknown[] } | null
    loading: boolean
    onRangeChange: (rangeKey: '1m') => void
  }) => (
    <>
      <div data-testid="instrument-price-chart">
        {loading
          ? `Loading detail chart; ${chart?.points.length ?? 0} retained points`
          : `${chart?.points.length ?? 0} detail chart points`}
      </div>
      <button type="button" onClick={() => onRangeChange('1m')}>1M</button>
    </>
  ),
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function compactHoldingProjection() {
  const row = { ...holdingFixture() }
  delete (row as Partial<typeof row>).price_chart_1m
  delete (row as Partial<typeof row>).price_chart_3m
  delete (row as Partial<typeof row>).price_chart_6m
  delete (row as Partial<typeof row>).price_chart_1y
  return row
}

describe('Security Detail lazy-load contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getHoldingsWorkspace.mockResolvedValue({
      portfolio_id: '3',
      portfolio_name: 'Contract Portfolio',
      base_currency: 'USD',
      as_of_date: '2026-07-15',
      view_label: 'View: Holdings',
      quality_warnings: [],
      rows: [compactHoldingProjection()],
    })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({
      portfolio_id: '3',
      derivative_contracts: [],
    })
    apiMocks.getPortfolioOptionDeliveryLinks.mockResolvedValue({
      portfolio_id: '3',
      links: [],
    })
    apiMocks.getPortfolioOptionObligations.mockResolvedValue({
      portfolio_id: '3',
      as_of_date: '2026-07-15',
      obligation_count: 0,
      obligations: [],
    })
    apiMocks.getPortfolioInstrumentPriceChart.mockResolvedValue({
      portfolio_id: '3',
      instrument_core: instrumentFixture(),
      as_of_date: '2026-07-15',
      range_key: '1y',
      chart_basis: 'total_return_nav',
      metric_family: 'total_return_nav',
      currency: 'USD',
      points: [
        { date: '2025-07-15', value: 60 },
        { date: '2026-07-15', value: 80 },
      ],
      summary: {
        point_count: 2,
        change_value: 20,
        change_pct: 1 / 3,
        high: 80,
        low: 60,
      },
    })
    apiMocks.getPortfolioPositionLots.mockResolvedValue({
      portfolio_id: '3',
      summary: {
        position_lot_count: 0,
        open_position_lot_count: 0,
        closed_position_lot_count: 0,
        realized_pnl: 0,
      },
      position_lots: [],
    })
    apiMocks.getPortfolioTransactions.mockResolvedValue({
      portfolio_id: '3',
      summary: {
        total_transactions: 0,
        security_transactions: 0,
        derivative_transactions: 0,
        fcn_transactions: 0,
        option_transactions: 0,
        cash_transactions: 0,
        external_cash_flows: 0,
        opening_balance_records: 0,
      },
      derivation_boundary: {
        ledger_postings: 'derived',
        positions: 'derived',
        holdings: 'derived',
        snapshot: 'derived',
      },
      transactions: [],
    })
  })

  it('fetches the holdings context and related ledgers only after the detail route opens', async () => {
    renderPortfolioPage(
      <PortfolioSecurityDetailPage />,
      '/portfolios/3/holdings/asset-1',
      '/portfolios/:portfolioId/holdings/:instrumentId',
    )

    expect(await screen.findByRole('heading', { name: 'Alpha Fund' })).toBeInTheDocument()
    expect(await screen.findByText('2 detail chart points')).toBeInTheDocument()

    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledWith(
      '3',
      { as_of_date: undefined },
    )
    await waitFor(() => {
      expect(apiMocks.getPortfolioInstrumentPriceChart).toHaveBeenCalledWith(
        '3',
        'asset-1',
        { as_of_date: '2026-07-15', range: '1y' },
      )
      expect(apiMocks.getPortfolioPositionLots).toHaveBeenCalledWith('3', {
        as_of_date: '2026-07-15',
      })
      expect(apiMocks.getPortfolioTransactions).toHaveBeenCalledWith('3', {
        end_date: '2026-07-15',
      })
    })
  })

  it('uses the holding line identity to distinguish a written option from the same-contract long position', async () => {
    const optionContract = optionContractFixture({
      derivative_contract_id: 'option-1',
      contract_name: 'Alpha 110 Call',
    })
    const position = {
      ...compactHoldingProjection(),
      line_id: 'option-1',
      holding_kind: 'derivative_contract',
      position_reference_id: 'option-1',
      derivative_contract_id: 'option-1',
      derivative_contract: optionContract,
      instrument_core: null,
      market_value: 500,
      market_value_base: 500,
      cost_basis: 500,
      cost_basis_base: 500,
      carrying_value: 500,
      carrying_value_base: 500,
      fair_value: null,
      fair_value_coverage_status: 'unavailable',
      valuation_basis: 'carried_cost',
      coverage_status: 'event-cost',
    }
    const obligation = {
      ...compactHoldingProjection(),
      line_id: 'option-1:obligation',
      holding_kind: 'option_obligation',
      position_reference_id: 'option-1',
      derivative_contract_id: 'option-1',
      derivative_contract: optionContract,
      instrument_core: null,
      quantity: -2,
      open_contract_quantity: 2,
      required_underlying_quantity: 200,
      obligation_status: 'open',
      option_type: 'call',
      premium_basis_remaining: 300,
      liability_value: 300,
      liability_value_base: 300,
      market_value: -300,
      market_value_base: -300,
      cost_basis: null,
      cost_basis_base: null,
      valuation_basis: 'premium_liability',
      coverage_status: 'event-liability',
      is_liability: true,
    }
    apiMocks.getHoldingsWorkspace.mockResolvedValue({
      portfolio_id: '3',
      portfolio_name: 'Contract Portfolio',
      base_currency: 'USD',
      as_of_date: '2026-07-15',
      view_label: 'View: Holdings',
      quality_warnings: [],
      rows: [position, obligation],
    })
    renderPortfolioPage(
      <PortfolioSecurityDetailPage />,
      '/portfolios/3/holdings/option-1?holding_line_id=option-1%3Aobligation',
      '/portfolios/:portfolioId/holdings/:instrumentId',
    )

    expect(await screen.findByRole('heading', { name: 'Alpha 110 Call' })).toBeInTheDocument()
    const obligationStrip = screen.getByLabelText('Short option position')
    expect(within(obligationStrip).getByText('Open')).toBeInTheDocument()
    expect(within(obligationStrip).getByText('Short Call')).toBeInTheDocument()
    expect(within(obligationStrip).getByText('2.00')).toBeInTheDocument()
    expect(within(obligationStrip).getByText('200.00')).toBeInTheDocument()
    expect(within(obligationStrip).getByText('$300.00 / $300.00')).toBeInTheDocument()
    const heroMetrics = document.querySelector<HTMLElement>('.portfolio-security-hero-metrics')
    expect(heroMetrics).not.toBeNull()
    expect(within(heroMetrics!).getByText('Side / type').parentElement).toHaveTextContent('Written Call')
    expect(within(heroMetrics!).queryByText('Unrealized P/L')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(apiMocks.getPortfolioPositionLots).toHaveBeenCalled()
    })
    expect(await screen.findByRole('tab', { name: 'Position Lots 0' })).toBeInTheDocument()
    await waitFor(() => {
      expect(apiMocks.getPortfolioInstrumentPriceChart).toHaveBeenCalledWith(
        '3',
        'equity-1',
        { as_of_date: '2026-07-15', range: '1y', price_level: true },
      )
    })
  })

  it('loads an underlying security detail even when the stock itself is not currently held', async () => {
    const linkedOption = optionContractFixture({
      terms: {
        underlying_instrument_id: 'asset-1',
        option_type: 'call',
        expiry_date: '2026-12-18',
        strike: 110,
        contract_multiplier: 100,
      },
    })
    apiMocks.getHoldingsWorkspace.mockResolvedValue({
      portfolio_id: '3',
      portfolio_name: 'Contract Portfolio',
      base_currency: 'USD',
      as_of_date: '2026-07-15',
      view_label: 'View: Holdings',
      quality_warnings: [],
      rows: [],
    })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({
      portfolio_id: '3',
      derivative_contracts: [linkedOption],
    })

    renderPortfolioPage(
      <PortfolioSecurityDetailPage />,
      '/portfolios/3/holdings/asset-1',
      '/portfolios/:portfolioId/holdings/:instrumentId',
    )

    expect(await screen.findByRole('heading', { name: 'Alpha Fund' })).toBeInTheDocument()
    expect(screen.getByText('Not held as of selected date.')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Security and linked options' })).toBeInTheDocument()
    expect(screen.getByText('Alpha 110 Call')).toBeInTheDocument()
    await waitFor(() => {
      expect(apiMocks.getPortfolioInstrumentPriceChart).toHaveBeenCalledWith(
        '3',
        'asset-1',
        { as_of_date: '2026-07-15', range: '1y' },
      )
    })
  })

  it('clears the old chart while a newly selected range is loading', async () => {
    const user = userEvent.setup()
    const nextChart = deferred<Awaited<ReturnType<typeof apiMocks.getPortfolioInstrumentPriceChart>>>()
    apiMocks.getPortfolioInstrumentPriceChart
      .mockResolvedValueOnce({
        portfolio_id: '3',
        instrument_core: instrumentFixture(),
        as_of_date: '2026-07-15',
        range_key: '1y',
        chart_basis: 'total_return_nav',
        metric_family: 'total_return_nav',
        currency: 'USD',
        points: [
          { date: '2025-07-15', value: 60 },
          { date: '2026-07-15', value: 80 },
        ],
        summary: {
          point_count: 2,
          change_value: 20,
          change_pct: 1 / 3,
          high: 80,
          low: 60,
        },
      })
      .mockReturnValueOnce(nextChart.promise)

    renderPortfolioPage(
      <PortfolioSecurityDetailPage />,
      '/portfolios/3/holdings/asset-1',
      '/portfolios/:portfolioId/holdings/:instrumentId',
    )

    expect(await screen.findByText('2 detail chart points')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '1M' }))

    expect(screen.getByText('Loading detail chart; 0 retained points')).toBeInTheDocument()

    act(() => nextChart.resolve({
      portfolio_id: '3',
      instrument_core: instrumentFixture(),
      as_of_date: '2026-07-15',
      range_key: '1m',
      chart_basis: 'total_return_nav',
      metric_family: 'total_return_nav',
      currency: 'USD',
      points: [{ date: '2026-07-15', value: 80 }],
      summary: {
        point_count: 1,
        change_value: null,
        change_pct: null,
        high: 80,
        low: 80,
      },
    }))
    expect(await screen.findByText('1 detail chart points')).toBeInTheDocument()
  })

  it('maps legacy realization links to lots and does not synthesize empty-ledger values', async () => {
    renderPortfolioPage(
      <PortfolioSecurityDetailPage />,
      '/portfolios/3/holdings/asset-1?detail_tab=realizations',
      '/portfolios/:portfolioId/holdings/:instrumentId',
    )

    expect(await screen.findByRole('heading', { name: 'Position lots' })).toBeInTheDocument()
    await waitFor(() => {
      expect(apiMocks.getPortfolioPositionLots).toHaveBeenCalled()
    })

    expect(screen.getByRole('tab', { name: 'Position Lots 0' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: /Realizations/ })).not.toBeInTheDocument()
    expect(screen.getByText('Remaining quantity').parentElement).toHaveTextContent('—')
    expect(screen.getByText('Remaining cost').parentElement).toHaveTextContent('—')
    expect(screen.getByText('Realized P/L').parentElement).toHaveTextContent('—')
    expect(apiMocks.getPortfolioInstrumentPriceChart).not.toHaveBeenCalled()
  })

  it('separates transactions from overview and keeps realizations inside the selected lot', async () => {
    const user = userEvent.setup()
    apiMocks.getPortfolioTransactions.mockResolvedValue({
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
        ledger_postings: 'derived',
        positions: 'derived',
        holdings: 'derived',
        snapshot: 'derived',
      },
      transactions: [
        {
          transaction_id: 'txn-buy',
          portfolio_id: '3',
          transaction_type: 'buy',
          flow_scope: 'internal',
          trade_date: '2026-07-03',
          trade_time: '12:00',
          trade_at: '2026-07-03T12:00:00+08:00',
          trade_timezone: 'Asia/Shanghai',
          trade_time_is_estimated: true,
          settlement_date: '2026-07-03',
          position_effective_date: '2026-07-04',
          economic_date: '2026-07-04',
          external_flow_date: null,
          entitlement_date: null,
          acquisition_date: null,
          account: {
            account_id: 'account-1',
            account_name: 'Fund Account',
            account_type: 'securities_account',
            account_category: 'security',
            currency: 'USD',
          },
          settlement_cash_account: {
            account_id: 'cash-1',
            account_name: 'Cash Account',
            account_type: 'deposit_account',
            account_category: 'cash',
            currency: 'USD',
          },
          instrument_id: 'asset-1',
          instrument_ref: instrumentFixture(),
          quantity: 10,
          source_quantity: '10',
          price: 70,
          source_price: '70',
          gross_amount: 700,
          source_gross_amount: '700',
          counter_amount: null,
          source_counter_amount: null,
          fx_rate: null,
          source_fx_rate: null,
          fees: 0,
          source_fees: '0',
          fee_category: 'unknown',
          taxes: 0,
          source_taxes: '0',
          currency: 'USD',
          transfer_scope: null,
          transfer_object_type: null,
          transfer_group_id: null,
          counterparty_account_id: null,
          net_cash_effect: -700,
          note: 'Confirmed subscription.',
          created_at: '2026-07-03T12:00:00+08:00',
          row_version: 1,
        },
      ],
    })
    apiMocks.getPortfolioPositionLots.mockResolvedValue({
      portfolio_id: '3',
      summary: {
        position_lot_count: 1,
        open_position_lot_count: 1,
        closed_position_lot_count: 0,
        realized_pnl: 25,
      },
      position_lots: [
        {
          position_lot_id: 'lot-1',
          portfolio_id: '3',
          account_id: 'account-1',
          position_reference_id: 'asset-1',
          instrument_id: 'asset-1',
          instrument_ref: instrumentFixture(),
          derivative_contract_id: null,
          derivative_contract: null,
          currency: 'USD',
          cost_basis_method: 'fifo',
          opened_by_transaction_id: 'txn-buy',
          opening_transaction_type: 'buy',
          opened_at: '2026-07-03',
          closed_at: null,
          status: 'open',
          entry_quantity: 10,
          remaining_quantity: 8,
          realized_quantity: 2,
          transferred_quantity: 0,
          entry_gross_amount: 700,
          entry_fee_amount: 0,
          entry_tax_amount: 0,
          entry_cost_basis: 700,
          remaining_cost_basis: 560,
          realized_cost_basis: 140,
          transferred_cost_basis: 0,
          realized_proceeds: 165,
          realized_pnl: 25,
          income_cash_amount: 0,
          expense_cash_amount: 0,
          return_of_capital_amount: 0,
          entry_price: 70,
          average_exit_price: 82.5,
          current_market_value: 640,
          unrealized_pnl: 80,
          holding_period_days: 25.4,
          linked_transaction_count: 2,
          realization_count: 1,
          realizations: [
            {
              realization_id: 'realization-1',
              transaction_id: 'txn-sell',
              transaction_type: 'sell',
              trade_date: '2026-07-20',
              position_effective_date: '2026-07-20',
              quantity: 2,
              proceeds: 165,
              cost_basis_released: 140,
              realized_pnl: 25,
              price: 82.5,
              remaining_quantity_after: 8,
              remaining_cost_basis_after: 560,
              status_after: 'open',
            },
          ],
        },
      ],
    })

    renderPortfolioPage(
      <PortfolioSecurityDetailPage />,
      '/portfolios/3/holdings/asset-1?detail_tab=transactions',
      '/portfolios/:portfolioId/holdings/:instrumentId',
    )

    expect(await screen.findByRole('heading', { name: 'Alpha Fund' })).toBeInTheDocument()
    expect(await screen.findByText('Subscription')).toBeInTheDocument()
    expect(screen.getByText('Position EOD 2026-07-04')).toBeInTheDocument()
    expect(screen.queryByTestId('instrument-price-chart')).not.toBeInTheDocument()
    expect(apiMocks.getPortfolioInstrumentPriceChart).not.toHaveBeenCalled()
    expect(screen.queryByRole('tab', { name: 'Realizations 1' })).not.toBeInTheDocument()

    await user.click(await screen.findByRole('tab', { name: 'Position Lots 1' }))

    expect(await screen.findByText('25 days held')).toBeInTheDocument()
    expect(screen.getByText('Matched exits')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Opening transaction' })).toHaveAttribute(
      'href',
      '/portfolios/3/transactions?transaction_id=txn-buy',
    )
    expect(screen.getByRole('link', { name: '2026-07-20' })).toHaveAttribute(
      'href',
      '/portfolios/3/transactions?transaction_id=txn-sell',
    )
  })
})
