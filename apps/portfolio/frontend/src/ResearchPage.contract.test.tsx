import { fireEvent, screen, waitFor, within } from '@testing-library/react'
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

vi.mock('./lib/api', () => apiMocks)
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
  default_planning_taxonomy_id: 'taxonomy-1',
  planning_taxonomy_options: [
    {
      taxonomy_id: 'taxonomy-1',
      name: 'Policy Allocation',
      taxonomy_type: 'allocation',
      budgeting_level: 'root',
    },
  ],
  planning_scope_options: [
    {
      taxonomy_node_id: null,
      label: 'Top Level',
      path: 'Top Level',
      depth: 0,
      default_target_dimension: 'weight',
      has_children: true,
    },
    {
      taxonomy_node_id: 'risk-assets',
      label: 'Risk Assets',
      path: 'Top Level / Risk Assets',
      depth: 1,
      default_target_dimension: 'risk_budget',
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
    target_dimension: 'scope_default',
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
    backtest_robustness_scenarios: [],
    backtest_walk_forward_training_months: 24,
    backtest_walk_forward_test_months: 6,
    notes: 'Keep this research note.',
  },
  current_context: {
    portfolio_id: '3',
    portfolio_name: 'Long-Term Portfolio',
    base_currency: 'USD',
    as_of_date: '2026-07-15',
    lookback_start: '2026-07-01',
    lookback_end: '2026-07-15',
    nav: 1024,
    holdings_count: 1,
    planning_group_count: 1,
    chart_label: 'Portfolio NAV',
    chart_note: 'Canonical portfolio NAV.',
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
      { date: '2026-07-01', value: 100 },
      { date: '2026-07-15', value: 102.4 },
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

describe('Research rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
    apiMocks.updatePortfolioResearchSettings.mockResolvedValue(workbenchFixture.settings)
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

    await screen.findByText('Solved Result')
    expect(screen.getByRole('button', { name: /Data quality warning: Quote coverage needs review/ }).closest('.research-command-meta')).not.toBeNull()
    expect(screen.queryByText('Research Eligibility')).not.toBeInTheDocument()
    expect(screen.queryByText('PM Approval')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve for research' })).not.toBeInTheDocument()
    expect(screen.getByText(/FCN and options are no-trade, zero-return capital/)).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Frozen Derivative' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Cash Target' })).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader', { name: 'Cash Yield (%)' })).toHaveLength(2)
    expect(screen.queryByText('Manual PM decision required.')).not.toBeInTheDocument()

    const annualReturnRow = screen.getByRole('row', { name: /Annual Return/ })
    const calmarRow = screen.getByRole('row', { name: /Calmar/ })
    expect(within(annualReturnRow).getAllByText('-')).toHaveLength(3)
    expect(within(calmarRow).getAllByText('-')).toHaveLength(3)

    const backtestMetricsSection = screen.getByText('Backtest Metrics').closest('.portfolio-section-block')
    expect(backtestMetricsSection).not.toBeNull()
    const periodReturnRow = within(backtestMetricsSection as HTMLElement).getByRole('row', { name: /Period Return/ })
    expect(within(periodReturnRow).getByText('1.80%')).toBeInTheDocument()
    expect(apiMocks.getPortfolioResearchWorkbench).toHaveBeenCalledWith('3')
    expect(apiMocks.getPortfolioResearchRun).toHaveBeenCalledWith('3', 'research-1')
  })

  it('preserves existing research notes when run settings auto-save', async () => {
    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const capitalMode = await screen.findByLabelText('Capital Mode')
    fireEvent.change(capitalMode, { target: { value: 'fixed_gross' } })

    await waitFor(() => {
      expect(apiMocks.updatePortfolioResearchSettings).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({ notes: 'Keep this research note.' }),
      )
    }, { timeout: 2_000 })
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
  })

  it('moves validation diagnostics to their related status fields', async () => {
    const unavailableReason = 'Backtest revision history is shorter than selected risk lookback window.'
    const methodologyNote =
      'Training and test dates are temporal diagnostics, not walk-forward optimization.'
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
          walk_forward: {
            methodology_note: methodologyNote,
            unavailable_reason: unavailableReason,
            windows: [],
            oos_metrics: null,
          },
        },
      },
    })

    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const coverageSection = (await screen.findByText('Point-in-Time Coverage')).closest('.portfolio-section-block')
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

    const oosHeading = screen.getByText('Rolling OOS Holdout')
    expect(oosHeading).toHaveAttribute('title', methodologyNote)
    const oosSection = oosHeading.closest('.portfolio-section-block')
    expect(oosSection).not.toBeNull()
    expect(oosSection?.querySelector('.empty-state-cell')).toHaveAttribute('title', unavailableReason)
    expect(within(oosSection as HTMLElement).queryByText(unavailableReason)).not.toBeInTheDocument()
  })

  it('keeps instrument-level rebalance evidence in one expandable solution table', async () => {
    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const instrumentDisclosure = (await screen.findByText('Instrument-level Solution')).closest('details')
    expect(instrumentDisclosure).not.toBeNull()
    expect(
      within(instrumentDisclosure as HTMLElement).getByRole('row', {
        name: /Alpha Fund Risk Assets 20\.00% 80\.00% 60\.00% \$200 \$800 100\.00% 100\.00% Increase/,
      }),
    ).toBeInTheDocument()
    expect(within(instrumentDisclosure as HTMLElement).queryByText(/PM review/i)).not.toBeInTheDocument()
  })

  it('renders sleeve-level solved results first and keeps capital in instrument detail', async () => {
    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const solvedSection = (await screen.findByText('Solved Result')).closest('section')
    expect(solvedSection).not.toBeNull()
    const sleeveTable = solvedSection?.querySelector('.research-solved-table') as HTMLElement
    expect(within(sleeveTable).getByText('Actual Weight')).toBeInTheDocument()
    expect(within(sleeveTable).getByText('Solved Weight')).toBeInTheDocument()
    expect(
      within(solvedSection as HTMLElement).getByRole('row', {
        name: /Risk Assets 20\.00% 80\.00% 60\.00% 100\.00% 100\.00% 60\.00% \/ 90\.00% Within/,
      }),
    ).toBeInTheDocument()
    expect(within(solvedSection as HTMLElement).getByRole('columnheader', { name: 'Target Capital' })).toBeInTheDocument()
  })

  it('compares actual NAV with the backtest on common eligible dates', async () => {
    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    const comparisonSection = (await screen.findByText('Comparable Metrics')).closest('.portfolio-section-block')
    expect(comparisonSection).not.toBeNull()
    expect(within(comparisonSection as HTMLElement).getByText(/2026-07-01 to 2026-07-15 · 2 observations/)).toBeInTheDocument()
    const periodReturnRow = within(comparisonSection as HTMLElement).getByRole('row', { name: /Period Return/ })
    expect(within(periodReturnRow).getByText('2.40%')).toBeInTheDocument()
    expect(within(periodReturnRow).getByText('1.80%')).toBeInTheDocument()
    expect(within(periodReturnRow).getByText('0.60%')).toBeInTheDocument()
  })
})
