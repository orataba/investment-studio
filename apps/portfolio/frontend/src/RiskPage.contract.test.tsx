import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RiskPage, { riskFrequencyProfileFromHoldingsWorkspace } from './pages/RiskPage'
import {
  fcnContractFixture,
  holdingFixture,
  holdingsWorkspaceFixture,
  instrumentFixture,
} from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  getHoldingsWorkspace: vi.fn(),
  getPortfolioAccountsWorkspace: vi.fn(),
  getPortfolioTaxonomyCatalog: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  getPortfolioInstrumentPriceChart: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

function isoDateDaysBefore(daysBefore: number) {
  const date = new Date(Date.UTC(2026, 6, 15 - daysBefore))
  return date.toISOString().slice(0, 10)
}

const returnPoints = Array.from({ length: 110 }, (_, index) => {
  const daysBefore = 109 - index
  return {
    start_date: isoDateDaysBefore(daysBefore + 1),
    date: isoDateDaysBefore(daysBefore),
    value: index % 5 === 0 ? -0.001 : index % 2 === 0 ? 0.0015 : 0.0005,
  }
})

const riskHolding = holdingFixture({
  instrument_return_series_all: {
    first_return_start_date: isoDateDaysBefore(110),
    points: returnPoints,
  },
})

const betaReturnPoints = returnPoints.map((point, index) => ({
  ...point,
  value: index % 7 === 0 ? -0.0007 : index % 3 === 0 ? 0.0011 : 0.0003,
}))

function betaHolding(returnSeriesPoints = betaReturnPoints) {
  return holdingFixture({
    line_id: 'holding:asset-2',
    instrument_core: instrumentFixture({
      instrument_id: 'asset-2',
      instrument_name: 'Beta Fund',
      identifiers: [{ identifier_type: 'ticker', identifier_value: 'BETA', is_primary: true }],
    }),
    market_value: 200,
    market_value_base: 200,
    allocation: 0.2,
    instrument_return_series_all: {
      first_return_start_date: isoDateDaysBefore(110),
      points: returnSeriesPoints,
    },
  })
}

function twoHoldingWorkspace(secondHolding = betaHolding()) {
  return holdingsWorkspaceFixture({
    rows: [riskHolding, secondHolding],
    totals: {
      market_value: 1000,
      day_change_pct: 0.01,
      day_change_value: 10,
      cost_basis: 900,
      allocation: 1,
    },
    risk_basis: {
      requested_frequency: 'daily',
      resolved_frequency: 'daily',
      default_frequency: 'daily',
      source_frequency_counts: { daily: 2 },
      status_label: 'Daily risk basis',
    },
  })
}

async function findCorrelationUnavailable() {
  return screen.findByRole('status', { name: /Correlation matrix unavailable/ })
}

const accountsWorkspace = {
  portfolio_id: '3',
  base_currency: 'USD',
  summary: {
    account_count: 1,
    deposit_account_count: 1,
    securities_account_count: 0,
    ledger_posting_count: 0,
    position_line_count: 0,
  },
  derivation_boundary: {
    ledger_postings: 'fixture',
    positions: 'fixture',
    holdings: 'fixture',
    snapshot: 'fixture',
  },
  selected_account_id: 'cash-1',
  accounts: [
    {
      account: {
        account_id: 'cash-1',
        portfolio_id: '3',
        account_name: 'Operating Cash',
        account_type: 'deposit_account',
        account_category: 'cash',
        currency: 'USD',
        status: 'active',
      },
      linked_transaction_count: 0,
      linked_posting_count: 0,
      derived_cash_balance: 200,
      derived_cash_balance_base: 200,
      pending_settlement: 0,
      pending_settlement_base: 0,
      account_value_base: 200,
      position_line_count: 0,
      position_market_value: 0,
      position_market_value_currency: 'USD',
    },
  ],
  ledger_postings: [],
  positions: [],
  linked_transactions: [],
}

