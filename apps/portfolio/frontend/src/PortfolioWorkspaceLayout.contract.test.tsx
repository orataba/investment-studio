import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfolioWorkspaceLayout from './components/PortfolioWorkspaceLayout'
import { PortfolioCapabilitiesContext } from './components/PortfolioCapabilitiesProvider'
import { workspaceSummaryFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  SUPPORTED_PORTFOLIO_CURRENCIES: ['USD', 'HKD', 'CNY', 'EUR', 'GBP', 'CHF'],
  createPortfolioOptionOutcome: vi.fn(),
  copyPortfolio: vi.fn(),
  deletePortfolio: vi.fn(),
  getPortfolios: vi.fn(),
  getPortfolioAccounts: vi.fn(),
  getPortfolioRiskPolicy: vi.fn(),
  getPortfolioUnresolvedOptionActions: vi.fn(),
  getWorkspaceSummaryForPortfolio: vi.fn(),
  updatePortfolioRiskPolicy: vi.fn(),
  updatePortfolioSettings: vi.fn(),
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

function renderLayout(busy = false, researchEnabled = false) {
  return renderPortfolioPage(
    <LanguageProvider enableDomTranslation={false}>
      <PortfolioCapabilitiesContext.Provider value={{ research_enabled: researchEnabled }}>
        <PortfolioWorkspaceLayout activeSection="Overview" busy={busy}>
          <div>Portfolio page content</div>
        </PortfolioWorkspaceLayout>
      </PortfolioCapabilitiesContext.Provider>
    </LanguageProvider>,
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

  it.each([false, true])('shows Research only when enabled by the deployment: %s', async (enabled) => {
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
    renderLayout(false, enabled)
    await screen.findByText('$1,000.00')
    const navigation = within(screen.getByRole('navigation', { name: 'Portfolio sections' }))
    expect(navigation.getByRole('link', { name: 'Overview' })).toBeInTheDocument()
    if (enabled) {
      expect(navigation.getByRole('link', { name: 'Research' })).toHaveAttribute('href', '/portfolios/3/research')
    } else {
      expect(navigation.queryByRole('link', { name: 'Research' })).not.toBeInTheDocument()
    }
  })

  it('keeps the workspace structure but never invents zero-valued summary facts while loading', async () => {
    const pendingSummary = deferred<ReturnType<typeof workspaceSummaryFixture>>()
    apiMocks.getWorkspaceSummaryForPortfolio.mockReturnValue(pendingSummary.promise)

    const { container } = renderLayout()
    const workspace = container.querySelector('.portfolio-workspace-page')
    const header = container.querySelector('.portfolio-workspace-shell')

    expect(workspace).toHaveAttribute('aria-busy', 'true')
    expect(header).toHaveAttribute('aria-busy', 'true')
    expect(container.querySelector('.workspace-breadcrumbs .language-switcher')).toContainElement(
      screen.getByRole('combobox', { name: 'Language' }),
    )
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

  it('changes the reporting currency from the in-workspace portfolio settings', async () => {
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(
      workspaceSummaryFixture({ base_currency: 'USD' }),
    )
    const policy = {
      model_name: 'Production Risk',
      model_role: 'production',
      covariance_model_id: 'ewma_vol_shrinkage_corr_covariance',
      lookback_days: 90,
      calculation_frequency: 'daily',
      resolved_calculation_frequency: 'daily',
      missing_return_policy: 'strict',
      contribution_mode: 'signed',
      parameters: {},
      parameters_by_frequency: {},
    }
    apiMocks.getPortfolioRiskPolicy.mockResolvedValue(policy)
    apiMocks.updatePortfolioRiskPolicy.mockResolvedValue(policy)
    apiMocks.updatePortfolioSettings.mockResolvedValue({
      portfolio_id: '3',
      portfolio_name: 'Portfolio 3',
      base_currency: 'CNY',
      inception_date: '2026-01-01',
      as_of_date: '2026-09-02',
      nav: null,
      day_change_value: null,
      day_change_pct: null,
      securities_count: 0,
      sort_order: 0,
    })

    renderLayout()
    const user = userEvent.setup()
    await screen.findByText('$1,000.00')
    await user.click(screen.getByRole('button', { name: 'Portfolio Settings' }))

    const dialog = await screen.findByRole('dialog', { name: 'Portfolio Settings' })
    await within(dialog).findByText(/Transactions keep their original currencies/)
    await user.selectOptions(
      within(dialog).getByLabelText('Reporting / Base Currency'),
      'CNY',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Save Settings' }))

    await waitFor(() => {
      expect(apiMocks.updatePortfolioSettings).toHaveBeenCalledWith('3', {
        base_currency: 'CNY',
      })
    })
    expect(await screen.findByText(/Reporting currency changed to CNY/i)).toBeInTheDocument()
  })
})
