import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ConcentrationPanel from './ConcentrationPanel'
import ConcentrationAlerts from './ConcentrationAlerts'
import { renderPortfolioPage } from '../test/renderPortfolioPage'
import { PortfolioAccessContext } from './PortfolioAccessProvider'
import type { PortfolioAccess } from '../lib/api'
import type { ConcentrationResponse, ConcentrationRow, ConcentrationSettingsRecord } from '../lib/concentrationApi'

const mocks = vi.hoisted(() => ({ getConcentration: vi.fn(), getConcentrationSettings: vi.fn(), saveConcentrationSettings: vi.fn() }))
vi.mock('../lib/concentrationApi', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/concentrationApi')>(), ...mocks }))

const row = (overrides: Partial<ConcentrationRow> = {}): ConcentrationRow => ({
  entity_id: 'stock-a', name: 'Stock A', depth: 0, parent_entity_id: null, exposure_base: 100,
  known_exposure_base: 100, lower_bound_weight: null, weight: .1, security_exposure_base: 100, fcn_exposure_base: 0,
  limit_weight: null, status: 'unconfigured', headroom_weight: null,
  sources: [{ source_id: 'position-a', title: 'Stock A in account 1', instrument_id: 'stock-a', amount_base: 100 }], coverage: [], ...overrides,
})
function projection(): ConcentrationResponse {
  return { portfolio_id: '3', as_of_date: '2026-09-08', base_currency: 'USD', nav: 1000, weight_basis: 'portfolio_nav', valuation_basis: 'operating_book', excluded_option_positions: 0, status: 'complete', settings_revision: 4,
    scopes: [
      { scope: 'security', taxonomy_id: null, name: 'Securities', enabled: true, rows: [row()], status: 'complete', coverage: [] },
      { scope: 'fcn', taxonomy_id: null, name: 'FCN', enabled: true, rows: [row({ entity_id: 'contract-a', name: 'FCN A', security_exposure_base: 0, fcn_exposure_base: 100 })], status: 'complete', coverage: [] },
      { scope: 'taxonomy', taxonomy_id: 'industry', name: 'Industry', enabled: true, rows: [row({ entity_id: 'tech', name: 'Technology', weight: .2, exposure_base: 200, fcn_exposure_base: 100, status: 'breached', limit_weight: .15, headroom_weight: -.05 }), row({ entity_id: 'software', parent_entity_id: 'tech', name: 'Software', depth: 1 })], status: 'complete', coverage: [] },
    ], coverage: [], sources: [], fcn_contracts: [{ contract_id: 'contract-a', name: 'FCN A', underlyings: [{ instrument_id: 'stock-a', name: 'Stock A' }, { instrument_id: 'stock-b', name: 'Stock B' }], allocation: { contract_id: 'contract-a', method: 'equal', weights: [] } }],
  }
}
function settings(): ConcentrationSettingsRecord {
  return { portfolio_id: '3', revision: 4, latest_revision: 6, effective_from: '2026-09-01', enabled_taxonomy_ids: ['industry'], limits: [
    { scope: 'fcn', taxonomy_id: null, entity_id: 'contract-a', limit_weight: .2 },
  ], fcn_allocations: [] }
}
function renderPanel(canEdit = true) {
  const access: PortfolioAccess = { portfolio_id: '3', team_id: 'team', role: canEdit ? 'editor' : 'viewer', can_read: true, can_edit: canEdit, can_manage: false }
  return renderPortfolioPage(<PortfolioAccessContext.Provider value={access}><ConcentrationPanel portfolioId="3" asOfDate="2026-09-08" /></PortfolioAccessContext.Provider>, '/portfolios/3/risk', '/portfolios/:portfolioId/risk')
}
function renderAlerts() {
  return renderPortfolioPage(<ConcentrationAlerts portfolioId="3" asOfDate="2026-09-08" />, '/portfolios/3/overview', '/portfolios/:portfolioId/overview')
}

describe('Risk concentration and Overview alerts', () => {
  beforeEach(() => {
    vi.clearAllMocks(); localStorage.clear()
    mocks.getConcentration.mockResolvedValue(projection())
    mocks.getConcentrationSettings.mockResolvedValue(settings())
    mocks.saveConcentrationSettings.mockResolvedValue({ ...settings(), revision: 7, latest_revision: 7 })
  })

  it('browses exposure scopes and sources without writes or a limits management drawer', async () => {
    const user = userEvent.setup()
    renderPanel()
    expect(await screen.findByRole('table', { name: 'Concentration exposures and limits' })).toHaveTextContent('10.00%')
    expect(screen.getByRole('columnheader', { name: 'Exposure Ratio' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Current Weight' })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Concentration Limit' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Current / NAV' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Portfolio limits' })).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Watch' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Concentration basis:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('Option exposure is not modeled')
    expect(screen.getByRole('tooltip')).toHaveTextContent('must not be read as zero')
    await user.click(screen.getByRole('button', { name: /^Limit headroom basis:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('expressed in percentage points')
    expect(screen.getByRole('tooltip')).toHaveTextContent('not an available cash amount')
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

  it('shows incomplete known lower-bound exposure and preserves a proven breach', async () => {
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

  it('saves FCN allocation inline using displayed-date settings and latest revision without changing caps', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.selectOptions(await screen.findByRole('combobox'), 'fcn')
    await user.click(screen.getByRole('button', { name: 'Edit FCN principal allocation' }))
    expect(mocks.getConcentrationSettings).toHaveBeenCalledWith('3', '2026-09-08')
    await user.selectOptions(await screen.findByRole('combobox', { name: 'FCN A principal allocation' }), 'custom')
    const a = screen.getByRole('spinbutton', { name: 'FCN A / Stock A %' })
    const b = screen.getByRole('spinbutton', { name: 'FCN A / Stock B %' })
    await user.clear(a); await user.type(a, '75')
    await user.clear(b); await user.type(b, '25')
    await user.click(screen.getByRole('button', { name: 'Save principal allocation' }))
    await waitFor(() => expect(mocks.saveConcentrationSettings).toHaveBeenCalledOnce())
    expect(mocks.saveConcentrationSettings).toHaveBeenCalledWith('3', {
      expected_revision: 6, effective_from: '2026-09-08', limits: settings().limits, enabled_taxonomy_ids: ['industry'],
      fcn_allocations: [{ contract_id: 'contract-a', method: 'custom', weights: [{ instrument_id: 'stock-a', weight: .75 }, { instrument_id: 'stock-b', weight: .25 }] }],
    })
    await waitFor(() => expect(mocks.getConcentration).toHaveBeenCalledTimes(2))
  })

  it('retains an allocation draft after a conflict and does not silently retry it', async () => {
    const user = userEvent.setup()
    mocks.saveConcentrationSettings.mockRejectedValueOnce(new Error('Settings changed; reload before saving.'))
    renderPanel()
    await user.selectOptions(await screen.findByRole('combobox'), 'fcn')
    await user.click(screen.getByRole('button', { name: 'Edit FCN principal allocation' }))
    await user.selectOptions(await screen.findByRole('combobox', { name: 'FCN A principal allocation' }), 'custom')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Concentration taxonomy' }), 'taxonomy:industry')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Concentration taxonomy' }), 'fcn')
    expect(screen.getByRole('combobox', { name: 'FCN A principal allocation' })).toHaveValue('custom')
    await user.click(screen.getByRole('button', { name: 'Save principal allocation' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Settings changed')
    expect(screen.getByRole('spinbutton', { name: 'FCN A / Stock A %' })).toHaveValue(50)
    expect(mocks.saveConcentrationSettings).toHaveBeenCalledOnce()
  })

  it('distinguishes an empty FCN allocation input from an explicit zero', async () => {
    const user = userEvent.setup(); renderPanel()
    await user.selectOptions(await screen.findByRole('combobox'), 'fcn')
    await user.click(screen.getByRole('button', { name: 'Edit FCN principal allocation' }))
    await user.selectOptions(await screen.findByRole('combobox', { name: 'FCN A principal allocation' }), 'custom')
    const a = screen.getByRole('spinbutton', { name: 'FCN A / Stock A %' })
    const b = screen.getByRole('spinbutton', { name: 'FCN A / Stock B %' })
    await user.clear(a); await user.clear(b); await user.type(b, '100')
    expect(screen.getByRole('button', { name: 'Save principal allocation' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('a blank value does not mean 0%')
    await user.type(a, '0'); await user.click(screen.getByRole('button', { name: 'Save principal allocation' }))
    await waitFor(() => expect(mocks.saveConcentrationSettings).toHaveBeenCalledOnce())
    expect(mocks.saveConcentrationSettings.mock.calls[0][1].fcn_allocations[0].weights).toEqual([
      { instrument_id: 'stock-a', weight: 0 }, { instrument_id: 'stock-b', weight: 1 },
    ])
  })

  it('keeps monitoring off separate from saved caps and viewer access', async () => {
    const data = projection(); data.scopes[2].enabled = false
    data.scopes[2].rows[0].status = 'unconfigured'
    mocks.getConcentration.mockResolvedValue(data)
    const user = userEvent.setup()
    renderPanel(false)
    await user.selectOptions(await screen.findByRole('combobox'), 'taxonomy:industry')
    expect(screen.getByRole('table')).toHaveTextContent('15.00%')
    expect(screen.getByRole('table')).toHaveTextContent('Alerts off')
    await user.selectOptions(screen.getByRole('combobox'), 'fcn')
    expect(screen.queryByRole('button', { name: 'Edit FCN principal allocation' })).not.toBeInTheDocument()
  })

  it('shows only active breaches and unassessable configured caps in the Overview summary', async () => {
    const data = projection()
    data.scopes[0].rows = [row({ weight: null, exposure_base: null, lower_bound_weight: .1, limit_weight: .3, status: 'unavailable' })]
    data.scopes[1].rows[0] = row({ name: 'Within FCN', limit_weight: .2, status: 'within' })
    mocks.getConcentration.mockResolvedValue(data)
    renderAlerts()
    const summary = await screen.findByRole('complementary', { name: 'Concentration alerts' })
    expect(summary).toHaveTextContent('Technology')
    expect(summary).not.toHaveTextContent('Within FCN')
    expect(summary).toHaveTextContent('1 configured limits cannot be assessed')
    expect(screen.getByRole('link', { name: 'View concentration' })).toHaveAttribute('href', '/portfolios/3/risk?concentration_date=2026-09-08#concentration')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('does not warn on disabled taxonomy caps or manufacture an all-clear message', async () => {
    const data = projection(); data.scopes[2].enabled = false
    mocks.getConcentration.mockResolvedValue(data)
    renderAlerts()
    await waitFor(() => expect(mocks.getConcentration).toHaveBeenCalledOnce())
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
  })

  it('discloses a calculation failure instead of reporting no breaches', async () => {
    mocks.getConcentration.mockRejectedValue(new Error('Account-level holdings are unavailable for concentration. Refresh holdings before retrying.'))
    renderAlerts()
    expect(await screen.findByRole('complementary')).toHaveTextContent('Concentration unavailable')
  })
})
