import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfoliosPage from './pages/PortfoliosPage'

const apiMocks = vi.hoisted(() => ({
  SUPPORTED_PORTFOLIO_CURRENCIES: ['USD', 'HKD', 'CNY', 'EUR', 'GBP', 'CHF'],
  copyPortfolio: vi.fn(),
  createPortfolio: vi.fn(),
  deletePortfolio: vi.fn(),
  getPortfolios: vi.fn(),
  reorderPortfolios: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)

describe('Portfolios rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

  it('creates a portfolio through one accessible form with explicit inception', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/portfolios']}>
        <Routes>
          <Route path="/portfolios" element={<PortfoliosPage />} />
          <Route path="/portfolios/:portfolioId/overview" element={<div>Portfolio overview</div>} />
        </Routes>
      </MemoryRouter>,
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
})
