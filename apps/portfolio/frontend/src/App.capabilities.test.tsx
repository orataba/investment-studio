import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const apiMocks = vi.hoisted(() => ({ getPortfolioBootstrap: vi.fn() }))
vi.mock('./lib/bootstrap', () => apiMocks)
vi.mock('./lib/api', () => ({ clearPortfolioApiCache: vi.fn() }))
const bootstrap = (enabled: boolean) => ({ user_id: 'alice', session_id: 'a', capabilities: { research_enabled: enabled }, access: { user_id: 'alice', portfolio_id: 'private', can_read: true } })
vi.mock('./pages/ResearchPage', () => ({ default: () => <p>Private research</p> }))
vi.mock('./pages/OverviewPage', () => ({ default: () => <p>Portfolio overview</p> }))

function CurrentPath() {
  return <span data-testid="current-path">{useLocation().pathname}</span>
}

function renderResearchLink() {
  return render(
    <MemoryRouter initialEntries={['/portfolios/private/research']}>
      <App />
      <CurrentPath />
    </MemoryRouter>,
  )
}

describe('Portfolio deployment capabilities', () => {
  beforeEach(() => vi.clearAllMocks())

  it.each([false, true])('uses runtime research enablement: %s', async (enabled) => {
    apiMocks.getPortfolioBootstrap.mockResolvedValue(bootstrap(enabled))
    renderResearchLink()

    expect(await screen.findByText(enabled ? 'Private research' : 'Portfolio overview')).toBeInTheDocument()
    expect(screen.getByTestId('current-path')).toHaveTextContent(
      `/portfolios/private/${enabled ? 'research' : 'overview'}`,
    )
    expect(screen.queryByText(enabled ? 'Portfolio overview' : 'Private research')).not.toBeInTheDocument()
  })

  it('waits for configuration without redirecting a local research bookmark', async () => {
    let resolve!: (value: ReturnType<typeof bootstrap>) => void
    apiMocks.getPortfolioBootstrap.mockReturnValue(new Promise((done) => { resolve = done }))
    renderResearchLink()
    expect(screen.getByRole('status')).toHaveTextContent('正在确认账号与组合权限')
    expect(screen.getByTestId('current-path')).toHaveTextContent('/portfolios/private/research')
    resolve(bootstrap(true))
    expect(await screen.findByText('Private research')).toBeInTheDocument()
  })

  it('does not open research when configuration cannot be loaded', async () => {
    apiMocks.getPortfolioBootstrap.mockRejectedValue(new Error('Settings unavailable'))
    renderResearchLink()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Settings unavailable'))
    expect(screen.queryByText('Private research')).not.toBeInTheDocument()
  })
})
