import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ConcentrationPanel from './ConcentrationPanel'
import { ConcentrationSettings } from './ConcentrationSettings'
import { renderPortfolioPage } from '../test/renderPortfolioPage'
import { PortfolioAccessContext } from './PortfolioAccessProvider'
import type { PortfolioAccess } from '../lib/api'
import type { ConcentrationResponse, ConcentrationRow, ConcentrationSettingsRecord } from '../lib/concentrationApi'

const mocks = vi.hoisted(() => ({ getConcentration: vi.fn(), getConcentrationSettings: vi.fn(), saveConcentrationSettings: vi.fn() }))
vi.mock('../lib/concentrationApi', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/concentrationApi')>(), ...mocks }))

const row = (overrides: Partial<ConcentrationRow> = {}): ConcentrationRow => ({
  entity_id: 'stock-a', name: 'Stock A', depth: 0, parent_entity_id: null, exposure_base: 100,
  known_exposure_base: 100, lower_bound_weight: .1, weight: .1, security_exposure_base: 100, fcn_exposure_base: 0,
  watch_weight: null, limit_weight: null, status: 'unconfigured', headroom_weight: null, rule_id: null,
  sources: [{ source_id: 'position-a', title: 'Stock A in account 1', instrument_id: 'stock-a', amount_base: 100 }], coverage: [], ...overrides,
})
function projection(): ConcentrationResponse {
  return { portfolio_id: '3', as_of_date: '2026-09-08', base_currency: 'USD', nav: 1000, weight_basis: 'portfolio_nav', status: 'complete', settings_revision: 4,
    scopes: [
      { scope: 'security', taxonomy_id: null, name: 'Securities', rows: [row()], status: 'complete', coverage: [] },
      { scope: 'fcn', taxonomy_id: null, name: 'FCN', rows: [row({ entity_id: 'contract-a', name: 'FCN A', security_exposure_base: 0, fcn_exposure_base: 100 })], status: 'complete', coverage: [] },
      { scope: 'taxonomy', taxonomy_id: 'industry', name: 'Industry', rows: [row({ entity_id: 'tech', name: 'Technology', weight: .2, exposure_base: 200, fcn_exposure_base: 100, status: 'breached', limit_weight: .15, headroom_weight: -.05 }), row({ entity_id: 'software', parent_entity_id: 'tech', name: 'Software', depth: 1 })], status: 'complete', coverage: [] },
    ], coverage: [], sources: [], fcn_contracts: [{ contract_id: 'contract-a', name: 'FCN A', underlyings: [{ instrument_id: 'stock-a', name: 'Stock A' }, { instrument_id: 'stock-b', name: 'Stock B' }], allocation: { contract_id: 'contract-a', method: 'equal', weights: [{ instrument_id: 'stock-a', weight: .5 }, { instrument_id: 'stock-b', weight: .5 }] } }],
  }
}
function settings(): ConcentrationSettingsRecord {
  return { portfolio_id: '3', revision: 4, effective_from: '2026-09-01', rules: [
    { rule_id: 'fcn-rule', scope: 'fcn', taxonomy_id: null, entity_id: null, enabled: true, watch_weight: .1, limit_weight: .2 },
  ], fcn_allocations: [] }
}
function renderPanel(canEdit = true) {
  const access: PortfolioAccess = { portfolio_id: '3', team_id: 'team', role: canEdit ? 'editor' : 'viewer', can_read: true, can_edit: canEdit, can_manage: false }
  return renderPortfolioPage(<PortfolioAccessContext.Provider value={access}><ConcentrationPanel portfolioId="3" asOfDate="2026-09-08" /></PortfolioAccessContext.Provider>, '/portfolios/3/risk', '/portfolios/:portfolioId/risk')
}

describe('Concentration panel and settings', () => {
  beforeEach(() => {
    vi.clearAllMocks(); localStorage.clear()
    mocks.getConcentration.mockResolvedValue(projection())
    mocks.getConcentrationSettings.mockResolvedValue(settings())
    mocks.saveConcentrationSettings.mockResolvedValue({ ...settings(), revision: 5 })
  })

  it('switches observation scopes without writes, expands taxonomy children and shows source details', async () => {
    const user = userEvent.setup()
    renderPanel()
    expect(await screen.findByRole('table', { name: 'Concentration exposures and limits' })).toHaveTextContent('10.00%')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Concentration taxonomy' }), 'taxonomy:industry')
    const table = screen.getByRole('table')
    expect(table).toHaveTextContent('Technology')
    expect(table).toHaveTextContent('Over limit')
    expect(table).not.toHaveTextContent('Software')
    await user.click(screen.getByRole('button', { name: 'Expand Technology' }))
    expect(table).toHaveTextContent('Software')
    await user.click(screen.getByRole('button', { name: 'Technology' }))
    expect(await screen.findByRole('dialog', { name: 'Technology' })).toHaveTextContent('Stock A in account 1')
    expect(mocks.getConcentration).toHaveBeenCalledTimes(1)
    expect(mocks.saveConcentrationSettings).not.toHaveBeenCalled()
  })

  it('discloses known lower-bound exposure without reporting an incomplete figure as current weight', async () => {
    const data = projection()
    data.status = 'partial'; data.scopes[0].status = 'partial'
    data.scopes[0].rows = [row({ exposure_base: null, weight: null, known_exposure_base: 100, lower_bound_weight: .1, limit_weight: .09, status: 'breached', coverage: ['Missing account valuation.'] })]
    mocks.getConcentration.mockResolvedValue(data)
    renderPanel()
    const table = await screen.findByRole('table')
    expect(table).toHaveTextContent('≥ 10.00%')
    expect(table).toHaveTextContent('Over limit')
    expect(screen.getByText('Incomplete coverage')).toBeInTheDocument()
  })

  it('saves percentages as ratios, preserves other scope rules, and reloads the panel after a successful save', async () => {
    const user = userEvent.setup()
    renderPanel()
    await screen.findByRole('table')
    await user.click(screen.getByRole('button', { name: 'Concentration settings' }))
    const dialog = await screen.findByRole('dialog', { name: 'Concentration settings' })
    await user.click(await within(dialog).findByRole('checkbox', { name: 'Enable concentration limits' }))
    await user.type(within(dialog).getByRole('spinbutton', { name: 'Default watch' }), '12')
    await user.type(within(dialog).getByRole('spinbutton', { name: 'Default limit' }), '20')
    await user.click(within(dialog).getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(mocks.saveConcentrationSettings).toHaveBeenCalledTimes(1))
    const [portfolioId, payload] = mocks.saveConcentrationSettings.mock.calls[0]
    expect(portfolioId).toBe('3')
    expect(payload.expected_revision).toBe(4)
    expect(payload.rules).toEqual(expect.arrayContaining([settings().rules[0], expect.objectContaining({ scope: 'security', entity_id: null, enabled: true, watch_weight: .12, limit_weight: .2 })]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(mocks.getConcentration).toHaveBeenCalledTimes(3))
  })

  it('keeps conflicting draft inputs visible and does not silently overwrite or resubmit', async () => {
    const user = userEvent.setup()
    mocks.saveConcentrationSettings.mockRejectedValueOnce(new Error('Settings changed; reload before saving.'))
    renderPanel()
    await user.click(screen.getByRole('button', { name: 'Concentration settings' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(await within(dialog).findByRole('spinbutton', { name: 'Default limit' }), '25')
    await user.click(within(dialog).getByRole('button', { name: 'Save settings' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Settings changed')
    expect(within(dialog).getByRole('spinbutton', { name: 'Default limit' })).toHaveValue(25)
    expect(mocks.saveConcentrationSettings).toHaveBeenCalledTimes(1)
  })

  it('rejects FCN custom allocation totals that are not 100 percent', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: 'Concentration settings' }))
    const dialog = await screen.findByRole('dialog')
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: 'Allocation method · FCN A' }), 'custom')
    const input = within(dialog).getByRole('spinbutton', { name: 'FCN A · Stock A' })
    await user.clear(input); await user.type(input, '60')
    await user.click(within(dialog).getByRole('button', { name: 'Save settings' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('must sum to 100%')
    expect(mocks.saveConcentrationSettings).not.toHaveBeenCalled()
  })

  it('restricts taxonomy embedding to its own scope and preserves read-only permissions', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(<ConcentrationSettings portfolioId="3" taxonomyId="industry" canEdit={false} />, '/portfolios/3/taxonomies', '/portfolios/:portfolioId/taxonomies')
    await user.click(screen.getByRole('button', { name: 'View concentration settings' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('checkbox', { name: 'Enable concentration limits' })).toBeDisabled()
    expect(within(dialog).getByRole('combobox', { name: 'Observe' })).toHaveValue('taxonomy:industry')
    expect(within(dialog).queryByRole('option', { name: 'Direct securities' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Save settings' })).not.toBeInTheDocument()
    expect(mocks.saveConcentrationSettings).not.toHaveBeenCalled()
  })

  it('clears explicit weights when an FCN changes back to equal allocation', async () => {
    const user = userEvent.setup()
    const stored = settings()
    stored.fcn_allocations = [{ contract_id: 'contract-a', method: 'custom', weights: [{ instrument_id: 'stock-a', weight: .4 }, { instrument_id: 'stock-b', weight: .6 }] }]
    mocks.getConcentrationSettings.mockResolvedValue(stored)
    renderPanel()
    await user.click(screen.getByRole('button', { name: 'Concentration settings' }))
    const dialog = await screen.findByRole('dialog')
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: 'Allocation method · FCN A' }), 'equal')
    await user.click(within(dialog).getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(mocks.saveConcentrationSettings).toHaveBeenCalledTimes(1))
    expect(mocks.saveConcentrationSettings.mock.calls[0][1].fcn_allocations).toEqual([{ contract_id: 'contract-a', method: 'equal', weights: [] }])
  })

  it('explains unavailable account-level holdings in Chinese in both the panel and settings', async () => {
    localStorage.setItem('investment_studio.language', 'zh-Hans')
    mocks.getConcentration.mockRejectedValue(new Error('Account-level holdings are unavailable for concentration. Refresh holdings before retrying.'))
    const user = userEvent.setup()
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('集中度计算所需的账户持仓明细暂不可用，请刷新持仓后重试。')
    await user.click(screen.getByRole('button', { name: '集中度设置' }))
    const dialog = await screen.findByRole('dialog', { name: '集中度设置' })
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('集中度计算所需的账户持仓明细暂不可用，请刷新持仓后重试。')
  })
})
