import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfoliosPage from './pages/PortfoliosPage'

const apiMocks = vi.hoisted(() => ({
  SUPPORTED_PORTFOLIO_CURRENCIES: ['USD', 'HKD', 'CNY', 'EUR', 'GBP', 'CHF'],
  clearPortfolioApiCache: vi.fn(),
  copyPortfolio: vi.fn(),
  createPortfolio: vi.fn(),
  deletePortfolio: vi.fn(),
  getPortfolios: vi.fn(),
  getPortfolioMembers: vi.fn(),
  getPortfolioMemberCandidates: vi.fn(),
  reorderPortfolios: vi.fn(),
  updatePortfolioSettings: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
const sessionState = vi.hoisted(() => ({ user_id: 'test-manager', display_name: 'Test Manager', can_create: true, is_team_owner: false, local_unrestricted: false }))
vi.mock('./components/PortfolioSessionProvider', () => ({ usePortfolioSession: () => sessionState }))

describe('Portfolios rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionState.local_unrestricted = false
    sessionState.is_team_owner = false
    apiMocks.getPortfolioMembers.mockResolvedValue({ members: [] })
    apiMocks.getPortfolioMemberCandidates.mockResolvedValue({ members: [] })
    apiMocks.getPortfolios.mockResolvedValue([])
    apiMocks.createPortfolio.mockResolvedValue({
      portfolio_id: 'new-portfolio',
      portfolio_name: 'New Portfolio',
      base_currency: 'USD',
      inception_date: '2026-08-01',
      as_of_date: '2026-08-01',
      nav: 0,
      day_change_value: 0,
      day_change_pct: 0,
      securities_count: 0,
      sort_order: 0,
    })
  })

  it('shows the account name without exposing permission or recovery controls', async () => {
    sessionState.local_unrestricted = true
    sessionState.is_team_owner = true
    render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><PortfoliosPage /></MemoryRouter></LanguageProvider>)
    expect(await screen.findByText('Test Manager')).toBeInTheDocument()
    expect(screen.queryByText('本机全权限')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '恢复组合管理权限' })).not.toBeInTheDocument()
  })

  it('creates a portfolio through one accessible form with explicit inception', async () => {
    const user = userEvent.setup()
    render(
      <LanguageProvider enableDomTranslation={false}>
        <MemoryRouter initialEntries={['/portfolios']}>
          <Routes>
            <Route path="/portfolios" element={<PortfoliosPage />} />
            <Route path="/portfolios/:portfolioId/overview" element={<div>Portfolio overview</div>} />
          </Routes>
        </MemoryRouter>
      </LanguageProvider>,
    )

    await screen.findByText('No portfolios')
    await user.click(screen.getByRole('button', { name: '+ Create Portfolio' }))

    const dialog = screen.getByRole('dialog', { name: 'Create Portfolio' })
    await user.type(screen.getByLabelText('Portfolio Name'), 'New Portfolio')
    await user.selectOptions(screen.getByLabelText('Base Currency'), 'USD')
    await user.clear(screen.getByLabelText(/Inception Date/))
    await user.type(screen.getByLabelText(/Inception Date/), '2026-08-01')
    await user.click(screen.getByRole('button', { name: 'Create Portfolio' }))

    expect(dialog).toHaveTextContent('Opening balances, if any, must use this date.')
    await waitFor(() => {
      expect(apiMocks.createPortfolio).toHaveBeenCalledWith({
        name: 'New Portfolio',
        base_currency: 'USD',
        inception_date: '2026-08-01',
      })
    })
    expect(await screen.findByText('Portfolio overview')).toBeInTheDocument()
  })

  it('changes base currency from portfolio settings and explains the full recalculation', async () => {
    const portfolio = {
      portfolio_id: 'portfolio-a',
      portfolio_name: 'Portfolio A',
      access: { can_edit: true, can_manage: true },
      base_currency: 'USD',
      inception_date: '2026-01-01',
      as_of_date: '2026-09-01',
      nav: 100,
      day_change_value: 1,
      day_change_pct: 0.01,
      securities_count: 2,
      sort_order: 0,
    }
    apiMocks.getPortfolios.mockResolvedValue([portfolio])
    apiMocks.updatePortfolioSettings.mockResolvedValue({
      ...portfolio,
      base_currency: 'CNY',
      nav: null,
      day_change_value: null,
      day_change_pct: null,
    })

    const user = userEvent.setup()
    render(
      <LanguageProvider enableDomTranslation={false}>
        <MemoryRouter initialEntries={['/portfolios']}>
          <Routes>
            <Route path="/portfolios" element={<PortfoliosPage />} />
          </Routes>
        </MemoryRouter>
      </LanguageProvider>,
    )

    await screen.findByText('Portfolio A')
    expect(screen.queryByRole('region', { name: 'Portfolio access' })).not.toBeInTheDocument()
    expect(apiMocks.getPortfolioMembers).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Portfolio A actions' }))
    await user.click(screen.getByRole('button', { name: 'Portfolio Settings' }))
    expect(screen.getByRole('region', { name: 'Portfolio access' })).toBeInTheDocument()
    await waitFor(() => expect(apiMocks.getPortfolioMembers).toHaveBeenCalledWith('portfolio-a'))

    expect(screen.getByRole('dialog', { name: 'Portfolio Settings' })).toHaveTextContent(
      'Transactions keep their original currencies.',
    )
    await user.selectOptions(screen.getByLabelText('Base Currency'), 'CNY')
    await user.click(screen.getByRole('button', { name: 'Save Settings' }))

    await waitFor(() => {
      expect(apiMocks.updatePortfolioSettings).toHaveBeenCalledWith('portfolio-a', {
        base_currency: 'CNY',
      })
    })
    expect(await screen.findByText(/historical values are recalculating/i)).toHaveClass('investment-studio-notice-toast-success')
    expect(screen.getByText('Recalculating')).toBeInTheDocument()
  })

  it.each([false, true])('keeps deletion and copying unavailable to a non-manager with can_edit=%s', async (canEdit) => {
    apiMocks.getPortfolios.mockResolvedValue([{
      portfolio_id: 'portfolio-a', portfolio_name: 'Portfolio A',
      access: { can_read: true, can_edit: canEdit, can_manage: false },
      base_currency: 'USD', inception_date: '2026-01-01', as_of_date: '2026-09-01',
      nav: 100, day_change_value: 1, day_change_pct: 0.01, securities_count: 2, sort_order: 0,
    }])
    const user = userEvent.setup()
    render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><PortfoliosPage /></MemoryRouter></LanguageProvider>)
    await user.click(await screen.findByRole('button', { name: 'Portfolio A actions' }))
    expect(screen.getByRole('button', { name: 'Copy Portfolio' })).toBeDisabled()
    const deleteButton = screen.getByRole('button', { name: 'Delete Portfolio' })
    expect(deleteButton).toBeDisabled()
    await user.click(deleteButton)
    expect(screen.queryByRole('dialog', { name: 'Delete Portfolio' })).not.toBeInTheDocument()
    expect(apiMocks.deletePortfolio).not.toHaveBeenCalled()
  })
})

