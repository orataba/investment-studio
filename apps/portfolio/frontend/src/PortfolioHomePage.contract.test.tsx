import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfolioHomePage from './pages/PortfolioHomePage'
import {
  fcnContractFixture,
  holdingFixture,
  holdingsWorkspaceFixture,
  instrumentFixture,
  optionContractFixture,
} from './test/portfolioFixtures'
import type {
  PortfolioHoldingRow,
  PortfolioTaxonomyCatalogResponse,
} from './lib/api'

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

function taxonomyCatalogFixture(
  overrides: Partial<PortfolioTaxonomyCatalogResponse> = {},
): PortfolioTaxonomyCatalogResponse {
  return {
    portfolio_id: '3',
    default_planning_taxonomy_id: null,
    taxonomies: [],
    taxonomy_nodes: [],
    taxonomy_assignments: [],
    analytics_scope_policy_version: 0,
    analytics_scope_policies: [],
    analytics_taxonomy_selections: [],
    instrument_universe: [],
    target_sets: [],
    target_set_lines: [],
    target_set_integrity_issues: [],
    ...overrides,
  }
}

function renderHoldings(
  workspace = holdingsWorkspaceFixture(),
  taxonomyCatalog = taxonomyCatalogFixture(),
) {
  apiMocks.getHoldingsWorkspace.mockResolvedValue(workspace)
  apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue(taxonomyCatalog)
  return render(
    <MemoryRouter initialEntries={['/portfolios/3/holdings']}>
      <Routes>
        <Route path="/portfolios/:portfolioId/holdings" element={<PortfolioHomePage />} />
        <Route
          path="/portfolios/:portfolioId/holdings/:instrumentId"
          element={<div data-testid="holding-detail-route">Holding detail</div>}
        />
      </Routes>
    </MemoryRouter>,
  )
}

async function waitForHoldings() {
  await waitFor(() => {
    const hasSection = ['Securities', 'FCN', 'Options', 'Cash & Settlement'].some(
      (name) => screen.queryByRole('region', { name }) != null,
    )
    expect(hasSection || screen.queryByText(/^No holdings as of /) != null).toBe(true)
  })
}

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

function cashHolding(overrides: Partial<PortfolioHoldingRow> = {}) {
  return holdingFixture({
    line_id: 'cash:USD',
    holding_kind: 'settled_cash',
    holding_category: 'cash_and_settlement',
    instrument_core: instrumentFixture({
      instrument_id: 'cash:USD',
      instrument_name: 'USD Cash',
      instrument_type: 'cash',
      currency: 'USD',
      identifiers: [],
    }),
    quantity: 200,
    last_price: 1,
    market_value: 200,
    market_value_base: 200,
    cost_basis: null,
    cost_basis_base: null,
    cash_cost_basis_base: 200,
    cash_cost_basis_fx_rate_to_base: 1,
    cash_fx_coverage_status: 'complete',
    unrealized_fx_pnl_base: 0,
    allocation: 0.2,
    day_change_pct: 0,
    day_change_value: 0,
    day_change_value_base: 0,
    price_chart_1m: [],
    price_chart_3m: [],
    price_chart_6m: [],
    price_chart_1y: [],
    instrument_return_1w: null,
    instrument_return_1m: null,
    instrument_return_mtd: null,
    instrument_return_ytd: null,
    coverage_status: 'cash',
    available_for_trading: true,
    risk_eligible: false,
    forward_risk_status: 'modeled_zero',
    forward_risk_share: 0,
    forward_contribution_to_variance: 0,
    forward_annualized_volatility: 0,
    account_ids: ['cash-account'],
    account_count: 1,
    open_position_lot_count: 0,
    ...overrides,
  })
}

function fcnHolding() {
  return holdingFixture({
    line_id: 'holding:fcn-1',
    position_reference_id: 'fcn-1',
    derivative_contract_id: 'fcn-1',
    holding_kind: 'derivative_contract',
    holding_category: 'derivatives',
    instrument_core: null,
    derivative_contract: fcnContractFixture(),
    quantity: 1,
    market_value: 500,
    market_value_base: 500,
    carrying_value: 500,
    carrying_value_base: 500,
    cost_basis: 500,
    cost_basis_base: 500,
    allocation: 0.5,
    coverage_status: 'event-cost',
    valuation_basis: 'carried_cost',
    fair_value: null,
    fair_value_coverage_status: 'unavailable',
    performance_eligible: false,
    risk_eligible: false,
    forward_risk_status: 'excluded',
    forward_risk_share: null,
    forward_contribution_to_variance: null,
    forward_annualized_volatility: null,
  })
}

