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
  updatePortfolioResearchInstrumentEligibility: vi.fn(),
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
    solved_result_groups: [
      {
        top_sleeve_id: 'risk-assets',
        top_sleeve_label: 'Risk Assets',
        solved_weight: 0.8,
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
            solved_weight: 0.8,
            target_risk_share: 1,
            forward_risk_contribution: 1,
          },
        ],
      },
    ],
    target_weight_gaps: [
      {
        member_type: 'instrument',
        member_id: 'former-asset',
        label: 'Former Holding Fund',
        current_weight: 0,
        target_weight: 0.05,
        gap: 0.05,
        current_value_base: 0,
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
    calculation_frequency: 'auto',
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
    notes: 'Keep this research note.',
  },
  current_context: {
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
        instrument_type: 'fund',
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
        instrument_type: 'fund',
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
        instrument_type: 'fund',
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
      calculation_frequency: 'auto',
      missing_return_policy: 'strict',
      covariance_model_id: 'sample_covariance',
      contribution_mode: 'signed',
    })
    apiMocks.updatePortfolioResearchSettings.mockResolvedValue(workbenchFixture.settings)
    apiMocks.updatePortfolioResearchInstrumentEligibility.mockResolvedValue({
      ...workbenchFixture.instrument_universe[2],
      research_eligibility: 'eligible',
      research_pm_approved: true,
      research_pm_approved_at: '2026-07-15T08:30:00Z',
    })
  })

  it('surfaces manual review and renders unavailable short-history metrics as unavailable values', async () => {
    renderPortfolioPage(
      <ResearchPage />,
      '/portfolios/3/research',
      '/portfolios/:portfolioId/research',
    )

    expect(await screen.findByText('Manual PM decision required.')).toBeInTheDocument()
    expect(
      screen.getByText(
        /Former Holding Fund: Status: Former · Research eligibility: manual PM review required/,
      ),
    ).toBeInTheDocument()

    expect(screen.getByRole('row', { name: /Current Holding Fund Held Eligible Not required/ })).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /Observed Fund Observed Eligible Not required/ })).toBeInTheDocument()
    const formerRow = screen.getByRole('row', {
      name: /Former Holding Fund Former PM review required Approve for research/,
    })
    fireEvent.click(within(formerRow).getByRole('button', { name: 'Approve for research' }))
    await waitFor(() => {
      expect(apiMocks.updatePortfolioResearchInstrumentEligibility).toHaveBeenCalledWith(
        '3',
        'former-asset',
        { pm_approved: true },
      )
    })
    expect(await screen.findByRole('row', {
      name: /Former Holding Fund Former Eligible Revoke approval/,
    })).toBeInTheDocument()

    const annualReturnRow = screen.getByRole('row', { name: /Annual Return/ })
    const calmarRow = screen.getByRole('row', { name: /Calmar/ })
    expect(within(annualReturnRow).getAllByText('-')).toHaveLength(3)
    expect(within(calmarRow).getAllByText('-')).toHaveLength(3)

    const periodReturnRow = screen.getByRole('row', { name: /Period Return/ })
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

    expect(await screen.findByText('Constrained solve — not execution-ready.')).toBeInTheDocument()
    expect(screen.getByText(/Risk Assets: Maximum target-share gap is 12.00%/)).toBeInTheDocument()
  })
})
