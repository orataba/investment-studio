import { act, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfolioWorkspaceLayout from './components/PortfolioWorkspaceLayout'
import { workspaceSummaryFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  createPortfolioOptionOutcome: vi.fn(),
  copyPortfolio: vi.fn(),
  deletePortfolio: vi.fn(),
  getPortfolios: vi.fn(),
  getPortfolioAccounts: vi.fn(),
  getPortfolioRiskPolicy: vi.fn(),
  getPortfolioUnresolvedOptionActions: vi.fn(),
  getWorkspaceSummaryForPortfolio: vi.fn(),
  updatePortfolioRiskPolicy: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./lib/preload', () => ({
  preloadPortfolioSection: vi.fn(),
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function renderLayout(busy = false) {
  return renderPortfolioPage(
    <PortfolioWorkspaceLayout activeSection="Overview" busy={busy}>
      <div>Portfolio page content</div>
    </PortfolioWorkspaceLayout>,
    '/portfolios/3/overview',
    '/portfolios/:portfolioId/overview',
  )
}

describe('Portfolio workspace loading contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getPortfolios.mockResolvedValue([])
    apiMocks.getPortfolioAccounts.mockResolvedValue({ portfolio_id: '3', accounts: [] })
    apiMocks.getPortfolioUnresolvedOptionActions.mockResolvedValue({
      portfolio_id: '3',
      operational_date: '2026-09-02',
      action_count: 0,
      actions: [],
    })
  })

  it('keeps the workspace structure but never invents zero-valued summary facts while loading', async () => {
    const pendingSummary = deferred<ReturnType<typeof workspaceSummaryFixture>>()
    apiMocks.getWorkspaceSummaryForPortfolio.mockReturnValue(pendingSummary.promise)

    const { container } = renderLayout()
    const workspace = container.querySelector('.portfolio-workspace-page')
    const header = container.querySelector('.portfolio-workspace-shell')

    expect(workspace).toHaveAttribute('aria-busy', 'true')
    expect(header).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('Loading portfolio summary.')
    expect(screen.getByText('Portfolio page content')).toBeInTheDocument()
    expect(screen.queryByText('$0.00')).not.toBeInTheDocument()
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument()

    await act(async () => {
      pendingSummary.resolve(workspaceSummaryFixture())
      await pendingSummary.promise
    })

    expect(await screen.findByText('$1,000.00')).toBeInTheDocument()
    expect(screen.getByText(/\+\$8\.00 \(0\.80%\)/)).toBeInTheDocument()
    expect(workspace).toHaveAttribute('aria-busy', 'false')
    expect(header).toHaveAttribute('aria-busy', 'false')
  })

  it('renders authoritative zero values after the summary request settles', async () => {
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(
      workspaceSummaryFixture({ nav: 0, day_change_value: 0, day_change_pct: 0 }),
    )

    const { container } = renderLayout()

    expect(await screen.findByText('$0.00')).toBeInTheDocument()
    expect(screen.getByText(/\$0\.00 \(0\.00%\)/)).toBeInTheDocument()
    expect(container.querySelector('.portfolio-workspace-page')).toHaveAttribute('aria-busy', 'false')
  })

  it('keeps the workspace busy when its active page is still loading', async () => {
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())

    const { container } = renderLayout(true)

    expect(await screen.findByText('$1,000.00')).toBeInTheDocument()
    expect(container.querySelector('.portfolio-workspace-shell')).toHaveAttribute('aria-busy', 'false')
    expect(container.querySelector('.portfolio-workspace-page')).toHaveAttribute('aria-busy', 'true')
  })
})
