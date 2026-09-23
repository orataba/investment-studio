import { act, fireEvent, render as renderUI, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { LanguageProvider, LanguageSelector } from '../../../../packages/ui/src/i18n'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate, useParams } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfolioHomePage from './pages/PortfolioHomePage'
import OptionOutcomePrompt from './components/OptionOutcomePrompt'
import { HOLDINGS_SECTION_COLUMN_KEYS } from './components/HoldingsSectionTables'
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
  createPortfolioOptionOutcome: vi.fn(),
  getHoldingsWorkspace: vi.fn(),
  getPortfolioAccounts: vi.fn(),
  getPortfolioTaxonomyCatalog: vi.fn(),
  getPortfolioTableViewStore: vi.fn(),
  getPortfolioUnresolvedOptionActions: vi.fn(),
  savePortfolioTableViewStore: vi.fn(),
  getConcentration: vi.fn(),
}))
const tableExportMocks = vi.hoisted(() => ({
  downloadTable: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./lib/concentrationApi', () => ({
  getConcentration: apiMocks.getConcentration,
  getConcentrationSettings: vi.fn(), saveConcentrationSettings: vi.fn(),
  concentrationScopeKey: (scope: { scope: string; taxonomy_id: string | null }) => scope.scope === 'taxonomy' ? `taxonomy:${scope.taxonomy_id}` : scope.scope,
}))
vi.mock('../../../../packages/ui/src/tableExport', () => tableExportMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

function render(ui: ReactNode) {
  return renderUI(<LanguageProvider>{ui}</LanguageProvider>)
}

function taxonomyCatalogFixture(
  overrides: Partial<PortfolioTaxonomyCatalogResponse> = {},
): PortfolioTaxonomyCatalogResponse {
  return {
    portfolio_id: '3',
    taxonomies: [],
    taxonomy_nodes: [],
    taxonomy_assignments: [],
    taxonomy_configuration_version: 0,
    instrument_universe: [],
    target_sets: [],
    target_set_lines: [],
    target_set_integrity_issues: [],
  target_resolution: [],
    ...overrides,
  }
}

function groupingCatalogFixture() {
  return taxonomyCatalogFixture({
    taxonomies: [
      { taxonomy_id: 'strategy', portfolio_id: '3', name: 'Strategy', taxonomy_type: 'allocation', primary_assignment_scope: 'instrument', root_allocation_basis: 'weight', status: 'active' },
      { taxonomy_id: 'region', portfolio_id: '3', name: 'Region', taxonomy_type: 'region', primary_assignment_scope: 'instrument', root_allocation_basis: 'weight', status: 'active' },
      { taxonomy_id: 'retired', portfolio_id: '3', name: 'Retired', taxonomy_type: 'allocation', primary_assignment_scope: 'instrument', root_allocation_basis: 'weight', status: 'archived' },
    ],
    taxonomy_nodes: [
      { taxonomy_node_id: 'growth', taxonomy_id: 'strategy', parent_taxonomy_node_id: null, node_name: 'Growth', sort_order: 0, is_terminal: false, allocation_basis: 'weight', status: 'active' },
      { taxonomy_node_id: 'listed-funds', taxonomy_id: 'strategy', parent_taxonomy_node_id: 'growth', node_name: 'Listed Funds', sort_order: 0, is_terminal: true, allocation_basis: 'weight', status: 'active' },
      { taxonomy_node_id: 'asia', taxonomy_id: 'region', parent_taxonomy_node_id: null, node_name: 'Asia', sort_order: 0, is_terminal: true, allocation_basis: 'weight', status: 'active' },
    ],
    taxonomy_assignments: [
      { assignment_id: 'strategy-assignment', taxonomy_id: 'strategy', target_scope: 'instrument', target_entity_id: 'asset-1', taxonomy_node_id: 'listed-funds', status: 'active' },
      { assignment_id: 'region-assignment', taxonomy_id: 'region', target_scope: 'instrument', target_entity_id: 'asset-1', taxonomy_node_id: 'asia', status: 'active' },
    ],
  })
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
          element={<HoldingDetailRouteProbe />}
        />
      </Routes>
    </MemoryRouter>,
  )
}

