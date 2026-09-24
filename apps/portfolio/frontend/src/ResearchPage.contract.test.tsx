import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ResearchPage from './pages/ResearchPage'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  createPortfolioResearchRun: vi.fn(),
  getPortfolioResearchBacktestBenchmarkComparison: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  getPortfolioRiskPolicy: vi.fn(),
  getPortfolioTaxonomyCatalog: vi.fn(),
  getPortfolioResearchRun: vi.fn(),
  getPortfolioResearchWorkbench: vi.fn(),
  updatePortfolioResearchSettings: vi.fn(),
}))
const accessMock = vi.hoisted(() => ({ can_edit: true }))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioAccessProvider', () => ({ usePortfolioAccess: () => accessMock }))
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

const shortHistoryMetrics = {
  start_date: '2026-07-01',
  end_date: '2026-07-15',
  period_return: 0.018,
  ytd_return: null,
  annualization_eligible: false,
  annualization_years: 14 / 365.25,
  annualization_unavailable_reason: 'measurement_period_shorter_than_one_year',
  annualized_return: null,
  annualized_volatility: null,
  sharpe_ratio: null,
  max_drawdown: -0.012,
  max_drawdown_start_date: '2026-07-08',
  max_drawdown_end_date: '2026-07-10',
  max_drawdown_days: 2,
  max_drawdown_recovery_date: null,
  max_drawdown_recovery_days: null,
  current_drawdown: -0.004,
  calmar_ratio: null,
}

const solutionTreeFixture = {
  schema_version: 2, as_of_date: '2026-07-15', base_currency: 'USD', portfolio_nav: 1000,
  risk_attribution_scope: 'portfolio', capital_weight_basis: 'portfolio_nav', hierarchy_status: 'complete', configuration_captured_at: '2026-07-15',
  rows: [
    { row_id: 'root', parent_row_id: null, row_kind: 'portfolio', member_type: 'portfolio', member_id: '3', label: 'Long-Term Portfolio', depth: 0, path: ['Long-Term Portfolio'] },
    { row_id: 'category:risk-assets', parent_row_id: 'root', row_kind: 'category', member_type: 'taxonomy_node', member_id: 'risk-assets', label: 'Risk Assets', depth: 1, path: ['Long-Term Portfolio', 'Risk Assets'] },
    { row_id: 'instrument:asset-1', parent_row_id: 'category:risk-assets', row_kind: 'instrument', member_type: 'instrument', member_id: 'asset-1', label: 'Alpha Fund', depth: 2, path: ['Long-Term Portfolio', 'Risk Assets', 'Alpha Fund'] },
  ].map((row) => ({ ...row, target_risk_share: 1, solved_risk_share: 1, current_value_base: 200,
    current_weight: .2, target_value_base: 800, target_weight: .8, rebalance_value_base: 600,
    trade_constraint: 'adjustable', risk_model_status: 'modeled', execution_status: 'ready', execution_note: null,
    min_weight: null, max_weight: null, bound_status: null,
  })),
}

const completedRun = {
  research_run_id: 'research-1',
  portfolio_id: '3',
  job_type: 'portfolio_research',
  status: 'completed',
  finished_at: '2026-07-15T08:00:00Z',
  as_of_date: '2026-07-15',
  planning_taxonomy_id: 'taxonomy-1',
  planning_taxonomy_name: 'Policy Allocation',
  lookback_days: 365,
  reliability_state: 'current',
  is_current: true,
  reliability_reasons: [],
  artifact_count: 0,
  artifacts: [],
  detail: {
    solution_tree: solutionTreeFixture,
    solve_event: {
      as_of_date: '2026-07-15',
      scope_node_id: null,
      scope_label: 'Top Level',
      target_dimension: 'risk_budget',
      solver_kind: 'risk-budget',
      solver_detail: 'convex_log_barrier',
      target_status: 'satisfied',
      execution_ready: true,
      covariance_model: 'sample_covariance',
      covariance_observations: 12,
      gap_turnover: 0.1,
      max_risk_share_gap: 0.001,
      estimated_risk_sleeve_volatility: 0.08,
      member_count: 1,
    },
    scope_solve_events: [],
    solved_result_groups: [
      {
        top_sleeve_id: 'risk-assets',
        top_sleeve_label: 'Risk Assets',
        current_weight: 0.2,
        solved_weight: 0.8,
        current_value_base: 200,
        target_value_base: 800,
        target_risk_share: 1,
        forward_risk_contribution: 1,
        min_weight: 0.6,
        max_weight: 0.9,
        bound_status: 'within',
        rows: [
          {
            member_type: 'instrument',
            member_id: 'asset-1',
            label: 'Alpha Fund',
            top_sleeve_id: 'risk-assets',
            top_sleeve_label: 'Risk Assets',
            current_weight: 0.2,
            solved_weight: 0.8,
            current_value_base: 200,
            target_value_base: 800,
            target_risk_share: 1,
            forward_risk_contribution: 1,
          },
        ],
      },
    ],
    target_weight_gaps: [
      {
        member_type: 'instrument',
        member_id: 'asset-1',
        label: 'Alpha Fund',
        current_weight: 0.2,
        target_weight: 0.3,
        gap: 0.1,
        current_value_base: 200,
        target_value_base: 300,
        base_currency: 'USD',
        action: 'Increase',
        research_lifecycle: 'held',
        research_eligibility: 'eligible',
        research_pm_approved: false,
        execution_status: 'ready',
        execution_note: null,
      },
      {
        member_type: 'instrument',
        member_id: 'former-asset',
        label: 'Former Holding Fund',
        current_weight: 0,
        target_weight: 0.05,
        gap: 0.05,
        current_value_base: 0,
        target_value_base: 50,
        base_currency: 'USD',
        action: 'Review',
        research_lifecycle: 'former',
        research_eligibility: 'pm_review_required',
        research_pm_approved: false,
        execution_status: 'manual_review_required',
        execution_note: 'Status: Former · Research eligibility: manual PM review required before execution.',
      },
    ],
    backtest: {
      rebalance_frequency: '1m',
      common_history_start_date: '2026-07-01',
      start_date: '2026-07-01',
      end_date: '2026-07-15',
      lookback_days: 365,
      points: [
        { date: '2026-07-01', value: 100 },
        { date: '2026-07-15', value: 101.8 },
      ],
      metrics: shortHistoryMetrics,
      top_sleeve_weight_points: [],
      top_sleeve_contribution_points: [],
      warnings: ['History is too short for annualized metrics.'],
    },
    backtest_benchmark: null,
    backtest_relative_metrics: null,
  },
}

