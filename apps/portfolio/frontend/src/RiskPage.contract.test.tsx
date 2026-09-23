import { act, fireEvent, screen, within, waitFor } from '@testing-library/react'
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
  requestInstrumentRisk: vi.fn().mockResolvedValue({ instruments: [], cases: [] }),
  getPortfolioAccountsWorkspace: vi.fn(),
  getPortfolioPerformance: vi.fn(),
  getPortfolioTaxonomyCatalog: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  getPortfolioInstrumentPriceChart: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))
vi.mock('./components/ConcentrationPanel', () => ({ default: () => <div>Concentration test panel</div> }))
vi.mock('./components/PortfolioTailRiskPanel', () => ({ default: () => <div>Tail risk test panel</div> }))

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
      nav: 1000,
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
  const status = await within(await screen.findByRole('region', { name: 'Correlation analysis' })).findByRole('status')
  const disclosure = within(status).getByText('View reasons and affected instruments')
  if (!disclosure.closest('details')?.open) await userEvent.setup().click(disclosure)
  return status
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
      root_allocation_basis: 'weight',
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
      allocation_basis: 'risk_budget',
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
  taxonomy_configuration_version: 1,
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
      status: 'active',
    },
    {
      target_set_id: 'taa-root',
      taxonomy_id: 'taxonomy-1',
      comparator_taxonomy_node_id: null,
      target_set_type: 'taa',
      name: 'TAA',
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
      target_value: 1,
    },
    {
      target_line_id: 'saa-cash',
      target_set_id: 'saa-root',
      target_member_type: 'cash_bucket',
      target_member_id: '__cash__',
      taxonomy_node_id: null,
      target_value: 0.2,
    },
    {
      target_line_id: 'taa-risk',
      target_set_id: 'taa-root',
      target_member_type: 'taxonomy_node',
      target_member_id: 'risk-assets',
      taxonomy_node_id: 'risk-assets',
      target_value: 1,
    },
    {
      target_line_id: 'taa-cash',
      target_set_id: 'taa-root',
      target_member_type: 'cash_bucket',
      target_member_id: '__cash__',
      taxonomy_node_id: null,
      target_value: 0.15,
    },
  ],
  target_set_integrity_issues: [],
  target_resolution: [{ taxonomy_id: 'taxonomy-1', scope_targets: [], errors: [], member_targets: [{ scope_node_id: null, member_type: 'taxonomy_node', member_id: 'risk-assets', taxonomy_node_id: 'risk-assets', target_basis: 'risk_budget', strategic_value: 1, tactical_value: 1, strategic_source: 'saa', tactical_source: 'taa', strategic_target_set_id: 'saa-root', tactical_target_set_id: 'taa-root', strategic_global_risk_target: 1, tactical_global_risk_target: 1, strategic_status: 'complete', tactical_status: 'complete' }] }],
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
    localStorage.removeItem('investment_studio.portfolio.risk.target-taxonomy.3')
    localStorage.removeItem('investment_studio.portfolio.risk.settings.v1')
    apiMocks.getPortfolioPerformance.mockResolvedValue({ portfolio_id: '3', base_currency: 'USD',
      summary: { risk_calculation_frequency: 'daily', risk_metric_basis: 'market_risk_return' },
      daily_series: returnPoints.map((point) => ({ as_of_date: point.date, market_risk_daily_return: point.value, market_risk_return_coverage_state: 'complete', market_risk_return_chain_continuous: true, market_risk_return_observation_eligible: true })) })
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
          }),
        ],
        totals: { nav: 1000, market_value: 800, day_change_pct: null, day_change_value: null, cost_basis: null, allocation: 0.8 },
      }),
    )
    apiMocks.getPortfolioAccountsWorkspace.mockResolvedValue(accountsWorkspace)
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue(taxonomyCatalog)
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [] })
  })

  it('preserves a historical cutoff selected before the initial date effect runs', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(twoHoldingWorkspace())
    const historicalDate = isoDateDaysBefore(5)
    let changed = false
    // Select on the first interactive render, before pending mount effects can
    // initialize the latest cutoff. Waiting for the page first misses this race.
    const observer = new MutationObserver(() => {
      const cutoff = screen.queryByLabelText('Matrix as of')
      if (cutoff && !changed) {
        changed = true
        fireEvent.change(cutoff, { target: { value: historicalDate } })
      }
    })
    observer.observe(document.body, { childList: true, subtree: true })
    try {
      renderRiskPage()
      await waitFor(() => expect(changed).toBe(true))
      await waitFor(() => {
        expect(screen.getByLabelText('Matrix as of')).toHaveValue(historicalDate)
        const sample = within(screen.getByRole('region', { name: 'Correlation analysis' })).getByLabelText('Analysis sample')
        expect(sample).toHaveTextContent('Observation window 2026-06-10 → 2026-07-10')
        expect(sample).toHaveTextContent('Actual sample 2026-06-11 → 2026-07-10')
      })
    } finally { observer.disconnect() }
  })

  it('changes the matrix observation cutoff and explains dates outside available history', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(twoHoldingWorkspace())
    renderRiskPage()
    const cutoff = await screen.findByLabelText('Matrix as of')
    const historicalDate = isoDateDaysBefore(5)
    fireEvent.change(cutoff, { target: { value: historicalDate } })
    expect(within(screen.getByRole('region', { name: 'Correlation analysis' })).getByLabelText('Analysis sample')).toHaveTextContent(historicalDate)
    fireEvent.change(cutoff, { target: { value: '2099-01-01' } })
    expect(cutoff).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText(/No return observation exists on this date/)).toBeVisible()
    expect(within(screen.getByRole('region', { name: 'Correlation analysis' })).getByLabelText('Analysis sample')).not.toHaveTextContent('2099-01-01')
    await userEvent.click(screen.getByRole('button', { name: 'Latest' }))
    expect(cutoff).toHaveValue('2026-07-15')
    expect(cutoff).toHaveAttribute('aria-invalid', 'false')
  })

  it('keeps quality details in the Risk Health heading and opens them on request', async () => {
    const user = userEvent.setup()
    const warning = 'Corporate action review required: confirm the issuer evidence.'
    apiMocks.getHoldingsWorkspace.mockResolvedValue({
      ...twoHoldingWorkspace(), quality_warnings: [warning],
    })
    renderRiskPage()

    const hint = await screen.findByRole('button', { name: /Data quality warning:/ })
    expect(hint.closest('.panel-title')).toHaveTextContent('Current Holdings Risk')
    expect(screen.queryByText(warning)).not.toBeInTheDocument()
    await user.click(hint)
    expect(within(screen.getByRole('tooltip')).getByText(warning)).toBeVisible()
  })

  it.each([
    { currency: 'USD', blocked: false, detail: 'Comparison uses the selected price-return series.' },
    { currency: 'HKD', blocked: true, detail: 'requires a base-currency return series; got HKD versus USD.' },
  ])('keeps $currency benchmark comparison details in the rolling-risk toolbar', async ({ currency, blocked, detail }) => {
    const user = userEvent.setup()
    const instrument = {
      instrument_id: 'benchmark-1', instrument_name: 'Market Benchmark',
      instrument_type: 'index', currency, identifiers: [], latest_market_data: [],
    }
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [instrument] })
    apiMocks.getPortfolioInstrumentPriceChart.mockResolvedValue({
      portfolio_id: '3', instrument_core: instrument, as_of_date: '2026-07-15', range_key: 'all',
      chart_basis: 'close', return_semantics: 'price_return', currency, metric_family: 'price',
      coverage_state: 'complete',
      points: returnPoints.map((point, index) => ({ date: point.date, value: 100 + index })),
    })
    renderRiskPage()
    await user.type(await screen.findByRole('searchbox', { name: 'Compare benchmark' }), 'Market')
    await user.click(await screen.findByRole('button', { name: /Market Benchmark/ }))

    const hint = await screen.findByRole('button', { name: /Benchmark comparison:/ })
    expect(hint.closest('.risk-rolling-toolbar')).not.toBeNull()
    expect(hint).toHaveAttribute('aria-label', expect.stringContaining(detail))
    expect(Boolean(screen.queryByText('Benchmark unavailable'))).toBe(blocked)
    await user.click(hint)
    expect(screen.getByRole('tooltip')).toHaveTextContent(detail)
  })

  it('keeps an excluded holding with unknown exposure visible without displaying zero', async () => {
    const workspace = twoHoldingWorkspace()
    workspace.risk_coverage_summary = {
      ...workspace.risk_coverage_summary!,
      coverage_ratio: null,
      excluded_rows: [{ line_id: 'missing-price', instrument_id: 'asset-2', instrument_name: 'Unknown valuation',
        holding_category: 'securities', exposure_base: null, exclusion_reason: 'Price unavailable', modeling_status: 'unavailable' }],
    }
    apiMocks.getHoldingsWorkspace.mockResolvedValue(workspace)
    renderRiskPage()
    const row = (await screen.findByText('Unknown valuation')).closest('tr')!
    expect(within(row).getByText('—')).toBeInTheDocument()
    expect(row).toHaveTextContent('Price unavailable')
    expect(row).not.toHaveTextContent('$0.00')
  })

  it('keeps frequency resolution separate from whole-history provider gap coverage', () => {
    const workspace = twoHoldingWorkspace()
    workspace.risk_basis = {
      ...workspace.risk_basis!,
      coverage_state: 'partial',
      gap_count: 2,
      gap_instrument_ids: ['asset-1', 'asset-2'],
      status_label: 'Risk basis partial - 2 instrument(s) have observation gaps',
    }

    const result = riskFrequencyProfileFromHoldingsWorkspace(workspace)

    expect(result.errors).toEqual([])
    expect(result.value.statusLabel).toBe('Daily risk basis')
  })

  it('does not let a provider gap on a holding excluded by the model block the eligible risk sleeve', () => {
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
          forward_risk_status: 'excluded',
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

  it('discloses historical provider gaps without marking every risk window unavailable', async () => {
    const workspace = twoHoldingWorkspace()
    workspace.risk_basis = {
      ...workspace.risk_basis!,
      coverage_state: 'partial',
      gap_count: 2,
      gap_instrument_ids: ['asset-1', 'asset-2'],
      status_label: 'Risk basis partial - 2 instrument(s) have observation gaps',
      gap_details: [{ instrument_id: 'asset-1', gap_count: 1, gap_detection_basis: 'market_calendar:TEST', gap_date_sample: ['2026-04-01'] }],
    }
    apiMocks.getHoldingsWorkspace.mockResolvedValue(workspace)

    renderRiskPage()

    expect(await screen.findByText(/Historical source coverage/)).toBeInTheDocument()
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

  it('compares only risk targets and keeps cash and derivatives in risk coverage', async () => {
    renderRiskPage()

    const riskGap = await screen.findByRole('img', { name: 'Risk budget target gap' })
    expect(screen.queryByRole('img', { name: 'Weight target drift' })).not.toBeInTheDocument()
    const riskHealth = screen.getByRole('region', { name: 'Risk health' })

    expect(screen.queryByRole('region', { name: '风险研判' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '持仓风险关注' })).not.toBeInTheDocument()
    expect(apiMocks.requestInstrumentRisk).not.toHaveBeenCalled()

    expect(within(riskGap).queryByText('Cash')).not.toBeInTheDocument()
    expect(within(riskGap).queryByText('Derivatives')).not.toBeInTheDocument()
    expect(within(riskGap).getByText('Risk Assets')).toBeInTheDocument()
    expect(within(riskGap).getAllByText('100.00%')).toHaveLength(3)
    expect(within(riskHealth).getByText('Modeled Market Sleeve Volatility')).toBeInTheDocument()
    expect(within(riskHealth).getByText('10.00%')).toBeInTheDocument()
    expect(within(riskHealth).getByText(/61\/61 complete/)).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Current drift' })).getByText('SAA 0.00% · TAA 0.00%')).toBeInTheDocument()
    expect(within(riskHealth).getByText('Cash / Unallocated')).toBeInTheDocument()
    expect(within(riskHealth).getByText('$200.00')).toBeInTheDocument()

    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledWith('3', {
      include_details: true,
    }, expect.any(AbortSignal))
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
    expect(await findCorrelationUnavailable()).toHaveTextContent('at least two members')
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
    expect(coverageStatus).toHaveTextContent('Beta Fund')
    expect(coverageStatus).toHaveTextContent('Full-history return series is missing')
    expect(within(screen.getByRole('region', { name: 'Correlation analysis' })).queryByRole('table')).not.toBeInTheDocument()
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
    expect(coverageStatus).toHaveTextContent('Current holding is missing its current portfolio weight.')
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
    expect(coverageStatus).toHaveTextContent('Beta Fund')
    expect(coverageStatus).toHaveTextContent('active universe payload')
    expect(within(screen.getByRole('region', { name: 'Correlation analysis' })).queryByRole('table')).not.toBeInTheDocument()
  })

  it('fails closed on misaligned member dates and lists the affected member and dates', async () => {
    const missingDate = isoDateDaysBefore(5)
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      twoHoldingWorkspace(
        betaHolding(betaReturnPoints.filter((point) => point.date !== missingDate)),
      ),
    )

    renderRiskPage()

    const coverageStatus = await findCorrelationUnavailable()
    expect(coverageStatus).toHaveTextContent('Beta Fund')
    expect(coverageStatus).toHaveTextContent('Return dates do not match')
    expect(coverageStatus).toHaveTextContent(missingDate)
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
    expect(coverageStatus).toHaveTextContent('Beta Fund')
    expect(coverageStatus).toHaveTextContent('scope members do not share one period identity')
    expect(coverageStatus).toHaveTextContent(mismatchedDate)
  })
  it('switches target taxonomy locally without changing model scope or NAV', async () => {
    const user = userEvent.setup()
    const customTaxonomy = { ...taxonomyCatalog.taxonomies[0], taxonomy_id: 'industry', name: 'Industry' }
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({
      ...taxonomyCatalog,
      taxonomies: [...taxonomyCatalog.taxonomies, customTaxonomy],
      taxonomy_nodes: [...taxonomyCatalog.taxonomy_nodes, { ...taxonomyCatalog.taxonomy_nodes[0], taxonomy_id: 'industry', taxonomy_node_id: 'technology', node_name: 'Technology' }],
      taxonomy_assignments: [...taxonomyCatalog.taxonomy_assignments, { ...taxonomyCatalog.taxonomy_assignments[0], assignment_id: 'industry-assignment', taxonomy_id: 'industry', taxonomy_node_id: 'technology' }],
    })
    renderRiskPage()
    const selector = await screen.findByRole('combobox', { name: 'Risk taxonomy' })
    const healthBefore = screen.getByRole('region', { name: 'Risk health' }).querySelector('.risk-health-strip')?.textContent
    const holdingsReads = apiMocks.getHoldingsWorkspace.mock.calls.length
    expect(selector).toHaveValue('')
    expect(within(selector).getByRole('option', { name: 'Choose taxonomy' })).toBeInTheDocument()
    expect(screen.queryByText('Portfolio risk target gap')).not.toBeInTheDocument()
    await user.selectOptions(selector, 'industry')
    const drift = screen.getByRole('region', { name: 'Current drift' })
    expect(drift).toHaveTextContent('No directly derived portfolio risk target for this taxonomy.')
    expect(within(drift).queryByRole('img')).not.toBeInTheDocument()
    expect(within(drift).queryByText('SAA')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Risk health' }).querySelector('.risk-health-strip')?.textContent).toBe(healthBefore)
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(holdingsReads)
    expect(localStorage.getItem('investment_studio.portfolio.risk.target-taxonomy.3')).toBe('industry')
    await user.selectOptions(selector, 'taxonomy-1')
    expect(within(drift).getByRole('img', { name: 'Risk budget target gap' })).toBeInTheDocument()
  })

  it('automatically selects the only active taxonomy only before any selection has been stored', async () => {
    const user = userEvent.setup()
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ ...taxonomyCatalog,
      taxonomies: [...taxonomyCatalog.taxonomies, { ...taxonomyCatalog.taxonomies[0], taxonomy_id: 'retired', name: 'Retired', status: 'archived' }],
    })
    const page = renderRiskPage()
    const selector = await screen.findByRole('combobox', { name: 'Risk taxonomy' })
    await waitFor(() => expect(selector).toHaveValue('taxonomy-1'))
    expect(localStorage.getItem('investment_studio.portfolio.risk.target-taxonomy.3')).toBe('taxonomy-1')
    expect(within(selector).queryByRole('option', { name: 'Retired' })).not.toBeInTheDocument()
    await user.selectOptions(selector, '')
    expect(selector).toHaveValue('')
    expect(screen.queryByText('Portfolio risk target gap')).not.toBeInTheDocument()
    expect(localStorage.getItem('investment_studio.portfolio.risk.target-taxonomy.3')).toBe('')
    page.unmount()
    renderRiskPage()
    expect(await screen.findByRole('combobox', { name: 'Risk taxonomy' })).toHaveValue('')
    expect(screen.queryByText('Portfolio risk target gap')).not.toBeInTheDocument()
  })

  it.each(['deleted', 'retired'])('does not replace a stored unavailable taxonomy %s with the remaining active taxonomy', async (taxonomyId) => {
    localStorage.setItem('investment_studio.portfolio.risk.target-taxonomy.3', taxonomyId)
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ ...taxonomyCatalog,
      taxonomies: [...taxonomyCatalog.taxonomies, { ...taxonomyCatalog.taxonomies[0], taxonomy_id: 'retired', name: 'Retired', status: 'archived' }],
    })
    renderRiskPage()
    const selector = await screen.findByRole('combobox', { name: 'Risk taxonomy' })
    expect(selector).toHaveValue('')
    expect(within(selector).queryByRole('option', { name: 'Retired' })).not.toBeInTheDocument()
    expect(screen.queryByText('Portfolio risk target gap')).not.toBeInTheDocument()
    expect(localStorage.getItem('investment_studio.portfolio.risk.target-taxonomy.3')).toBe(taxonomyId)
    await userEvent.setup().selectOptions(selector, 'taxonomy-1')
    expect(screen.getByRole('img', { name: 'Risk budget target gap' })).toBeInTheDocument()
  })

  it('preserves the stored taxonomy selection when its catalog cannot be read', async () => {
    localStorage.setItem('investment_studio.portfolio.risk.target-taxonomy.3', 'taxonomy-1')
    apiMocks.getPortfolioTaxonomyCatalog.mockRejectedValueOnce(new Error('Classification catalog offline.'))
    renderRiskPage()
    expect(await screen.findByText('Classification catalog offline.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Risk health' })).toBeInTheDocument()
    expect(localStorage.getItem('investment_studio.portfolio.risk.target-taxonomy.3')).toBe('taxonomy-1')
    expect(screen.queryByRole('combobox', { name: 'Risk taxonomy' })).not.toBeInTheDocument()
  })

  it('does not substitute capital weights when portfolio risk targets cannot be derived', async () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ ...taxonomyCatalog, target_resolution: [] })
    renderRiskPage()
    const drift = await screen.findByRole('region', { name: 'Current drift' })
    expect(drift).toHaveTextContent('No directly derived portfolio risk target for this taxonomy.')
    expect(within(drift).queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByText('Weight Target Gap')).not.toBeInTheDocument()
  })

  it.each([
    [true, 'modeled holdings remain unclassified'],
    [false, 'held securities are outside the production risk model'],
  ])('does not compare a partial classified or modeled universe to portfolio targets (%s)', async (eligible, reason) => {
    const workspace = twoHoldingWorkspace()
    workspace.rows[0].forward_risk_share = eligible ? .8 : 1
    workspace.rows[1].risk_eligible = eligible
    workspace.rows[1].forward_risk_share = eligible ? .2 : null
    workspace.rows[1].forward_risk_status = eligible ? 'ok' : 'unavailable'
    apiMocks.getHoldingsWorkspace.mockResolvedValue(workspace)
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ ...taxonomyCatalog,
      taxonomy_assignments: taxonomyCatalog.taxonomy_assignments.filter((item) => item.target_entity_id !== 'asset-2'),
    })
    renderRiskPage()
    const drift = await screen.findByRole('region', { name: 'Current drift' })
    expect(drift).toHaveTextContent(reason)
    expect(within(drift).queryByRole('img', { name: 'Risk budget target gap' })).not.toBeInTheDocument()
  })

  it('regroups production RC and retains unheld targets in another taxonomy', async () => {
    const user = userEvent.setup()
    const industryTaxonomy = { ...taxonomyCatalog.taxonomies[0], taxonomy_id: 'industry', name: 'Industry' }
    const industryNodes = ['technology', 'healthcare'].map((id, index) => ({
      ...taxonomyCatalog.taxonomy_nodes[0], taxonomy_id: 'industry', taxonomy_node_id: id,
      node_name: index === 0 ? 'Technology' : 'Healthcare', sort_order: index,
    }))
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({
      ...taxonomyCatalog,
      taxonomies: [...taxonomyCatalog.taxonomies, industryTaxonomy],
      taxonomy_nodes: [...taxonomyCatalog.taxonomy_nodes, ...industryNodes],
      taxonomy_assignments: [...taxonomyCatalog.taxonomy_assignments, {
        ...taxonomyCatalog.taxonomy_assignments[0], assignment_id: 'industry-assignment', taxonomy_id: 'industry', taxonomy_node_id: 'technology',
      }],
      target_resolution: [...taxonomyCatalog.target_resolution, { taxonomy_id: 'industry', scope_targets: [], errors: [], member_targets: industryNodes.map((node) => ({ ...taxonomyCatalog.target_resolution[0].member_targets[0], member_id: node.taxonomy_node_id, taxonomy_node_id: node.taxonomy_node_id, strategic_global_risk_target: node.taxonomy_node_id === 'technology' ? .6 : .4, tactical_global_risk_target: node.taxonomy_node_id === 'technology' ? .6 : .4 })) }],
    })
    renderRiskPage()
    const selector = await screen.findByRole('combobox', { name: 'Risk taxonomy' })
    const healthBefore = screen.getByRole('region', { name: 'Risk health' }).querySelector('.risk-health-strip')?.textContent
    const holdingsReads = apiMocks.getHoldingsWorkspace.mock.calls.length
    await user.selectOptions(selector, 'industry')
    expect(within(screen.getByRole('combobox', { name: 'Matrix scope' })).getByRole('option', { name: 'Taxonomy: Technology' })).toBeInTheDocument()
    const riskChart = screen.getByRole('img', { name: 'Risk budget target gap' })
    const techRow = within(riskChart).getByText('Technology').closest('.risk-target-gap-row')!
    const healthcareRow = within(riskChart).getByText('Healthcare').closest('.risk-target-gap-row')!
    expect(techRow).toHaveTextContent('100.00%')
    expect(techRow).toHaveTextContent('60.00%')
    expect(healthcareRow).toHaveTextContent('0.00%')
    expect(healthcareRow).toHaveTextContent('40.00%')
    expect(screen.getByRole('region', { name: 'Risk health' }).querySelector('.risk-health-strip')?.textContent).toBe(healthBefore)
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(holdingsReads)
    expect(screen.getByRole('button', { name: /Target drift risk basis:/ })).toHaveAccessibleName(expect.stringContaining('Switching taxonomy regroups those contributions'))
  })


  it('loads current taxonomy in parallel with holdings and defers unused universe history', async () => {
    let resolveHoldings!: (value: ReturnType<typeof twoHoldingWorkspace>) => void
    apiMocks.getHoldingsWorkspace.mockReturnValueOnce(new Promise((resolve) => { resolveHoldings = resolve }))
    renderRiskPage()
    await waitFor(() => expect(apiMocks.getPortfolioTaxonomyCatalog).toHaveBeenCalledWith('3', {}, expect.any(AbortSignal)))
    expect(apiMocks.getPortfolioTaxonomyCatalog).toHaveBeenCalledTimes(1)
    await act(async () => { resolveHoldings(twoHoldingWorkspace()) })
    await screen.findByRole('combobox', { name: 'Matrix scope' })
    expect(apiMocks.getPortfolioTaxonomyCatalog).toHaveBeenCalledTimes(1)
  })

  it('cancels unused universe history without replacing the current-holdings matrix with a late result', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(twoHoldingWorkspace())
    let resolveHistory!: (value: ReturnType<typeof taxonomyCatalogWithFullUniverse>) => void
    apiMocks.getPortfolioTaxonomyCatalog.mockImplementation((_id, filters) => filters.include_market_profile
      ? new Promise((resolve) => { resolveHistory = resolve })
      : Promise.resolve(taxonomyCatalog))
    const user = userEvent.setup()
    renderRiskPage()
    const scope = await screen.findByRole('combobox', { name: 'Matrix scope' })
    await user.selectOptions(scope, '__full_universe__')
    expect(await screen.findByText('Loading Full Universe history')).toBeInTheDocument()
    const signal = apiMocks.getPortfolioTaxonomyCatalog.mock.calls[apiMocks.getPortfolioTaxonomyCatalog.mock.calls.length - 1]?.[2] as AbortSignal
    expect(signal.aborted).toBe(false)
    await user.selectOptions(scope, '__current_holdings__')
    expect(signal.aborted).toBe(true)
    const matrix = screen.getByRole('region', { name: 'Correlation analysis' })
    const currentMatrix = matrix.textContent
    await act(async () => { resolveHistory(taxonomyCatalogWithFullUniverse()) })
    expect(matrix.textContent).toBe(currentMatrix)
    expect(screen.queryByText('Loading Full Universe history')).not.toBeInTheDocument()
  })

  it('keeps a universe history failure local to that matrix scope', async () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockImplementation((_id, filters) => filters.include_market_profile
      ? Promise.reject(new Error('Universe history unavailable'))
      : Promise.resolve(taxonomyCatalog))
    const user = userEvent.setup()
    renderRiskPage()
    const scope = await screen.findByRole('combobox', { name: 'Matrix scope' })
    await user.selectOptions(scope, '__full_universe__')
    expect(await screen.findByRole('alert')).toHaveTextContent('Universe history unavailable')
    expect(screen.getByRole('region', { name: 'Risk health' })).toBeInTheDocument()
    await user.selectOptions(scope, '__current_holdings__')
    expect(screen.queryByText('Universe history unavailable')).not.toBeInTheDocument()
    expect(await findCorrelationUnavailable()).toHaveTextContent('at least two members')
  })

  it('loads taxonomy market history at the holdings cutoff, not the wall-clock date', async () => {
    renderRiskPage()
    const scope = await screen.findByRole('combobox', { name: 'Matrix scope' })
    await userEvent.selectOptions(scope, '__full_universe__')
    await waitFor(() => expect(apiMocks.getPortfolioTaxonomyCatalog).toHaveBeenCalledWith('3', {
      include_market_profile: true, as_of_date: '2026-07-15',
    }, expect.any(AbortSignal)))
  })

  it('keeps actual-path loading errors isolated from current-holdings rolling estimates', async () => {
    apiMocks.getPortfolioPerformance.mockRejectedValue(new Error('Actual history service unavailable'))
    renderRiskPage()
    expect(await screen.findByText('Actual history service unavailable')).toBeInTheDocument()
    const perspective = screen.getByRole('combobox', { name: 'Rolling risk perspective' })
    expect(perspective).toHaveValue('realized')
    await userEvent.setup().selectOptions(perspective, 'current')
    expect(screen.queryByText('Actual history service unavailable')).not.toBeInTheDocument()
    expect(await screen.findByRole('img', { name: 'Annualized Volatility' })).toBeInTheDocument()
  })

  it('compares assets in a leaf classification without inheriting an out-of-scope currency failure', async () => {
    const workspace = twoHoldingWorkspace()
    workspace.rows.push(holdingFixture({ line_id: 'holding:gamma', allocation: 0.1,
      instrument_core: instrumentFixture({ instrument_id: 'gamma', instrument_name: 'Gamma', currency: 'HKD' }),
      instrument_return_series_all: null }))
    apiMocks.getHoldingsWorkspace.mockResolvedValue(workspace)
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ ...taxonomyCatalog,
      taxonomy_nodes: [...taxonomyCatalog.taxonomy_nodes, { ...taxonomyCatalog.taxonomy_nodes[0], taxonomy_node_id: 'other', node_name: 'Other' }],
      taxonomy_assignments: [...taxonomyCatalog.taxonomy_assignments,
        { ...taxonomyCatalog.taxonomy_assignments[0], assignment_id: 'beta-assignment', target_entity_id: 'asset-2' },
        { ...taxonomyCatalog.taxonomy_assignments[0], assignment_id: 'gamma-assignment', target_entity_id: 'gamma', taxonomy_node_id: 'other' }],
    })
    renderRiskPage()
    await findCorrelationUnavailable()
    await userEvent.setup().selectOptions(screen.getByRole('combobox', { name: 'Matrix scope' }), 'risk-assets')
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Correlation analysis' })).queryByRole('status')).not.toBeInTheDocument())
    expect(screen.getAllByText('Alpha Fund').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Beta Fund').length).toBeGreaterThan(0)
    expect(screen.queryByText('Gamma')).not.toBeInTheDocument()
  })

  it('excludes zero-exposure rows from the selected classification member count', async () => {
    const workspace = twoHoldingWorkspace()
    workspace.rows.push(holdingFixture({ line_id: 'holding:closed', quantity: 0, allocation: 0, market_value_base: 0,
      instrument_core: instrumentFixture({ instrument_id: 'closed', instrument_name: 'Closed Position' }),
      instrument_return_series_all: null }))
    apiMocks.getHoldingsWorkspace.mockResolvedValue(workspace)
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ ...taxonomyCatalog,
      taxonomy_assignments: [...taxonomyCatalog.taxonomy_assignments,
        { ...taxonomyCatalog.taxonomy_assignments[0], assignment_id: 'beta-assignment', target_entity_id: 'asset-2' },
        { ...taxonomyCatalog.taxonomy_assignments[0], assignment_id: 'closed-assignment', target_entity_id: 'closed' }],
    })
    renderRiskPage()
    const scope = await screen.findByRole('combobox', { name: 'Matrix scope' })
    // Taxonomy choices arrive after the holdings-backed selector is mounted.
    const classification = await within(scope).findByRole('option', { name: /Risk Assets/ })
    await userEvent.setup().selectOptions(scope, classification)
    expect(await within(screen.getByRole('region', { name: 'Correlation analysis' })).findByRole('table')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Correlation analysis' })).queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByText('Closed Position')).not.toBeInTheDocument()
  })

  it('invalidates the previous benchmark immediately while another selection is loading', async () => {
    const first = { instrument_id: 'first', instrument_name: 'First Benchmark', instrument_type: 'index', currency: 'USD', identifiers: [], latest_market_data: [] }
    const second = { ...first, instrument_id: 'second', instrument_name: 'Second Benchmark' }
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [first, second] })
    apiMocks.getPortfolioInstrumentPriceChart.mockResolvedValueOnce({ portfolio_id: '3', instrument_core: first,
      as_of_date: '2026-07-15', range_key: 'all', currency: 'USD', chart_basis: 'close', return_semantics: 'price_return',
      coverage_state: 'complete', points: returnPoints.map((point, index) => ({ date: point.date, value: 100 + index })) })
      .mockImplementationOnce(() => new Promise(() => {}))
    const user = userEvent.setup()
    renderRiskPage()
    const search = await screen.findByRole('searchbox', { name: 'Compare benchmark' })
    await user.type(search, 'First')
    await user.click(await screen.findByRole('button', { name: /First Benchmark/ }))
    await waitFor(() => expect(document.querySelector('.rolling-risk-line-benchmark')).not.toBeNull())
    await user.clear(search)
    await user.type(search, 'Second')
    await user.click(await screen.findByRole('button', { name: /Second Benchmark/ }))
    await waitFor(() => expect(apiMocks.getPortfolioInstrumentPriceChart).toHaveBeenCalledTimes(2))
    expect(document.querySelector('.rolling-risk-line-benchmark')).toBeNull()
  })

})
