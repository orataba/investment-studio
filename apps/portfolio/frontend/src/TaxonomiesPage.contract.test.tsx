import type { ReactNode } from 'react'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TaxonomiesPage from './pages/TaxonomiesPage'
import {
  fcnContractFixture,
  holdingFixture,
  holdingsWorkspaceFixture,
  instrumentFixture,
} from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const accessState = vi.hoisted(() => ({ can_edit: true }))
vi.mock('./components/PortfolioAccessProvider', () => ({ usePortfolioAccess: () => accessState }))

const apiMocks = vi.hoisted(() => ({
  getPortfolioTaxonomyCatalog: vi.fn(),
  getHoldingsWorkspace: vi.fn(),
  getPortfolioAccountsWorkspace: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  createPortfolioInstrumentUniverseRecord: vi.fn(),
  createPortfolioTaxonomy: vi.fn(),
  createPortfolioTaxonomyAssignment: vi.fn(),
  createPortfolioTaxonomyNode: vi.fn(),
  savePortfolioTaxonomyTargetConfiguration: vi.fn(),
  deletePortfolioInstrumentUniverseRecord: vi.fn(),
  deletePortfolioTaxonomy: vi.fn(),
  deletePortfolioTaxonomyNode: vi.fn(),
  deletePortfolioTargetSet: vi.fn(),
  updatePortfolioDefaultPlanningTaxonomy: vi.fn(),
  updatePortfolioTaxonomy: vi.fn(),
  updatePortfolioTaxonomyAssignment: vi.fn(),
  updatePortfolioTaxonomyNode: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children, busy }: { children: ReactNode; busy?: boolean }) => (
    <section data-testid="portfolio-workspace" aria-busy={busy}>
      {children}
    </section>
  ),
}))

const taxonomyCatalog = {
  portfolio_id: '3',
  default_planning_taxonomy_id: 'taxonomy-1',
  risk_basis: null,
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
      node_code: 'RISK',
      sort_order: 1,
      is_terminal: true,
      default_target_dimension: 'weight',
      status: 'active',
    },
  ],
  taxonomy_assignments: [
    {
      assignment_id: 'assignment-1',
      taxonomy_id: 'taxonomy-1',
      target_scope: 'instrument',
      target_entity_id: 'asset-1',
      taxonomy_node_id: 'risk-assets',
      status: 'active',
    },
  ],
  instrument_universe: [],
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
      target_line_id: 'saa-risk-assets',
      target_set_id: 'saa-root',
      target_member_type: 'taxonomy_node',
      target_member_id: 'risk-assets',
      taxonomy_node_id: 'risk-assets',
      target_weight: 0.8,
      target_risk_share: 1,
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
      target_line_id: 'saa-derivatives',
      target_set_id: 'saa-root',
      target_member_type: 'derivative_bucket',
      target_member_id: '__derivatives__',
      taxonomy_node_id: null,
      target_weight: 0,
      target_risk_share: null,
    },
    {
      target_line_id: 'taa-risk-assets',
      target_set_id: 'taa-root',
      target_member_type: 'taxonomy_node',
      target_member_id: 'risk-assets',
      taxonomy_node_id: 'risk-assets',
      target_weight: 0.8,
      target_risk_share: 1,
    },
    {
      target_line_id: 'taa-cash',
      target_set_id: 'taa-root',
      target_member_type: 'cash_bucket',
      target_member_id: '__cash__',
      taxonomy_node_id: null,
      target_weight: 0.2,
      target_risk_share: null,
    },
    {
      target_line_id: 'taa-derivatives',
      target_set_id: 'taa-root',
      target_member_type: 'derivative_bucket',
      target_member_id: '__derivatives__',
      taxonomy_node_id: null,
      target_weight: 0,
      target_risk_share: null,
    },
  ],
  target_set_integrity_issues: [],
}