const compactCompletedRun = {
  ...completedRun,
  artifacts: [],
  detail: null,
}

const workbenchFixture = {
  portfolio_id: '3',
  portfolio_name: 'Long-Term Portfolio',
  base_currency: 'USD',
  as_of_date: '2026-07-15',
  planning_taxonomy_options: [
    {
      taxonomy_id: 'taxonomy-1',
      name: 'Policy Allocation',
      taxonomy_type: 'allocation',
    },
  ],
  planning_scope_options: [
    {
      taxonomy_node_id: null,
      label: 'Top Level',
      path: 'Top Level',
      depth: 0,
      allocation_basis: 'weight',
      has_children: true,
    },
    {
      taxonomy_node_id: 'risk-assets',
      label: 'Risk Assets',
      path: 'Top Level / Risk Assets',
      depth: 1,
      allocation_basis: 'risk_budget',
      has_children: false,
    },
  ],
  settings: {
    portfolio_id: '3',
    planning_taxonomy_id: 'taxonomy-1',
    planning_taxonomy_name: 'Policy Allocation',
    as_of_mode: 'dynamic',
    as_of_date: '2026-07-15',
    pinned_as_of_date: null,
    lookback_days: 365,
    calculation_frequency: 'daily',
    missing_return_policy: 'strict',
    capital_mode: 'unit_notional',
    gross_exposure: null,
    target_volatility: null,
    max_gross_exposure: null,
    frozen_taxonomy_node_ids: [],
    top_sleeve_weight_bounds: [],
    backtest_rebalance_frequency: '1m',
    backtest_benchmark_instrument_id: null,
    backtest_cash_yield_annual: 0.02,
    backtest_commission_bps: 2,
    backtest_tax_bps: 10,
    backtest_slippage_bps: 5,
    backtest_implementation_delay_days: 1,
    notes: 'Keep this research note.',
  },
  current_context: {
    portfolio_id: '3',
    portfolio_name: 'Long-Term Portfolio',
    base_currency: 'USD',
    as_of_date: '2026-07-15',
    target_configuration: 'current_snapshot',
    target_snapshot_fingerprint: 'current-target-fixture',
    lookback_start: '2026-07-01',
    lookback_end: '2026-07-15',
    nav: 1024,
    holdings_count: 1,
    planning_group_count: 1,
    chart_label: 'Portfolio TWR Index',
    portfolio_inception_date: '2026-07-01',
    performance_start_date: '2026-07-01',
    performance_end_date: '2026-07-15',
    performance_coverage_state: 'complete',
    performance_valuation_basis: 'market_value',
    chart_note: 'Canonical portfolio time-weighted return.',
    chart_currency: 'USD',
    summary: {
      period_return: 0.024,
      annualized_volatility: null,
      current_drawdown: 0,
      max_drawdown: 0,
      start_nav: 100,
      end_nav: 102.4,
    },
    chart_points: [
      { date: '2026-07-01', value: 1 },
      { date: '2026-07-15', value: 1.024 },
    ],
    top_holdings: [],
    planning_groups: [],
    quality_warnings: [],
  },
  detail_level: 'compact',
  instrument_universe: [
    {
      portfolio_id: '3',
      instrument_id: 'held-asset',
      instrument_ref: {
        instrument_id: 'held-asset',
        instrument_name: 'Current Holding Fund',
        instrument_type: 'public_fund',
        currency: 'USD',
        identifiers: [],
      },
      source: 'transaction',
      holding_state: 'held',
      transaction_count: 1,
      research_lifecycle: 'held',
      research_eligibility: 'eligible',
      research_pm_approved: false,
      status: 'active',
    },
    {
      portfolio_id: '3',
      instrument_id: 'observed-asset',
      instrument_ref: {
        instrument_id: 'observed-asset',
        instrument_name: 'Observed Fund',
        instrument_type: 'public_fund',
        currency: 'USD',
        identifiers: [],
      },
      source: 'manual',
      holding_state: 'not_held',
      transaction_count: 0,
      research_lifecycle: 'observed',
      research_eligibility: 'eligible',
      research_pm_approved: false,
      status: 'active',
    },
    {
      portfolio_id: '3',
      instrument_id: 'former-asset',
      instrument_ref: {
        instrument_id: 'former-asset',
        instrument_name: 'Former Holding Fund',
        instrument_type: 'public_fund',
        currency: 'USD',
        identifiers: [],
      },
      source: 'transaction',
      holding_state: 'not_held',
      transaction_count: 2,
      research_lifecycle: 'former',
      research_eligibility: 'pm_review_required',
      research_pm_approved: false,
      status: 'active',
    },
  ],
  runs: [compactCompletedRun],
  selected_run: compactCompletedRun,
}