const editablePortfolio = {
  portfolio_id: 'portfolio-a', portfolio_name: 'Portfolio A',
  access: { can_read: true, can_edit: true, can_manage: true },
  base_currency: 'USD', inception_date: '2026-01-01', as_of_date: '2026-09-01',
  nav: 60, day_change_value: 10, day_change_pct: 0.1, day_return_capital: 100, securities_count: 2, sort_order: 0,
}

it('renames without changing currency, retains controls, and supports cancel and failure', async () => {
  apiMocks.getPortfolios.mockResolvedValue([editablePortfolio])
  apiMocks.updatePortfolioSettings.mockRejectedValueOnce(new Error('Please retry')).mockResolvedValue({ ...editablePortfolio, portfolio_name: 'Renamed Portfolio' })
  const user = userEvent.setup()
  render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><PortfoliosPage /></MemoryRouter></LanguageProvider>)
  await user.click(await screen.findByRole('button', { name: 'Portfolio A actions' }))
  await user.click(screen.getByRole('button', { name: 'Rename Portfolio' }))
  await user.clear(screen.getByLabelText('Portfolio Name'))
  expect(screen.getByRole('button', { name: 'Save Settings' })).toBeDisabled()
  await user.type(screen.getByLabelText('Portfolio Name'), '  Renamed Portfolio  ')
  await user.click(screen.getByRole('button', { name: 'Save Settings' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Please retry')
  expect(screen.getByLabelText('Portfolio Name')).toHaveValue('  Renamed Portfolio  ')
  await user.click(screen.getByRole('button', { name: 'Save Settings' }))
  await waitFor(() => expect(apiMocks.updatePortfolioSettings).toHaveBeenLastCalledWith('portfolio-a', { name: 'Renamed Portfolio' }))
  await user.click(await screen.findByRole('button', { name: 'Renamed Portfolio actions' }))
  await user.click(screen.getByRole('button', { name: 'Rename Portfolio' }))
  await user.type(screen.getByLabelText('Portfolio Name'), ' unsaved')
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog', { name: 'Portfolio Settings' })).not.toBeInTheDocument()
  expect(screen.getByText('Renamed Portfolio')).toBeInTheDocument()
})

it('persists successive ordering changes and restores order on failed save', async () => {
  apiMocks.getPortfolios.mockResolvedValue([editablePortfolio, { ...editablePortfolio, portfolio_id: 'portfolio-b', portfolio_name: 'Portfolio B' }])
  apiMocks.reorderPortfolios.mockResolvedValueOnce([]).mockRejectedValueOnce(new Error('Cannot save order'))
  const user = userEvent.setup()
  render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><PortfoliosPage /></MemoryRouter></LanguageProvider>)
  await user.click(await screen.findByRole('button', { name: 'Move Portfolio B up' }))
  await waitFor(() => expect(apiMocks.reorderPortfolios).toHaveBeenLastCalledWith(['portfolio-b', 'portfolio-a']))
  await user.click(screen.getByRole('button', { name: 'Move Portfolio A up' }))
  expect(await screen.findByText('Cannot save order')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Move Portfolio B up' })).toBeDisabled()
})

it('uses beginning capital for aggregate returns and keeps different valuation dates separate', async () => {
  apiMocks.getPortfolios.mockResolvedValue([editablePortfolio, { ...editablePortfolio, portfolio_id: 'portfolio-b', portfolio_name: 'Portfolio B' }])
  const view = render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><PortfoliosPage /></MemoryRouter></LanguageProvider>)
  expect(await screen.findByText('+$20.00 (10.00%)')).toBeInTheDocument()
  view.unmount()
  apiMocks.getPortfolios.mockResolvedValue([editablePortfolio, { ...editablePortfolio, portfolio_id: 'portfolio-b', portfolio_name: 'Portfolio B', as_of_date: '2026-08-31' }])
  render(<LanguageProvider enableDomTranslation={false}><MemoryRouter><PortfoliosPage /></MemoryRouter></LanguageProvider>)
  expect(await screen.findByText('Different valuation dates')).toBeInTheDocument()
  expect(screen.queryByText('+$20.00 (10.00%)')).not.toBeInTheDocument()
})
