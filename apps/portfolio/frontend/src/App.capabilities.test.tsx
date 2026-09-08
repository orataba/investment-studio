import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const apiMocks = vi.hoisted(() => ({ getPortfolioCapabilities: vi.fn() }))
vi.mock('./lib/api', () => apiMocks)
// Account/portfolio authorization has its own boundary contract tests.
vi.mock('./components/PortfolioSessionProvider', () => ({ default: ({ children }: { children: React.ReactNode }) => children }))
vi.mock('./components/PortfolioAccessProvider', () => ({ default: ({ children }: { children: React.ReactNode }) => children }))
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
    apiMocks.getPortfolioCapabilities.mockResolvedValue({ research_enabled: enabled })
    renderResearchLink()

    expect(await screen.findByText(enabled ? 'Private research' : 'Portfolio overview')).toBeInTheDocument()
    expect(screen.getByTestId('current-path')).toHaveTextContent(
      `/portfolios/private/${enabled ? 'research' : 'overview'}`,
    )
    expect(screen.queryByText(enabled ? 'Portfolio overview' : 'Private research')).not.toBeInTheDocument()
  })

  it('waits for configuration without redirecting a local research bookmark', async () => {
    let resolve!: (value: { research_enabled: boolean }) => void
    apiMocks.getPortfolioCapabilities.mockReturnValue(new Promise((done) => { resolve = done }))
    renderResearchLink()
    expect(screen.getByRole('status')).toHaveTextContent('Loading settings')
    expect(screen.getByTestId('current-path')).toHaveTextContent('/portfolios/private/research')
    resolve({ research_enabled: true })
    expect(await screen.findByText('Private research')).toBeInTheDocument()
  })

  it('does not open research when configuration cannot be loaded', async () => {
    apiMocks.getPortfolioCapabilities.mockRejectedValue(new Error('Settings unavailable'))
    renderResearchLink()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Settings unavailable'))
    expect(screen.queryByText('Private research')).not.toBeInTheDocument()
  })
})