async function openParameters() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Optimization Parameters' }))
  return screen.findByRole('dialog', { name: 'Optimization Parameters' })
}

async function saveParameters() {
  const save = screen.getByRole('button', { name: 'Save' })
  await waitFor(() => expect(save).toBeEnabled())
  await userEvent.click(save)
}

describe('Research rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    accessMock.can_edit = true
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue(workbenchFixture)
    apiMocks.getPortfolioResearchRun.mockResolvedValue(completedRun)
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [] })
    apiMocks.getPortfolioRiskPolicy.mockResolvedValue({
      lookback_days: 365,
      calculation_frequency: 'daily',
      missing_return_policy: 'strict',
      covariance_model_id: 'sample_covariance',
      contribution_mode: 'signed',
    })
    apiMocks.updatePortfolioResearchSettings.mockImplementation(async (_portfolioId, settings) => {
      const reads = apiMocks.getPortfolioResearchWorkbench.mock.results
      const current = reads.length ? await reads[reads.length - 1].value : workbenchFixture
      const saved = { ...current.settings, ...settings }
      apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({ ...current, settings: saved })
      return saved
    })
  })

  it('keeps governance controls off the research page and renders short-history metrics honestly', async () => {
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({
      ...workbenchFixture,
      current_context: { ...workbenchFixture.current_context, quality_warnings: ['Quote coverage needs review.'] },
    })
    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    await screen.findByTestId('research-result-taxonomy')
    expect(screen.queryByText('Research Settings')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Cash Yield (%)')).not.toBeInTheDocument()
    const audit = screen.getByText('Run Audit').closest('details')!
    expect(audit).not.toHaveAttribute('open')
    fireEvent.click(screen.getByText('Run Audit'))
    expect(screen.getByRole('button', { name: /Data quality warning: Quote coverage needs review/ }).closest('.research-command-meta')).not.toBeNull()
    expect(screen.queryByText('Research Eligibility')).not.toBeInTheDocument()
    expect(screen.queryByText('PM Approval')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve for research' })).not.toBeInTheDocument()
    expect(screen.getByText(/FCN and options are no-trade, zero-return capital/)).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^Derivative Weight/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Target Cash Weight' })).toBeInTheDocument()
    expect(screen.queryByText('Robustness Scenarios')).not.toBeInTheDocument()
    expect(screen.queryByText('Robustness')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add robustness scenario' })).not.toBeInTheDocument()
    expect(screen.queryByText('Manual PM decision required.')).not.toBeInTheDocument()

    const annualReturnRow = screen.getByRole('row', { name: /Annual Return/ })
    const calmarRow = screen.getByRole('row', { name: /Calmar/ })
    expect(within(annualReturnRow).getAllByText('—')).toHaveLength(3)
    expect(within(calmarRow).getAllByText('—')).toHaveLength(3)

    const backtestMetricsSection = screen.getByText('Comparable Metrics').closest('.portfolio-section-block')
    expect(backtestMetricsSection).not.toBeNull()
    const periodReturnRow = within(backtestMetricsSection as HTMLElement).getByRole('row', { name: /Period Return/ })
    expect(within(periodReturnRow).getByText('1.80%')).toBeInTheDocument()
    expect(apiMocks.getPortfolioResearchWorkbench).toHaveBeenCalledWith('3')
    expect(apiMocks.getPortfolioResearchRun).toHaveBeenCalledWith('3', 'research-1')
    const dialog = await openParameters()
    expect(within(dialog).getByLabelText('Cash Yield (%)')).toHaveValue(2)
    expect(within(dialog).getByLabelText('Commission (bps)')).toHaveValue(2)
    expect(within(dialog).getByLabelText('Slippage (bps)')).toHaveValue(5)
    expect(within(dialog).queryByText('WF Train (months)')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('WF Test (months)')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Run Optimization' })).not.toBeInTheDocument()
  })

  it('keeps the saved research taxonomy and marks results from another taxonomy as historical', async () => {
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({ ...workbenchFixture,
      settings: { ...workbenchFixture.settings, planning_taxonomy_id: 'industry', planning_taxonomy_name: 'Industry' },
      planning_taxonomy_options: [...workbenchFixture.planning_taxonomy_options, { taxonomy_id: 'industry', name: 'Industry', taxonomy_type: 'custom' }],
    })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    await screen.findByTestId('research-result-taxonomy')
    await openParameters()
    expect(screen.getByLabelText('Optimization taxonomy')).toHaveValue('industry')
    expect(screen.getByTestId('research-result-taxonomy')).toHaveTextContent('Policy Allocation')
    expect(screen.getByTestId('research-result-taxonomy')).toHaveTextContent('Run Optimization again')
    expect(screen.getByText('Historical result — not current or execution-ready.')).toBeInTheDocument()
  })

  it('requires a taxonomy choice when multiple classifications exist and none is saved', async () => {
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({ ...workbenchFixture,
      settings: { ...workbenchFixture.settings, planning_taxonomy_id: null, planning_taxonomy_name: null },
      planning_taxonomy_options: [...workbenchFixture.planning_taxonomy_options, { taxonomy_id: 'industry', name: 'Industry', taxonomy_type: 'custom' }],
    })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    await openParameters()
    expect(await screen.findByLabelText('Optimization taxonomy')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Run Optimization' })).toBeDisabled()
    expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
  })

  it('does not reuse a saved research classification that is no longer available', async () => {
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({ ...workbenchFixture,
      settings: { ...workbenchFixture.settings, planning_taxonomy_id: 'deleted', planning_taxonomy_name: 'Deleted Plan' },
    })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    await openParameters()
    expect(await screen.findByLabelText('Optimization taxonomy')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Run Optimization' })).toBeDisabled()
    expect(screen.queryByText('Deleted Plan')).not.toBeInTheDocument()
    expect(screen.getByText('No optimization taxonomy selected')).toBeVisible()
    expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
  })

  it('refreshes available research schemes without replacing a valid explicit choice', async () => {
    const industry = { taxonomy_id: 'industry', name: 'Industry', taxonomy_type: 'custom' }
    const region = { taxonomy_id: 'region', name: 'Region', taxonomy_type: 'custom' }
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({ ...workbenchFixture,
      planning_taxonomy_options: [...workbenchFixture.planning_taxonomy_options, industry],
    })
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ taxonomies: [{
      ...industry, root_allocation_basis: 'weight', status: 'active',
    }], taxonomy_nodes: [] })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    await openParameters()
    fireEvent.change(screen.getByLabelText('Optimization taxonomy'), { target: { value: 'industry' } })
    expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
    await saveParameters()
    await waitFor(() => expect(apiMocks.updatePortfolioResearchSettings).toHaveBeenCalled())
    expect(apiMocks.updatePortfolioResearchSettings.mock.calls[0][1]).not.toHaveProperty('backtest_robustness_scenarios')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await openParameters()
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({ ...workbenchFixture,
      settings: { ...workbenchFixture.settings, planning_taxonomy_id: 'industry', planning_taxonomy_name: 'Industry' },
      planning_taxonomy_options: [...workbenchFixture.planning_taxonomy_options, industry, region],
    })
    fireEvent(window, new CustomEvent('portfolio-risk-policy-updated', { detail: { portfolioId: '3' } }))
    await waitFor(() => expect(screen.getByLabelText('Optimization taxonomy').querySelector('option[value="region"]')).toHaveTextContent('Region'))
    expect(screen.getByLabelText('Optimization taxonomy')).toHaveValue('industry')
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
  })

  it('clears a removed local research choice and prevents an in-flight save from writing it after refresh', async () => {
    const industry = { taxonomy_id: 'industry', name: 'Industry', taxonomy_type: 'custom' }
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({ ...workbenchFixture,
      planning_taxonomy_options: [...workbenchFixture.planning_taxonomy_options, industry],
    })
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ taxonomies: [{
      ...industry, root_allocation_basis: 'weight', status: 'active',
    }], taxonomy_nodes: [] })
    let resolveRiskPolicy: ((value: object) => void) | undefined
    apiMocks.getPortfolioRiskPolicy.mockReturnValueOnce(new Promise((resolve) => { resolveRiskPolicy = resolve }))
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    await openParameters()
    fireEvent.change(screen.getByLabelText('Optimization taxonomy'), { target: { value: 'industry' } })
    await saveParameters()
    await waitFor(() => expect(apiMocks.getPortfolioRiskPolicy).toHaveBeenCalled())
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({ ...workbenchFixture,
      settings: { ...workbenchFixture.settings, planning_taxonomy_id: null, planning_taxonomy_name: null },
    })
    fireEvent(window, new CustomEvent('portfolio-risk-policy-updated', { detail: { portfolioId: '3' } }))
    await waitFor(() => expect(screen.getByLabelText('Optimization taxonomy')).toHaveValue(''))
    await act(async () => {
      resolveRiskPolicy?.({ lookback_days: 365, calculation_frequency: 'daily', missing_return_policy: 'strict',
        covariance_model_id: 'sample_covariance', contribution_mode: 'signed' })
    })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run Optimization' })).toBeDisabled())
    expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Optimization taxonomy').querySelector('option[value="industry"]')).toBeNull()
  })

  it('saves changed parameters explicitly while preserving notes and keeping Run separate', async () => {
    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const dialog = await openParameters()
    const capitalMode = within(dialog).getByLabelText('Capital Mode')
    fireEvent.change(capitalMode, { target: { value: 'fixed_gross' } })
    fireEvent.change(within(dialog).getByLabelText('Gross Exposure'), { target: { value: '1.2' } })
    expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
    await saveParameters()

    await waitFor(() => {
      expect(apiMocks.updatePortfolioResearchSettings).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({ notes: 'Keep this research note.', capital_mode: 'fixed_gross', gross_exposure: 1.2 }),
      )
    })
    expect(apiMocks.updatePortfolioResearchSettings).toHaveBeenCalledTimes(1)
    expect(apiMocks.updatePortfolioResearchSettings.mock.calls[0][1]).not.toHaveProperty('backtest_walk_forward_training_months')
    expect(apiMocks.updatePortfolioResearchSettings.mock.calls[0][1]).not.toHaveProperty('backtest_walk_forward_test_months')
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const reopened = await openParameters()
    expect(within(reopened).getByLabelText('Capital Mode')).toHaveValue('fixed_gross')
    expect(within(reopened).getByLabelText('Gross Exposure')).toHaveValue(1.2)
  })

  it('saves a selected custom taxonomy and clears constraints from the previous taxonomy', async () => {
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({ ...workbenchFixture,
      settings: { ...workbenchFixture.settings, frozen_taxonomy_node_ids: ['risk-assets'] },
      planning_taxonomy_options: [...workbenchFixture.planning_taxonomy_options, { taxonomy_id: 'industry', name: 'Industry', taxonomy_type: 'custom', targets_available: false }],
    })
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ taxonomies: [{ taxonomy_id: 'industry', name: 'Industry', root_allocation_basis: 'weight', status: 'active' }], taxonomy_nodes: [] })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    await openParameters()
    fireEvent.change(await screen.findByLabelText('Optimization taxonomy'), { target: { value: 'industry' } })
    expect(screen.getByText('Configure targets before running optimization')).toHaveAttribute('href', '/portfolios/3/taxonomies')
    expect(screen.getByTestId('research-result-taxonomy')).not.toHaveTextContent('Run Optimization again')
    expect(screen.queryByText('Historical result — not current or execution-ready.')).not.toBeInTheDocument()
    await saveParameters()
    await waitFor(() => expect(apiMocks.updatePortfolioResearchSettings).toHaveBeenCalledWith('3',
      expect.objectContaining({ planning_taxonomy_id: 'industry', frozen_taxonomy_node_ids: [], top_sleeve_weight_bounds: [] }),
    ), { timeout: 2_000 })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await openParameters()
    expect(screen.getByLabelText('Optimization taxonomy')).toHaveValue('industry')
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
  })

  it.each(['Cancel', 'Escape', 'Close', 'backdrop'])('discards parameter drafts on %s and restores focus to Parameters', async (method) => {
    const user = userEvent.setup()
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const dialog = await openParameters()
    fireEvent.change(within(dialog).getByLabelText('Commission (bps)'), { target: { value: '9' } })
    if (method === 'Escape') await user.keyboard('{Escape}')
    else if (method === 'backdrop') fireEvent.click(dialog.parentElement!)
    else await user.click(within(dialog).getByRole('button', { name: method }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Optimization Parameters' })).toHaveFocus())
    expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
    const reopened = await openParameters()
    expect(within(reopened).getByLabelText('Commission (bps)')).toHaveValue(2)
  })

  it('keeps parameter edits local instead of auto-saving after a pause', async () => {
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const dialog = await openParameters()
    vi.useFakeTimers()
    try {
      fireEvent.change(within(dialog).getByLabelText('Commission (bps)'), { target: { value: '9' } })
      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      expect(apiMocks.getPortfolioRiskPolicy).not.toHaveBeenCalled()
      expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
      expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('lets read-only members inspect parameters without editing, saving, or running', async () => {
    accessMock.can_edit = false
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const dialog = await openParameters()
    expect(within(dialog).getByLabelText('Capital Mode')).toBeDisabled()
    expect(within(dialog).getByLabelText('Commission (bps)')).toBeDisabled()
    expect(within(dialog).getByLabelText('Optimization taxonomy')).toBeDisabled()
    expect(within(dialog).queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Optimization' })).toBeDisabled()
    await userEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[0])
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
  })

  it('does not change the saved comparison while a benchmark selection is only a draft', async () => {
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [{
      instrument_id: 'benchmark-1', instrument_name: 'Market Benchmark', instrument_type: 'index', currency: 'USD',
      identifiers: [{ identifier_type: 'ticker', identifier_value: 'MKT', is_primary: true }], latest_market_data: [],
    }] })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const dialog = await openParameters()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Show benchmark choices' }))
    await userEvent.click(within(dialog).getByRole('button', { name: /Market Benchmark/ }))
    expect(within(dialog).getByRole('searchbox', { name: 'Compare benchmark' })).toHaveValue('MKT · Market Benchmark')
    expect(apiMocks.getPortfolioResearchBacktestBenchmarkComparison).not.toHaveBeenCalled()
    expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    const reopened = await openParameters()
    expect(within(reopened).getByRole('searchbox', { name: 'Compare benchmark' })).toHaveValue('')
    await userEvent.type(within(reopened).getByRole('searchbox', { name: 'Compare benchmark' }), 'Unselected search')
    await userEvent.click(within(reopened).getByRole('button', { name: 'Cancel' }))
    const reopenedAgain = await openParameters()
    expect(within(reopenedAgain).getByRole('searchbox', { name: 'Compare benchmark' })).toHaveValue('')
    expect(apiMocks.getPortfolioResearchBacktestBenchmarkComparison).not.toHaveBeenCalled()
  })

  it('does not replace a dirty parameter draft when the saved run detail finishes loading', async () => {
    let resolveDetail: ((value: typeof completedRun) => void) | undefined
    apiMocks.getPortfolioResearchRun.mockReturnValueOnce(new Promise((resolve) => { resolveDetail = resolve }))
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const dialog = await openParameters()
    fireEvent.change(within(dialog).getByLabelText('Commission (bps)'), { target: { value: '9' } })
    await act(async () => { resolveDetail?.(completedRun) })
    await screen.findByTestId('research-result-taxonomy')
    expect(within(dialog).getByLabelText('Commission (bps)')).toHaveValue(9)
    expect(apiMocks.updatePortfolioResearchSettings).not.toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    const reopened = await openParameters()
    expect(within(reopened).getByLabelText('Commission (bps)')).toHaveValue(2)
  })

  it('keeps failed parameter saves visible with the draft available for correction', async () => {
    apiMocks.updatePortfolioResearchSettings.mockRejectedValueOnce(new Error('Parameter save unavailable.'))
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const dialog = await openParameters()
    fireEvent.change(within(dialog).getByLabelText('Commission (bps)'), { target: { value: '9' } })
    await saveParameters()
    expect(await within(dialog).findByText('Parameter save unavailable.')).toBeVisible()
    expect(within(dialog).getByLabelText('Commission (bps)')).toHaveValue(9)
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeEnabled()
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
  })

  it('restores the Parameters entry focus after a successful save with a slow refresh', async () => {
    const saved = { ...workbenchFixture.settings, backtest_commission_bps: 9 }
    let resolveRefresh: ((value: typeof workbenchFixture) => void) | undefined
    apiMocks.updatePortfolioResearchSettings.mockImplementationOnce(async () => {
      apiMocks.getPortfolioResearchWorkbench.mockReturnValueOnce(new Promise((resolve) => { resolveRefresh = resolve }))
      return saved
    })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const dialog = await openParameters()
    fireEvent.change(within(dialog).getByLabelText('Commission (bps)'), { target: { value: '9' } })
    await saveParameters()
    await waitFor(() => expect(apiMocks.getPortfolioResearchWorkbench).toHaveBeenCalledTimes(2))
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())) })
    await act(async () => { resolveRefresh?.({ ...workbenchFixture, settings: saved }) })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Optimization Parameters' })).toHaveFocus())
    expect(apiMocks.updatePortfolioResearchSettings).toHaveBeenCalledTimes(1)
    expect(apiMocks.createPortfolioResearchRun).not.toHaveBeenCalled()
  })

  it('marks a constrained risk-budget target miss as not execution-ready', async () => {
    apiMocks.getPortfolioResearchRun.mockResolvedValue({
      ...completedRun,
      detail: {
        ...completedRun.detail,
        scope_solve_events: [
          {
            as_of_date: '2026-07-15',
            scope_node_id: 'risk-assets',
            scope_label: 'Risk Assets',
            target_status: 'constrained_target_miss',
            execution_ready: false,
            solver_message: 'Maximum target-share gap is 12.00%.',
            member_count: 2,
          },
        ],
      },
    })

    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const warning = await screen.findByRole('status', { name: /Constrained solve — not execution-ready/ })
    expect(warning).toHaveTextContent('Constrained solve — not execution-ready.')
    expect(warning).toHaveAttribute(
      'title',
      'Risk Assets: Maximum target-share gap is 12.00%.',
    )
    expect(screen.queryByText(/Risk Assets: Maximum target-share gap is 12.00%/)).not.toBeInTheDocument()
  })

  it('keeps stale-run reasons on hover while retaining the execution block', async () => {
    const staleCompactRun = {
      ...compactCompletedRun,
      reliability_state: 'stale',
      is_current: false,
      reliability_reasons: [
        'Planning taxonomy changed after this run.',
        'Research settings changed after this run.',
      ],
    }
    apiMocks.getPortfolioResearchWorkbench.mockResolvedValue({
      ...workbenchFixture,
      runs: [staleCompactRun],
      selected_run: staleCompactRun,
    })
    apiMocks.getPortfolioResearchRun.mockResolvedValue({
      ...completedRun,
    })

    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const warning = await screen.findByRole('status', {
      name: /Historical result — not current or execution-ready/,
    })
    expect(warning).toHaveTextContent('Historical result — not current or execution-ready.')
    expect(warning).toHaveAttribute(
      'title',
      expect.stringContaining('Planning taxonomy changed after this run.'),
    )
    expect(screen.queryByText('Planning taxonomy changed after this run.')).not.toBeInTheDocument()
    expect(screen.queryByText('Historical Solved Result')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Solved result' })).toContainElement(warning)
  })

  it('moves validation diagnostics to their related status fields', async () => {
    const unavailableReason = 'Backtest revision history is shorter than selected risk lookback window.'
    apiMocks.getPortfolioResearchRun.mockResolvedValue({
      ...completedRun,
      detail: {
        ...completedRun.detail,
        backtest: {
          ...completedRun.detail.backtest,
          methodology: { name: 'Point-in-time backtest' },
          point_in_time_coverage: {
            status: 'unavailable',
            decision_count: 0,
            skipped_rebalances: [
              { date: '2026-07-01', reason: 'No eligible revision was available.' },
            ],
            pending_rebalances: [
              { date: '2026-07-15', reason: 'Scheduled execution is after the cutoff.' },
            ],
            configuration_versions_used: [],
            first_decision_date: null,
            last_decision_date: null,
            unavailable_reason: unavailableReason,
          },
        },
      },
    })

    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const auditSummary = await screen.findByText('Run Audit')
    expect(auditSummary.closest('details')).not.toHaveAttribute('open')
    fireEvent.click(auditSummary)
    const coverageSection = screen.getByText('Historical Data Coverage').closest('.portfolio-section-block')
    expect(coverageSection).not.toBeNull()
    const coverageStatus = within(coverageSection as HTMLElement).getByRole('row', { name: /Status Unavailable/ })
    expect(within(coverageStatus).getByText('Unavailable')).toHaveAttribute('title', unavailableReason)
    const skippedStatus = within(coverageSection as HTMLElement).getByRole('row', { name: /Skipped Decisions 1/ })
    expect(within(skippedStatus).getByText('1')).toHaveAttribute(
      'title',
      '2026-07-01: No eligible revision was available.',
    )
    expect(within(coverageSection as HTMLElement).queryByText(unavailableReason)).not.toBeInTheDocument()
    const pendingStatus = within(coverageSection as HTMLElement).getByRole('row', { name: /Pending Decisions 1/ })
    expect(within(pendingStatus).getByText('1')).toHaveAttribute('title', 'Scheduled execution is after the cutoff.')

    expect(screen.queryByText('Archived Rolling Holdout')).not.toBeInTheDocument()
  })

  it('distinguishes current-target simulation from archived target rules', async () => {
    apiMocks.getPortfolioResearchRun.mockResolvedValue({
      ...completedRun,
      detail: {
        ...completedRun.detail,
        backtest: {
          ...completedRun.detail.backtest,
          methodology: {
            name: 'Current-target historical simulation',
            target_configuration: 'current_snapshot',
            target_snapshot_fingerprint: 'current-target-fixture',
            point_in_time_taxonomy: false,
            point_in_time_universe: false,
          },
        },
      },
    })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')

    expect(await screen.findByText('Actual vs Backtest')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Return basis:.*saved current targets/ })).toBeInTheDocument()
    expect(screen.getByText('Current targets')).toBeInTheDocument()
    expect(screen.getByText('Target Snapshot Version')).toBeInTheDocument()
    expect(screen.queryByText('Rolling Historical Windows')).not.toBeInTheDocument()
    expect(screen.queryByText('Archived Historical Backtest')).not.toBeInTheDocument()
    expect(screen.queryByText(/Historical taxonomy and targets are effective-dated/)).not.toBeInTheDocument()
    const dialog = await openParameters()
    expect(within(dialog).getByText('Data Cutoff Mode')).toBeInTheDocument()
    expect(within(dialog).getByText('Data Cutoff Date')).toBeInTheDocument()
  })

  it('renders one hierarchical solution with signed trade amounts and an Excel download', async () => {
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const table = await screen.findByRole('table', { name: 'Research solution tree' })
    expect(within(table).getByRole('row', { name: /Alpha Fund.*100.00%.*100.00%.*\$200.00.*20.00%.*80.00%.*\+\$600.00$/ })).toBeInTheDocument()
    expect(within(table).queryByText('Buy')).not.toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Current Weight' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Target Weight' })).toBeInTheDocument()
    expect(screen.queryByText('Instrument-level Solution')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export Excel' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Collapse all' }))
    expect(within(table).getByText('Risk Assets')).toBeInTheDocument()
    expect(within(table).queryByText('Alpha Fund')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Expand all' }))
    expect(within(table).getByText('Alpha Fund')).toBeInTheDocument()
  })

  it('compares actual TWR with the backtest on common eligible dates', async () => {
    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const comparisonSection = (await screen.findByText('Comparable Metrics')).closest('.portfolio-section-block')
    expect(comparisonSection).not.toBeNull()
    expect(within(comparisonSection as HTMLElement).getByText(/2026-07-01 – 2026-07-15/)).toBeInTheDocument()
    const periodReturnRow = within(comparisonSection as HTMLElement).getByRole('row', { name: /Period Return/ })
    expect(within(periodReturnRow).getByText('2.40%')).toBeInTheDocument()
    expect(within(periodReturnRow).getByText('1.80%')).toBeInTheDocument()
    expect(within(periodReturnRow).getByText('0.60%')).toBeInTheDocument()
  })

  it('shows sleeve charts below the monthly matrix while keeping run audit collapsed', async () => {
    const points = ['2026-07-01', '2026-07-15'].map((date, index) => ({
      date, sleeves: [{ top_sleeve_id: 'risk-assets', top_sleeve_label: 'Risk Assets', value: .8 + .1 * index }],
    }))
    apiMocks.getPortfolioResearchRun.mockResolvedValue({ ...completedRun, detail: {
      ...completedRun.detail, backtest: { ...completedRun.detail.backtest,
        top_sleeve_weight_points: points,
        top_sleeve_contribution_points: points.map((point, index) => ({ ...point,
          sleeves: point.sleeves.map((sleeve) => ({ ...sleeve, value: index * .018 })),
        })),
      },
    } })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const matrix = await screen.findByRole('table', { name: 'Monthly return matrix' })
    for (const name of ['Top-Level Weights · Backtest', 'Top-Level Return Contributions · Backtest']) {
      const chart = screen.getByRole('img', { name })
      expect(chart).toBeVisible()
      expect(chart.closest('details')).toBeNull()
      expect(matrix.compareDocumentPosition(chart) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0)
    }
    expect(screen.getByText('Run Audit').closest('details')).not.toHaveAttribute('open')
    expect(screen.queryByText('Historical Solved Result')).not.toBeInTheDocument()
    const result = screen.getByRole('region', { name: 'Solved result' })
    expect(within(result).queryByText('Solved Volatility')).not.toBeInTheDocument()
    expect(within(result).queryByText('Risk Budget Gap')).not.toBeInTheDocument()
    expect(within(result).queryByText('Rebalance Turnover')).not.toBeInTheDocument()
    expect(within(result).queryByText('Material Gaps')).not.toBeInTheDocument()
  })

  it.each([
    ['portfolio', 'One portfolio-wide solve with each level retaining its weight or risk-budget basis.'],
    ['selected_research_scope', 'One global solve within the selected research scope; risk contributions are relative to that scope.'],
  ])('discloses the global attribution universe for %s', async (scope, note) => {
    apiMocks.getPortfolioResearchRun.mockResolvedValue({ ...completedRun, detail: {
      ...completedRun.detail, solver_version: 'global_leaf_scalar_targets_v3', risk_attribution_scope: scope,
    } })
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    expect(await screen.findByText(note)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Run Audit'))
    expect(screen.getByRole('button', { name: /Risk contribution basis:/ })).toBeInTheDocument()
  })

  it('keeps the recorded attribution distinct for an archived solver result', async () => {
    renderPortfolioPage(<ResearchPage />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
    const result = await screen.findByRole('button', { name: /Risk contribution basis:/ })
    expect(result).toBeInTheDocument()
    expect(screen.queryByText(/One portfolio-wide solve/)).not.toBeInTheDocument()
  })

})
