import type { ReactNode } from 'react'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { useNavigate } from 'react-router'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TaxonomiesPage from './pages/TaxonomiesPage'
import type { PortfolioTaxonomyCatalogResponse, PortfolioResolvedMemberTarget, SharedInstrumentRecord } from './lib/api'
import { fcnContractFixture, holdingFixture, holdingsWorkspaceFixture, instrumentFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'
import { useLanguage } from '../../../../packages/ui/src/i18n'

const access = vi.hoisted(() => ({ can_edit: true }))
const concentrationMocks = vi.hoisted(() => ({ getConcentration: vi.fn(), getConcentrationSettings: vi.fn() }))
const api = vi.hoisted(() => ({
  getPortfolioTaxonomyCatalog: vi.fn(), getHoldingsWorkspace: vi.fn(), getPortfolioAccountsWorkspace: vi.fn(), getPortfolioInstruments: vi.fn(),
  createPortfolioInstrumentUniverseRecord: vi.fn(), createPortfolioTaxonomy: vi.fn(), createPortfolioTaxonomyAssignment: vi.fn(), createPortfolioTaxonomyNode: vi.fn(),
  savePortfolioTaxonomyTargetConfiguration: vi.fn(), deletePortfolioInstrumentUniverseRecord: vi.fn(), deletePortfolioTaxonomy: vi.fn(), deletePortfolioTaxonomyNode: vi.fn(),
  updatePortfolioTaxonomy: vi.fn(), updatePortfolioTaxonomyAssignment: vi.fn(), updatePortfolioTaxonomyNode: vi.fn(),
  searchPortfolioSecurities: vi.fn(), materializePortfolioSecurity: vi.fn(),
}))
vi.mock('./components/PortfolioAccessProvider', () => ({ usePortfolioAccess: () => access }))
vi.mock('./lib/api', () => api)
vi.mock('./lib/concentrationApi', () => concentrationMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({ default: ({ children, busy }: { children: ReactNode; busy?: boolean }) => <section aria-busy={busy}>{children}</section> }))

function resolved(memberId: string, parent: string | null, saa: number, taa: number, global: number | null, overrides: Partial<PortfolioResolvedMemberTarget> = {}): PortfolioResolvedMemberTarget {
  return { scope_node_id: parent, member_type: 'taxonomy_node', member_id: memberId, taxonomy_node_id: memberId, target_basis: 'risk_budget',
    strategic_value: saa, tactical_value: taa, strategic_source: 'saa', tactical_source: 'taa', strategic_target_set_id: 'saa-root', tactical_target_set_id: 'taa-root',
    strategic_global_risk_target: global, tactical_global_risk_target: global, strategic_status: 'complete', tactical_status: 'complete', ...overrides }
}
function catalogFixture(): PortfolioTaxonomyCatalogResponse {
  const node = { taxonomy_id: 'tax', parent_taxonomy_node_id: null, node_code: null, sort_order: 1, is_terminal: false, allocation_basis: 'risk_budget' as const, status: 'active' }
  return { portfolio_id: '3', taxonomy_configuration_version: 2, taxonomies: [{ taxonomy_id: 'tax', portfolio_id: '3', name: 'Allocation', taxonomy_type: 'allocation', primary_assignment_scope: 'instrument', root_allocation_basis: 'risk_budget', status: 'active' }],
    taxonomy_nodes: [{ ...node, taxonomy_node_id: 'risk', node_name: 'Risk Assets' },
      { ...node, taxonomy_node_id: 'growth', parent_taxonomy_node_id: 'risk', node_name: 'Growth', is_terminal: true, allocation_basis: 'weight' },
      { ...node, taxonomy_node_id: 'defensive', parent_taxonomy_node_id: 'risk', node_name: 'Defensive', is_terminal: true, sort_order: 2 }],
    taxonomy_assignments: [{ assignment_id: 'assignment-alpha', taxonomy_id: 'tax', target_scope: 'instrument', target_entity_id: 'asset-1', taxonomy_node_id: 'growth', status: 'active' }],
    instrument_universe: [], target_set_integrity_issues: [],
    target_sets: [{ target_set_id: 'saa-root', taxonomy_id: 'tax', comparator_taxonomy_node_id: null, target_set_type: 'saa', name: 'Root SAA', status: 'active' },
      { target_set_id: 'saa-risk', taxonomy_id: 'tax', comparator_taxonomy_node_id: 'risk', target_set_type: 'saa', name: 'Risk SAA', status: 'active' },
      { target_set_id: 'taa-risk', taxonomy_id: 'tax', comparator_taxonomy_node_id: 'risk', target_set_type: 'taa', name: 'Risk TAA', status: 'active' }],
    target_set_lines: [
      { target_line_id: 'saa-root-risk', target_set_id: 'saa-root', target_member_type: 'taxonomy_node', target_member_id: 'risk', taxonomy_node_id: 'risk', target_value: 1 },
      { target_line_id: 'saa-root-cash', target_set_id: 'saa-root', target_member_type: 'cash_bucket', target_member_id: '__cash__', target_value: .2 },
      ...(['saa', 'taa'] as const).flatMap((stage) => ['growth', 'defensive'].map((id, index) => ({ target_line_id: `${stage}-${id}`, target_set_id: `${stage}-risk`, target_member_type: 'taxonomy_node' as const, target_member_id: id, taxonomy_node_id: id, target_value: stage === 'saa' ? [0.6,0.4][index] : [0.65,0.35][index] }))),
    ],
    target_resolution: [{ taxonomy_id: 'tax', scope_targets: [], errors: [], member_targets: [resolved('risk', null, 1, 1, 1, { tactical_source: 'saa' }),
      resolved('growth', 'risk', .6, .65, .65), resolved('defensive', 'risk', .4, .35, .35),
      resolved('asset-1', 'growth', 1, 1, .65, { member_type: 'instrument', taxonomy_node_id: null, target_basis: 'weight', strategic_source: 'single_member', tactical_source: 'single_member' }),
      resolved('__cash__', null, .2, .2, null, { member_type: 'cash_bucket', taxonomy_node_id: null, target_basis: 'weight', tactical_source: 'saa' })] }],
  }
}
const settings = { portfolio_id: '3', revision: 2, latest_revision: 5, effective_from: '2026-07-01', enabled_taxonomy_ids: ['tax', 'another-tax'],
  limits: [{ scope: 'taxonomy', taxonomy_id: 'tax', entity_id: 'risk', limit_weight: .85 }, { scope: 'security', taxonomy_id: null, entity_id: 'other-security', limit_weight: .1 }], fcn_allocations: [] }
const accountResponse = { portfolio_id: '3', base_currency: 'USD', accounts: [{ account: { account_id: 'cash-1', portfolio_id: '3', account_name: 'Operating Cash', account_type: 'deposit_account', account_category: 'cash', currency: 'USD', status: 'active' }, derived_cash_balance_base: 200, pending_settlement_base: 0 }], positions: [], linked_transactions: [], ledger_postings: [] }
function registry(id = 'asset-2', name = 'Beta Fund'): SharedInstrumentRecord {
  return { ...instrumentFixture({ instrument_id: id, instrument_name: name, identifiers: [{ identifier_type: 'ticker', identifier_value: id === 'asset-1' ? 'ALPHA' : 'BETA', is_primary: true }] }), latest_market_data: [] } as unknown as SharedInstrumentRecord
}
function renderPage() { return renderPortfolioPage(<TaxonomiesPage />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies') }
function PortfolioNavigation() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/portfolios/4/taxonomies')}>Next portfolio</button>
}
function LanguageSwitch() {
  const { setLanguage } = useLanguage()
  return <button onClick={() => setLanguage('zh-Hans')}>Switch language</button>
}
async function ready() { await screen.findByRole('button', { name: 'Risk Assets' }); await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Taxonomy concentration reminders' })).toBeChecked()) }
async function changeNumber(name: string, value: string) { fireEvent.change(screen.getByRole('spinbutton', { name }), { target: { value } }) }

describe('Taxonomies integrated tree contract', () => {
  beforeEach(() => {
    vi.resetAllMocks(); access.can_edit = true
    api.getPortfolioTaxonomyCatalog.mockResolvedValue(catalogFixture())
    api.getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture({ totals: { ...holdingsWorkspaceFixture().totals, nav: 1200 } }))
    api.getPortfolioAccountsWorkspace.mockResolvedValue(accountResponse)
    api.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [registry('asset-1', 'Alpha Fund'), registry()] })
    api.searchPortfolioSecurities.mockResolvedValue({ results: [], catalog_errors: {} })
    api.savePortfolioTaxonomyTargetConfiguration.mockResolvedValue({})
    concentrationMocks.getConcentrationSettings.mockResolvedValue(structuredClone(settings))
    concentrationMocks.getConcentration.mockResolvedValue({ portfolio_id: '3', as_of_date: '2026-07-15', base_currency: 'USD', nav: 1200, scopes: [
      { scope: 'taxonomy', taxonomy_id: 'tax', enabled: true, rows: [{ entity_id: 'risk', weight: .75, exposure_base: 900, lower_bound_weight: .75, limit_weight: .85, status: 'within', coverage: [] }] },
      { scope: 'security', taxonomy_id: null, enabled: true, rows: [{ entity_id: 'asset-1', weight: 800/1200, exposure_base: 800, limit_weight: null, status: 'unconfigured', coverage: [] }] }], fcn_contracts: [] })
  })

  it('shows every level and both stages with each parent basis and backend-derived portfolio targets', async () => {
    renderPage(); await ready()
    const table = screen.getByRole('table', { name: 'Classification and asset overview' })
    expect(within(table).getByRole('button', { name: 'Allocation' })).toHaveAttribute('translate', 'no')
    expect(within(table).getByRole('button', { name: 'Growth' })).toHaveAttribute('translate', 'no')
    expect(within(table).getByRole('button', { name: 'Allocation' })).toHaveAttribute('data-tree-level', 'root')
    expect(within(table).getByRole('button', { name: 'Risk Assets' })).toHaveAttribute('data-tree-level', 'primary')
    expect(within(table).getByRole('button', { name: 'Growth' })).toHaveAttribute('data-tree-level', 'nested')
    expect(within(table).getByText('ALPHA · Alpha Fund')).toHaveAttribute('data-tree-level', 'item')
    for (const [label, level] of [['Allocation', 'root'], ['Risk Assets', 'primary'], ['Growth', 'nested'], ['ALPHA · Alpha Fund', 'item'], ['Cash', 'primary'], ['Operating Cash', 'item'], ['Unassigned', 'primary']]) {
      const row = within(table).getByText(label).closest('tr')!
      expect(row).toHaveClass('portfolio-tree-row')
      expect(row).toHaveAttribute('data-tree-level', level)
      expect(within(row).getAllByRole('cell')).toHaveLength(8)
    }
    expect(within(table).getAllByRole('columnheader')).toHaveLength(8)
    const growth = screen.getByRole('button', { name: 'Growth' }).closest('tr')!
    expect(within(growth).getByText('Weight')).toBeInTheDocument()
    expect(within(growth).getByText('60.00%')).toBeInTheDocument()
    expect(within(growth).getAllByText('65.00%')).toHaveLength(2)
    expect(within(table).getByText('ALPHA · Alpha Fund')).toBeInTheDocument()
    expect(within(table).getByText('75.00%')).toBeInTheDocument()
    expect(within(table).getByText('66.67%')).toBeInTheDocument()
    for (const name of ['Cash', 'Operating Cash']) {
      const cells = within(within(table).getByText(name).closest('tr')!).getAllByRole('cell')
      expect(cells[2]).toHaveTextContent('$200.00')
      expect(cells[3]).toHaveTextContent(/^—$/)
    }
    const cashCells = within(within(table).getByText('Cash').closest('tr')!).getAllByRole('cell')
    expect(cashCells[4]).toHaveTextContent('20.00%')
    expect(cashCells[5]).toHaveTextContent('20.00%')
    expect(screen.queryByText(/Manage taxonomy|Planning taxonomy|Configure children of|Taxonomy default limits|Portfolio limits/)).not.toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })

  it('preserves the table and row hierarchy while keeping Edit or Save last in the natural action flow', async () => {
    const user = userEvent.setup(); renderPage(); await ready()
    const table = screen.getByRole('table') as HTMLTableElement
    const columns = Array.from(table.querySelectorAll('col'))
    const columnWidths = columns.map((column) => column.style.width)
    const rows = Array.from(table.rows)
    const cells = rows.map((row) => Array.from(row.cells))
    const actionArea = screen.getByRole('button', { name: 'Edit' }).parentElement!
    const rowLevels = rows.map((row) => row.dataset.treeLevel)
    expect(Array.from(actionArea.children).map((child) => child.textContent)).toEqual(['Edit'])
    expect(columns).toHaveLength(8)
    expect(columnWidths.every((width) => Number.parseFloat(width) > 0)).toBe(true)
    expect(columnWidths.reduce((total, width) => total + Number.parseFloat(width), 0)).toBe(100)

    function expectStableTable() {
      expect(screen.getByRole('table')).toBe(table)
      expect(table.rows).toHaveLength(rows.length)
      columns.forEach((column, index) => {
        expect(table.querySelectorAll('col')[index]).toBe(column)
        expect(column.style.width).toBe(columnWidths[index])
      })
      rows.forEach((row, rowIndex) => {
        expect(table.rows[rowIndex]).toBe(row)
        expect(row.dataset.treeLevel).toBe(rowLevels[rowIndex])
        cells[rowIndex].forEach((cell, cellIndex) => expect(row.cells[cellIndex]).toBe(cell))
      })
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    }

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expectStableTable()
    expect(screen.getByRole('button', { name: 'Save' }).parentElement).toBe(actionArea)
    expect(screen.getByRole('button', { name: 'Cancel' }).parentElement).toBe(actionArea)
    expect(within(actionArea).getAllByRole('button').map((button) => button.textContent)).toEqual(['Cancel', 'Save'])
    const growthCells = cells[rows.findIndex((row) => within(row).queryByRole('button', { name: 'Growth' }))]
    expect(within(growthCells[1]).getByRole('combobox')).toHaveValue('weight')
    expect(within(growthCells[4]).getByRole('spinbutton')).toHaveValue(60)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expectStableTable()
    expect(screen.getByRole('button', { name: 'Edit' }).parentElement).toBe(actionArea)
    expect(Array.from(actionArea.children).map((child) => child.textContent)).toEqual(['Edit'])
    expect(growthCells[1]).toHaveTextContent('Weight')
    expect(growthCells[4]).toHaveTextContent('60.00%')
  })

  it('labels amounts in the reporting currency and uses converted values without adding currencies to asset names', async () => {
    api.getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture({ base_currency: 'USD', rows: [
      holdingFixture({ instrument_core: instrumentFixture({ currency: 'HKD' }), market_value: 6240, market_value_base: 800 }),
      holdingFixture({ line_id: 'fcn-holding', instrument_core: null, holding_category: 'derivatives', holding_kind: 'fcn', derivative_contract_id: 'fcn-1', derivative_contract: fcnContractFixture({ currency: 'HKD' }), market_value: 390, market_value_base: 50 }),
    ] }))
    renderPage(); await ready()
    expect(screen.getByRole('columnheader', { name: 'Carrying Amount (USD)' })).toBeInTheDocument()
    for (const [name, amount] of [['ALPHA · Alpha Fund', '$800.00'], ['Alpha FCN', '$50.00'], ['Operating Cash', '$200.00']]) {
      const row = screen.getByText(name).closest('tr')!
      expect(row).toHaveClass('portfolio-tree-row')
      expect(row).toHaveAttribute('data-tree-level', 'item')
      const cells = within(row).getAllByRole('cell')
      expect(cells[0]).not.toHaveTextContent(/USD|HKD/)
      expect(cells[2]).toHaveTextContent(amount)
    }
    const root = within(screen.getByRole('table')).getByRole('button', { name: 'Allocation' }).closest('tr')!
    expect(within(root).getAllByRole('cell')[2]).toHaveTextContent('$1,050.00')
  })

  it('uses the accounts reporting currency when holdings cannot be loaded', async () => {
    api.getHoldingsWorkspace.mockRejectedValue(new Error('Holdings unavailable'))
    renderPage()
    expect(await screen.findByRole('columnheader', { name: 'Carrying Amount (USD)' })).toBeInTheDocument()
    const row = screen.getByText('Operating Cash').closest('tr')!
    expect(within(row).getAllByRole('cell')[2]).toHaveTextContent('$200.00')
    expect(screen.getByText(/Current holdings coverage unavailable/)).toBeInTheDocument()
  })

  it('does not invent a reporting currency when monetary data is unavailable', async () => {
    api.getHoldingsWorkspace.mockRejectedValue(new Error('Holdings unavailable'))
    api.getPortfolioAccountsWorkspace.mockRejectedValue(new Error('Accounts unavailable'))
    renderPage()
    expect(await screen.findByRole('columnheader', { name: 'Carrying Amount' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /Carrying Amount \(/ })).not.toBeInTheDocument()
    const root = within(screen.getByRole('table')).getByRole('button', { name: 'Allocation' }).closest('tr')!
    expect(within(root).getAllByRole('cell')[2]).toHaveTextContent('—')
    expect(screen.getByText(/Account coverage unavailable/)).toBeInTheDocument()
  })

  it('labels gross concentration exposure separately from carrying amount and current weight', async () => {
    const user = userEvent.setup()
    api.getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture({
      rows: [holdingFixture({ market_value_base: 600, allocation: .5 })],
      totals: { ...holdingsWorkspaceFixture().totals, nav: 1200 },
    }))
    concentrationMocks.getConcentration.mockResolvedValue({ portfolio_id: '3', as_of_date: '2026-07-15', base_currency: 'USD', nav: 1200,
      scopes: [{ scope: 'security', taxonomy_id: null, enabled: true, rows: [{ entity_id: 'asset-1', weight: 1.25, exposure_base: 1500, limit_weight: null, status: 'unconfigured', coverage: [] }] }], fcn_contracts: [] })
    renderPage(); await ready()
    const table = screen.getByRole('table', { name: 'Classification and asset overview' })
    expect(within(table).getByRole('columnheader', { name: /^Exposure Ratio/ })).toBeInTheDocument()
    expect(within(table).queryByRole('columnheader', { name: 'Current Weight' })).not.toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: /^Target RC/ })).toBeInTheDocument()
    const cells = within(screen.getByText('ALPHA · Alpha Fund').closest('tr')!).getAllByRole('cell')
    expect(cells[2]).toHaveTextContent('$600.00')
    expect(cells[3]).toHaveTextContent('125.00%')
    await user.click(screen.getByRole('button', { name: /^Exposure basis:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('absolute market values across accounts')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Option exposure is not modeled')
    expect(screen.getByRole('tooltip')).toHaveTextContent('must not be read as zero')
  })

  it('saves basis, targets across levels, and all concentration limits in one request', async () => {
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Risk Assets' })).toHaveValue(100)
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Growth' })).toHaveValue(60)
    expect(screen.getByRole('spinbutton', { name: 'TAA target for Growth' })).toHaveValue(65)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Allocation basis for Allocation' }), 'weight')
    await changeNumber('SAA target for Growth', '55'); await changeNumber('SAA target for Defensive', '45')
    await changeNumber('TAA target for ALPHA · Alpha Fund', '100'); await changeNumber('Concentration limit for Risk Assets', '80')
    expect(screen.getAllByLabelText('Portfolio risk target updates after save').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.savePortfolioTaxonomyTargetConfiguration).toHaveBeenCalledTimes(1))
    const payload = api.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2]
    expect(payload.expected_configuration_version).toBe(2)
    expect(payload.root_allocation_basis).toBe('weight')
    expect(payload.node_allocation_bases).toEqual({})
    expect(payload.target_sets).toEqual(expect.arrayContaining([
      expect.objectContaining({ comparator_taxonomy_node_id: 'risk', target_set_type: 'saa', lines: expect.arrayContaining([expect.objectContaining({ target_member_id: 'growth', target_value: .55 })]) }),
      expect.objectContaining({ comparator_taxonomy_node_id: 'growth', target_set_type: 'taa', lines: [expect.objectContaining({ target_member_id: 'asset-1', target_value: 1 })] }),
    ]))
    expect(payload.concentration).toEqual(expect.objectContaining({ expected_revision: 5, effective_from: '2026-07-15', enabled_taxonomy_ids: ['tax', 'another-tax'], limits: [
      { scope: 'taxonomy', taxonomy_id: 'tax', entity_id: 'risk', limit_weight: .8 }, { scope: 'security', taxonomy_id: null, entity_id: 'other-security', limit_weight: .1 },
    ] }))
    expect(JSON.stringify(payload)).not.toMatch(/weight_enabled|risk_budget_enabled|target_weight|target_risk_share|node_defaults/)
  })

  it('clears an entire TAA layer to inherit SAA and blocks a partial blank layer', async () => {
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: 'Edit' }))
    await changeNumber('TAA target for Growth', '')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('Missing target for Growth')
    await changeNumber('TAA target for Defensive', '')
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.savePortfolioTaxonomyTargetConfiguration).toHaveBeenCalledWith('3', 'tax', expect.objectContaining({ target_sets: [expect.objectContaining({ target_set_id: 'taa-risk', lines: [] })] })))
  })

  it('keeps cash reserve outside the 100% security target sum and exposes no derivative target', async () => {
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: 'Edit' }))
    await changeNumber('SAA target for Cash', '25')
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Risk Assets' })).toHaveValue(100)
    expect(screen.queryByRole('spinbutton', { name: /target for Derivatives/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('keeps derived risk targets visible for concentration-only edits and preserves zero limits', async () => {
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: 'Edit' }))
    await changeNumber('Concentration limit for ALPHA · Alpha Fund', '0')
    expect(screen.queryByLabelText('Portfolio risk target updates after save')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.savePortfolioTaxonomyTargetConfiguration).toHaveBeenCalled())
    const payload = api.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2]
    expect(payload.target_sets).toEqual([]); expect(payload.node_allocation_bases).toEqual({}); expect(payload).not.toHaveProperty('root_allocation_basis')
    expect(payload.concentration.limits).toContainEqual({ scope: 'security', taxonomy_id: null, entity_id: 'asset-1', limit_weight: 0 })
  })

  it('cancels all drafts and locks assignment interactions throughout editing', async () => {
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Allocation basis for Growth' }), 'risk_budget')
    await changeNumber('SAA target for Growth', '55'); await changeNumber('Concentration limit for Risk Assets', '75')
    expect(document.querySelector('[data-assignment-drag="enabled"]')).toBeNull()
    expect(document.querySelector('[data-assignment-drop="enabled"]')).toBeNull()
    expect(screen.getByRole('button', { name: '+ Add instrument' })).toBeDisabled()
    fireEvent.keyDown(screen.getByRole('spinbutton', { name: 'SAA target for Growth' }), { key: 'Enter', ctrlKey: true })
    expect(api.createPortfolioTaxonomyAssignment).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Cancel' })); await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByRole('combobox', { name: 'Allocation basis for Growth' })).toHaveValue('weight')
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Growth' })).toHaveValue(60)
    expect(screen.getByRole('spinbutton', { name: 'Concentration limit for Risk Assets' })).toHaveValue(85)
  })

  it('explicitly admits a searched registry instrument and assigns it to the selected leaf', async () => {
    const user = userEvent.setup(); renderPage(); await ready()
    fireEvent.contextMenu(screen.getByRole('button', { name: 'Growth' }), { clientX: 50, clientY: 50 })
    await user.click(screen.getByRole('button', { name: 'Add instrument' }))
    expect(screen.getByRole('combobox', { name: 'Instrument destination' })).toHaveValue('growth')
    await user.type(screen.getByRole('searchbox', { name: 'Search instrument' }), 'Beta')
    await user.click(screen.getByRole('button', { name: /BETA.*Beta Fund/ }))
    await user.click(within(screen.getByRole('form', { name: 'Add instrument' })).getByRole('button', { name: 'Add instrument' }))
    await waitFor(() => expect(api.createPortfolioTaxonomyAssignment).toHaveBeenCalledWith('3', 'tax', { target_scope: 'instrument', target_entity_id: 'asset-2', taxonomy_node_id: 'growth' }))
    expect(api.createPortfolioInstrumentUniverseRecord).toHaveBeenCalledWith('3', { instrument_id: 'asset-2' })
  })

  it('registers a remote security through the shared picker before assigning it', async () => {
    api.searchPortfolioSecurities.mockResolvedValue({ results: [{ instrument_type: 'equity', catalog_provider: 'fmp', catalog_symbol: 'ZETA', symbol: 'ZETA', name: 'Zeta Stock', currency: 'USD', currency_verified: true, exchange_label: 'NASDAQ', existing_instrument_id: null }], catalog_errors: {} })
    api.materializePortfolioSecurity.mockResolvedValue({ ...registry('zeta', 'Zeta Stock'), instrument_type: 'equity', identifiers: [{ identifier_type: 'ticker', identifier_value: 'ZETA', is_primary: true }] })
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: '+ Add instrument' }))
    await user.type(screen.getByRole('searchbox', { name: 'Search instrument' }), 'Zeta')
    await user.click(await screen.findByRole('button', { name: /ZETA.*Zeta Stock/ }))
    await waitFor(() => expect(screen.getByRole('searchbox', { name: 'Search instrument' })).toHaveValue('ZETA · Zeta Stock'))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Instrument destination' }), 'growth')
    await user.click(within(screen.getByRole('form', { name: 'Add instrument' })).getByRole('button', { name: 'Add instrument' }))
    await waitFor(() => expect(api.createPortfolioTaxonomyAssignment).toHaveBeenCalledWith('3', 'tax', { target_scope: 'instrument', target_entity_id: 'zeta', taxonomy_node_id: 'growth' }))
    expect(api.materializePortfolioSecurity).toHaveBeenCalledTimes(1)
    expect(api.createPortfolioInstrumentUniverseRecord).toHaveBeenCalledWith('3', { instrument_id: 'zeta' })
  })

  it('explicitly admits an already classified FCN-only underlying without moving it', async () => {
    const catalog = catalogFixture()
    catalog.taxonomy_assignments.push({ assignment_id: 'beta-assignment', taxonomy_id: 'tax', target_scope: 'instrument', target_entity_id: 'asset-2', taxonomy_node_id: 'growth', status: 'active' })
    api.getPortfolioTaxonomyCatalog.mockResolvedValue(catalog)
    api.getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture({ rows: [holdingFixture(), holdingFixture({ line_id: 'fcn-beta', instrument_core: null, holding_category: 'derivatives', derivative_contract: fcnContractFixture({ terms: { ...fcnContractFixture().terms, underlyings: [{ ...fcnContractFixture().terms.underlyings[0], instrument_id: 'asset-2' }] } }) })] }))
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: 'Growth' })); await user.click(screen.getByRole('button', { name: '+ Add instrument' }))
    await user.type(screen.getByRole('searchbox', { name: 'Search instrument' }), 'Beta'); await user.click(screen.getByRole('button', { name: /BETA.*Beta Fund/ }))
    await user.click(within(screen.getByRole('form', { name: 'Add instrument' })).getByRole('button', { name: 'Add instrument' }))
    await waitFor(() => expect(api.createPortfolioInstrumentUniverseRecord).toHaveBeenCalledWith('3', { instrument_id: 'asset-2' }))
    expect(api.createPortfolioTaxonomyAssignment).not.toHaveBeenCalled()
    expect(api.updatePortfolioTaxonomyAssignment).not.toHaveBeenCalled()
  })

  it('reloads committed assignments when a later item in a bulk move fails', async () => {
    const catalog = catalogFixture()
    const movedCatalog = structuredClone(catalog)
    movedCatalog.taxonomy_assignments[0].taxonomy_node_id = 'defensive'
    api.getPortfolioTaxonomyCatalog.mockResolvedValueOnce(catalog).mockResolvedValue(movedCatalog)
    api.getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture({ rows: [holdingFixture(),
      holdingFixture({ line_id: 'holding:asset-2', instrument_core: instrumentFixture({ instrument_id: 'asset-2', instrument_name: 'Beta Fund', identifiers: [{ identifier_type: 'ticker', identifier_value: 'BETA', is_primary: true }] }) }),
    ] }))
    api.updatePortfolioTaxonomyAssignment.mockResolvedValue({})
    api.createPortfolioTaxonomyAssignment.mockRejectedValue(new Error('Assignment conflict'))
    const user = userEvent.setup(); renderPage(); await ready()
    await user.click(screen.getByRole('checkbox', { name: 'Select ALPHA · Alpha Fund' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select BETA · Beta Fund' }))
    fireEvent.contextMenu(screen.getByRole('button', { name: 'Defensive' }).closest('tr')!)
    await user.click(screen.getByRole('button', { name: 'Assign Selected Items Here' }))
    await screen.findByText('1 assignment(s) were saved before the remaining update failed: Assignment conflict')
    await waitFor(() => expect(api.getPortfolioTaxonomyCatalog).toHaveBeenCalledTimes(2))
    expect(api.updatePortfolioTaxonomyAssignment).toHaveBeenCalledTimes(1)
    expect(api.createPortfolioTaxonomyAssignment).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('checkbox', { name: 'Select ALPHA · Alpha Fund' })).not.toBeChecked()
  })

  it('retains every draft when an atomic save fails', async () => {
    api.savePortfolioTaxonomyTargetConfiguration.mockRejectedValue(new Error('Concentration settings changed. Reload and try again.'))
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: 'Edit' }))
    await changeNumber('SAA target for Growth', '55'); await changeNumber('SAA target for Defensive', '45')
    await changeNumber('Concentration limit for Risk Assets', '')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('Concentration settings changed. Reload and try again.')
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Growth' })).toHaveValue(55)
    expect(screen.getByRole('spinbutton', { name: 'Concentration limit for Risk Assets' })).toHaveValue(null)
    expect(api.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2].concentration.limits).toEqual([settings.limits[1]])
    expect(api.getPortfolioTaxonomyCatalog).toHaveBeenCalledTimes(1)
  })

  it('keeps a conflicted draft until explicitly discarded and edits the reloaded configuration version', async () => {
    api.savePortfolioTaxonomyTargetConfiguration.mockRejectedValueOnce(new Error('Taxonomy configuration changed. Reload before saving.'))
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: 'Edit' }))
    await changeNumber('SAA target for Cash', '25')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    const reload = await screen.findByRole('button', { name: 'Discard draft and reload' })
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Cash' })).toHaveValue(25)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(api.savePortfolioTaxonomyTargetConfiguration.mock.calls[0][2].expected_configuration_version).toBe(2)
    api.getPortfolioTaxonomyCatalog.mockResolvedValue({ ...catalogFixture(), taxonomy_configuration_version: 11 })
    await user.click(reload)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Cash' })).toHaveValue(20)
    await changeNumber('SAA target for Cash', '30')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.savePortfolioTaxonomyTargetConfiguration).toHaveBeenCalledTimes(2))
    expect(api.savePortfolioTaxonomyTargetConfiguration.mock.calls[1][2].expected_configuration_version).toBe(11)
  })

  it('locks submitted values and ignores an old save completion after switching portfolios', async () => {
    let finishSave!: (value: object) => void
    api.savePortfolioTaxonomyTargetConfiguration.mockImplementationOnce(() => new Promise((resolve) => { finishSave = resolve }))
    api.getPortfolioTaxonomyCatalog.mockImplementation((id) => Promise.resolve({ ...catalogFixture(), portfolio_id: id }))
    const user = userEvent.setup()
    renderPortfolioPage(<><PortfolioNavigation /><TaxonomiesPage /></>, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    await ready(); await user.click(screen.getByRole('button', { name: 'Edit' }))
    await changeNumber('SAA target for Cash', '25'); await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Cash' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Next portfolio' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await changeNumber('SAA target for Cash', '35')
    await act(async () => { finishSave({}) })
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Cash' })).toHaveValue(35)
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
    expect(api.getPortfolioTaxonomyCatalog.mock.calls.map(([id]) => id)).toEqual(['3', '4'])
  })

  it('keeps missing securities-account cash in coverage instead of treating it as a zero balance', async () => {
    api.getPortfolioAccountsWorkspace.mockResolvedValue({ ...accountResponse, accounts: [{
      ...accountResponse.accounts[0], account: { ...accountResponse.accounts[0].account, account_name: 'Broker cash missing', account_category: 'securities' }, derived_cash_balance_base: null,
    }] })
    renderPage(); await ready()
    const row = screen.getByText('Broker cash missing').closest('tr')!
    expect(within(row).getAllByRole('cell')[2]).toHaveTextContent('—')
    expect(screen.getByRole('table').querySelector('.taxonomy-root-row td:nth-child(3)')).toHaveTextContent('—')
    expect(screen.getByRole('table').querySelector('.taxonomy-system-cash-row td:nth-child(3)')).toHaveTextContent('—')
  })

  it('shows the existing assignment and explicitly moves an instrument without duplicating it', async () => {
    const user = userEvent.setup(); renderPage(); await ready(); await user.click(screen.getByRole('button', { name: '+ Add instrument' }))
    await user.type(screen.getByRole('searchbox', { name: 'Search instrument' }), 'Alpha'); await user.click(screen.getByRole('button', { name: /ALPHA.*Alpha Fund/ }))
    await user.click(screen.getByRole('button', { name: 'Current assignment: Growth' }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('Growth')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Instrument destination' }), 'defensive')
    await user.click(screen.getByRole('button', { name: 'Move instrument' }))
    await waitFor(() => expect(api.updatePortfolioTaxonomyAssignment).toHaveBeenCalledWith('3', 'tax', 'assignment-alpha', { taxonomy_node_id: 'defensive' }))
    expect(api.createPortfolioTaxonomyAssignment).not.toHaveBeenCalled()
  })

  it('exposes structural actions through the root context menu and protects destructive deletion', async () => {
    const user = userEvent.setup(); renderPage(); await ready()
    const root = screen.getByRole('table').querySelector('.taxonomy-root-row')!
    fireEvent.contextMenu(root, { clientX: 50, clientY: 50 })
    expect(screen.getByRole('button', { name: 'Add child category' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Delete Taxonomy' }))
    const dialog = screen.getByRole('alertdialog'); expect(within(dialog).getByRole('button', { name: 'Delete Taxonomy' })).toBeDisabled()
    expect(api.deletePortfolioTaxonomy).not.toHaveBeenCalled()
  })

  it('preserves the full tree with expand/collapse and blocks editors for read-only users', async () => {
    access.can_edit = false
    const user = userEvent.setup(); renderPage(); await ready()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '+ Add instrument' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '+ Add taxonomy' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Collapse Allocation' }))
    await user.click(screen.getByRole('button', { name: 'Collapse all' }))
    expect(screen.getByRole('button', { name: 'Risk Assets' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Collapse Allocation' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Toggle cash' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: 'Toggle unassigned' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Growth' })).not.toBeInTheDocument()
    expect(screen.queryByText('ALPHA · Alpha Fund')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Expand all' })); expect(screen.getByRole('button', { name: 'Growth' })).toBeInTheDocument()
    expect(screen.getByText('ALPHA · Alpha Fund')).toBeInTheDocument()
  })

  it.each([false, true])('creates a taxonomy from the visible toolbar, including an empty catalog (%s)', async (empty) => {
    const catalog = catalogFixture()
    if (empty) {
      catalog.taxonomies = []; catalog.taxonomy_nodes = []; catalog.taxonomy_assignments = []
      catalog.target_sets = []; catalog.target_set_lines = []; catalog.target_resolution = []
      api.getPortfolioTaxonomyCatalog.mockResolvedValue(catalog)
    }
    api.createPortfolioTaxonomy.mockResolvedValue({ taxonomy_id: 'new-taxonomy', name: 'Regions' })
    const user = userEvent.setup(); renderPage()
    const add = await screen.findByRole('button', { name: '+ Add taxonomy' })
    await user.click(add)
    const dialog = screen.getByRole('dialog', { name: 'New Taxonomy' })
    await user.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Regions')
    await user.click(within(dialog).getByRole('button', { name: 'Create Taxonomy' }))
    await waitFor(() => expect(api.createPortfolioTaxonomy).toHaveBeenCalledWith('3', { name: 'Regions', taxonomy_type: 'custom', purpose: null, root_allocation_basis: 'weight' }))
  })

  it('shows necessary explanations on demand through the shared info control', async () => {
    const user = userEvent.setup(); renderPage(); await ready()
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    expect(screen.queryByText(/Each parent’s basis controls/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^TAA target:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('A completely blank TAA level inherits SAA')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.queryByText(/Limits take effect/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Editing and saving:|^Taxonomy actions:|^Value:|^Exposure \/ NAV:|^SAA target:/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Concentration limit:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('Limits take effect from 2026-07-15')
    expect(screen.getByRole('button', { name: '+ Add taxonomy' })).toBeDisabled()
  })

  it('reports an old server contract without crashing or treating its configuration as empty', async () => {
    const oldCatalog = { ...catalogFixture(), taxonomy_configuration_version: undefined, target_resolution: undefined }
    api.getPortfolioTaxonomyCatalog.mockResolvedValue(oldCatalog)
    renderPage()
    expect(await screen.findByText(/The taxonomy page and server versions do not match/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(concentrationMocks.getConcentrationSettings).not.toHaveBeenCalled()
  })

  it('reports an old concentration contract without calling includes on a missing field', async () => {
    concentrationMocks.getConcentrationSettings.mockResolvedValue({ portfolio_id: '3', revision: 1, rules: [] })
    renderPage()
    expect(await screen.findByText(/The concentration settings and page versions do not match/)).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Classification and asset overview' })).toBeInTheDocument()
  })

  it('preserves unsaved configuration and the editing session when the language changes', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(<><TaxonomiesPage /><LanguageSwitch /></>, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    await ready(); await user.click(screen.getByRole('button', { name: 'Edit' }))
    await changeNumber('SAA target for Cash', '25')
    await user.click(screen.getByRole('button', { name: 'Switch language' }))
    expect(screen.getByRole('spinbutton', { name: 'SAA target for Cash' })).toHaveValue(25)
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '+ 添加分类' })).toBeDisabled()
    expect(api.getPortfolioTaxonomyCatalog).toHaveBeenCalledTimes(1)
    expect(api.savePortfolioTaxonomyTargetConfiguration).not.toHaveBeenCalled()
  })

  it('keeps derivative carrying value separate from FCN concentration exposure and inline limits', async () => {
    api.getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture({ rows: [holdingFixture(), holdingFixture({ line_id: 'fcn-holding', instrument_core: null, holding_category: 'derivatives', holding_kind: 'fcn', derivative_contract_id: 'fcn-1', derivative_contract: fcnContractFixture(), market_value_base: 50 })] }))
    concentrationMocks.getConcentration.mockResolvedValue({ scopes: [{ scope: 'fcn', taxonomy_id: null, enabled: true, rows: [{ entity_id: 'fcn-1', weight: .2, limit_weight: .1, status: 'breached', coverage: [] }] }], fcn_contracts: [] })
    const user = userEvent.setup(); renderPage(); await ready()
    const row = screen.getByText('Alpha FCN').closest('tr')!
    expect(within(row).getByText('20.00%')).toBeInTheDocument(); expect(within(row).getByText('10.00% !')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByRole('spinbutton', { name: 'Concentration limit for Alpha FCN' })).toBeInTheDocument()
    expect(screen.queryByRole('spinbutton', { name: 'SAA target for Alpha FCN' })).not.toBeInTheDocument()
  })
})
