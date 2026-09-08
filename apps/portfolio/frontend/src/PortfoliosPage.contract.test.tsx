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
    expect(screen.queryByRole('region', { name: '组合权限' })).not.toBeInTheDocument()
    expect(apiMocks.getPortfolioMembers).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Portfolio A actions' }))
    await user.click(screen.getByRole('button', { name: 'Portfolio Settings' }))
    expect(screen.getByRole('region', { name: '组合权限' })).toBeInTheDocument()
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
})