function HoldingDetailRouteProbe() {
  const { instrumentId } = useParams()
  return <div data-testid="holding-detail-route">{instrumentId}</div>
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
    cost_basis_historical_base: 200,
    cost_basis_fx_rate_to_base: 1,
    cost_basis_fx_coverage_status: 'complete',
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
  const contract = fcnContractFixture()
  return holdingFixture({
    line_id: 'holding:fcn-1',
    position_reference_id: 'fcn-1',
    derivative_contract_id: 'fcn-1',
    holding_kind: 'derivative_contract',
    holding_category: 'derivatives',
    instrument_core: null,
    derivative_contract: contract,
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
    fcn_risk: {
      lifecycle_status: 'open',
      risk_state: 'open',
      delivery_buffer_underlying_instrument_id: 'asset-1',
      underlyings: [
        {
          instrument_id: 'asset-1',
          instrument_name: 'Alpha Fund',
          currency: 'USD',
          deliverable: true,
          spot: 110,
          quote_as_of_date: '2026-07-15',
          quote_status: 'complete',
          initial_reference_price: 100,
          strike_price: 100,
          knock_in_price: 70,
          knock_out_price: 120,
          performance_to_reference_pct: 0.1,
          distance_to_strike_pct: 0.1,
          distance_to_knock_in_pct: 110 / 70 - 1,
          distance_to_knock_out_pct: 110 / 120 - 1,
          current_region: 'between_strike_and_knock_out',
          missing_terms: [],
        },
      ],
    },
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
    window.sessionStorage.clear()
    apiMocks.getPortfolioAccounts.mockResolvedValue({ portfolio_id: '3', accounts: [] })
    apiMocks.getPortfolioUnresolvedOptionActions.mockResolvedValue({
      portfolio_id: '3',
      operational_date: '2026-09-02',
      action_count: 0,
      actions: [],
    })
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue(taxonomyCatalogFixture())
    apiMocks.getPortfolioTableViewStore.mockImplementation(async () => ({ store: null }))
    apiMocks.savePortfolioTableViewStore.mockResolvedValue({})
  })

  it('opens concentration from Holdings and preserves the separate book-value view', async () => {
    const user = userEvent.setup()
    const workspace = holdingsWorkspaceFixture()
    apiMocks.getConcentration.mockResolvedValue({ portfolio_id: '3', as_of_date: workspace.as_of_date, base_currency: 'USD', nav: 1000,
      weight_basis: 'portfolio_nav', valuation_basis: 'operating_book', excluded_option_positions: 0, status: 'complete', settings_revision: 0,
      scopes: [{ scope: 'security', taxonomy_id: null, name: 'Securities', enabled: true, rows: [], status: 'complete', coverage: [] }], coverage: [], sources: [], fcn_contracts: [] })
    renderHoldings(workspace)
    await waitForHoldings()
    await user.click(screen.getByRole('tab', { name: 'Exposure and concentration' }))
    expect(await screen.findByRole('region', { name: 'Concentration' })).toBeInTheDocument()
    expect(apiMocks.getConcentration).toHaveBeenCalledWith('3', workspace.as_of_date)
    expect(screen.queryByRole('region', { name: 'Securities' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Holdings' }))
    await waitForHoldings()
    expect(screen.queryByRole('region', { name: 'Concentration' })).not.toBeInTheDocument()
  })

  it('translates currency-qualified holdings fields while preserving user names across language changes', async () => {
    const fcn = fcnHolding()
    const option = writtenOptionHolding()
    const workspace = holdingsWorkspaceFixture({
      base_currency: 'CNY',
      rows: [
        holdingFixture({ instrument_core: instrumentFixture({ instrument_name: 'Base Value (CNY)' }) }),
        {
          ...fcn,
          derivative_contract: {
            ...fcn.derivative_contract!,
            contract_name: 'Securities Subtotal (CNY)',
            account_id: 'Historical Carrying Basis (CNY)',
          },
        },
        {
          ...option,
          derivative_contract: {
            ...option.derivative_contract!,
            contract_name: 'Cash & Settlement Subtotal (CNY)',
          },
        },
        cashHolding({
          line_id: 'cash:CNY',
          instrument_core: instrumentFixture({
            instrument_id: 'cash:CNY',
            instrument_type: 'cash',
            instrument_name: 'Cash (CNY)',
            currency: 'CNY',
          }),
        }),
      ],
    })
    apiMocks.getHoldingsWorkspace.mockResolvedValue(workspace)
    const sectionViewsReady = deferred<void>()
    apiMocks.getPortfolioTableViewStore.mockImplementation(async (_portfolioId, scope) => {
      const columns = scope === 'holdings_fcn'
        ? HOLDINGS_SECTION_COLUMN_KEYS.fcn
        : scope === 'holdings_options' ? HOLDINGS_SECTION_COLUMN_KEYS.options : null
      if (columns) await sectionViewsReady.promise
      return {
        store: columns ? {
          activeViewId: 'custom:all',
          views: [{ id: 'custom:all', name: 'My Fields (CNY)', state: { columns } }],
        } : null,
      }
    })
    await act(async () => {
      render(
        <>
          <LanguageSelector />
          <MemoryRouter initialEntries={['/portfolios/3/holdings']}>
            <Routes>
              <Route path="/portfolios/:portfolioId/holdings" element={<PortfolioHomePage />} />
            </Routes>
          </MemoryRouter>
        </>,
      )
    })
    expect(apiMocks.getPortfolioTableViewStore).toHaveBeenCalledWith('3', 'holdings_fcn')
    expect(apiMocks.getPortfolioTableViewStore).toHaveBeenCalledWith('3', 'holdings_options')
    expect(screen.queryByRole('columnheader', { name: 'Strike Notional (CNY)' })).not.toBeInTheDocument()

    // The page appears before saved section views load. Resolve those requests and flush
    // their React updates before testing columns outside the default view.
    await act(async () => sectionViewsReady.resolve())
    expect(screen.getAllByRole('button', { name: /View\s*: My Fields \(CNY\)/ })).toHaveLength(2)
    expect(screen.getByRole('columnheader', { name: 'Strike Notional (CNY)' })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
    const cashTable = await screen.findByRole('table', { name: '现金与结算持仓' })
    const fcnTable = screen.getByRole('table', { name: 'FCN持仓' })
    const optionTable = screen.getByRole('table', { name: '期权持仓' })
    await waitFor(() => {
      expect(screen.getByText('证券小计 (CNY)')).toBeInTheDocument()
      expect(within(cashTable).getByRole('columnheader', { name: '本位币价值 (CNY)' })).toBeInTheDocument()
      expect(within(cashTable).getByRole('columnheader', { name: '汇兑成本基础 (CNY)' })).toBeInTheDocument()
      expect(within(cashTable).getByRole('columnheader', { name: '未实现汇兑损益 (CNY)' })).toBeInTheDocument()
      expect(within(cashTable).getByRole('button', { name: '现金 (CNY)' })).toBeInTheDocument()
      expect(within(cashTable).getByText('现金与结算小计 (CNY)')).toBeInTheDocument()
      for (const table of [cashTable, fcnTable, optionTable]) {
        expect(within(table).getByRole('columnheader', { name: '资金权重' })).toBeInTheDocument()
      }
      expect(within(screen.getByRole('table', { name: '证券持仓' })).getByText('资金权重')).toBeInTheDocument()
      for (const table of [fcnTable, optionTable]) {
        for (const name of ['带方向净值金额 (CNY)', '历史账面基础 (CNY)', '账面汇率折算 (CNY)']) {
          expect(within(table).getByRole('columnheader', { name })).toBeInTheDocument()
        }
      }
      expect(within(optionTable).getByRole('columnheader', { name: '执行价名义金额 (CNY)' })).toBeInTheDocument()
      expect(within(fcnTable).getByText('FCN小计 (CNY)')).toBeInTheDocument()
      expect(within(optionTable).getByText('期权小计 (CNY)')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Base Value (CNY)' })).toBeInTheDocument()
    expect(within(fcnTable).getByRole('button', { name: 'Securities Subtotal (CNY)' })).toBeInTheDocument()
    expect(within(fcnTable).getByText('Historical Carrying Basis (CNY)')).toBeInTheDocument()
    expect(within(optionTable).getByRole('button', { name: 'Cash & Settlement Subtotal (CNY)' })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
    const englishCashTable = await screen.findByRole('table', { name: 'Cash and settlement holdings' })
    expect(within(englishCashTable).getByRole('button', { name: 'Cash (CNY)' })).toBeInTheDocument()
    expect(within(englishCashTable).getByRole('columnheader', { name: 'FX Cost Basis (CNY)' })).toBeInTheDocument()
    for (const table of [englishCashTable, screen.getByRole('table', { name: 'FCN holdings' }), screen.getByRole('table', { name: 'Option holdings' })]) {
      expect(within(table).getByRole('columnheader', { name: 'Capital Weight' })).toBeInTheDocument()
    }
    expect(within(screen.getByRole('table', { name: 'Security holdings' })).getByText('Capital Weight')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Strike Notional (CNY)' })).toBeInTheDocument()
  })

  it('renders each populated category with aligned table controls and title counts', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [holdingFixture(), fcnHolding(), writtenOptionHolding(), cashHolding()],
        quality_warnings: ['Quote coverage needs review.'],
      }),
    )
    await waitForHoldings()
    expect(screen.getByRole('button', { name: /Data quality warning: Quote coverage needs review/ }).closest('.holdings-filter-actions')).not.toBeNull()

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
      const rows = Array.from(within(region).getByRole('table').querySelectorAll('tbody tr'))
      expect(rows).toHaveLength(2)
      expect(rows[0]).toHaveClass('portfolio-tree-row')
      expect(rows[0]).toHaveAttribute('data-tree-level', 'item')
      expect(rows[1]).toHaveClass('portfolio-tree-row')
      expect(rows[1]).toHaveAttribute('data-tree-level', 'primary')
    }
    expect(within(screen.getByRole('region', { name: 'Securities' })).getByRole('button', { name: /Group By\s*: None/ })).toBeInTheDocument()
    expect(screen.queryByText(/Contract terms and carrying amounts are explicit/)).not.toBeInTheDocument()
    expect(screen.getByText('Securities Subtotal (USD)')).toBeInTheDocument()
    expect(screen.queryByText('Portfolio Total (USD)')).not.toBeInTheDocument()
  })

  it('keeps operational alerts visible when their holding category is empty', async () => {
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
    const operationalStatus = screen.getByRole('region', { name: 'Operational Status' })
    expect(within(operationalStatus).getByText('Overdue settlement')).toBeInTheDocument()
    expect(within(operationalStatus).getByText('A settlement is overdue.')).toBeInTheDocument()
  })

  it('prompts once for an expired option and records expiry on the contract date', async () => {
    const expiredContract = optionContractFixture({
      terms: {
        underlying_instrument_id: 'equity-1',
        option_type: 'call',
        expiry_date: '2026-07-10',
        strike: 110,
        contract_multiplier: 100,
      },
    })
    apiMocks.getPortfolioUnresolvedOptionActions
      .mockResolvedValueOnce({
        portfolio_id: '3',
        operational_date: '2026-07-15',
        action_count: 1,
        actions: [
          {
            action_key: 'option-1:long:broker-1',
            derivative_contract_id: 'option-1',
            derivative_contract: expiredContract,
            side: 'long',
            account_id: 'broker-1',
            open_contract_quantity: 2,
            underlying_instrument_id: 'equity-1',
            expiry_date: '2026-07-10',
            days_past_expiry: 5,
          },
        ],
      })
      .mockResolvedValue({
        portfolio_id: '3',
        operational_date: '2026-07-15',
        action_count: 0,
        actions: [],
      })
    apiMocks.createPortfolioOptionOutcome.mockResolvedValue({
      portfolio_id: '3',
      transactions: [],
      option_delivery_link: null,
    })

    render(<OptionOutcomePrompt portfolioId="3" />)
    const dialog = await screen.findByRole('dialog', { name: 'Resolve expired option' })
    expect(within(dialog).getByText('Alpha 110 Call')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Event date')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Settlement date')).not.toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm & record' }))

    await waitFor(() => {
      expect(apiMocks.createPortfolioOptionOutcome).toHaveBeenCalledWith(
        '3',
        {
          derivative_contract_id: 'option-1',
          side: 'long',
          outcome: 'expired',
          quantity: 2,
          event_date: '2026-07-10',
          trade_time: null,
          settlement_date: '2026-07-10',
          stock_account_id: null,
          settlement_cash_account_id: null,
          cash_settlement_amount: null,
          note: null,
          fees: 0,
          taxes: 0,
        },
        expect.stringMatching(/^option-outcome-/),
      )
    })
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Resolve expired option' })).not.toBeInTheDocument()
    })
  })

  it('keeps a committed option outcome successful when the follow-up refresh fails', async () => {
    const expiredContract = optionContractFixture({
      terms: {
        underlying_instrument_id: 'equity-1',
        option_type: 'call',
        expiry_date: '2026-07-10',
        strike: 110,
        contract_multiplier: 100,
      },
    })
    apiMocks.getPortfolioUnresolvedOptionActions
      .mockResolvedValueOnce({
        portfolio_id: '3',
        operational_date: '2026-07-15',
        action_count: 1,
        actions: [
          {
            action_key: 'option-1:long:broker-1',
            derivative_contract_id: 'option-1',
            derivative_contract: expiredContract,
            side: 'long',
            account_id: 'broker-1',
            open_contract_quantity: 2,
            underlying_instrument_id: 'equity-1',
            expiry_date: '2026-07-10',
            days_past_expiry: 5,
          },
        ],
      })
      .mockRejectedValueOnce(new Error('Action refresh unavailable'))
    apiMocks.createPortfolioOptionOutcome.mockResolvedValue({
      portfolio_id: '3',
      transactions: [],
      option_delivery_link: null,
    })
    const onRecorded = vi.fn()

    render(<OptionOutcomePrompt portfolioId="3" onRecorded={onRecorded} />)
    const dialog = await screen.findByRole('dialog', { name: 'Resolve expired option' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm & record' }))

    await waitFor(() => {
      expect(onRecorded).toHaveBeenCalledWith('Option outcome recorded.')
      expect(screen.queryByRole('dialog', { name: 'Resolve expired option' })).not.toBeInTheDocument()
    })
    expect(screen.queryByText('Action refresh unavailable')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /option needs action/ })).not.toBeInTheDocument()
  })

  it('does not silently hide an expired-option check failure', async () => {
    apiMocks.getPortfolioUnresolvedOptionActions.mockRejectedValueOnce(
      new Error('Option action service unavailable'),
    )

    render(<OptionOutcomePrompt portfolioId="3" />)

    const status = await screen.findByRole('button', { name: /Option action check failed:/ })
    expect(status).toHaveAttribute('aria-label', expect.stringContaining('Option action service unavailable'))
    await userEvent.setup().click(status)
    expect(screen.getByRole('tooltip')).toHaveTextContent('Option action service unavailable')
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

  it('opens cash and settlement rows with their own monetary identity', async () => {
    const user = userEvent.setup()
    const pending = cashHolding({
      line_id: 'pending:settlement_receivable:cash-account:asset-1:USD:2026-07-17:2026-07-17',
      position_reference_id: null,
      holding_kind: 'settlement_receivable',
      economic_instrument_id: 'asset-1',
      instrument_core: instrumentFixture({
        instrument_id: 'pending:settlement_receivable:cash-account:asset-1:USD:2026-07-17:2026-07-17',
        instrument_name: 'Settlement receivable · Alpha Fund',
        instrument_type: 'other',
        currency: 'USD',
        identifiers: [],
      }),
      available_for_trading: false,
      settlement_date: '2026-07-17',
      pending_until_date: '2026-07-17',
      pending_status: 'awaiting_settlement',
    })
    renderHoldings(holdingsWorkspaceFixture({ rows: [pending] }))
    await waitForHoldings()

    const cashTable = screen.getByRole('table', { name: 'Cash and settlement holdings' })
    await user.click(within(cashTable).getByRole('button', { name: 'Settlement receivable · Alpha Fund' }))

    expect(await screen.findByTestId('holding-detail-route')).toHaveTextContent(
      'pending:settlement_receivable:cash-account:asset-1:USD:2026-07-17:2026-07-17',
    )
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
    apiMocks.savePortfolioTableViewStore
      .mockRejectedValueOnce(new Error('View save offline.'))
      .mockResolvedValue({})
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
    await user.click(screen.getByRole('button', { name: 'Retry save' }))
    await waitFor(() => {
      expect(apiMocks.savePortfolioTableViewStore).toHaveBeenCalledTimes(2)
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
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
    expect(
      exportedRows[fcnSectionIndex + 2].some((cell) =>
        String(cell).includes('spot 110 USD, strike 100 USD (100%)'),
      ),
    ).toBe(true)
    expect(
      exportedRows[fcnSectionIndex + 2].some((cell) =>
        String(cell).includes('Alpha Fund: 0.1 vs delivery strike'),
      ),
    ).toBe(true)

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
      }, expect.any(AbortSignal))
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

  it('migrates stale system views to the current FCN column contract', async () => {
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
      within(screen.getByRole('table', { name: 'FCN holdings' })).getByRole(
        'columnheader',
        { name: 'Annual Coupon' },
      ),
    ).toBeInTheDocument()
    await waitFor(() => {
      const savedStore = apiMocks.savePortfolioTableViewStore.mock.calls.find(
        ([portfolioId, viewScope]) => portfolioId === '3' && viewScope === 'holdings_fcn',
      )?.[2] as { systemViewSignature?: string } | undefined
      expect(savedStore?.systemViewSignature).toBeTruthy()
    })
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

  it('shows the default holdings view while the independent taxonomy request is pending', async () => {
    const taxonomy = deferred<PortfolioTaxonomyCatalogResponse>()
    apiMocks.getPortfolioTaxonomyCatalog.mockReturnValueOnce(taxonomy.promise)
    renderHoldings()

    expect(await screen.findByRole('cell', { name: 'Alpha Fund' })).toBeInTheDocument()
    await act(async () => taxonomy.resolve(taxonomyCatalogFixture()))
  })

  it('waits for taxonomy data when the selected view groups by taxonomy', async () => {
    const taxonomy = deferred<PortfolioTaxonomyCatalogResponse>()
    apiMocks.getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture())
    apiMocks.getPortfolioTaxonomyCatalog.mockReturnValueOnce(taxonomy.promise)
    apiMocks.getPortfolioTableViewStore.mockResolvedValueOnce({
      store: {
        activeViewId: 'custom:taxonomy',
        views: [{ id: 'custom:taxonomy', name: 'By Taxonomy', state: { groupBy: 'taxonomy_top' } }],
      },
    })
    render(
      <MemoryRouter initialEntries={['/portfolios/3/holdings?holdings_group_by=taxonomy_top']}>
        <Routes>
          <Route path="/portfolios/:portfolioId/holdings" element={<PortfolioHomePage />} />
        </Routes>
      </MemoryRouter>,
    )

    await waitFor(() => expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalled())
    expect(screen.queryByRole('table', { name: 'Security holdings' })).not.toBeInTheDocument()
    await act(async () => taxonomy.resolve(taxonomyCatalogFixture()))
    expect(await screen.findByRole('table', { name: 'Security holdings' })).toBeInTheDocument()
  })

  it('keeps the current table mounted and identifies its date while new holdings load', async () => {
    renderHoldings()
    await waitForHoldings()
    const originalTable = screen.getByRole('table', { name: 'Security holdings' })
    const refreshedHoldings = deferred<ReturnType<typeof holdingsWorkspaceFixture>>()
    apiMocks.getHoldingsWorkspace.mockReturnValueOnce(refreshedHoldings.promise)

    fireEvent.change(screen.getByLabelText('As Of Date'), { target: { value: '2026-07-14' } })

    expect(screen.getByRole('table', { name: 'Security holdings' })).toBe(originalTable)
    expect(screen.getByText('As Of Date').parentElement).toHaveTextContent('2026-07-15')
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenLastCalledWith('3', { as_of_date: '2026-07-14' }, expect.any(AbortSignal))
    expect(apiMocks.getPortfolioTaxonomyCatalog).toHaveBeenCalledTimes(1)

    await act(async () => refreshedHoldings.resolve(holdingsWorkspaceFixture({ as_of_date: '2026-07-14' })))
    expect(screen.getByRole('table', { name: 'Security holdings' })).toBe(originalTable)
    expect(screen.queryByText('As Of Date')).not.toBeInTheDocument()
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
      'Current Risk',
      'Annual Coupon',
    ]) {
      expect(within(fcnTable).getByRole('columnheader', { name: label })).toBeInTheDocument()
    }
    const fcnNameCell = within(fcnTable).getByRole('cell', { name: 'Alpha FCN' })
    expect(fcnNameCell).toHaveTextContent(/^Alpha FCN$/)
    const underlyingCell = within(fcnTable).getByRole('link', { name: 'Alpha Fund' }).closest('td')!
    expect(underlyingCell).toHaveTextContent('Spot $110.0000')
    expect(underlyingCell).toHaveTextContent('Strike $100.0000 (100.00%)')
    expect(underlyingCell).toHaveTextContent('KI $70.0000 (70.00%)')
    expect(underlyingCell).toHaveTextContent('KO $120.0000 (120.00%)')
    const deliveryRiskCell = within(fcnTable).getByText('Delivery buffer').closest('td')!
    expect(deliveryRiskCell).toHaveTextContent('Alpha Fund: 10.00% above delivery strike')

    const fcnTableWidth = fcnTable.style.minWidth
    await user.click(await within(fcnRegion).findByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Terms & Events' }))
    expect(fcnTable.style.minWidth).toBe(fcnTableWidth)
    for (const label of ['Final Observation', 'Issuer', 'Counterparty']) {
      expect(within(fcnTable).getByRole('columnheader', { name: label })).toBeInTheDocument()
    }
    expect(within(fcnTable).getByText('Fixture Issuer')).toBeInTheDocument()

    await user.click(within(fcnRegion).getByRole('button', { name: /View\s*: Terms & Events/ }))
    await user.click(screen.getByRole('option', { name: 'Valuation' }))
    expect(fcnTable.style.minWidth).toBe(fcnTableWidth)
    expect(within(fcnTable).getByRole('columnheader', { name: 'Fair Value Status' })).toBeInTheDocument()

    const optionsRegion = screen.getByRole('region', { name: 'Options' })
    const optionTable = screen.getByRole('table', { name: 'Option holdings' })
    for (const label of [
      'Side',
      'Type',
      'Underlying',
      'Underlying Spot',
      'Strike',
      'Moneyness',
      'Open Contracts',
      'Portfolio Backing',
      'Current Risk',
    ]) {
      expect(within(optionTable).getByRole('columnheader', { name: label })).toBeInTheDocument()
    }
    expect(within(optionTable).getByText('Written')).toBeInTheDocument()

    const optionTableWidth = optionTable.style.minWidth
    await user.click(within(optionsRegion).getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Contract Terms' }))
    expect(optionTable.style.minWidth).toBe(optionTableWidth)
    expect(within(optionTable).getByRole('columnheader', { name: 'Underlying Equivalent' })).toBeInTheDocument()
    expect(within(optionTable).getByRole('columnheader', { name: 'Multiplier' })).toBeInTheDocument()
    expect(within(optionTable).getByRole('columnheader', { name: 'Strike Notional (USD)' })).toBeInTheDocument()
    expect(within(optionTable).getAllByText('$22,000.00').length).toBeGreaterThanOrEqual(1)

    await user.click(within(optionsRegion).getByRole('button', { name: /View\s*: Contract Terms/ }))
    await user.click(screen.getByRole('option', { name: 'Valuation' }))
    expect(optionTable.style.minWidth).toBe(optionTableWidth)
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
      'Capital Weight',
      'Settlement Date',
      'Pending Until',
      'Related Instrument',
      'Settlement Status',
    ]) {
      expect(within(table).getByRole('columnheader', { name: label })).toBeInTheDocument()
    }
    expect(within(table).getByRole('columnheader', { name: 'FX Cost Basis (USD)' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Unrealized FX P&L (USD)' })).toBeInTheDocument()
    expect(within(table).getByText('2026-07-16')).toBeInTheDocument()
    expect(within(table).getByText('Alpha Fund')).toBeInTheDocument()
    expect(screen.queryByText('Portfolio Total (USD)')).not.toBeInTheDocument()
  })

  it('groups only Securities with the current taxonomy, independently of the holdings date', async () => {
    const security = holdingFixture()
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [security, fcnHolding(), cashHolding()],
      }),
      taxonomyCatalogFixture({
        taxonomies: [
          {
            taxonomy_id: 'current-taxonomy',
            portfolio_id: '3',
            name: 'Current Allocation',
            taxonomy_type: 'allocation',
            primary_assignment_scope: 'instrument',
            root_allocation_basis: 'weight',
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
            allocation_basis: 'weight',
            status: 'active',
          },
          {
            taxonomy_node_id: 'current-listed-funds',
            taxonomy_id: 'current-taxonomy',
            parent_taxonomy_node_id: 'current-risk-assets',
            node_name: 'Current Listed Funds',
            sort_order: 0,
            is_terminal: true,
            allocation_basis: 'weight',
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
        name: 'Current Allocation · Top level',
      }),
    )

    const securityTable = screen.getByRole('table', { name: 'Security holdings' })
    expect(within(securityTable).getAllByText('Current Risk Assets')).toHaveLength(1)
    expect(within(securityTable).getByText('Current Risk Assets')).toHaveAttribute('data-tree-level', 'primary')
    expect(within(securityTable).getByText('Alpha Fund')).toHaveAttribute('data-tree-level', 'item')
    expect(within(securityTable).getByText('Current Risk Assets').closest('tr')).toHaveAttribute('data-tree-level', 'primary')
    expect(within(securityTable).getByText('Alpha Fund').closest('tr')).toHaveAttribute('data-tree-level', 'item')
    expect(within(screen.getByRole('table', { name: 'FCN holdings' })).queryByText('Current Risk Assets')).not.toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Cash and settlement holdings' })).queryByText('Current Risk Assets')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Group By.*Current Allocation/ }))
    await user.click(screen.getByRole('button', { name: 'Current Allocation · Leaf level' }))
    expect(within(securityTable).getByText('Current Listed Funds')).toHaveAttribute('data-tree-level', 'nested')
    expect(within(securityTable).getByText('Current Listed Funds').closest('tr')).toHaveClass('portfolio-tree-row')
    expect(within(securityTable).getByText('Current Listed Funds').closest('tr')).toHaveAttribute('data-tree-level', 'nested')
    expect(within(securityTable).queryByText('Current Risk Assets')).not.toBeInTheDocument()
  })

  it('shows Unassigned when a Security has no assignment in the current taxonomy', async () => {
    renderHoldings(
      holdingsWorkspaceFixture({
        rows: [holdingFixture()],
      }),
      taxonomyCatalogFixture({
        taxonomies: [
          {
            taxonomy_id: 'current-taxonomy',
            portfolio_id: '3',
            name: 'Current Allocation',
            taxonomy_type: 'allocation',
            primary_assignment_scope: 'instrument',
            root_allocation_basis: 'weight',
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
        name: 'Current Allocation · Top level',
      }),
    )
    expect(document.querySelector('.holdings-subgroup-row')).toHaveTextContent('Unassigned')
  })

  it('lists active taxonomies directly and saves the selected taxonomy and depth in the view', async () => {
    renderHoldings(holdingsWorkspaceFixture(), groupingCatalogFixture())
    const user = userEvent.setup()
    await waitForHoldings()
    await user.click(screen.getByRole('button', { name: /Group By.*None/ }))
    const dialog = screen.getByRole('dialog', { name: 'Choose grouping' })
    expect(within(dialog).queryByRole('combobox')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Taxonomy' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /Retired/ })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Strategy · Top level' })).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Region · Leaf level' }))

    expect(screen.getByRole('button', { name: /Group By.*Region · Leaf level/ })).toBeInTheDocument()
    expect(screen.getByText('Asia')).toHaveAttribute('data-tree-level', 'primary')
    expect(screen.queryByText('Growth')).not.toBeInTheDocument()
    await waitFor(() => {
      const calls = apiMocks.savePortfolioTableViewStore.mock.calls.filter(([, scope]) => scope === 'holdings')
      const store = calls[calls.length - 1]?.[2] as { views: Array<{ id: string; state: { groupBy: string; groupingTaxonomyId?: string } }> } | undefined
      expect(store?.views.find((view) => view.id === 'default')?.state).toMatchObject({ groupBy: 'taxonomy_leaf', groupingTaxonomyId: 'region' })
    })
  })

  it('restores each saved view with its own explicit taxonomy instead of the first taxonomy', async () => {
    apiMocks.getPortfolioTableViewStore.mockResolvedValueOnce({ store: {
      activeViewId: 'custom:region',
      views: [
        { id: 'custom:region', name: 'By Region', state: { groupBy: 'taxonomy_leaf', groupingTaxonomyId: 'region' } },
        { id: 'custom:strategy', name: 'By Strategy', state: { groupBy: 'taxonomy_top', groupingTaxonomyId: 'strategy' } },
      ],
    } })
    renderHoldings(holdingsWorkspaceFixture(), groupingCatalogFixture())
    const user = userEvent.setup()
    await waitForHoldings()
    expect(await screen.findByText('Asia')).toBeInTheDocument()
    expect(screen.queryByText('Growth')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /View\s*: By Region/ }))
    await user.click(screen.getByRole('option', { name: 'By Strategy' }))
    expect(screen.getByText('Growth')).toBeInTheDocument()
    expect(screen.queryByText('Asia')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /View\s*: By Strategy/ }))
    await user.click(screen.getByRole('option', { name: 'By Region' }))
    expect(screen.getByText('Asia')).toBeInTheDocument()
  })

  it.each(['', 'deleted', 'retired'])('clears an unavailable saved taxonomy %s without calling assigned holdings Unassigned', async (taxonomyId) => {
    apiMocks.getPortfolioTableViewStore.mockResolvedValueOnce({ store: {
      activeViewId: 'custom:taxonomy',
      views: [{ id: 'custom:taxonomy', name: 'By Taxonomy', state: { columns: ['instrument', 'taxonomy_top', 'taxonomy_leaf'], groupBy: 'taxonomy_top', groupingTaxonomyId: taxonomyId } }],
    } })
    renderHoldings(holdingsWorkspaceFixture(), groupingCatalogFixture())
    await waitForHoldings()
    await waitFor(() => expect(screen.getByRole('button', { name: /Group By.*None/ })).toBeInTheDocument())
    const table = screen.getByRole('table', { name: 'Security holdings' })
    expect(within(table).queryByText('Unassigned')).not.toBeInTheDocument()
    expect(within(table).queryByText('Growth')).not.toBeInTheDocument()
    expect(within(table).queryByText('Asia')).not.toBeInTheDocument()
    const row = within(table).getByRole('cell', { name: 'Alpha Fund' }).closest('tr')!
    expect(row.querySelector('[data-column-key="taxonomy_top"]')).toHaveTextContent('—')
    expect(row.querySelector('[data-column-key="taxonomy_leaf"]')).toHaveTextContent('—')
  })

  it('keeps taxonomy identity columns when switching to another grouping dimension', async () => {
    apiMocks.getPortfolioTableViewStore.mockResolvedValueOnce({ store: {
      activeViewId: 'custom:region',
      views: [{ id: 'custom:region', name: 'By Region', state: { columns: ['instrument', 'taxonomy_top', 'taxonomy_leaf'], groupBy: 'taxonomy_top', groupingTaxonomyId: 'region' } }],
    } })
    renderHoldings(holdingsWorkspaceFixture(), groupingCatalogFixture())
    const user = userEvent.setup()
    await waitForHoldings()
    await user.click(screen.getByRole('button', { name: /Group By.*Region/ }))
    await user.click(screen.getByRole('button', { name: 'Currency' }))
    const row = screen.getByRole('cell', { name: 'Alpha Fund' }).closest('tr')!
    expect(row.querySelector('[data-column-key="taxonomy_top"]')).toHaveTextContent('Asia')
    expect(row.querySelector('[data-column-key="taxonomy_leaf"]')).toHaveTextContent('Asia')
    expect(screen.queryByText('Unassigned')).not.toBeInTheDocument()
  })

  it('retains a saved taxonomy when its catalog read fails without misclassifying holdings', async () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockRejectedValueOnce(new Error('Classification catalog offline.'))
    apiMocks.getPortfolioTableViewStore.mockResolvedValueOnce({ store: {
      activeViewId: 'custom:region',
      views: [{ id: 'custom:region', name: 'By Region', state: { columns: ['instrument', 'taxonomy_top'], groupBy: 'taxonomy_top', groupingTaxonomyId: 'region' } }],
    } })
    renderHoldings()
    await waitForHoldings()
    expect(await screen.findByText('Classification catalog offline.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Group By.*Unavailable/ })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Alpha Fund' })).toBeInTheDocument()
    expect(screen.queryByText('Unassigned')).not.toBeInTheDocument()
    await waitFor(() => {
      const calls = apiMocks.savePortfolioTableViewStore.mock.calls.filter(([, scope]) => scope === 'holdings')
      const store = calls[calls.length - 1]?.[2] as { views: Array<{ id: string; state: { groupBy: string; groupingTaxonomyId?: string } }> } | undefined
      expect(store?.views.find((view) => view.id === 'custom:region')?.state).toMatchObject({ groupBy: 'taxonomy_top', groupingTaxonomyId: 'region' })
    })
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
      }, expect.any(AbortSignal))
    })
  })

  it('reuses loaded full inputs for column changes but requests fresh inputs for another date or portfolio', async () => {
    apiMocks.getHoldingsWorkspace.mockImplementation(async (portfolioId, filters) => holdingsWorkspaceFixture({
      portfolio_id: portfolioId,
      as_of_date: filters?.as_of_date ?? '2026-07-15',
      detail_level: filters?.include_details ? 'full' : 'compact',
    }))
    render(<MemoryRouter initialEntries={['/portfolios/3/holdings']}><HoldingsRouteHarness /></MemoryRouter>)
    await waitForHoldings()
    const user = userEvent.setup()
    const selectView = async (current: string, next: string) => {
      await user.click(within(screen.getByRole('region', { name: 'Securities' })).getByRole('button', { name: new RegExp(`View\\s*: ${current}$`) }))
      await user.click(screen.getByRole('option', { name: next }))
    }
    await selectView('Default', 'Return & Risk')
    await waitFor(() => expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(2))
    await selectView('Return & Risk', 'Default')
    await selectView('Default', 'Return & Risk')
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(2)

    fireEvent.change(screen.getByLabelText('As Of Date'), { target: { value: '2026-07-14' } })
    await waitFor(() => expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(3))
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenLastCalledWith('3', { as_of_date: '2026-07-14', include_details: true }, expect.any(AbortSignal))
    await selectView('Return & Risk', 'Default')
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(3)
    fireEvent.change(screen.getByLabelText('As Of Date'), { target: { value: '2026-07-13' } })
    await waitFor(() => expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(4))
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenLastCalledWith('3', { as_of_date: '2026-07-13' }, expect.any(AbortSignal))

    await user.click(screen.getByRole('button', { name: 'Switch portfolio' }))
    await waitFor(() => expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(5))
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenLastCalledWith('4', { as_of_date: undefined }, expect.any(AbortSignal))
  })

  it('shares aligned risk inputs across subtotal cells, column edits, and sorting', async () => {
    const points = [
      { start_date: '2026-07-12', date: '2026-07-13', value: 0.1 },
      { start_date: '2026-07-13', date: '2026-07-14', value: -0.2 },
      { start_date: '2026-07-14', date: '2026-07-15', value: 0.05 },
    ]
    const readPoints = vi.fn(() => points)
    renderHoldings(holdingsWorkspaceFixture({
      detail_level: 'full',
      rows: [holdingFixture({ instrument_return_series_all: {
        first_return_start_date: '2026-07-12', get points() { return readPoints() },
      } })],
    }))
    await waitForHoldings()
    expect(readPoints).toHaveBeenCalledTimes(2)
    const user = userEvent.setup()
    await user.click(within(screen.getByRole('region', { name: 'Securities' })).getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))
    await user.click(screen.getByRole('button', { name: 'Securities Columns' }))
    await user.click(within(screen.getByRole('dialog', { name: 'Choose security columns' })).getByRole('button', { name: 'Cancel' }))
    await user.click(screen.getByRole('button', { name: 'Sort Instrument: ascending' }))
    expect(readPoints).toHaveBeenCalledTimes(2)
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /Group By\s*: None/ }))
    await user.click(within(screen.getByRole('dialog', { name: 'Choose grouping' })).getByRole('button', { name: 'Currency' }))
    // The data regrouping builds one shared path for the subtotal and one for USD.
    expect(readPoints).toHaveBeenCalledTimes(6)
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Download' }))
    await user.click(screen.getByRole('menuitem', { name: 'CSV' }))
    const exportedRows = tableExportMocks.downloadTable.mock.calls[0][1] as Array<Array<string | number | null>>
    const header = exportedRows.find((row) => row.includes('Current DD'))!
    const subtotal = exportedRows.find((row) => row.includes('Securities Subtotal (USD)'))!
    expect(subtotal[header.indexOf('Current DD')]).toBeCloseTo(-0.16)
    expect(subtotal[header.indexOf('Max DD')]).toBeCloseTo(-0.2)
    expect(readPoints).toHaveBeenCalledTimes(6)
  })
})