const taxonomyCatalog = {
  portfolio_id: '3',
  default_planning_taxonomy_id: 'taxonomy-1',
  risk_basis: {
    requested_frequency: 'daily',
    resolved_frequency: 'daily',
    default_frequency: 'daily',
    source_frequency_counts: { daily: 1 },
    status_label: 'Daily risk basis',
  },
  taxonomies: [
    {
      taxonomy_id: 'taxonomy-1',
      portfolio_id: '3',
      name: 'Policy Allocation',
      taxonomy_type: 'allocation',
      primary_assignment_scope: 'instrument',
      planning_enabled: true,
      budgeting_level: 'root',
      root_default_target_dimension: 'weight',
      status: 'active',
    },
  ],
  taxonomy_nodes: [
    {
      taxonomy_node_id: 'risk-assets',
      taxonomy_id: 'taxonomy-1',
      parent_taxonomy_node_id: null,
      node_name: 'Risk Assets',
      sort_order: 1,
      is_terminal: true,
      default_target_dimension: 'risk_budget',
      status: 'active',
    },
  ],
  taxonomy_assignments: [
    {
      assignment_id: 'assignment-asset',
      taxonomy_id: 'taxonomy-1',
      target_scope: 'instrument',
      target_entity_id: 'asset-1',
      taxonomy_node_id: 'risk-assets',
      status: 'active',
    },
  ],
  analytics_scope_policy_version: 1,
  analytics_scope_policies: [
    {
      analytics_scope_policy_id: 'scope-policy-root',
      portfolio_id: '3',
      taxonomy_id: 'taxonomy-1',
      taxonomy_node_id: '__root__',
      risk_eligible: true,
      risk_budget_eligible: true,
      performance_scope: 'ordinary',
      valuation_basis: 'market',
      exclusion_reason: null,
      effective_from: '2020-01-01',
      effective_to: null,
      policy_version: 1,
      superseded_by_policy_id: null,
      created_at: '2020-01-01T00:00:00Z',
    },
  ],
  analytics_taxonomy_selections: [],
  instrument_universe: [
    {
      portfolio_id: '3',
      instrument_id: 'asset-1',
      instrument_ref: instrumentFixture(),
      source: 'transaction',
      holding_state: 'held',
      transaction_count: 1,
      status: 'active',
      instrument_trend_basis: 'total_return_nav',
      instrument_risk_frequency: 'daily',
      instrument_return_series_all: riskHolding.instrument_return_series_all,
    },
  ],
  target_sets: [
    {
      target_set_id: 'saa-root',
      taxonomy_id: 'taxonomy-1',
      comparator_taxonomy_node_id: null,
      target_set_type: 'saa',
      name: 'SAA',
      weight_enabled: true,
      risk_budget_enabled: true,
      status: 'active',
    },
    {
      target_set_id: 'taa-root',
      taxonomy_id: 'taxonomy-1',
      comparator_taxonomy_node_id: null,
      target_set_type: 'taa',
      name: 'TAA',
      weight_enabled: true,
      risk_budget_enabled: true,
      status: 'active',
    },
  ],
  target_set_lines: [
    {
      target_line_id: 'saa-risk',
      target_set_id: 'saa-root',
      target_member_type: 'taxonomy_node',
      target_member_id: 'risk-assets',
      taxonomy_node_id: 'risk-assets',
      target_weight: 0.7,
      target_risk_share: 1,
    },
    {
      target_line_id: 'saa-derivatives',
      target_set_id: 'saa-root',
      target_member_type: 'derivative_bucket',
      target_member_id: '__derivatives__',
      taxonomy_node_id: null,
      target_weight: 0.1,
      target_risk_share: null,
    },
    {
      target_line_id: 'saa-cash',
      target_set_id: 'saa-root',
      target_member_type: 'cash_bucket',
      target_member_id: '__cash__',
      taxonomy_node_id: null,
      target_weight: 0.2,
      target_risk_share: null,
    },
    {
      target_line_id: 'taa-risk',
      target_set_id: 'taa-root',
      target_member_type: 'taxonomy_node',
      target_member_id: 'risk-assets',
      taxonomy_node_id: 'risk-assets',
      target_weight: 0.75,
      target_risk_share: 1,
    },
    {
      target_line_id: 'taa-derivatives',
      target_set_id: 'taa-root',
      target_member_type: 'derivative_bucket',
      target_member_id: '__derivatives__',
      taxonomy_node_id: null,
      target_weight: 0.1,
      target_risk_share: null,
    },
    {
      target_line_id: 'taa-cash',
      target_set_id: 'taa-root',
      target_member_type: 'cash_bucket',
      target_member_id: '__cash__',
      taxonomy_node_id: null,
      target_weight: 0.15,
      target_risk_share: null,
    },
  ],
  target_set_integrity_issues: [],
}