function writtenOptionHolding() {
  return holdingFixture({
    line_id: 'broker:option-1:obligation',
    position_reference_id: 'option-1',
    derivative_contract_id: 'option-1',
    holding_kind: 'option_obligation',
    holding_category: 'derivatives',
    instrument_core: null,
    derivative_contract: optionContractFixture(),
    quantity: -2,
    market_value: -300,
    market_value_base: -300,
    cost_basis: null,
    cost_basis_base: null,
    allocation: -0.3,
    open_contract_quantity: 2,
    required_underlying_quantity: 200,
    obligation_status: 'open',
    related_underlying_id: 'equity-1',
    expiry_date: '2026-12-18',
    days_to_expiry: 156,
    strike: 110,
    option_type: 'call',
    contract_multiplier: 100,
    strike_notional: 22_000,
    strike_notional_base: 22_000,
    premium_basis_remaining: 300,
    liability_value: 300,
    liability_value_base: 300,
    carrying_value: 300,
    carrying_value_base: 300,
    coverage_status: 'event-liability',
    valuation_basis: 'premium_liability',
    fair_value: null,
    fair_value_coverage_status: 'unavailable',
    performance_eligible: false,
    risk_eligible: false,
    is_liability: true,
    forward_risk_status: 'excluded',
    forward_risk_share: null,
    forward_contribution_to_variance: null,
    forward_annualized_volatility: null,
  })
}

