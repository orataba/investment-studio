import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfolioHomePage from './pages/PortfolioHomePage'
import {
  fcnInstrumentFixture,
  holdingFixture,
  holdingsWorkspaceFixture,
  instrumentFixture,
  optionInstrumentFixture,
} from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  getHoldingsWorkspace: vi.fn(),
  getPortfolioTaxonomyCatalog: vi.fn(),
  getPortfolioTableViewStore: vi.fn(),
  savePortfolioTableViewStore: vi.fn(),
}))
const tableExportMocks = vi.hoisted(() => ({
  downloadTable: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('../../../../packages/ui/src/tableExport', () => tableExportMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function HoldingsRouteHarness() {
  const navigate = useNavigate()
  return (
    <>
      <button type="button" onClick={() => navigate('/portfolios/4/holdings')}>
        Switch portfolio
      </button>
      <Routes>
        <Route path="/portfolios/:portfolioId/holdings" element={<PortfolioHomePage />} />
      </Routes>
    </>
  )
}

async function openAdvancedTable(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Advanced Table' }))
}

describe('Holdings rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        quality_warnings: [
          'Alpha Fund YTD return is unavailable because the 2025-12-31 start anchor is missing.',
        ],
        rows: [holdingFixture({ instrument_return_ytd: null })],
      }),
    )
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({
      portfolio_id: '3',
      default_planning_taxonomy_id: null,
      taxonomies: [],
      taxonomy_nodes: [],
      taxonomy_assignments: [],
      instrument_universe: [],
      target_sets: [],
      target_set_lines: [],
      target_set_integrity_issues: [],
    })
    apiMocks.getPortfolioTableViewStore.mockResolvedValue({ store: null })
    apiMocks.savePortfolioTableViewStore.mockResolvedValue({})
  })

  it('uses canonical defaults for an empty backend store and ignores legacy browser views', async () => {
    const legacyStore = JSON.stringify({
      activeViewId: 'return-risk',
      views: [],
    })
    window.localStorage.setItem('portfolio_ops.portfolio.holdings.views.v4', legacyStore)

    const user = userEvent.setup()
    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    expect(await screen.findByRole('button', { name: 'Regions' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    const marketRegion = screen.getByRole('region', { name: 'Market-valued Positions' })
    const structuredRegion = screen.getByRole('region', {
      name: 'Structured / Long Derivatives',
    })
    const obligationRegion = screen.getByRole('region', {
      name: 'Written Option Obligations',
    })
    const cashRegion = screen.getByRole('region', { name: 'Cash & Settlement' })
    expect(within(marketRegion).getByRole('columnheader', { name: 'Day Return' })).toBeInTheDocument()
    expect(within(marketRegion).getByRole('columnheader', { name: 'Scoped RC' })).toBeInTheDocument()
    expect(within(structuredRegion).queryByRole('columnheader', { name: 'Day Return' })).not.toBeInTheDocument()
    expect(within(structuredRegion).queryByRole('columnheader', { name: 'Scoped RC' })).not.toBeInTheDocument()
    expect(within(obligationRegion).getByRole('columnheader', { name: 'Premium Basis' })).toBeInTheDocument()
    expect(within(obligationRegion).getByRole('columnheader', { name: 'Carrying Liability' })).toBeInTheDocument()
    expect(within(obligationRegion).getByRole('columnheader', { name: 'Expiry' })).toBeInTheDocument()
    expect(within(obligationRegion).getByRole('columnheader', { name: 'Uncovered' })).toBeInTheDocument()
    expect(within(obligationRegion).getByRole('columnheader', { name: 'Covered Ratio' })).toBeInTheDocument()
    expect(within(cashRegion).getByRole('columnheader', { name: 'Settlement Date' })).toBeInTheDocument()
    expect(within(cashRegion).getByRole('columnheader', { name: 'Amount Base' })).toBeInTheDocument()
    expect(within(cashRegion).getByRole('columnheader', { name: 'Pending Status' })).toBeInTheDocument()
    expect(within(cashRegion).queryByRole('columnheader', { name: 'Day Return' })).not.toBeInTheDocument()
    expect(within(cashRegion).queryByRole('columnheader', { name: 'Scoped RC' })).not.toBeInTheDocument()
    const operationalStatus = screen.getByRole('region', { name: 'Holdings operational status' })
    expect(within(operationalStatus).getByText('Uncovered underlying').nextElementSibling).toHaveTextContent('0.00')
    expect(within(operationalStatus).getByText('No operational exceptions.')).toBeInTheDocument()

    await openAdvancedTable(user)
    expect(await screen.findByRole('button', { name: /View\s*: Default/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sort Quote: ascending' })).toBeInTheDocument()
    expect(window.localStorage.getItem('portfolio_ops.portfolio.holdings.views.v4')).toBe(legacyStore)
  })

  it('exports the four regions with a fixed canonical schema and explicit N/A values', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValueOnce(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture(),
          holdingFixture({
            line_id: 'holding:fcn-1',
            holding_region: 'structured_and_long_derivatives',
            instrument_core: fcnInstrumentFixture(),
            quantity: 1,
            market_value: 500,
            market_value_base: 500,
            carrying_value: 500,
            carrying_value_base: 500,
            cost_basis: 500,
            cost_basis_base: 500,
            coverage_status: 'event-cost',
            valuation_basis: 'carried_cost',
            performance_eligible: false,
            risk_eligible: false,
          }),
          holdingFixture({
            line_id: 'broker:option-1:obligation',
            holding_kind: 'option_obligation',
            holding_region: 'written_option_obligations',
            instrument_core: optionInstrumentFixture(),
            quantity: 100,
            market_value: -300,
            market_value_base: -300,
            cost_basis: null,
            cost_basis_base: null,
            open_contract_quantity: 1,
            required_underlying_quantity: 100,
            underlying_position_quantity: 100,
            covered_underlying_quantity: 100,
            uncovered_underlying_quantity: 0,
            covered_ratio: 1,
            obligation_status: 'open',
            obligation_coverage_status: 'covered',
            related_underlying_id: 'equity-1',
            expiry_date: '2026-12-18',
            strike: 110,
            settlement_type: 'physical',
            premium_basis_remaining: 300,
            liability_value: 300,
            liability_value_base: 300,
            carrying_value: 300,
            carrying_value_base: 300,
            coverage_status: 'event-liability',
            valuation_basis: 'premium_liability',
            performance_eligible: false,
            risk_eligible: false,
            is_liability: true,
          }),
          holdingFixture({
            line_id: 'pending:settlement_payable',
            holding_kind: 'settlement_payable',
            holding_region: 'cash_and_settlement',
            instrument_core: instrumentFixture({
              instrument_id: 'pending:settlement_payable',
              instrument_name: 'Settlement payable · Alpha Fund',
              instrument_type: 'other',
              identifiers: [],
            }),
            quantity: -250,
            market_value: -250,
            market_value_base: -250,
            cost_basis: null,
            cost_basis_base: null,
            settlement_date: '2026-07-17',
            pending_until_date: '2026-07-17',
            pending_status: 'awaiting_settlement',
            settlement_amount: -250,
            settlement_amount_base: -250,
            coverage_status: 'pending-settlement',
            analytics_scope: 'unallocated',
            performance_scope: 'unallocated',
            performance_eligible: false,
            risk_eligible: false,
            risk_budget_eligible: false,
          }),
        ],
      }),
    )
    const user = userEvent.setup()

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    expect(await screen.findByText('Open · matures 2026-12-31')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Download' }))
    await user.click(screen.getByRole('menuitem', { name: 'CSV' }))

    const exportedRows = tableExportMocks.downloadTable.mock.calls[0][1] as Array<
      Array<string | number | null>
    >
    expect(exportedRows[0]).toEqual([
      'Region',
      'Instrument',
      'Identifier',
      'Currency',
      'Lifecycle/Pending Status',
      'Quantity',
      'Market Value Base',
      'Carrying Value Base',
      'Cost Basis Base',
      'Maturity/Expiry',
      'Open Contracts',
      'Required Underlying',
      'Covered Underlying',
      'Uncovered Underlying',
      'Covered Ratio',
      'Strike',
      'Settlement Type',
      'Premium Basis',
      'Carrying Liability Base',
      'Settlement Date',
      'Settlement Amount',
      'Settlement Amount Base',
      'Analytics Scope',
      'Coverage Status',
    ])
    expect(exportedRows).toHaveLength(5)
    const marketRow = exportedRows.find((row) => row[1] === 'Alpha Fund')
    const fcnRow = exportedRows.find((row) => row[1] === 'Alpha FCN')
    const obligationRow = exportedRows.find((row) => row[1] === 'Alpha 110 Call')
    const settlementRow = exportedRows.find(
      (row) => row[1] === 'Settlement payable · Alpha Fund',
    )
    expect(marketRow?.[10]).toBe('N/A')
    expect(fcnRow?.[6]).toBe('N/A')
    expect(fcnRow?.[7]).toBe(500)
    expect(fcnRow?.[9]).toBe('2026-12-31')
    expect(obligationRow?.slice(10, 19)).toEqual([
      1,
      100,
      100,
      0,
      1,
      110,
      'Physical',
      300,
      300,
    ])
    expect(settlementRow?.slice(19, 22)).toEqual(['2026-07-17', -250, -250])
    expect(settlementRow?.[6]).toBe('N/A')
  })

  it('marks table views unavailable when the backend view store cannot be loaded', async () => {
    apiMocks.getPortfolioTableViewStore.mockRejectedValueOnce(new Error('View store offline.'))

    const user = userEvent.setup()
    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('View store offline.')
    expect(screen.getByText('Table views unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /View\s*:/ })).not.toBeInTheDocument()
  })

  it('upgrades the persisted Return & Risk system view with instrument 1M return', async () => {
    apiMocks.getPortfolioTableViewStore.mockResolvedValueOnce({
      store: {
        activeViewId: 'return-risk',
        views: [
          {
            id: 'return-risk',
            name: 'Return & Risk',
            readonly: true,
            state: {
              columns: [
                'instrument',
                'instrument_return_1w',
                'instrument_return_mtd',
                'unrealized_pct',
              ],
              columnWidths: {},
              groupBy: 'none',
              sortField: 'unrealized_pct',
              sortDirection: 'desc',
            },
          },
        ],
      },
    })

    const user = userEvent.setup()
    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    expect(await screen.findByRole('button', { name: /View\s*: Return & Risk/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /1M Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Unrealized Return/ })).toBeInTheDocument()
  })

  it('does not render the prior portfolio workspace while the next portfolio is loading', async () => {
    const nextWorkspace = deferred<ReturnType<typeof holdingsWorkspaceFixture>>()
    apiMocks.getHoldingsWorkspace
      .mockResolvedValueOnce(holdingsWorkspaceFixture())
      .mockReturnValueOnce(nextWorkspace.promise)
    apiMocks.getPortfolioTaxonomyCatalog.mockImplementation((portfolioId: string) =>
      Promise.resolve({
        portfolio_id: portfolioId,
        default_planning_taxonomy_id: null,
        taxonomies: [],
        taxonomy_nodes: [],
        taxonomy_assignments: [],
        instrument_universe: [],
        target_sets: [],
        target_set_lines: [],
        target_set_integrity_issues: [],
      }),
    )

    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/portfolios/3/holdings']}>
        <HoldingsRouteHarness />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('cell', { name: /Alpha Fund/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Switch portfolio' }))

    expect(screen.queryByRole('cell', { name: /Alpha Fund/ })).not.toBeInTheDocument()

    act(() =>
      nextWorkspace.resolve(
        holdingsWorkspaceFixture({
          portfolio_id: '4',
          rows: [
            holdingFixture({
              instrument_core: {
                ...holdingFixture().instrument_core,
                instrument_id: 'asset-4',
                instrument_name: 'Beta Fund',
              },
            }),
          ],
        }),
      ),
    )
    expect(await screen.findByRole('cell', { name: /Beta Fund/ })).toBeInTheDocument()
  })

  it('renders the default trend, explains a legitimate unavailable return, and preserves total-row semantics', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    expect(await screen.findByRole('cell', { name: /Alpha Fund/ })).toBeInTheDocument()
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledWith('3', {
      as_of_date: undefined,
    })
    expect(
      screen.getByText(
        'Trend: Dividend-Reinvested Total Return NAV · Complete · 250 observations',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Policy-preferred trend basis selected.')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Chart 6M/ })).toBeInTheDocument()
    const unavailableReason = screen.getByText(
      'Alpha Fund YTD return is unavailable because the 2025-12-31 start anchor is missing.',
    )
    expect(unavailableReason.closest('[role="status"]')).not.toBeNull()
    const totalLabel = screen.getByText('Portfolio Total (USD)')
    const totalRow = totalLabel.closest('tr')
    expect(totalRow).not.toBeNull()
    expect(within(totalRow!).getByText('$800.00')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))

    await waitFor(() => {
      expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledWith('3', {
        as_of_date: undefined,
        include_details: true,
      })
    })
    expect(screen.getByRole('columnheader', { name: /1W Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /1M Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /3M Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /6M Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Holding Since/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^MTD/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^YTD/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^1Y Resize/ })).toBeInTheDocument()

    const holdingRow = screen.getByRole('cell', { name: /Alpha Fund/ }).closest('tr')
    expect(holdingRow).not.toBeNull()
    expect(within(holdingRow!).getByText('+1.23%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('2026-06-23')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+3.45%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+4.56%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+6.78%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+2.34%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+10.00%')).toBeInTheDocument()

    const ytdHeader = screen.getByRole('columnheader', { name: /^YTD/ })
    const ytdColumnIndex = Array.from(ytdHeader.parentElement!.children).indexOf(ytdHeader)
    expect(holdingRow!.children[ytdColumnIndex]).toHaveTextContent('—')

    const refreshedTotalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(refreshedTotalRow).not.toBeNull()
    expect(refreshedTotalRow).not.toHaveTextContent(/TWR/i)
    const expectedTotalReturns = {
      instrument_return_1w: '+1.23%',
      instrument_return_1m: '+3.45%',
      instrument_return_3m: '+4.56%',
      instrument_return_6m: '+6.78%',
      instrument_return_mtd: '+2.34%',
      instrument_return_ytd: '—',
      instrument_return_1y: '+10.00%',
    }
    for (const [columnKey, expectedReturn] of Object.entries(expectedTotalReturns)) {
      const totalCell = refreshedTotalRow!.querySelector(`[data-column-key="${columnKey}"]`)
      expect(totalCell).not.toBeNull()
      expect(totalCell).toHaveTextContent(expectedReturn)
      expect(totalCell).toHaveAttribute(
        'title',
        'Current-holdings basket market return using as-of market-value weights; it may include distributions and is neither book unrealized P&L nor historical portfolio TWR.',
      )
    }
  })

  it('shows written-option obligation facts by default and renders event day change as N/A', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'broker:option-1:obligation',
            holding_kind: 'option_obligation',
            holding_region: 'written_option_obligations',
            instrument_core: optionInstrumentFixture({
              instrument_id: 'option-1',
              instrument_name: 'Alpha 110 Covered Call',
              identifiers: [
                {
                  identifier_type: 'internal',
                  identifier_value: 'OPT-ALPHA-110C',
                  is_primary: true,
                },
              ],
            }),
            quantity: 200,
            open_contract_quantity: 2,
            required_underlying_quantity: 200,
            underlying_position_quantity: 150,
            covered_underlying_quantity: 150,
            uncovered_underlying_quantity: 50,
            covered_ratio: 0.75,
            obligation_status: 'open',
            obligation_coverage_status: 'uncovered',
            coverage_type: 'covered_call',
            related_underlying_id: 'equity-1',
            expiry_date: '2026-12-18',
            days_to_expiry: 156,
            strike: 110,
            option_type: 'call',
            contract_multiplier: 100,
            settlement_type: 'physical',
            assignment_notional: 22_000,
            assignment_notional_base: 22_000,
            premium_basis_remaining: 300,
            liability_value: 300,
            liability_value_base: 300,
            carrying_value: 300,
            carrying_value_base: 300,
            market_value: -300,
            market_value_base: -300,
            cost_basis: null,
            cost_basis_base: null,
            last_price: null,
            quote_as_of_date: null,
            quote_basis: 'premium_liability',
            quote_provider: null,
            quote_status: 'event-cost',
            coverage_status: 'uncovered-obligation',
            valuation_basis: 'premium_liability',
            fair_value: null,
            fair_value_coverage_status: 'unavailable',
            performance_eligible: false,
            risk_eligible: false,
            is_liability: true,
            day_change_value: 0,
            day_change_value_base: 0,
            day_change_pct: 0,
            allocation: -0.03,
            price_chart_1m: [],
            price_chart_3m: [],
            price_chart_6m: [],
            price_chart_1y: [],
          }),
        ],
        totals: {
          market_value: -300,
          day_change_pct: null,
          day_change_value: null,
          cost_basis: 0,
          allocation: -0.03,
        },
        operational_summary: {
          uncovered_obligation_count: 1,
          uncovered_underlying_quantity: 50,
          expiry_buckets: [
            {
              bucket: 'later',
              obligation_count: 1,
              open_contract_quantity: 2,
              required_underlying_quantity: 200,
              uncovered_underlying_quantity: 50,
              carrying_liability_base: 300,
            },
          ],
          assignment_exposure: {
            obligation_count: 1,
            open_contract_quantity: 2,
            deliverable_underlying_quantity: 200,
            uncovered_underlying_quantity: 50,
            strike_notional_base: 22_000,
          },
          settlement_exposure: {
            pending_line_count: 0,
            receivable_base: 0,
            payable_base: 0,
            net_base: 0,
            earliest_settlement_date: null,
            overdue_line_count: 0,
            unavailable_base_line_count: 0,
          },
        },
        operational_alerts: [
          {
            code: 'uncovered_option_obligation',
            severity: 'critical',
            title: 'Uncovered option obligation',
            message: '1 obligation line(s) have 50 uncovered underlying units.',
            related_line_ids: ['broker:option-1:obligation'],
          },
        ],
      }),
    )
    const user = userEvent.setup()

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    const obligationCell = await screen.findByRole('cell', {
      name: /Alpha 110 Covered Call/,
    })
    const regionRow = obligationCell.closest('tr')
    expect(regionRow).not.toBeNull()
    expect(within(regionRow!).getByText('2.00')).toBeInTheDocument()
    expect(within(regionRow!).getByText('200.00')).toBeInTheDocument()
    expect(within(regionRow!).getByText('150.00')).toBeInTheDocument()
    expect(within(regionRow!).getByText('50.00')).toHaveClass('holdings-obligation-uncovered')
    expect(within(regionRow!).getByText('75.00%')).toBeInTheDocument()
    expect(within(regionRow!).getByText('2026-12-18')).toBeInTheDocument()
    expect(within(regionRow!).getByText('$110.0000')).toBeInTheDocument()
    expect(within(regionRow!).getByText('Physical')).toBeInTheDocument()
    expect(within(regionRow!).getAllByText('$300.00')).toHaveLength(2)
    expect(within(regionRow!).getByText('Uncovered')).toHaveClass('holdings-obligation-uncovered')
    expect(screen.getByRole('alert')).toHaveTextContent('Uncovered option obligation')
    expect(screen.getByRole('alert')).toHaveTextContent('50 uncovered underlying units')

    await openAdvancedTable(user)
    const advancedObligationCell = await screen.findByRole('cell', {
      name: /Alpha 110 Covered Call/,
    })
    expect(advancedObligationCell).toHaveTextContent(
      'Written option obligation · Open · Covered Call',
    )
    expect(advancedObligationCell).toHaveTextContent(
      '2.00 open contracts · 200.00 required · 150.00 covered · 50.00 uncovered',
    )
    expect(advancedObligationCell).toHaveTextContent(
      'Remaining premium basis $300.00 · Carrying liability $300.00',
    )
    await user.click(screen.getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))
    const obligationRow = screen.getByRole('cell', { name: /Alpha 110 Covered Call/ }).closest('tr')
    const dayChangeHeader = screen.getByRole('columnheader', { name: /Day Change/ })
    const dayReturnHeader = screen.getByRole('columnheader', { name: /Day Return/ })
    const dayChangeIndex = Array.from(dayChangeHeader.parentElement!.children).indexOf(dayChangeHeader)
    const dayReturnIndex = Array.from(dayReturnHeader.parentElement!.children).indexOf(dayReturnHeader)
    expect(obligationRow!.children[dayChangeIndex]).toHaveTextContent('N/A')
    expect(obligationRow!.children[dayReturnIndex]).toHaveTextContent('N/A')
    expect(obligationRow!.children[dayChangeIndex]).not.toHaveTextContent('$0.00')
    expect(obligationRow!.children[dayReturnIndex]).not.toHaveTextContent('0.00%')
  })

  it('keeps event-valued P&L unavailable while preserving a genuine quoted zero', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:event-option',
            holding_region: 'structured_and_long_derivatives',
            instrument_core: optionInstrumentFixture({
              instrument_id: 'event-option',
              instrument_name: 'Carried Option',
              identifiers: [],
            }),
            quantity: 1,
            market_value: 500,
            market_value_base: 500,
            cost_basis: 500,
            cost_basis_base: 500,
            carrying_value: 500,
            carrying_value_base: 500,
            valuation_basis: 'carried_cost',
            coverage_status: 'event-cost',
            performance_eligible: false,
            risk_eligible: false,
            day_change_value: 0,
            day_change_value_base: 0,
            day_change_pct: 0,
            allocation: 5 / 12,
          }),
          holdingFixture({
            line_id: 'holding:quoted-fund',
            instrument_core: instrumentFixture({
              instrument_id: 'quoted-fund',
              instrument_name: 'Quoted Fund',
              instrument_type: 'fund',
              currency: 'USD',
              identifiers: [],
            }),
            quantity: 7,
            last_price: 100,
            market_value: 700,
            market_value_base: 700,
            cost_basis: 700,
            cost_basis_base: 700,
            valuation_basis: 'market_quote',
            fair_value: 700,
            fair_value_coverage_status: 'complete',
            day_change_value: 0,
            day_change_value_base: 0,
            day_change_pct: 0,
            allocation: 7 / 12,
          }),
        ],
        totals: {
          market_value: 1200,
          day_change_pct: 0,
          day_change_value: 0,
          cost_basis: 1200,
          allocation: 1,
        },
      }),
    )
    const user = userEvent.setup()

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    await user.click(await screen.findByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))

    const unrealizedValueHeader = screen.getByRole('columnheader', { name: /Unrealized P&L/ })
    const unrealizedPctHeader = screen.getByRole('columnheader', { name: /Unrealized Return/ })
    const chartHeader = screen.getByRole('columnheader', { name: /Chart 6M/ })
    const returnHeader = screen.getByRole('columnheader', { name: /1M Return/ })
    const volatilityHeader = screen.getByRole('columnheader', { name: /1M Vol/ })
    const forwardRiskHeader = screen.getByRole('columnheader', { name: /Forward RC/ })
    const drawdownHeader = screen.getByRole('columnheader', { name: /Current DD/ })
    const headerCells = Array.from(unrealizedValueHeader.parentElement!.children)
    const unrealizedValueIndex = headerCells.indexOf(unrealizedValueHeader)
    const unrealizedPctIndex = headerCells.indexOf(unrealizedPctHeader)
    const chartIndex = headerCells.indexOf(chartHeader)
    const returnIndex = headerCells.indexOf(returnHeader)
    const volatilityIndex = headerCells.indexOf(volatilityHeader)
    const forwardRiskIndex = headerCells.indexOf(forwardRiskHeader)
    const drawdownIndex = headerCells.indexOf(drawdownHeader)
    const eventRow = screen.getByRole('cell', { name: /Carried Option/ }).closest('tr')
    const quotedRow = screen.getByRole('cell', { name: /Quoted Fund/ }).closest('tr')

    expect(eventRow!.children[unrealizedValueIndex]).toHaveTextContent('N/A')
    expect(eventRow!.children[unrealizedPctIndex]).toHaveTextContent('N/A')
    expect(eventRow!.children[chartIndex]).toHaveTextContent('N/A')
    expect(eventRow!.children[returnIndex]).toHaveTextContent('N/A')
    expect(eventRow!.children[volatilityIndex]).toHaveTextContent('N/A')
    expect(eventRow!.children[forwardRiskIndex]).toHaveTextContent('N/A')
    expect(eventRow!.children[drawdownIndex]).toHaveTextContent('N/A')
    expect(quotedRow!.children[unrealizedValueIndex]).toHaveTextContent('$0.00')
    expect(quotedRow!.children[unrealizedPctIndex]).toHaveTextContent('0.00%')
    expect(quotedRow!.children[returnIndex]).toHaveTextContent('+3.45%')

    const totalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(totalRow!.querySelector('[data-column-key="day_change_value"]')).toHaveTextContent('N/A')
    expect(totalRow!.querySelector('[data-column-key="day_change_pct"]')).toHaveTextContent('N/A')
    expect(totalRow!.querySelector('[data-column-key="unrealized_value"]')).toHaveTextContent('N/A')
    expect(totalRow!.querySelector('[data-column-key="unrealized_pct"]')).toHaveTextContent('N/A')

    await user.click(screen.getByRole('button', { name: /Group By/ }))
    await user.click(within(screen.getByRole('dialog', { name: 'Group holdings' })).getByRole('button', { name: 'Currency' }))
    const groupRow = document.querySelector('.holdings-group-row')
    expect(groupRow!.querySelector('[data-column-key="day_change_value"]')).toHaveTextContent('N/A')
    expect(groupRow!.querySelector('[data-column-key="unrealized_value"]')).toHaveTextContent('N/A')
    expect(groupRow!.querySelector('[data-column-key="unrealized_pct"]')).toHaveTextContent('N/A')
    expect(groupRow!.querySelector('[data-column-key="forward_risk_share"]')).toHaveTextContent('N/A')

    await user.click(screen.getByRole('button', { name: 'Download' }))
    await user.click(screen.getByRole('menuitem', { name: 'CSV' }))
    expect(tableExportMocks.downloadTable).toHaveBeenCalledTimes(1)
    const exportedRows = tableExportMocks.downloadTable.mock.calls[0][1] as Array<Array<string | number | null>>
    const exportHeader = exportedRows[0]
    const exportUnrealizedValueIndex = exportHeader.indexOf('Unrealized P&L')
    const exportUnrealizedPctIndex = exportHeader.indexOf('Unrealized Return')
    const exportReturnIndex = exportHeader.indexOf('1M Return')
    const exportVolatilityIndex = exportHeader.indexOf('1M Vol')
    const exportForwardRiskIndex = exportHeader.indexOf('Forward RC')
    const exportedEventRow = exportedRows.find((row) => row.includes('Carried Option'))
    const exportedQuotedRow = exportedRows.find((row) => row.includes('Quoted Fund'))
    const exportedTotalRow = exportedRows.find((row) => row[0] === 'Portfolio Total')
    expect(exportedEventRow?.[exportUnrealizedValueIndex]).toBe('N/A')
    expect(exportedEventRow?.[exportUnrealizedPctIndex]).toBe('N/A')
    expect(exportedEventRow?.[exportReturnIndex]).toBe('N/A')
    expect(exportedEventRow?.[exportVolatilityIndex]).toBe('N/A')
    expect(exportedEventRow?.[exportForwardRiskIndex]).toBe('N/A')
    expect(exportedQuotedRow?.[exportUnrealizedValueIndex]).toBe(0)
    expect(exportedQuotedRow?.[exportUnrealizedPctIndex]).toBe(0)
    expect(exportedTotalRow?.[exportUnrealizedValueIndex]).toBe('N/A')
    expect(exportedTotalRow?.[exportUnrealizedPctIndex]).toBe('N/A')
  })

  it('excludes cash from the unrealized-return denominator', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValueOnce(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            quantity: 10,
            last_price: 12,
            market_value: 120,
            market_value_base: 120,
            cost_basis: 100,
            cost_basis_base: 100,
            allocation: 0.6,
          }),
          holdingFixture({
            line_id: 'cash:USD',
            holding_region: 'cash_and_settlement',
            instrument_core: instrumentFixture({
              instrument_id: 'cash:USD',
              instrument_name: 'USD Cash',
              instrument_type: 'cash',
              currency: 'USD',
              identifiers: [],
            }),
            quantity: 80,
            last_price: 1,
            market_value: 80,
            market_value_base: 80,
            cost_basis_method: null,
            cost_basis: null,
            cost_basis_base: null,
            allocation: 0.4,
            day_change_pct: 0,
            day_change_value: 0,
            day_change_value_base: 0,
            price_chart_1m: [],
            price_chart_3m: [],
            price_chart_6m: [],
            price_chart_1y: [],
            coverage_status: 'cash',
            account_ids: ['cash-account'],
            account_count: 1,
            open_position_lot_count: 0,
          }),
        ],
        totals: {
          market_value: 200,
          day_change_pct: 0,
          day_change_value: 0,
          cost_basis: 100,
          allocation: 1,
        },
      }),
    )

    const user = userEvent.setup()
    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    const totalRow = (await screen.findByText('Portfolio Total (USD)')).closest('tr')
    expect(totalRow).not.toBeNull()
    expect(totalRow!.querySelector('[data-column-key="unrealized_pct"]')).toHaveTextContent('+20.00%')
  })

  it('uses current base-market-value weights for group and total instrument returns', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:fund-a',
            instrument_core: instrumentFixture({
              instrument_id: 'fund-a',
              instrument_name: 'Fund A',
              instrument_type: 'fund',
              currency: 'USD',
              identifiers: [{ identifier_type: 'ticker', identifier_value: 'FUNDA', is_primary: true }],
            }),
            market_value: 750,
            market_value_base: 750,
            cost_basis: 750,
            cost_basis_base: 750,
            allocation: 0.75,
            instrument_return_1m: 0.10,
          }),
          holdingFixture({
            line_id: 'holding:fund-b',
            instrument_core: instrumentFixture({
              instrument_id: 'fund-b',
              instrument_name: 'Fund B',
              instrument_type: 'fund',
              currency: 'USD',
              identifiers: [{ identifier_type: 'ticker', identifier_value: 'FUNDB', is_primary: true }],
            }),
            market_value: 250,
            market_value_base: 250,
            cost_basis: 250,
            cost_basis_base: 250,
            allocation: 0.25,
            instrument_return_1m: -0.10,
          }),
        ],
        totals: {
          market_value: 1000,
          day_change_pct: 0.01,
          day_change_value: 10,
          cost_basis: 1000,
          allocation: 1,
        },
      }),
    )
    const user = userEvent.setup()

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    await screen.findByRole('cell', { name: /Fund A/ })
    await user.click(screen.getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))
    await user.click(screen.getByRole('button', { name: /Group By\s*: None/ }))
    await user.click(screen.getByRole('button', { name: 'Instrument Type' }))

    const groupRow = document.querySelector('.holdings-group-row')
    const totalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(groupRow).not.toBeNull()
    expect(totalRow).not.toBeNull()
    expect(groupRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('+5.00%')
    expect(totalRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('+5.00%')
    expect(groupRow!.querySelector('[data-column-key="instrument_holding_max_drawdown"]')?.textContent?.trim()).toBe('')
    expect(totalRow!.querySelector('[data-column-key="instrument_holding_max_drawdown"]')?.textContent?.trim()).toBe('')
    expect(groupRow!.querySelector('[data-column-key="holding_date"]')?.textContent?.trim()).toBe('')
    expect(totalRow!.querySelector('[data-column-key="holding_date"]')?.textContent?.trim()).toBe('')
    expect(groupRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveAttribute(
      'data-aggregation-kind',
      'current_weight_return',
    )
    expect(groupRow!.querySelector('[data-column-key="instrument_holding_max_drawdown"]')).toHaveAttribute(
      'data-aggregation-kind',
      'none',
    )
  })

  it('withholds grouped local-currency returns when the current basket spans currencies', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:usd-fund',
            instrument_core: instrumentFixture({
              instrument_id: 'usd-fund',
              instrument_name: 'USD Fund',
              instrument_type: 'fund',
              currency: 'USD',
              identifiers: [],
            }),
            market_value: 500,
            market_value_base: 500,
            cost_basis: 500,
            cost_basis_base: 500,
            allocation: 0.5,
            instrument_return_1m: 0.1,
          }),
          holdingFixture({
            line_id: 'holding:cny-fund',
            instrument_core: instrumentFixture({
              instrument_id: 'cny-fund',
              instrument_name: 'CNY Fund',
              instrument_type: 'fund',
              currency: 'CNY',
              identifiers: [],
            }),
            market_value: 3_500,
            market_value_base: 500,
            cost_basis: 3_500,
            cost_basis_base: 500,
            allocation: 0.5,
            instrument_return_1m: 0.1,
          }),
        ],
        totals: {
          market_value: 1000,
          day_change_pct: 0,
          day_change_value: 0,
          cost_basis: 1000,
          allocation: 1,
        },
      }),
    )
    const user = userEvent.setup()

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    await screen.findByRole('cell', { name: /CNY Fund/ })
    await user.click(screen.getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))
    await user.click(screen.getByRole('button', { name: /Group By\s*: None/ }))
    await user.click(screen.getByRole('button', { name: 'Instrument Type' }))

    const groupRow = document.querySelector('.holdings-group-row')
    const totalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(groupRow).not.toBeNull()
    expect(totalRow).not.toBeNull()
    expect(groupRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('—')
    expect(totalRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('—')
  })

  it('withholds a current-basket return when any material current holding lacks the window', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:covered',
            market_value: 900,
            market_value_base: 900,
            cost_basis: 900,
            cost_basis_base: 900,
            allocation: 0.9,
            instrument_return_1m: 0.10,
          }),
          holdingFixture({
            line_id: 'holding:missing',
            instrument_core: instrumentFixture({
              instrument_id: 'fund-missing',
              instrument_name: 'Missing Return Fund',
              instrument_type: 'fund',
              currency: 'USD',
              identifiers: [],
            }),
            market_value: 100,
            market_value_base: 100,
            cost_basis: 100,
            cost_basis_base: 100,
            allocation: 0.1,
            instrument_return_1m: null,
          }),
        ],
        totals: {
          market_value: 1000,
          day_change_pct: 0.01,
          day_change_value: 10,
          cost_basis: 1000,
          allocation: 1,
        },
      }),
    )
    const user = userEvent.setup()

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    await screen.findByRole('cell', { name: /Missing Return Fund/ })
    await user.click(screen.getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))

    const totalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(totalRow).not.toBeNull()
    expect(totalRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('—')
  })

  it('does not substitute a chart date when the valuation quote date is unavailable', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValueOnce(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            quote_as_of_date: null,
            price_chart_6m: [
              { date: '2026-06-15', value: 75 },
              { date: '2026-07-15', value: 80 },
            ],
          }),
        ],
      }),
    )

    const user = userEvent.setup()
    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    const holdingRow = (await screen.findByRole('cell', { name: /Alpha Fund/ })).closest('tr')
    const quoteDateHeader = screen.getByRole('columnheader', { name: /Quote Date/ })
    const quoteDateColumnIndex = Array.from(quoteDateHeader.parentElement!.children).indexOf(quoteDateHeader)
    expect(holdingRow!.children[quoteDateColumnIndex]).toHaveTextContent('—')
    expect(holdingRow!.children[quoteDateColumnIndex]).not.toHaveTextContent('2026-07-15')
  })

  it('discloses alternate-basis selection and partial history instead of hiding the trend semantics', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            instrument_trend_basis: 'close',
            instrument_trend_coverage: {
              state: 'partial',
              observation_count: 40,
              start_date: '2026-05-01',
              end_date: '2026-07-15',
              available_return_windows: ['1w', 'mtd'],
            },
            instrument_trend_reason: 'selected_more_complete_alternate_series',
          }),
        ],
      }),
    )

    const user = userEvent.setup()
    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await openAdvancedTable(user)
    expect(await screen.findByText('Trend: Close · Partial · 40 observations')).toBeInTheDocument()
    expect(
      screen.getByText('Using Close because it provides more complete history than the preferred basis.'),
    ).toBeInTheDocument()
  })
})
