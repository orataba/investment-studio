import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import * as tableExport from '../../../../../packages/ui/src/tableExport'
import type { PortfolioResearchRunRecord } from '../lib/api'
import { solutionTreeExportRows, type ResearchSolutionTreeData, type ResearchSolutionTreeRow } from '../lib/researchSolutionTree'
import ResearchSolutionTree from './ResearchSolutionTree'

function row(id: string, parent: ResearchSolutionTreeRow | null, kind: ResearchSolutionTreeRow['row_kind'], overrides: Partial<ResearchSolutionTreeRow> = {}): ResearchSolutionTreeRow {
  return { row_id: id, parent_row_id: parent?.row_id ?? null, row_kind: kind, member_type: kind, member_id: id, label: id,
    depth: parent ? parent.depth + 1 : 0, path: [...(parent?.path ?? []), id], target_risk_share: .5, solved_risk_share: .5,
    current_value_base: 100, current_weight: .1, target_value_base: 120, target_weight: .12,
    rebalance_value_base: 20, trade_constraint: 'adjustable', risk_model_status: 'modeled',
    execution_status: 'ready', execution_note: null, min_weight: null, max_weight: null, bound_status: null, ...overrides }
}

const root = row('Saved portfolio', null, 'portfolio')
const a = row('Saved category', root, 'category', { max_weight: .2, bound_status: 'max' })
const nested = row('Saved nested category', a, 'category')
const x = row('HKD instrument', nested, 'instrument')
const y = row('Second instrument', a, 'instrument', { rebalance_value_base: -30, target_value_base: 70 })
const cash = row('Cash', root, 'cash', { target_risk_share: null, solved_risk_share: null, rebalance_value_base: 10 })
const derivatives = row('Derivatives', root, 'derivatives', { current_value_base: -50, current_weight: -.05, target_value_base: -50, target_weight: -.05, target_risk_share: null, solved_risk_share: null, trade_constraint: 'no_trade', execution_status: 'no_trade', risk_model_status: 'excluded', rebalance_value_base: 0 })
const tree: ResearchSolutionTreeData = { schema_version: 2, as_of_date: '2026-06-30', base_currency: 'USD', portfolio_nav: 1000,
  capital_weight_basis: 'portfolio_nav', risk_attribution_scope: 'portfolio', hierarchy_status: 'complete', configuration_captured_at: '2026-06-30T12:00:00Z',
  rows: [root, a, nested, x, y, cash, derivatives] }
const run = { research_run_id: 'saved-1', as_of_date: '2026-06-30', reliability_state: 'stale',
  detail: { solver_version: 'global_leaf_scalar_targets_v4', solution_tree: tree, solve_event: { target_status: 'constrained_solution', execution_ready: false } },
} as PortfolioResearchRunRecord

function renderTree(record = run) {
  return render(<LanguageProvider enableDomTranslation={false}><ResearchSolutionTree run={record} /></LanguageProvider>)
}