describe('Holdings rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue(taxonomyCatalogFixture())
    apiMocks.getPortfolioTableViewStore.mockImplementation(async () => ({ store: null }))
    apiMocks.savePortfolioTableViewStore.mockResolvedValue({})
  })

  it('renders each populated category with aligned table controls and title counts', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [holdingFixture(), fcnHolding(), writtenOptionHolding(), cashHolding()],
      }),
    )
    await waitForHoldings()

    expect(screen.getByRole('region', { name: 'Securities' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'FCN' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Options' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Cash & Settlement' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Portfolio Total' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Derivatives' })).not.toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'FCN holdings' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Option holdings' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Cash and settlement holdings' })).toBeInTheDocument()

    const configurableSections = [
      ['Securities', 'Default', 'Securities Columns'],
      ['FCN', 'Default', 'FCN Columns'],
      ['Options', 'Default', 'Options Columns'],
    ] as const
    for (const [name, view, columnsLabel] of configurableSections) {
      const region = screen.getByRole('region', { name })
      expect(await within(region).findByRole('button', { name: new RegExp(`View\\s*: ${view}`) })).toBeInTheDocument()
      expect(within(region).getByRole('button', { name: columnsLabel })).toHaveTextContent('Columns')
    }
    const cashRegion = screen.getByRole('region', { name: 'Cash & Settlement' })
    expect(within(cashRegion).queryByRole('button', { name: /View\s*:/ })).not.toBeInTheDocument()
    expect(within(cashRegion).queryByRole('button', { name: /Columns/ })).not.toBeInTheDocument()

    for (const name of ['Securities', 'FCN', 'Options', 'Cash & Settlement']) {
      const region = screen.getByRole('region', { name })
      const title = within(region).getByRole('heading', { name })
      expect(title.parentElement).toHaveClass('holdings-section-title')
      expect(title.parentElement?.querySelector('.holdings-section-count')).toHaveTextContent('1')
      expect(region.querySelector('.holdings-section-heading-actions .holdings-section-count')).toBeNull()
    }
    expect(within(screen.getByRole('region', { name: 'Securities' })).getByRole('button', { name: /Group By\s*: None/ })).toBeInTheDocument()
    expect(screen.queryByText(/Contract terms and carrying amounts are explicit/)).not.toBeInTheDocument()
    expect(screen.getByText('Securities Subtotal (USD)')).toBeInTheDocument()
    expect(screen.queryByText('Portfolio Total (USD)')).not.toBeInTheDocument()
  })

  it('omits Operational Status and categories without holdings', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        operational_summary: {
          expiry_buckets: [
            {
              bucket: 'next_7_days',
              obligation_count: 1,
              open_contract_quantity: 2,
              required_underlying_quantity: 200,
              carrying_liability_base: 300,
            },
          ],
          option_obligation_exposure: {
            obligation_count: 1,
            open_contract_quantity: 2,
            underlying_equivalent_quantity: 200,
            strike_notional_base: 22_000,
          },
          settlement_exposure: {
            pending_line_count: 1,
            receivable_base: 100,
            payable_base: 0,
            net_base: 100,
            earliest_settlement_date: '2026-07-14',
            overdue_line_count: 1,
            unavailable_base_line_count: 0,
          },
        },
        operational_alerts: [
          {
            code: 'overdue_settlement',
            severity: 'critical',
            title: 'Overdue settlement',
            message: 'A settlement is overdue.',
            related_line_ids: ['holding:asset-1'],
          },
        ],
      }),
    )
    await waitForHoldings()

    expect(screen.getByRole('region', { name: 'Securities' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Portfolio Total' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'FCN' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Options' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Cash & Settlement' })).not.toBeInTheDocument()
    expect(screen.queryByText('Operational Status')).not.toBeInTheDocument()
    expect(screen.queryByText('Overdue settlement')).not.toBeInTheDocument()
  })

  it('opens details only from the instrument name, not an ordinary table cell', async () => {
    const user = userEvent.setup()
    renderHoldings()
    await waitForHoldings()

    const securityTable = screen.getByRole('table', { name: 'Security holdings' })
    const quantityCell = securityTable.querySelector<HTMLTableCellElement>(
      'tbody td[data-column-key="quantity"]',
    )
    expect(quantityCell).not.toBeNull()
    fireEvent.click(quantityCell!)
    expect(screen.getByRole('table', { name: 'Security holdings' })).toBeInTheDocument()
    expect(screen.queryByTestId('holding-detail-route')).not.toBeInTheDocument()

    await user.click(within(securityTable).getByRole('button', { name: 'Alpha Fund' }))
    expect(await screen.findByTestId('holding-detail-route')).toBeInTheDocument()
  })

  it('shows one holdings empty state without empty tables', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [],
        totals: {
          nav: 0,
          market_value: 0,
          day_change_pct: null,
          day_change_value: null,
          cost_basis: 0,
          allocation: 0,
        },
      }),
    )
    await waitForHoldings()

    expect(screen.getByText('No holdings as of 2026-07-15.')).toHaveAttribute('role', 'status')
    expect(screen.queryByRole('region', { name: 'Securities' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'FCN' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Options' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Cash & Settlement' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Portfolio Total' })).not.toBeInTheDocument()
  })

  it('surfaces a failed Security view store without blocking holdings facts', async () => {
    apiMocks.getPortfolioTableViewStore.mockImplementation(
      async (_portfolioId: string, viewScope: string) => {
        if (viewScope === 'holdings') {
          throw new Error('View store offline.')
        }
        return { store: null }
      },
    )
    renderHoldings()
    await waitForHoldings()

    expect(screen.getByRole('alert')).toHaveTextContent('View store offline.')
    expect(screen.getByText('Views unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^View\s*:/ })).not.toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Alpha Fund' })).toBeInTheDocument()
  })

  it('surfaces a failed section view save', async () => {
    apiMocks.savePortfolioTableViewStore.mockRejectedValue(new Error('View save offline.'))
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [fcnHolding()],
      }),
    )
    const user = userEvent.setup()
    await waitForHoldings()

    const fcnRegion = screen.getByRole('region', { name: 'FCN' })
    await user.click(await within(fcnRegion).findByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Terms & Events' }))

    expect(await screen.findByRole('status')).toHaveTextContent('View save failed')
    expect(screen.getByRole('status')).toHaveAttribute('title', 'View save offline.')
  })

  it('keeps each table field selection and persisted view scope independent', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [holdingFixture(), fcnHolding(), writtenOptionHolding(), cashHolding()],
      }),
    )
    const user = userEvent.setup()
    await waitForHoldings()
    const fcnRegion = screen.getByRole('region', { name: 'FCN' })
    const optionsRegion = screen.getByRole('region', { name: 'Options' })
    await within(fcnRegion).findByRole('button', { name: /View\s*: Default/ })

    await user.click(within(fcnRegion).getByRole('button', { name: 'FCN Columns' }))
    const dialog = screen.getByRole('dialog', { name: 'Choose FCN columns' })
    const couponField = within(dialog).getByText('Annual Coupon').closest('label')!
    await user.click(within(couponField).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Update' }))

    expect(
      within(screen.getByRole('table', { name: 'FCN holdings' })).queryByRole(
        'columnheader',
        { name: 'Annual Coupon' },
      ),
    ).not.toBeInTheDocument()
    expect(
      within(screen.getByRole('table', { name: 'Option holdings' })).getByRole(
        'columnheader',
        { name: 'Type' },
      ),
    ).toBeInTheDocument()
    expect(within(fcnRegion).getByRole('button', { name: /View\s*: Default$/ })).toBeInTheDocument()
    expect(within(fcnRegion).queryByRole('button', { name: 'Update View' })).not.toBeInTheDocument()
    expect(within(optionsRegion).getByRole('button', { name: /View\s*: Default$/ })).toBeInTheDocument()

    await waitFor(() => {
      const savedStore = apiMocks.savePortfolioTableViewStore.mock.calls.find(
        ([portfolioId, viewScope]) => portfolioId === '3' && viewScope === 'holdings_fcn',
      )?.[2] as { views: Array<{ id: string; state: { columns: string[] } }> } | undefined
      expect(savedStore?.views.find((view) => view.id === 'position')?.state.columns).not.toContain('coupon')
    })

    await user.click(screen.getByRole('button', { name: 'Download' }))
    await user.click(screen.getByRole('menuitem', { name: 'CSV' }))
    const exportedRows = tableExportMocks.downloadTable.mock.calls[0][1] as Array<
      Array<string | number | null>
    >
    const fcnSectionIndex = exportedRows.findIndex((row) => row[0] === 'FCN')
    expect(exportedRows[fcnSectionIndex + 1]).not.toContain('Annual Coupon')

    for (const scope of [
      'holdings',
      'holdings_fcn',
      'holdings_options',
    ]) {
      expect(apiMocks.getPortfolioTableViewStore).toHaveBeenCalledWith('3', scope)
    }
  })

  it('keeps the canonical Security Return & Risk system view authoritative', async () => {
    apiMocks.getPortfolioTableViewStore.mockImplementation(
      async (_portfolioId: string, viewScope: string) => ({
        store:
          viewScope === 'holdings'
            ? { activeViewId: 'return-risk', views: [] }
            : null,
      }),
    )
    renderHoldings()
    await waitFor(() => {
      expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledWith('3', {
        as_of_date: undefined,
        include_details: true,
      })
    })
    await waitFor(() => {
      expect(within(screen.getByRole('region', { name: 'Securities' })).getByRole('button', { name: /View\s*: Return & Risk/ })).toBeInTheDocument()
      expect(screen.getByRole('table', { name: 'Security holdings' })).toBeInTheDocument()
    })
    const securityTable = screen.getByRole('table', { name: 'Security holdings' })
    for (const label of [
      'Chart 6M',
      '1W Total Return',
      '1M Total Return',
      '3M Total Return',
      'MTD Total Return',
      'YTD Total Return',
      'Forward RC',
      'Total Unrealized Return (Base)',
    ]) {
      const escapedLabel = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      expect(within(securityTable).getByRole('columnheader', { name: new RegExp(`^${escapedLabel}`) })).toBeInTheDocument()
    }
  })

  it('restores a saved column override for the FCN Default view', async () => {
    apiMocks.getPortfolioTableViewStore.mockImplementation(
      async (_portfolioId: string, viewScope: string) => ({
        store:
          viewScope === 'holdings_fcn'
            ? {
                activeViewId: 'position',
                views: [
                  {
                    id: 'position',
                    name: 'Default',
                    state: { columns: ['contract', 'currency', 'notional'] },
                  },
                ],
              }
            : null,
      }),
    )
    renderHoldings(holdingsWorkspaceFixture({ rows: [fcnHolding()] }))
    await waitForHoldings()

    const fcnRegion = screen.getByRole('region', { name: 'FCN' })
    expect(await within(fcnRegion).findByRole('button', { name: /View\s*: Default$/ })).toBeInTheDocument()
    expect(
      within(screen.getByRole('table', { name: 'FCN holdings' })).queryByRole(
        'columnheader',
        { name: 'Annual Coupon' },
      ),
    ).not.toBeInTheDocument()
    expect(apiMocks.savePortfolioTableViewStore).not.toHaveBeenCalled()
  })

  it('updates the Security Default view directly from the Columns dialog', async () => {
    renderHoldings()
    const user = userEvent.setup()
    await waitForHoldings()

    const securitiesRegion = screen.getByRole('region', { name: 'Securities' })
    await user.click(within(securitiesRegion).getByRole('button', { name: 'Securities Columns' }))
    const dialog = screen.getByRole('dialog', { name: 'Choose security columns' })
    await user.click(within(dialog).getByRole('button', { name: 'Position' }))
    const quantityField = within(dialog).getByText('Quantity').closest('label')!
    await user.click(within(quantityField).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Update' }))

    expect(within(securitiesRegion).getByRole('button', { name: /View\s*: Default$/ })).toBeInTheDocument()
    expect(within(securitiesRegion).queryByRole('button', { name: 'Update View' })).not.toBeInTheDocument()
    expect(
      within(screen.getByRole('table', { name: 'Security holdings' })).queryByRole(
        'columnheader',
        { name: 'Quantity' },
      ),
    ).not.toBeInTheDocument()
    await waitFor(() => {
      const savedStore = apiMocks.savePortfolioTableViewStore.mock.calls.find(
        ([portfolioId, viewScope]) => portfolioId === '3' && viewScope === 'holdings',
      )?.[2] as { views: Array<{ id: string; state: { columns: string[] } }> } | undefined
      expect(savedStore?.views.find((view) => view.id === 'default')?.state.columns).not.toContain('quantity')
    })

    apiMocks.savePortfolioTableViewStore.mockClear()
    await user.click(within(securitiesRegion).getByRole('button', { name: /Group By\s*: None/ }))
    await user.click(within(screen.getByRole('dialog', { name: 'Choose grouping' })).getByRole('button', { name: 'Currency' }))
    expect(screen.queryByRole('button', { name: 'Update View' })).not.toBeInTheDocument()
    await waitFor(() => {
      const savedStore = apiMocks.savePortfolioTableViewStore.mock.calls.find(
        ([portfolioId, viewScope]) => portfolioId === '3' && viewScope === 'holdings',
      )?.[2] as { views: Array<{ id: string; state: { groupBy: string } }> } | undefined
      expect(savedStore?.views.find((view) => view.id === 'default')?.state.groupBy).toBe('currency')
    })

    apiMocks.savePortfolioTableViewStore.mockClear()
    await user.click(within(securitiesRegion).getByRole('button', { name: 'Sort Instrument: ascending' }))
    await waitFor(() => {
      const savedStore = apiMocks.savePortfolioTableViewStore.mock.calls.find(
        ([portfolioId, viewScope]) => portfolioId === '3' && viewScope === 'holdings',
      )?.[2] as { views: Array<{ id: string; state: { sortField: string | null } }> } | undefined
      expect(savedStore?.views.find((view) => view.id === 'default')?.state.sortField).toBe('instrument')
    })
  })

  it('does not render a prior portfolio workspace while a new portfolio is loading', async () => {
    const nextWorkspace = deferred<ReturnType<typeof holdingsWorkspaceFixture>>()
    apiMocks.getHoldingsWorkspace
      .mockResolvedValueOnce(holdingsWorkspaceFixture())
      .mockReturnValueOnce(nextWorkspace.promise)

    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/portfolios/3/holdings']}>
        <HoldingsRouteHarness />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('cell', { name: 'Alpha Fund' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Switch portfolio' }))
    expect(screen.queryByRole('cell', { name: 'Alpha Fund' })).not.toBeInTheDocument()

    act(() =>
      nextWorkspace.resolve(
        holdingsWorkspaceFixture({
          portfolio_id: '4',
          rows: [
            holdingFixture({
              instrument_core: instrumentFixture({
                instrument_id: 'asset-4',
                instrument_name: 'Beta Fund',
              }),
            }),
          ],
        }),
      ),
    )
    expect(await screen.findByRole('cell', { name: 'Beta Fund' })).toBeInTheDocument()
  })

  it('puts FCN and option facts in explicit derivative columns instead of name annotations', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [fcnHolding(), writtenOptionHolding()],
        totals: {
          nav: 200,
          market_value: 200,
          day_change_pct: null,
          day_change_value: null,
          cost_basis: 500,
          allocation: 0.2,
        },
      }),
    )
    const user = userEvent.setup()
    await waitForHoldings()

    const fcnRegion = screen.getByRole('region', { name: 'FCN' })
    const fcnTable = screen.getByRole('table', { name: 'FCN holdings' })
    for (const label of [
      'Notional',
      'Underlying Terms',
      'Annual Coupon',
      'Remaining Basis',
    ]) {
      expect(within(fcnTable).getByRole('columnheader', { name: label })).toBeInTheDocument()
    }
    const fcnNameCell = within(fcnTable).getByRole('cell', { name: 'Alpha FCN' })
    expect(fcnNameCell).toHaveTextContent(/^Alpha FCN$/)
    expect(within(fcnTable).getByText(/Initial 100\.0000/)).toBeInTheDocument()

    expect(fcnTable).toHaveStyle({ minWidth: '1970px' })
    await user.click(await within(fcnRegion).findByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Terms & Events' }))
    expect(fcnTable).toHaveStyle({ minWidth: '1970px' })
    for (const label of ['Final Observation', 'Issuer', 'Counterparty']) {
      expect(within(fcnTable).getByRole('columnheader', { name: label })).toBeInTheDocument()
    }
    expect(within(fcnTable).getByText('Fixture Issuer')).toBeInTheDocument()

    await user.click(within(fcnRegion).getByRole('button', { name: /View\s*: Terms & Events/ }))
    await user.click(screen.getByRole('option', { name: 'Valuation' }))
    expect(fcnTable).toHaveStyle({ minWidth: '1970px' })
    expect(within(fcnTable).getByRole('columnheader', { name: 'Fair Value Status' })).toBeInTheDocument()

    const optionsRegion = screen.getByRole('region', { name: 'Options' })
    const optionTable = screen.getByRole('table', { name: 'Option holdings' })
    for (const label of [
      'Side',
      'Type',
      'Underlying',
      'Strike',
      'Open Contracts',
      'Remaining Basis',
      'Strike Notional (USD)',
      'Status',
    ]) {
      expect(within(optionTable).getByRole('columnheader', { name: label })).toBeInTheDocument()
    }
    expect(within(optionTable).getByText('Written')).toBeInTheDocument()
    expect(within(optionTable).getAllByText('$22,000.00')).toHaveLength(2)

    expect(optionTable).toHaveStyle({ minWidth: '2380px' })
    await user.click(within(optionsRegion).getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Contract Terms' }))
    expect(optionTable).toHaveStyle({ minWidth: '2380px' })
    expect(within(optionTable).getByRole('columnheader', { name: 'Underlying Equivalent' })).toBeInTheDocument()
    expect(within(optionTable).getByRole('columnheader', { name: 'Multiplier' })).toBeInTheDocument()

    await user.click(within(optionsRegion).getByRole('button', { name: /View\s*: Contract Terms/ }))
    await user.click(screen.getByRole('option', { name: 'Valuation' }))
    expect(optionTable).toHaveStyle({ minWidth: '2380px' })
    expect(within(optionTable).getByRole('columnheader', { name: 'Basis Type' })).toBeInTheDocument()
    expect(within(optionTable).getByText('Remaining Premium')).toBeInTheDocument()
  })

  it('gives monetary balances settlement fields and cash FX attribution without security cost fields', async () => {
    const pending = cashHolding({
      line_id: 'pending:settlement_receivable',
      holding_kind: 'settlement_receivable',
      instrument_core: instrumentFixture({
        instrument_id: 'pending:settlement_receivable',
        instrument_name: 'Settlement receivable · Alpha Fund',
        instrument_type: 'other',
        currency: 'USD',
        identifiers: [],
      }),
      available_for_trading: false,
      settlement_date: '2026-07-16',
      pending_until_date: '2026-07-17',
      pending_status: 'awaiting_settlement',
      economic_instrument_id: 'asset-1',
      economic_instrument_ref: instrumentFixture(),
      coverage_status: 'pending-settlement',
    })
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [pending],
        totals: {
          nav: 200,
          market_value: 200,
          day_change_pct: 0,
          day_change_value: 0,
          cost_basis: 0,
          allocation: 1,
        },
      }),
    )
    await waitForHoldings()

    const table = screen.getByRole('table', { name: 'Cash and settlement holdings' })
    for (const label of [
      'Type',
      'Available',
      'Local Amount',
      'Base Value (USD)',
      'Portfolio Weight',
      'Settlement Date',
      'Pending Until',
      'Related Instrument',
      'Settlement Status',
    ]) {
      expect(within(table).getByRole('columnheader', { name: label })).toBeInTheDocument()
    }
    expect(within(table).queryByRole('columnheader', { name: /Cost Basis/ })).not.toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Unrealized FX P&L (USD)' })).toBeInTheDocument()
    expect(within(table).getByText('2026-07-16')).toBeInTheDocument()
    expect(within(table).getByText('Alpha Fund')).toBeInTheDocument()
    expect(screen.queryByText('Portfolio Total (USD)')).not.toBeInTheDocument()
  })

  it('groups only Securities with the current taxonomy, independently of the holdings date', async () => {
    const security = holdingFixture({
      taxonomy_id: 'historical-taxonomy',
      taxonomy_node_id: 'historical-leaf',
    })
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [security, fcnHolding(), cashHolding()],
      }),
      taxonomyCatalogFixture({
        default_planning_taxonomy_id: 'current-taxonomy',
        taxonomies: [
          {
            taxonomy_id: 'current-taxonomy',
            portfolio_id: '3',
            name: 'Current Allocation',
            taxonomy_type: 'allocation',
            primary_assignment_scope: 'instrument',
            planning_enabled: true,
            root_default_target_dimension: 'weight',
            status: 'active',
          },
        ],
        taxonomy_nodes: [
          {
            taxonomy_node_id: 'current-risk-assets',
            taxonomy_id: 'current-taxonomy',
            parent_taxonomy_node_id: null,
            node_name: 'Current Risk Assets',
            sort_order: 0,
            is_terminal: false,
            default_target_dimension: 'weight',
            status: 'active',
          },
          {
            taxonomy_node_id: 'current-listed-funds',
            taxonomy_id: 'current-taxonomy',
            parent_taxonomy_node_id: 'current-risk-assets',
            node_name: 'Current Listed Funds',
            sort_order: 0,
            is_terminal: true,
            default_target_dimension: 'weight',
            status: 'active',
          },
        ],
        taxonomy_assignments: [
          {
            assignment_id: 'current-assignment',
            taxonomy_id: 'current-taxonomy',
            target_scope: 'instrument',
            target_entity_id: 'asset-1',
            taxonomy_node_id: 'current-listed-funds',
            status: 'active',
          },
        ],
      }),
    )
    const user = userEvent.setup()
    await waitForHoldings()
    await user.click(within(screen.getByRole('region', { name: 'Securities' })).getByRole('button', { name: /Group By\s*: None/ }))
    await user.click(
      within(screen.getByRole('dialog', { name: 'Choose grouping' })).getByRole('button', {
        name: 'Taxonomy',
      }),
    )

    const securityTable = screen.getByRole('table', { name: 'Security holdings' })
    expect(within(securityTable).getAllByText('Current Risk Assets')).toHaveLength(1)
    expect(within(screen.getByRole('table', { name: 'FCN holdings' })).queryByText('Current Risk Assets')).not.toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Cash and settlement holdings' })).queryByText('Current Risk Assets')).not.toBeInTheDocument()
  })

  it('shows Unassigned when a Security has no assignment in the current taxonomy', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [holdingFixture()],
      }),
      taxonomyCatalogFixture({
        default_planning_taxonomy_id: 'current-taxonomy',
        taxonomies: [
          {
            taxonomy_id: 'current-taxonomy',
            portfolio_id: '3',
            name: 'Current Allocation',
            taxonomy_type: 'allocation',
            primary_assignment_scope: 'instrument',
            planning_enabled: true,
            root_default_target_dimension: 'weight',
            status: 'active',
          },
        ],
      }),
    )
    const user = userEvent.setup()
    await waitForHoldings()

    await user.click(within(screen.getByRole('region', { name: 'Securities' })).getByRole('button', { name: /Group By\s*: None/ }))
    await user.click(
      within(screen.getByRole('dialog', { name: 'Choose grouping' })).getByRole('button', {
        name: 'Taxonomy',
      }),
    )
    expect(document.querySelector('.holdings-subgroup-row')).toHaveTextContent('Unassigned')
  })

  it('fails closed when only local-currency returns exist for a non-base security', async () => {
    const foreignSecurity = holdingFixture({
      instrument_core: instrumentFixture({ currency: 'HKD' }),
      market_value: 7800,
      market_value_base: 1000,
      cost_basis_historical_base: 930,
      unrealized_return_base: 0.07,
      cost_basis: 7800,
      cost_basis_base: 1000,
      allocation: 1,
      day_change_pct: 0.05,
      day_change_value_base: 50,
      local_day_change_value_base: 30,
      fx_day_change_value_base: 20,
      fx_rate_to_base: 1 / 7.5,
      fx_rate_as_of_date: '2026-07-15',
      previous_fx_rate_to_base: 1 / 7.8,
      previous_fx_rate_as_of_date: '2026-07-14',
      fx_rate_source_instrument_ids: ['fx-usd-hkd'],
      fx_rate_stale: false,
      instrument_return_1m: 0.1,
    })
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [foreignSecurity],
        totals: {
          nav: 1000,
          market_value: 1000,
          day_change_pct: 0.05,
          day_change_value: 50,
          cost_basis: 1000,
          allocation: 1,
        },
      }),
    )
    await waitForHoldings()

    const securitiesRegion = screen.getByRole('region', { name: 'Securities' })
    const user = userEvent.setup()
    await user.click(within(securitiesRegion).getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))
    const securityRow = within(screen.getByRole('table', { name: 'Security holdings' })).getByRole('cell', { name: 'Alpha Fund' }).closest('tr')!
    expect(securityRow).toHaveTextContent('+10.00%')
    expect(securityRow).toHaveTextContent('+7.00%')
    expect(within(screen.getByRole('table', { name: 'Security holdings' })).queryByRole('columnheader', { name: /Day P&L/ })).not.toBeInTheDocument()
    const subtotalRow = screen.getByText('Securities Subtotal (USD)').closest('tr')!
    expect(subtotalRow.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('—')
  })

  it('keeps event-carried derivatives outside the Securities subtotal', async () => {
    const quotedSecurity = holdingFixture({
      market_value: 500,
      market_value_base: 500,
      cost_basis: 500,
      cost_basis_base: 500,
      cost_basis_historical_base: 500,
      unrealized_price_pnl: 0,
      unrealized_price_pnl_base: 0,
      unrealized_fx_pnl_base: 0,
      unrealized_pnl_base: 0,
      unrealized_return: 0,
      unrealized_return_base: 0,
      allocation: 0.5,
      instrument_return_1m: 0,
    })
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [quotedSecurity, fcnHolding()],
        totals: {
          nav: 1000,
          market_value: 1000,
          day_change_pct: null,
          day_change_value: null,
          cost_basis: 1000,
          allocation: 1,
        },
      }),
    )
    await waitForHoldings()

    const securitySubtotal = screen.getByText('Securities Subtotal (USD)').closest('tr')!
    expect(securitySubtotal.querySelector('[data-column-key="unrealized_pnl_base"]')).toHaveTextContent('$0.00')
    expect(screen.getByRole('table', { name: 'FCN holdings' })).toBeInTheDocument()
    expect(screen.queryByText('Portfolio Total (USD)')).not.toBeInTheDocument()
  })

  it('withholds a current-basket return when any material security lacks the window', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:covered',
            market_value: 900,
            market_value_base: 900,
            cost_basis: 900,
            cost_basis_base: 900,
            allocation: 0.9,
            instrument_return_1m: 0.1,
          }),
          holdingFixture({
            line_id: 'holding:missing',
            instrument_core: instrumentFixture({
              instrument_id: 'fund-missing',
              instrument_name: 'Missing Return Fund',
              currency: 'USD',
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
          nav: 1000,
          market_value: 1000,
          day_change_pct: 0,
          day_change_value: 0,
          cost_basis: 1000,
          allocation: 1,
        },
      }),
    )
    await waitForHoldings()

    const securitiesRegion = screen.getByRole('region', { name: 'Securities' })
    const user = userEvent.setup()
    await user.click(within(securitiesRegion).getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))
    expect(screen.getByRole('cell', { name: 'Missing Return Fund' })).toBeInTheDocument()
    const securitySubtotal = screen.getByText('Securities Subtotal (USD)').closest('tr')!
    expect(securitySubtotal.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('—')
  })

  it('keeps trend-basis diagnostics on Security hover rather than visible derivative-style annotations', async () => {
    renderHoldings(
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
    await waitForHoldings()

    const securityName = screen.getByRole('cell', { name: 'Alpha Fund' })
    expect(securityName.querySelector('.holding-name-stack')).toHaveAttribute(
      'title',
      'Trend: Close · Partial · 40 observations. Using Close because it provides more complete history than the preferred basis.',
    )
    expect(screen.queryByText('Trend: Close · Partial · 40 observations')).not.toBeInTheDocument()
  })

  it('exports sectioned schemas instead of reconstructing the obsolete unified table', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [holdingFixture(), fcnHolding(), writtenOptionHolding(), cashHolding()],
      }),
    )
    const user = userEvent.setup()
    await waitForHoldings()
    await user.click(screen.getByRole('button', { name: 'Download' }))
    await user.click(screen.getByRole('menuitem', { name: 'CSV' }))

    expect(tableExportMocks.downloadTable).toHaveBeenCalledTimes(1)
    const rows = tableExportMocks.downloadTable.mock.calls[0][1] as Array<Array<string | number | null>>
    expect(rows[0][0]).toBe('Holdings as of 2026-07-15')
    expect(rows.some((row) => row[0] === 'Securities')).toBe(true)
    expect(rows.some((row) => row[0] === 'FCN')).toBe(true)
    expect(rows.some((row) => row[0] === 'Options')).toBe(true)
    expect(rows.some((row) => row[0] === 'Derivatives')).toBe(false)
    expect(rows.some((row) => row[0] === 'Cash & Settlement')).toBe(true)
    expect(rows.some((row) => row[0] === 'Portfolio Total')).toBe(false)
    expect(rows.some((row) => row[0] === 'Category')).toBe(false)

    const exportedHeader = (section: string) => {
      const sectionIndex = rows.findIndex((row) => row[0] === section)
      return rows[sectionIndex + 1]
    }
    const exportedFirstDataRow = (section: string) => {
      const sectionIndex = rows.findIndex((row) => row[0] === section)
      return rows[sectionIndex + 2]
    }
    const renderedHeader = (tableName: string) =>
      within(screen.getByRole('table', { name: tableName }))
        .getAllByRole('columnheader')
        .map((header) => header.textContent)

    expect(exportedHeader('FCN')).toEqual(renderedHeader('FCN holdings'))
    expect(exportedHeader('Options')).toEqual(renderedHeader('Option holdings'))
    expect(exportedHeader('Cash & Settlement')).toEqual(renderedHeader('Cash and settlement holdings'))

    for (const section of [
      'FCN',
      'Options',
      'Cash & Settlement',
    ]) {
      expect(exportedFirstDataRow(section)).toHaveLength(exportedHeader(section).length)
    }
    const fcnExport = Object.fromEntries(
      exportedHeader('FCN').map((header, index) => [
        header,
        exportedFirstDataRow('FCN')[index],
      ]),
    )
    expect(fcnExport['Annual Coupon']).toBe('12%')
    expect(fcnExport.Days).toBe(169)
  })

  it('does not export empty holding categories', async () => {
    renderHoldings()
    const user = userEvent.setup()
    await waitForHoldings()
    await user.click(screen.getByRole('button', { name: 'Download' }))
    await user.click(screen.getByRole('menuitem', { name: 'CSV' }))

    const rows = tableExportMocks.downloadTable.mock.calls[0][1] as Array<Array<string | number | null>>
    expect(rows.some((row) => row[0] === 'Securities')).toBe(true)
    expect(rows.some((row) => row[0] === 'FCN')).toBe(false)
    expect(rows.some((row) => row[0] === 'Options')).toBe(false)
    expect(rows.some((row) => row[0] === 'Cash & Settlement')).toBe(false)
    expect(rows.some((row) => row[0] === 'Portfolio Total')).toBe(false)
  })

  it('does not substitute a chart endpoint for a missing valuation quote date', async () => {
    renderHoldings(
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
    await waitForHoldings()
    await user.click(screen.getByRole('button', { name: 'Securities Columns' }))
    const dialog = screen.getByRole('dialog', { name: 'Choose security columns' })
    await user.click(within(dialog).getByRole('button', { name: 'Quote' }))
    const quoteDateField = within(dialog).getByText('Quote Date').closest('label')!
    await user.click(within(quoteDateField).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Update' }))

    const table = screen.getByRole('table', { name: 'Security holdings' })
    const quoteDateHeader = within(table).getByRole('columnheader', { name: /Quote Date/ })
    const holdingRow = within(table).getByRole('cell', { name: 'Alpha Fund' }).closest('tr')!
    const index = Array.from(quoteDateHeader.parentElement!.children).indexOf(quoteDateHeader)
    expect(holdingRow.children[index]).toHaveTextContent('—')
    expect(holdingRow.children[index]).not.toHaveTextContent('2026-07-15')
  })

  it('requests full detail when the Return & Risk view needs current-basket fields', async () => {
    renderHoldings()
    await waitForHoldings()
    const user = userEvent.setup()
    await user.click(within(screen.getByRole('region', { name: 'Securities' })).getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))
    await waitFor(() => {
      expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledWith('3', {
        as_of_date: undefined,
        include_details: true,
      })
    })
  })
})