describe('Taxonomies rendered page contract', () => {
  beforeEach(() => {
    accessState.can_edit = true
    vi.clearAllMocks()
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue(taxonomyCatalog)
    apiMocks.getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture())
    apiMocks.getPortfolioAccountsWorkspace.mockResolvedValue({
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
    })
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [] })
    apiMocks.updatePortfolioTaxonomy.mockResolvedValue({})
    apiMocks.savePortfolioTaxonomyTargetConfiguration.mockResolvedValue({})
  })

  it('lets readers browse taxonomies while blocking assignment shortcuts and shared changes', async () => {
    accessState.can_edit = false
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({
      ...taxonomyCatalog,
      taxonomies: [...taxonomyCatalog.taxonomies, { ...taxonomyCatalog.taxonomies[0], taxonomy_id: 'taxonomy-2', name: 'Alternative View' }],
    })
    const user = userEvent.setup()
    renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    const node = await screen.findByRole('button', { name: 'Risk Assets' })
    expect(screen.getByRole('button', { name: 'Add Taxonomy' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Add Instrument' })).toBeDisabled()
    await user.click(node)
    await user.click(screen.getByRole('checkbox', { name: /Select .*Alpha Fund/ }))
    fireEvent.keyDown(node, { key: 'Enter', ctrlKey: true })
    expect(apiMocks.createPortfolioTaxonomyAssignment).not.toHaveBeenCalled()
    expect(apiMocks.updatePortfolioTaxonomyAssignment).not.toHaveBeenCalled()
    expect(document.querySelector('[data-assignment-drag="enabled"]')).toBeNull()
    expect(document.querySelector('[data-assignment-drop="enabled"]')).toBeNull()
    const picker = document.querySelector('.taxonomy-picker') as HTMLElement
    await user.click(within(picker).getByRole('button', { name: 'Policy Allocation' }))
    await user.click(screen.getByRole('button', { name: 'Alternative View' }))
    expect(within(picker).getByRole('button', { name: 'Alternative View' })).toBeEnabled()
    expect(apiMocks.updatePortfolioDefaultPlanningTaxonomy).not.toHaveBeenCalled()
    expect(apiMocks.updatePortfolioTaxonomy).not.toHaveBeenCalled()
  })

  it('lets editors change the displayed classification without changing portfolio planning', async () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({
      ...taxonomyCatalog,
      taxonomies: [...taxonomyCatalog.taxonomies, { ...taxonomyCatalog.taxonomies[0], taxonomy_id: 'industry', name: 'Industry', planning_enabled: false }],
    })
    const user = userEvent.setup()
    renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    await screen.findByRole('button', { name: 'Risk Assets' })
    const picker = document.querySelector('.taxonomy-picker') as HTMLElement
    await user.click(within(picker).getByRole('button', { name: 'Policy Allocation' }))
    await user.click(screen.getByRole('button', { name: 'Industry' }))
    expect(apiMocks.updatePortfolioDefaultPlanningTaxonomy).not.toHaveBeenCalled()
    expect(apiMocks.updatePortfolioTaxonomy).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Use for portfolio planning' })).toBeEnabled()
  })

  it('enables weight targets on a custom taxonomy without making it the production taxonomy', async () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ ...taxonomyCatalog,
      default_planning_taxonomy_id: null,
      taxonomies: [{ ...taxonomyCatalog.taxonomies[0], planning_enabled: false, budgeting_level: null }],
      target_sets: [], target_set_lines: [],
    })
    const user = userEvent.setup()
    renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    await user.click(await screen.findByRole('button', { name: 'Edit Targets' }))
    await user.click(screen.getByRole('checkbox', { name: 'SAA weight targets' }))
    for (const [name, value] of [['Risk Assets', '80'], ['Cash', '20'], ['Derivatives', '0']]) {
      await user.type(screen.getByRole('spinbutton', { name: `SAA target weight for ${name}` }), value)
    }
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(apiMocks.savePortfolioTaxonomyTargetConfiguration).toHaveBeenCalledTimes(1))
    expect(apiMocks.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2]).toMatchObject({
      target_sets: [expect.objectContaining({ weight_enabled: true, risk_budget_enabled: false })],
    })
    expect(screen.queryByLabelText(/effective date/i)).not.toBeInTheDocument()
    expect(apiMocks.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2]).not.toHaveProperty('effective_from')
    expect(apiMocks.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2]).not.toHaveProperty('effective_to')
    expect(apiMocks.updatePortfolioTaxonomy).not.toHaveBeenCalled()
    expect(apiMocks.updatePortfolioDefaultPlanningTaxonomy).not.toHaveBeenCalled()
  })

  it('archives the existing target set when its final enabled dimension is turned off', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    await user.click(await screen.findByRole('button', { name: 'Edit Targets' }))
    await user.click(screen.getByRole('checkbox', { name: 'SAA weight targets' }))
    await user.click(screen.getByRole('checkbox', { name: 'SAA risk contribution targets' }))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(apiMocks.savePortfolioTaxonomyTargetConfiguration).toHaveBeenCalledTimes(1))
    expect(apiMocks.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2]).toMatchObject({
      target_sets: [expect.objectContaining({ target_set_id: 'saa-root', weight_enabled: false, risk_budget_enabled: false })],
    })
  })

  it('keeps all four target switches and inputs editable when supplemental holdings fail', async () => {
    apiMocks.getHoldingsWorkspace.mockRejectedValue(new Error('Calculation pending'))
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({
      ...taxonomyCatalog, target_sets: [], target_set_lines: [],
    })
    const user = userEvent.setup()
    renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    await user.click(await screen.findByRole('button', { name: 'Edit Targets' }))
    for (const kind of ['SAA', 'TAA']) {
      for (const dimension of ['weight', 'risk contribution']) {
        const checkbox = screen.getByRole('checkbox', { name: `${kind} ${dimension} targets` })
        await user.click(checkbox)
        expect(checkbox).toBeChecked()
      }
      const weight = screen.getByRole('spinbutton', { name: `${kind} target weight for Risk Assets` })
      await user.type(weight, '100')
      expect(weight).toHaveValue(100)
      const risk = screen.getByRole('spinbutton', { name: `${kind} target risk budget for Risk Assets` })
      await user.type(risk, '100')
      expect(risk).toHaveValue(100)
    }
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(apiMocks.savePortfolioTaxonomyTargetConfiguration).toHaveBeenCalledTimes(1))
    const savedTargets = apiMocks.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2].target_sets
    expect(savedTargets).toHaveLength(2)
    for (const kind of ['saa', 'taa']) {
      expect(savedTargets).toContainEqual(expect.objectContaining({
        target_set_type: kind, weight_enabled: true, risk_budget_enabled: true,
        lines: expect.arrayContaining([expect.objectContaining({
          target_member_id: 'risk-assets', target_weight: 1, target_risk_share: 1,
        })]),
      }))
    }
  })

  it('preserves edited targets when selecting a node and reveals a collapsed target scope', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    await user.click(await screen.findByRole('button', { name: 'Edit Targets' }))
    const weight = screen.getByRole('spinbutton', { name: 'SAA target weight for Risk Assets' })
    await user.clear(weight)
    await user.type(weight, '75')
    const node = screen.getByRole('button', { name: 'Risk Assets' })
    await user.click(node)
    expect(weight).toHaveValue(75)
    const row = node.closest('tr')!
    await user.click(row.querySelector('.taxonomy-tree-toggle') as HTMLButtonElement)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Target scope' }), 'risk-assets')
    await user.click(screen.getByRole('checkbox', { name: 'SAA weight targets' }))
    const childWeight = screen.getByRole('spinbutton', { name: /SAA target weight for .*Alpha Fund/ })
    await user.type(childWeight, '100')
    expect(childWeight).toHaveValue(100)
    expect(weight).toHaveValue(75)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(screen.getByRole('button', { name: 'Edit Targets' }))
    expect(screen.getByRole('spinbutton', { name: 'SAA target weight for Risk Assets' })).toHaveValue(80)
  })

  it('explains subtree deletion and requires the node name before deleting', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    const node = await screen.findByRole('button', { name: 'Risk Assets' })
    fireEvent.contextMenu(node.closest('tr')!)
    await user.click(screen.getByRole('button', { name: 'Delete Node' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Delete Taxonomy Node' })
    expect(dialog).toHaveTextContent('node and all descendants')
    expect(dialog).toHaveTextContent('Transactions, holdings, and saved research snapshots are retained')
    expect(within(dialog).getByRole('button', { name: 'Delete Node' })).toBeDisabled()
    expect(apiMocks.deletePortfolioTaxonomyNode).not.toHaveBeenCalled()
    await user.type(within(dialog).getByRole('textbox'), 'Risk Assets')
    await user.click(within(dialog).getByRole('button', { name: 'Delete Node' }))
    await waitFor(() => expect(apiMocks.deletePortfolioTaxonomyNode).toHaveBeenCalledWith('3', 'taxonomy-1', 'risk-assets'))
  })

  it('saves target changes from multiple scopes together', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    await user.click(await screen.findByRole('button', { name: 'Edit Targets' }))
    await user.click(screen.getByRole('checkbox', { name: 'SAA weight targets' }))
    await user.click(screen.getByRole('checkbox', { name: 'SAA risk contribution targets' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Target scope' }), 'risk-assets')
    await user.click(screen.getByRole('checkbox', { name: 'SAA weight targets' }))
    await user.type(screen.getByRole('spinbutton', { name: /SAA target weight for .*Alpha Fund/ }), '100')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(apiMocks.savePortfolioTaxonomyTargetConfiguration).toHaveBeenCalledTimes(1))
    expect(apiMocks.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2]).toMatchObject({
      target_sets: expect.arrayContaining([
        expect.objectContaining({ target_set_id: 'saa-root', weight_enabled: false, risk_budget_enabled: false }),
        expect.objectContaining({ target_set_id: null, comparator_taxonomy_node_id: 'risk-assets', weight_enabled: true }),
      ]),
    })
    expect(apiMocks.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2].target_sets).toHaveLength(2)
  })

  it('shows FCN-only underlyings for classification without creating a direct holding or observed candidate', async () => {
    const contract = fcnContractFixture()
    contract.terms.underlyings[0].instrument_id = 'linked-only'
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [instrumentFixture({ instrument_id: 'linked-only', instrument_name: 'Linked stock' })] })
    apiMocks.getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture({ rows: [holdingFixture(), holdingFixture({
      line_id: 'fcn-line', holding_category: 'derivatives', quantity: 1, instrument_core: null, derivative_contract: contract,
    })] }))
    renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    const row = (await screen.findByText(/Linked stock/, { selector: '.taxonomy-level-label' })).closest('tr')!
    expect(within(row).getByLabelText('Contract linked')).toBeInTheDocument()
    expect(within(row).getByText('FCN: Alpha FCN')).toBeInTheDocument()
    expect(within(row).getAllByText('—').length).toBeGreaterThanOrEqual(2)
    expect(apiMocks.createPortfolioInstrumentUniverseRecord).not.toHaveBeenCalled()
  })

  it('withholds derived zero-state taxonomy panels until the workspace request settles', () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockReturnValue(new Promise(() => {}))

    renderPortfolioPage(
      <TaxonomiesPage />,
      '/portfolios/3/taxonomies',
      '/portfolios/:portfolioId/taxonomies',
    )

    expect(screen.getByTestId('portfolio-workspace')).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('Loading')
    expect(screen.queryByRole('button', { name: 'Add Taxonomy' })).not.toBeInTheDocument()
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument()
  })

  it('keeps the taxonomy workspace mounted while assignment data refreshes', async () => {
    const user = userEvent.setup()
    let resolveCatalogRefresh: ((value: typeof taxonomyCatalog) => void) | undefined

    renderPortfolioPage(
      <TaxonomiesPage />,
      '/portfolios/3/taxonomies',
      '/portfolios/:portfolioId/taxonomies',
    )

    const nodeButton = await screen.findByRole('button', { name: 'Risk Assets' })
    apiMocks.getPortfolioTaxonomyCatalog.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveCatalogRefresh = resolve
        }),
    )

    await user.click(nodeButton)
    await user.click(screen.getByRole('checkbox', { name: /Select .*Alpha Fund/ }))
    expect(fireEvent.keyDown(nodeButton, { key: 'Enter', ctrlKey: true })).toBe(false)

    await waitFor(() => expect(screen.getByTestId('portfolio-workspace')).toHaveAttribute('aria-busy', 'true'))
    expect(screen.getByRole('button', { name: 'Risk Assets' })).toBeInTheDocument()
    expect(screen.queryByText('Loading')).not.toBeInTheDocument()

    resolveCatalogRefresh?.(taxonomyCatalog)
    await waitFor(() => expect(screen.getByTestId('portfolio-workspace')).toHaveAttribute('aria-busy', 'false'))
  })

  it('hydrates policy editing from the nearest effective ancestor in a deep node chain', async () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValueOnce({
      ...taxonomyCatalog,
      taxonomy_nodes: [
        ...taxonomyCatalog.taxonomy_nodes,
        {
          taxonomy_node_id: 'structured',
          taxonomy_id: 'taxonomy-1',
          parent_taxonomy_node_id: null,
          node_name: 'Structured',
          node_code: 'STRUCT',
          sort_order: 2,
          is_terminal: false,
          default_target_dimension: 'weight',
          status: 'active',
        },
        {
          taxonomy_node_id: 'options',
          taxonomy_id: 'taxonomy-1',
          parent_taxonomy_node_id: 'structured',
          node_name: 'Options',
          node_code: 'OPTIONS',
          sort_order: 1,
          is_terminal: false,
          default_target_dimension: 'weight',
          status: 'active',
        },
        {
          taxonomy_node_id: 'written-calls',
          taxonomy_id: 'taxonomy-1',
          parent_taxonomy_node_id: 'options',
          node_name: 'Written Calls',
          node_code: 'WRITTEN',
          sort_order: 1,
          is_terminal: true,
          default_target_dimension: 'weight',
          status: 'active',
        },
      ],
      analytics_scope_policy_version: 3,
      analytics_scope_policies: [
        {
          analytics_scope_policy_id: 'policy-root',
          portfolio_id: '3',
          taxonomy_id: 'taxonomy-1',
          taxonomy_node_id: '__root__',
          risk_eligible: true,
          risk_budget_eligible: true,
          performance_scope: 'ordinary',
          valuation_basis: 'market',
          exclusion_reason: null,
          policy_version: 1,
          superseded_by_policy_id: null,
          created_at: '2026-01-01T00:00:00Z',
        },
        {
          analytics_scope_policy_id: 'policy-structured',
          portfolio_id: '3',
          taxonomy_id: 'taxonomy-1',
          taxonomy_node_id: 'structured',
          risk_eligible: true,
          risk_budget_eligible: false,
          performance_scope: 'operational_only',
          valuation_basis: 'event',
          exclusion_reason: 'Grandparent policy must not win.',
          policy_version: 2,
          superseded_by_policy_id: null,
          created_at: '2026-01-02T00:00:00Z',
        },
        {
          analytics_scope_policy_id: 'policy-options',
          portfolio_id: '3',
          taxonomy_id: 'taxonomy-1',
          taxonomy_node_id: 'options',
          risk_eligible: false,
          risk_budget_eligible: false,
          performance_scope: 'derivative_lifecycle',
          valuation_basis: 'obligation',
          exclusion_reason: 'Nearest option ancestor.',
          policy_version: 3,
          superseded_by_policy_id: null,
          created_at: '2026-01-03T00:00:00Z',
        },
      ],
      analytics_taxonomy_selections: [],
    })
    const user = userEvent.setup()

    renderPortfolioPage(
      <TaxonomiesPage />,
      '/portfolios/3/taxonomies',
      '/portfolios/:portfolioId/taxonomies',
    )

    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Analytics Scope' }),
      'written-calls',
    )

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'Risk Eligible' })).not.toBeChecked()
      expect(screen.getByRole('checkbox', { name: 'Risk Budget' })).not.toBeChecked()
      expect(screen.getByRole('combobox', { name: 'Performance Scope' })).toHaveValue(
        'derivative_lifecycle',
      )
      expect(screen.getByRole('combobox', { name: 'Valuation' })).toHaveValue(
        'obligation',
      )
      expect(screen.getByRole('textbox', { name: 'Exclusion Reason' })).toHaveValue(
        'Nearest option ancestor.',
      )
    })
  })

  it('keeps system cash and derivative weight targets while their risk targets stay N/A', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <TaxonomiesPage />,
      '/portfolios/3/taxonomies',
      '/portfolios/:portfolioId/taxonomies',
    )

    const cashLabel = await screen.findByText('Cash', { selector: '.taxonomy-level-label' })
    const cashRow = cashLabel.closest('tr')
    const derivativeRow = screen
      .getByText('Derivatives', { selector: '.taxonomy-level-label' })
      .closest('tr')
    expect(cashRow).not.toBeNull()
    expect(derivativeRow).not.toBeNull()
    expect(within(cashRow!).getAllByText('20.00%')).toHaveLength(3)
    expect(within(cashRow!).getAllByText('N/A')).toHaveLength(2)
    expect(within(derivativeRow!).getAllByText('0.00%')).toHaveLength(3)
    expect(within(derivativeRow!).getAllByText('N/A')).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: 'Edit Targets' }))

    const saaCashWeight = screen.getByRole('spinbutton', { name: 'SAA target weight for Cash' })
    expect(saaCashWeight).toHaveValue(20)
    expect(screen.getByRole('spinbutton', { name: 'TAA target weight for Cash' })).toHaveValue(20)
    expect(screen.queryByRole('spinbutton', { name: 'SAA target risk budget for Cash' })).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton', { name: 'TAA target risk budget for Cash' })).not.toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: 'SAA target weight for Derivatives' })).toHaveValue(0)
    expect(screen.getByRole('spinbutton', { name: 'TAA target weight for Derivatives' })).toHaveValue(0)
    expect(screen.queryByRole('spinbutton', { name: 'SAA target risk budget for Derivatives' })).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton', { name: 'TAA target risk budget for Derivatives' })).not.toBeInTheDocument()
    expect(within(cashRow!).getAllByText('N/A')).toHaveLength(2)

    const saaRiskAssetsWeight = screen.getByRole('spinbutton', {
      name: 'SAA target weight for Risk Assets',
    })
    await user.clear(saaRiskAssetsWeight)
    await user.type(saaRiskAssetsWeight, '79')
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Target weight total must be 100% within the selected scope.',
    )
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(apiMocks.savePortfolioTaxonomyTargetConfiguration).not.toHaveBeenCalled()

    await user.clear(saaCashWeight)
    await user.type(saaCashWeight, '21')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(apiMocks.savePortfolioTaxonomyTargetConfiguration).toHaveBeenCalledTimes(1))
    const payload = apiMocks.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2].target_sets[0]
    expect(payload.lines).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          target_member_type: 'cash_bucket',
          target_member_id: '__cash__',
          target_weight: 0.21,
          target_risk_share: null,
        }),
        expect.objectContaining({
          target_member_type: 'derivative_bucket',
          target_member_id: '__derivatives__',
          target_weight: 0,
          target_risk_share: null,
        }),
        expect.objectContaining({
          target_member_type: 'taxonomy_node',
          target_member_id: 'risk-assets',
          target_weight: 0.79,
          target_risk_share: 1,
        }),
      ]),
    )
  })

  it('shows derivative contracts under the locked system bucket instead of taxonomy assignments', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValueOnce(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture(),
          holdingFixture({
            line_id: 'holding:taxonomy-fcn',
            position_reference_id: 'taxonomy-fcn',
            derivative_contract_id: 'taxonomy-fcn',
            holding_category: 'derivatives',
            instrument_core: null,
            derivative_contract: fcnContractFixture({
              derivative_contract_id: 'taxonomy-fcn',
              contract_name: 'Locked Taxonomy FCN',
            }),
            market_value: 100,
            market_value_base: 100,
          }),
        ],
      }),
    )

    renderPortfolioPage(
      <TaxonomiesPage />,
      '/portfolios/3/taxonomies',
      '/portfolios/:portfolioId/taxonomies',
    )

    const derivativeContractLabel = await screen.findByText('Locked Taxonomy FCN', {
      selector: '.taxonomy-level-label',
    })
    const derivativeContractRow = derivativeContractLabel.closest('tr')
    expect(derivativeContractRow).not.toBeNull()
    expect(derivativeContractRow).toHaveClass('taxonomy-entity-row-drag-locked')
    expect(screen.queryByRole('checkbox', { name: /Select Locked Taxonomy FCN/ })).not.toBeInTheDocument()
    expect(within(derivativeContractRow!).getAllByText('N/A')).toHaveLength(2)
  })

  it('withholds the base denominator and all actual weights when one participating value is missing', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture(),
          holdingFixture({
            line_id: 'holding:cny-missing-fx',
            instrument_core: instrumentFixture({
              instrument_id: 'cny-missing-fx',
              instrument_name: 'CNY Missing FX',
              currency: 'CNY',
              identifiers: [
                { identifier_type: 'ticker', identifier_value: 'CNYFX', is_primary: true },
              ],
            }),
            market_value: 1_000,
            market_value_base: null,
            allocation: 0.9,
          }),
        ],
      }),
    )

    renderPortfolioPage(
      <TaxonomiesPage />,
      '/portfolios/3/taxonomies',
      '/portfolios/:portfolioId/taxonomies',
    )

    const rootRow = (
      await screen.findByText('Policy Allocation', { selector: '.taxonomy-node-select span' })
    ).closest('tr')
    const usdRow = screen.getByText(/ALPHA · Alpha Fund/, { selector: '.taxonomy-level-label' }).closest('tr')
    const cnyRow = screen.getByText(/CNYFX · CNY Missing FX/, { selector: '.taxonomy-level-label' }).closest('tr')
    const cashRow = screen.getByText('Operating Cash', { selector: '.taxonomy-level-label' }).closest('tr')
    expect(rootRow).not.toBeNull()
    expect(usdRow).not.toBeNull()
    expect(cnyRow).not.toBeNull()
    expect(cashRow).not.toBeNull()

    const rootCells = within(rootRow!).getAllByRole('cell')
    const usdCells = within(usdRow!).getAllByRole('cell')
    const cnyCells = within(cnyRow!).getAllByRole('cell')
    const cashCells = within(cashRow!).getAllByRole('cell')
    expect(rootCells[6]).toHaveTextContent('—')
    expect(rootCells[7]).toHaveTextContent('—')
    expect(usdCells[6]).toHaveTextContent('—')
    expect(usdCells[7]).toHaveTextContent('$800.00')
    expect(cnyCells[6]).toHaveTextContent('—')
    expect(cnyCells[7]).toHaveTextContent('—')
    expect(cashCells[6]).toHaveTextContent('—')
    expect(cashCells[7]).toHaveTextContent('$200.00')
  })

  it('does not intercept global Tab or unmodified Enter for taxonomy mutations', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <TaxonomiesPage />,
      '/portfolios/3/taxonomies',
      '/portfolios/:portfolioId/taxonomies',
    )

    await user.click(await screen.findByRole('button', { name: 'Risk Assets' }))
    const outsideShortcutScope = screen.getByRole('button', { name: 'Add Taxonomy' })
    outsideShortcutScope.focus()

    expect(fireEvent.keyDown(outsideShortcutScope, { key: 'Tab' })).toBe(true)
    expect(fireEvent.keyDown(outsideShortcutScope, { key: 'Enter' })).toBe(true)
    expect(screen.queryByRole('dialog', { name: /Taxonomy Node/ })).not.toBeInTheDocument()
    expect(apiMocks.createPortfolioTaxonomyNode).not.toHaveBeenCalled()

    await user.click(screen.getByRole('checkbox', { name: /Select .*Alpha Fund/ }))
    expect(
      fireEvent.keyDown(outsideShortcutScope, { key: 'Enter', ctrlKey: true }),
    ).toBe(true)
    expect(screen.queryByText(/Assignments updated/)).not.toBeInTheDocument()

    const scopedNode = screen.getByRole('button', { name: 'Risk Assets' })
    scopedNode.focus()
    expect(fireEvent.keyDown(scopedNode, { key: 'Enter', ctrlKey: true })).toBe(false)
    expect(await screen.findByText(/1 unchanged/)).toBeInTheDocument()
  })
})