function taxonomyCatalogWithFullUniverse() {
  return {
    ...taxonomyCatalog,
    risk_basis: {
      ...taxonomyCatalog.risk_basis,
      source_frequency_counts: { daily: 2 },
    },
    instrument_universe: [
      ...taxonomyCatalog.instrument_universe,
      {
        portfolio_id: '3',
        instrument_id: 'asset-2',
        instrument_ref: instrumentFixture({
          instrument_id: 'asset-2',
          instrument_name: 'Beta Fund',
          identifiers: [{ identifier_type: 'ticker', identifier_value: 'BETA', is_primary: true }],
        }),
        source: 'manual',
        holding_state: 'not_held',
        transaction_count: 0,
        status: 'active',
        instrument_trend_basis: 'total_return_nav',
        instrument_risk_frequency: 'daily',
        instrument_return_series_all: {
          first_return_start_date: isoDateDaysBefore(110),
          points: betaReturnPoints,
        },
      },
    ],
  }
}

function renderRiskPage() {
  return renderPortfolioPage(
    <RiskPage />,
    '/portfolios/3/risk',
    '/portfolios/:portfolioId/risk',
  )
}

describe('Risk rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          {
            ...riskHolding,
            market_value: 700,
            market_value_base: 700,
            allocation: 0.7,
          },
          holdingFixture({
            line_id: 'holding:planning-fcn',
            position_reference_id: 'planning-fcn',
            derivative_contract_id: 'planning-fcn',
            holding_category: 'derivatives',
            holding_kind: 'derivative_contract',
            instrument_core: null,
            derivative_contract: fcnContractFixture({
              derivative_contract_id: 'planning-fcn',
              contract_name: 'Planning FCN',
            }),
            market_value: 100,
            market_value_base: 100,
            allocation: 0.1,
            risk_eligible: false,
            risk_budget_eligible: false,
          }),
        ],
        totals: { market_value: 800, day_change_pct: null, day_change_value: null, cost_basis: null, allocation: 0.8 },
      }),
    )
    apiMocks.getPortfolioAccountsWorkspace.mockResolvedValue(accountsWorkspace)
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue(taxonomyCatalog)
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [] })
  })

  it('fails closed when every active member shares a provider observation gap', () => {
    const workspace = twoHoldingWorkspace()
    workspace.risk_basis = {
      ...workspace.risk_basis!,
      coverage_state: 'partial',
      gap_count: 2,
      gap_instrument_ids: ['asset-1', 'asset-2'],
      status_label: 'Risk basis partial - 2 instrument(s) have observation gaps',
    }

    const result = riskFrequencyProfileFromHoldingsWorkspace(workspace)

    expect(result.errors).toEqual([
      'Risk basis partial - 2 instrument(s) have observation gaps',
    ])
    expect(result.value.statusLabel).toBe('Risk basis unavailable')
  })

  it('does not let a provider gap on a policy-excluded holding block the eligible risk sleeve', () => {
    const workspace = holdingsWorkspaceFixture({
      rows: [
        riskHolding,
        holdingFixture({
          line_id: 'holding:fcn-1',
          position_reference_id: 'fcn-1',
          derivative_contract_id: 'fcn-1',
          holding_category: 'derivatives',
          instrument_core: null,
          derivative_contract: fcnContractFixture({
            derivative_contract_id: 'fcn-1',
            contract_name: 'Excluded FCN',
          }),
          allocation: 0.6,
          market_value: 600,
          market_value_base: 600,
          risk_eligible: false,
          risk_budget_eligible: false,
          forward_risk_status: 'policy_excluded',
          forward_risk_share: null,
          forward_contribution_to_variance: null,
        }),
      ],
      risk_basis: {
        requested_frequency: 'daily',
        resolved_frequency: 'daily',
        default_frequency: 'daily',
        source_frequency_counts: { daily: 1 },
        status_label: 'Risk basis partial - 1 instrument has an observation gap',
        coverage_state: 'partial',
        gap_count: 1,
        gap_instrument_ids: ['fcn-1'],
      },
    })

    const result = riskFrequencyProfileFromHoldingsWorkspace(workspace)

    expect(result.errors).toEqual([])
    expect(result.value.statusLabel).toBe('Daily risk basis')
  })

  it('keeps a blocking risk-basis reason on hover instead of repeating it as an alert', async () => {
    const workspace = twoHoldingWorkspace()
    workspace.risk_basis = {
      ...workspace.risk_basis!,
      coverage_state: 'partial',
      gap_count: 2,
      gap_instrument_ids: ['asset-1', 'asset-2'],
      status_label: 'Risk basis partial - 2 instrument(s) have observation gaps',
    }
    apiMocks.getHoldingsWorkspace.mockResolvedValue(workspace)

    renderRiskPage()

    const riskHealth = await screen.findByRole('region', { name: 'Risk health' })
    const status = within(riskHealth).getByText(/Risk basis unavailable/)
    expect(status).toHaveAttribute(
      'title',
      expect.stringContaining('Risk basis partial - 2 instrument(s) have observation gaps'),
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('excludes cash-account pending settlement rows from the risk member count', () => {
    const workspace = twoHoldingWorkspace()
    workspace.rows.push(
      holdingFixture({
        line_id: 'pending:pending_subscription:cash-1:asset-1:USD',
        holding_kind: 'pending_subscription',
        available_for_trading: false,
        economic_instrument_id: 'asset-1',
        instrument_core: instrumentFixture({
          instrument_id:
            'pending:pending_subscription:cash-1:asset-1:USD',
          instrument_name: 'Subscription receivable · Alpha Fund',
          instrument_type: 'other',
          identifiers: [],
        }),
        market_value: 100,
        market_value_base: 100,
        allocation: 0.1,
      }),
    )

    const result = riskFrequencyProfileFromHoldingsWorkspace(workspace)

    expect(result.errors).toEqual([])
    expect(result.value.statusLabel).toBe('Daily risk basis')
  })

  it('shows Cash and Derivatives in weight drift but excludes both from risk drift', async () => {
    renderRiskPage()

    const weightGap = await screen.findByRole('img', { name: 'Weight target drift' })
    const riskGap = screen.getByRole('img', { name: 'Risk budget target gap' })
    const riskHealth = screen.getByRole('region', { name: 'Risk health' })

    expect(within(weightGap).getByText('Cash')).toBeInTheDocument()
    expect(within(weightGap).getByText('Derivatives')).toBeInTheDocument()
    expect(within(weightGap).getAllByText('10.00%').length).toBeGreaterThan(0)
    expect(within(riskGap).queryByText('Cash')).not.toBeInTheDocument()
    expect(within(riskGap).queryByText('Derivatives')).not.toBeInTheDocument()
    expect(within(riskGap).getByText('Risk Assets')).toBeInTheDocument()
    expect(within(riskGap).getAllByText('100.00%')).toHaveLength(3)
    expect(within(riskHealth).getByText('Modeled Market Sleeve Volatility')).toBeInTheDocument()
    expect(within(riskHealth).getByText('10.00%')).toBeInTheDocument()
    expect(within(riskHealth).getByText(/61\/61 complete/)).toBeInTheDocument()
    expect(within(riskHealth).getByText('SAA 0.00% · TAA 0.00%')).toBeInTheDocument()
    expect(within(riskHealth).getByText('Cash / Unallocated')).toBeInTheDocument()
    expect(within(riskHealth).getByText('$200.00')).toBeInTheDocument()

    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledWith('3', {
      include_details: true,
    })
  })

  it('uses the scoped backend cash disclosure instead of deriving it from account liquidity', async () => {
    apiMocks.getPortfolioAccountsWorkspace.mockResolvedValue({
      ...accountsWorkspace,
      accounts: accountsWorkspace.accounts.map((account) => ({
        ...account,
        derived_cash_balance_base: null,
      })),
    })

    renderRiskPage()

    const riskHealth = await screen.findByRole('region', { name: 'Risk health' })
    const liquidityCard = within(riskHealth).getByText('Cash / Unallocated').closest('article')
    expect(liquidityCard).not.toBeNull()
    expect(within(liquidityCard as HTMLElement).getByText('$200.00')).toBeInTheDocument()
    expect(liquidityCard).toHaveAttribute(
      'title',
      'Disclosed exposure outside the covariance model.',
    )
  })

  it('defaults the correlation matrix to active non-cash Current Holdings', async () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue(taxonomyCatalogWithFullUniverse())

    renderRiskPage()

    const scopeSelect = await screen.findByRole('combobox', { name: 'Matrix scope' })
    expect(scopeSelect).toHaveValue('__current_holdings__')
    expect(within(scopeSelect).getByRole('option', { name: 'Current Holdings' })).toBeInTheDocument()
    expect(within(scopeSelect).getByRole('option', { name: 'Full Universe' })).toBeInTheDocument()
    expect(await screen.findAllByText('Alpha Fund')).not.toHaveLength(0)
    expect(screen.queryByText('Beta Fund')).not.toBeInTheDocument()
    const matrixSection = screen.getByText('Correlation Matrix').closest('section')
    expect(matrixSection).not.toBeNull()
    expect(within(matrixSection as HTMLElement).queryByText('Cash')).not.toBeInTheDocument()
  })

  it('switches explicitly to Full Universe without changing the page structure', async () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue(taxonomyCatalogWithFullUniverse())
    const user = userEvent.setup()

    renderRiskPage()

    const scopeSelect = await screen.findByRole('combobox', { name: 'Matrix scope' })
    await user.selectOptions(scopeSelect, '__full_universe__')

    expect(scopeSelect).toHaveValue('__full_universe__')
    expect(await screen.findAllByText('Beta Fund')).not.toHaveLength(0)
    expect(screen.getAllByText('Alpha Fund')).not.toHaveLength(0)
  })

  it('fails closed and names a Current Holdings member whose return series is missing', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      twoHoldingWorkspace(
        betaHolding([]),
      ),
    )

    renderRiskPage()

    const coverageStatus = await findCorrelationUnavailable()
    expect(coverageStatus).toHaveTextContent('Correlation matrix unavailable')
    expect(coverageStatus).toHaveAttribute('title', expect.stringContaining('Beta Fund'))
    expect(coverageStatus).toHaveAttribute(
      'title',
      expect.stringContaining('Full-history return series is missing'),
    )
    expect(coverageStatus).toHaveAttribute(
      'title',
      expect.stringContaining('no members or dates were dropped'),
    )
  })

  it('fails closed when a non-cash position has quantity but no allocation or base value', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            quantity: 10,
            allocation: null,
            market_value_base: null,
            instrument_return_series_all: riskHolding.instrument_return_series_all,
          }),
        ],
      }),
    )

    renderRiskPage()

    const coverageStatus = await findCorrelationUnavailable()
    expect(coverageStatus).toHaveAttribute(
      'title',
      expect.stringContaining('Alpha Fund: Current holding is missing its current portfolio weight.'),
    )
  })

  it('does not silently drop an active Full Universe member with missing history', async () => {
    const fullUniverseCatalog = taxonomyCatalogWithFullUniverse()
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({
      ...fullUniverseCatalog,
      instrument_universe: fullUniverseCatalog.instrument_universe.map((record) =>
        record.instrument_id === 'asset-2'
          ? { ...record, instrument_return_series_all: null }
          : record,
      ),
    })
    const user = userEvent.setup()

    renderRiskPage()

    const scopeSelect = await screen.findByRole('combobox', { name: 'Matrix scope' })
    await user.selectOptions(scopeSelect, '__full_universe__')
    const coverageStatus = await findCorrelationUnavailable()
    expect(coverageStatus).toHaveAttribute('title', expect.stringContaining('Beta Fund'))
    expect(coverageStatus).toHaveAttribute('title', expect.stringContaining('active universe payload'))
    expect(coverageStatus).toHaveAttribute('title', expect.stringContaining('All 2 scope members'))
  })

  it('fails closed on misaligned member dates and lists the first dates plus total count', async () => {
    const missingDate = isoDateDaysBefore(5)
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      twoHoldingWorkspace(
        betaHolding(betaReturnPoints.filter((point) => point.date !== missingDate)),
      ),
    )

    renderRiskPage()

    const coverageStatus = await findCorrelationUnavailable()
    expect(coverageStatus).toHaveAttribute('title', expect.stringContaining('Beta Fund'))
    expect(coverageStatus).toHaveAttribute('title', expect.stringContaining('Return dates do not match'))
    expect(coverageStatus).toHaveAttribute(
      'title',
      expect.stringContaining(`Missing dates (1 total): ${missingDate}`),
    )
  })

  it('fails closed when equal end dates represent different return periods', async () => {
    const mismatchedDate = isoDateDaysBefore(4)
    const mismatchedStartDate = isoDateDaysBefore(6)
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      twoHoldingWorkspace(
        betaHolding(
          betaReturnPoints.map((point) =>
            point.date === mismatchedDate
              ? { ...point, start_date: mismatchedStartDate }
              : point,
          ),
        ),
      ),
    )

    renderRiskPage()

    const coverageStatus = await findCorrelationUnavailable()
    expect(coverageStatus).toHaveAttribute('title', expect.stringContaining('Beta Fund'))
    expect(coverageStatus).toHaveAttribute(
      'title',
      expect.stringContaining('scope members do not share one period identity'),
    )
    expect(coverageStatus).toHaveAttribute('title', expect.stringContaining(mismatchedDate))
  })
})
