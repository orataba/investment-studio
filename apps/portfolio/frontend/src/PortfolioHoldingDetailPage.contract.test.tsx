import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfolioHoldingDetailPage from './pages/PortfolioHoldingDetailPage'
import {
  holdingFixture,
  fcnContractFixture,
  instrumentFixture,
  optionContractFixture,
} from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  getPortfolioPositionHoldingProjection: vi.fn(),
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

describe('Portfolio holding detail contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getPortfolioPositionHoldingProjection.mockResolvedValue({
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
      <PortfolioHoldingDetailPage />,
      '/portfolios/3/holdings/asset-1',
      '/portfolios/:portfolioId/holdings/:holdingId',
    )

    expect(await screen.findByRole('heading', { name: 'Alpha Fund' })).toBeInTheDocument()
    expect(await screen.findByText('2 detail chart points')).toBeInTheDocument()

    expect(apiMocks.getPortfolioPositionHoldingProjection).toHaveBeenCalledWith(
      '3',
      'asset-1',
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
        position_reference_id: 'asset-1',
      })
      expect(apiMocks.getPortfolioTransactions).toHaveBeenCalledWith('3', {
        end_date: '2026-07-15',
        position_reference_id: 'asset-1',
      })
    })
  })

  it('shows canonical base unrealized P/L including the FX component', async () => {
    apiMocks.getPortfolioPositionHoldingProjection.mockResolvedValue({
      portfolio_id: '3',
      portfolio_name: 'Contract Portfolio',
      base_currency: 'CNY',
      as_of_date: '2026-07-15',
      view_label: 'View: Holdings',
      quality_warnings: [],
      rows: [holdingFixture({
        market_value: 800,
        market_value_base: 6000,
        cost_basis: 700,
        cost_basis_base: 5250,
        cost_basis_historical_base: 4900,
        unrealized_price_pnl: 100,
        unrealized_price_pnl_base: 750,
        unrealized_fx_pnl_base: 350,
        unrealized_pnl_base: 1100,
      })],
    })

    renderPortfolioPage(
      <PortfolioHoldingDetailPage />,
      '/portfolios/3/holdings/asset-1',
      '/portfolios/:portfolioId/holdings/:holdingId',
    )

    expect(await screen.findByRole('heading', { name: 'Alpha Fund' })).toBeInTheDocument()
    const heroMetrics = document.querySelector<HTMLElement>('.portfolio-security-hero-metrics')
    expect(heroMetrics).not.toBeNull()
    expect(within(heroMetrics!).getByText('Unrealized P/L').parentElement).toHaveTextContent(
      '+CN¥1,100.00',
    )
    expect(within(heroMetrics!).getByText('Unrealized P/L').parentElement).not.toHaveTextContent(
      '+CN¥750.00',
    )
  })

  it('keeps the instrument chart and ledger usable when linked option metadata fails', async () => {
    apiMocks.getPortfolioOptionDeliveryLinks.mockRejectedValueOnce(new Error('Link service unavailable'))
    renderPortfolioPage(<PortfolioHoldingDetailPage />, '/portfolios/3/holdings/asset-1', '/portfolios/:portfolioId/holdings/:holdingId')
    expect(await screen.findByText('2 detail chart points')).toBeInTheDocument()
    expect(screen.getByText(/Some linked contract data is unavailable/)).toBeInTheDocument()
    await waitFor(() => expect(apiMocks.getPortfolioPositionLots).toHaveBeenCalledWith('3', { as_of_date: '2026-07-15', position_reference_id: 'asset-1' }))
    expect(apiMocks.getPortfolioTransactions).toHaveBeenCalled()
  })

  it('labels closed foreign-currency lots in their original currency even without a current holding', async () => {
    apiMocks.getPortfolioPositionHoldingProjection.mockResolvedValue({ portfolio_id: '3', base_currency: 'CNY', as_of_date: '2026-07-15', rows: [] })
    apiMocks.getPortfolioPositionLots.mockResolvedValue({ portfolio_id: '3', position_lots: [{ position_lot_id: 'closed-usd', position_reference_id: 'asset-1', instrument_ref: instrumentFixture(), account_id: 'account-1', currency: 'USD', status: 'closed', remaining_quantity: 0, remaining_cost_basis: 0, current_market_value: 0, realized_pnl: 125, realization_count: 0, realizations: [], opened_at: '2026-06-01', entry_cost_basis: 1000, closed_at: '2026-07-10' }] })
    renderPortfolioPage(<PortfolioHoldingDetailPage />, '/portfolios/3/holdings/asset-1?detail_tab=lots', '/portfolios/:portfolioId/holdings/:holdingId')
    await waitFor(() => expect(screen.getByText('Realized P/L').parentElement).toHaveTextContent('+$125.00'))
    expect(screen.getByText('Realized P/L').parentElement).not.toHaveTextContent('CN¥')
    expect(screen.getByRole('heading', { name: instrumentFixture().instrument_name, level: 1 })).toBeInTheDocument()
  })

  it('renders cash as a monetary and FX workspace without security analytics', async () => {
    const user = userEvent.setup()
    const cashReferenceId = 'cash:EUR:cash-account'
    apiMocks.getPortfolioTransactions.mockResolvedValue({
      portfolio_id: '3',
      transactions: [{
        transaction_id: 'txn-funding', transaction_type: 'fx_conversion',
        trade_date: '2026-07-15', trade_time: '09:00', economic_date: '2026-07-15',
        settlement_date: '2026-07-15', position_effective_date: null,
        account: { account_id: 'usd-account', account_name: 'USD Cash', account_category: 'cash' },
        settlement_cash_account: null, counterparty_account_id: 'cash-account',
        currency: 'USD', gross_amount: 1100, counter_amount: 1000,
        fees: 0, taxes: 0, net_cash_effect: null, quantity: null, price: null,
      }],
    })
    apiMocks.getPortfolioPositionHoldingProjection.mockResolvedValue({
      portfolio_id: '3',
      portfolio_name: 'Contract Portfolio',
      base_currency: 'USD',
      as_of_date: '2026-07-15',
      view_label: 'View: Holdings',
      quality_warnings: [],
      rows: [holdingFixture({
        line_id: cashReferenceId,
        position_reference_id: cashReferenceId,
        holding_kind: 'settled_cash',
        holding_category: 'cash_and_settlement',
        instrument_core: instrumentFixture({
          instrument_id: 'cash:EUR',
          instrument_name: 'EUR Cash',
          instrument_type: 'cash',
          currency: 'EUR',
          identifiers: [],
        }),
        quantity: 1_000,
        market_value: 1_000,
        market_value_base: 1_100,
        cost_basis: null,
        cost_basis_base: null,
        cost_basis_historical_base: 1_050,
        cost_basis_fx_rate_to_base: 1.05,
        fx_rate_to_base: 1.1,
        fx_rate_as_of_date: '2026-07-15',
        unrealized_fx_pnl_base: 50,
        account_ids: ['cash-account'],
        transaction_ids: ['txn-funding'],
        available_for_trading: true,
        coverage_status: 'cash',
        open_position_lot_count: 0,
      })],
    })

    renderPortfolioPage(
      <PortfolioHoldingDetailPage />,
      `/portfolios/3/holdings/${encodeURIComponent(cashReferenceId)}`,
      '/portfolios/:portfolioId/holdings/:holdingId',
    )

    expect(await screen.findByRole('heading', { name: 'EUR Cash' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Overview Cash & FX' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /Position Lots/ })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Cash availability' })).toBeInTheDocument()
    expect(screen.getByText('Historical FX basis').parentElement).toHaveTextContent('$1,050.00')
    const cashSummary = document.querySelector<HTMLElement>('.cash-detail-summary')
    expect(cashSummary).not.toBeNull()
    expect(within(cashSummary!).getByText('Unrealized FX P/L').parentElement).toHaveTextContent('+$50.00')
    await waitFor(() => {
      expect(apiMocks.getPortfolioTransactions).toHaveBeenCalledWith('3', {
        end_date: '2026-07-15',
        account_id: 'cash-account',
      })
    })
    expect(apiMocks.getPortfolioDerivativeContracts).not.toHaveBeenCalled()
    expect(apiMocks.getPortfolioPositionLots).not.toHaveBeenCalled()
    expect(apiMocks.getPortfolioInstrumentPriceChart).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('tab', { name: 'Transactions 1' }))
    expect(screen.getByRole('columnheader', { name: 'Account cash movement' })).toBeInTheDocument()
    expect(screen.getByText('+€1,000.00')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open transaction ledger' })).toHaveAttribute(
      'href', '/portfolios/3/transactions?account_id=cash-account&end_date=2026-07-15',
    )
  })

  it('keeps a zero-balance cash account scoped to its own ledger without security requests', async () => {
    apiMocks.getPortfolioPositionHoldingProjection.mockResolvedValue({
      portfolio_id: '3', base_currency: 'USD', as_of_date: '2026-07-15', rows: [],
    })
    renderPortfolioPage(
      <PortfolioHoldingDetailPage />,
      '/portfolios/3/holdings/cash%3AEUR%3Acash-account',
      '/portfolios/:portfolioId/holdings/:holdingId',
    )
    expect(await screen.findByText('No monetary balance at this date. Use Transactions to review account activity.')).toBeInTheDocument()
    await waitFor(() => expect(apiMocks.getPortfolioTransactions).toHaveBeenCalledWith('3', {
      end_date: '2026-07-15', account_id: 'cash-account',
    }))
    expect(apiMocks.getPortfolioPositionLots).not.toHaveBeenCalled()
    expect(apiMocks.getPortfolioInstrumentPriceChart).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Current position' })).not.toBeInTheDocument()
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
    apiMocks.getPortfolioPositionHoldingProjection.mockResolvedValue({
      portfolio_id: '3',
      portfolio_name: 'Contract Portfolio',
      base_currency: 'USD',
      as_of_date: '2026-07-15',
      view_label: 'View: Holdings',
      quality_warnings: [],
      rows: [position, obligation],
    })
    renderPortfolioPage(
      <PortfolioHoldingDetailPage />,
      '/portfolios/3/holdings/option-1?holding_line_id=option-1%3Aobligation',
      '/portfolios/:portfolioId/holdings/:holdingId',
    )

    expect(await screen.findByRole('heading', { name: 'Alpha 110 Call' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Written obligation' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Long option asset' })).toHaveAttribute('aria-pressed', 'false')
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

  it.each([optionContractFixture(), fcnContractFixture()])('keeps $contract_type contract detail when no position is open', async (contract) => {
    apiMocks.getPortfolioPositionHoldingProjection.mockResolvedValue({
      portfolio_id: '3', base_currency: 'USD', as_of_date: '2026-07-15', rows: [],
    })
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({
      portfolio_id: '3', derivative_contracts: [contract],
    })
    renderPortfolioPage(
      <PortfolioHoldingDetailPage />,
      `/portfolios/3/holdings/${contract.derivative_contract_id}`,
      '/portfolios/:portfolioId/holdings/:holdingId',
    )
    expect(await screen.findByRole('heading', { name: 'Contract terms' })).toBeInTheDocument()
    expect(screen.getByText('No open position')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Current position' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Linked option results' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Option expiry payoff' })).not.toBeInTheDocument()
  })

  it('does not show zero exposure or a security template while an option projection is loading', async () => {
    const projection = deferred<object>()
    const contract = optionContractFixture()
    apiMocks.getPortfolioPositionHoldingProjection.mockReturnValue(projection.promise)
    apiMocks.getPortfolioDerivativeContracts.mockResolvedValue({
      portfolio_id: '3', derivative_contracts: [contract],
    })
    renderPortfolioPage(
      <PortfolioHoldingDetailPage />,
      `/portfolios/3/holdings/${contract.derivative_contract_id}?as_of_date=2026-07-15`,
      '/portfolios/:portfolioId/holdings/:holdingId',
    )
    expect(await screen.findByRole('heading', { name: contract.contract_name })).toBeInTheDocument()
    expect(screen.getByText('Open contracts').parentElement).toHaveTextContent('—')
    expect(screen.queryByRole('heading', { name: 'Current position' })).not.toBeInTheDocument()
    await act(async () => projection.resolve({
      portfolio_id: '3', base_currency: 'USD', as_of_date: '2026-07-15', rows: [],
    }))
    expect(await screen.findByRole('heading', { name: 'Contract terms' })).toBeInTheDocument()
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
    apiMocks.getPortfolioPositionHoldingProjection.mockResolvedValue({
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
    apiMocks.getPortfolioOptionObligations.mockResolvedValue({
      portfolio_id: '3',
      as_of_date: '2026-07-15',
      obligation_count: 1,
      obligations: [
        {
          obligation_id: 'obligation-1',
          portfolio_id: '3',
          account_id: 'account-1',
          derivative_contract_id: linkedOption.derivative_contract_id,
          derivative_contract: linkedOption,
          related_underlying_id: 'asset-1',
          open_contract_quantity: 0,
          required_underlying_quantity: 0,
          remaining_quantity: 0,
          premium_received_gross: 100,
          premium_basis_remaining: 0,
          carrying_liability: 0,
          status: 'closed',
          realized_pnl: 100,
        },
      ],
    })

    renderPortfolioPage(
      <PortfolioHoldingDetailPage />,
      '/portfolios/3/holdings/asset-1',
      '/portfolios/:portfolioId/holdings/:holdingId',
    )

    expect(await screen.findByRole('heading', { name: 'Alpha Fund' })).toBeInTheDocument()
    expect(screen.getByText('Not held as of selected date.')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Linked option results' })).toBeInTheDocument()
    expect(screen.getByText('Alpha 110 Call')).toBeInTheDocument()
    expect(screen.getByText('Open written liability').parentElement).toHaveTextContent('$0.00')
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
      <PortfolioHoldingDetailPage />,
      '/portfolios/3/holdings/asset-1',
      '/portfolios/:portfolioId/holdings/:holdingId',
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
      <PortfolioHoldingDetailPage />,
      '/portfolios/3/holdings/asset-1?detail_tab=realizations',
      '/portfolios/:portfolioId/holdings/:holdingId',
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
      <PortfolioHoldingDetailPage />,
      '/portfolios/3/holdings/asset-1?detail_tab=transactions',
      '/portfolios/:portfolioId/holdings/:holdingId',
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