describe('Research solution tree', () => {
  it('shows the saved full hierarchy, report currency, signed trades and no-trade semantics', () => {
    renderTree()
    expect(screen.getByRole('columnheader', { name: 'Carrying Amount (USD)' })).toBeInTheDocument()
    const instrument = screen.getByText('HKD instrument').closest('tr')!
    expect(instrument).toHaveAttribute('data-tree-level', 'item')
    expect(within(instrument).getByText('$100.00')).toBeInTheDocument()
    expect(within(instrument).getByText('+$20.00')).toBeInTheDocument()
    expect(within(instrument).getByText('10.00%')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Current Weight' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Target Weight' })).toBeInTheDocument()
    expect(screen.queryByText('Exposure / NAV')).not.toBeInTheDocument()
    expect(screen.queryByText('Buy')).not.toBeInTheDocument()
    expect(within(screen.getByText('Second instrument').closest('tr')!).getByText('−$30.00')).toBeInTheDocument()
    expect(screen.queryByText('Cash change')).not.toBeInTheDocument()
    const derivative = screen.getByText('Derivatives').closest('tr')!
    expect(within(derivative).getByText('-$50.00')).toBeInTheDocument()
    expect(within(derivative).getByText('$0.00')).toBeInTheDocument()
    expect(within(derivative).getAllByText('No trade')).toHaveLength(1)
    expect(within(derivative).getAllByText('-5.00%')).toHaveLength(2)
    expect(screen.getByText('Weight bound')).toBeInTheDocument()
    // Global stale/convergence warnings and the date are owned by the page.
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByText('2026-06-30')).not.toBeInTheDocument()
  })

  it('collapses to first-level categories and exports all hidden descendants as numbers', async () => {
    const user = userEvent.setup()
    const exportSpy = vi.spyOn(tableExport, 'downloadXlsx').mockImplementation(() => {})
    renderTree()
    await user.click(screen.getByRole('button', { name: 'Collapse all' }))
    expect(screen.getByText('Saved category')).toBeInTheDocument()
    expect(screen.getByText('Cash')).toBeInTheDocument()
    expect(screen.getByText('Derivatives')).toBeInTheDocument()
    expect(screen.queryByText('Saved nested category')).not.toBeInTheDocument()
    expect(screen.queryByText('HKD instrument')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Export Excel' }))
    const exported = exportSpy.mock.calls[0][1]
    expect(exported).toHaveLength(tree.rows.length + 1)
    const instrument = exported.find((cells) => cells[2] === 'HKD instrument')!
    expect(instrument[5]).toBe(100)
    expect(instrument[6]).toBe(10)
    expect(instrument[8]).toBe(20)
    expect(instrument[12]).toBe('2026-06-30')
    expect(instrument[13]).toBe('USD')
    expect(instrument[18]).toBe('stale / constrained_solution')
    expect(exported[0]).not.toContain('Direction')
    expect(exported[0]).not.toContain('Exposure status')
    await user.click(screen.getByRole('button', { name: 'Expand all' }))
    expect(screen.getByText('HKD instrument')).toBeInTheDocument()
    exportSpy.mockRestore()
  })

  it('discloses archived scope and risk basis instead of claiming a new global NAV calculation', async () => {
    const user = userEvent.setup()
    const legacy = { ...run, detail: { ...run.detail!, solver_version: null, solution_tree: { ...tree, portfolio_nav: null, capital_weight_basis: 'saved_scope' as const } } }
    renderTree(legacy)
    await user.click(screen.getByRole('button', { name: /^Weight basis:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('current weight is unavailable')
    await user.click(screen.getByRole('button', { name: /^Risk contribution basis:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('targets with missing intermediate budgets remain blank')
    const exported = solutionTreeExportRows(legacy.detail.solution_tree, 'legacy', 'stale', false)
    expect(exported[0][7]).toBe('Target weight (%)')
    expect(exported[1][19]).toContain('target weight uses Saved scope capital')
  })

  it('writes a real OOXML workbook with numeric amount and percentage cells', () => {
    const workbook = tableExport.buildXlsxWorkbook(solutionTreeExportRows(tree, 'run', 'current', false), 'Solution')
    expect(Array.from(workbook.slice(0, 4))).toEqual([0x50, 0x4b, 0x03, 0x04])
    const xml = new TextDecoder().decode(workbook)
    expect(xml).toContain('xl/worksheets/sheet1.xml')
    expect(xml).toContain('<c r="F5"><v>100</v></c>')
    expect(xml).toContain('<c r="G5"><v>10</v></c>')
    expect(xml).toContain('<c r="I5"><v>20</v></c>')
  })

  it('keeps the selected-scope denominator warning for legacy runs without a solver version', async () => {
    const user = userEvent.setup()
    renderTree({ ...run, detail: { ...run.detail!, solver_version: null, solution_tree: { ...tree, risk_attribution_scope: 'selected_research_scope' } } })
    await user.click(screen.getByRole('button', { name: /^Risk contribution basis:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('solved RC retains that scope’s risk denominator')
    expect(screen.getByRole('tooltip')).toHaveTextContent('cannot be directly subtracted')
  })

  it('retains original archived amounts when currency was not saved without borrowing today’s currency', async () => {
    const user = userEvent.setup()
    renderTree({ ...run, detail: { ...run.detail!, solution_tree: { ...tree, base_currency: null } } })
    const instrument = screen.getByText('HKD instrument').closest('tr')!
    expect(within(instrument).getByText('100.00')).toBeInTheDocument()
    expect(within(instrument).getByText('+20.00')).toBeInTheDocument()
    expect(within(instrument).queryByText('Buy')).not.toBeInTheDocument()
    expect(within(instrument).queryByText(/\$/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Reporting currency not recorded:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('original numeric values without conversion')
  })
})
