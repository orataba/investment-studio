import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useLocation } from 'react-router'

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
  getPortfolioMembers: vi.fn(),
  getPortfolioMemberCandidates: vi.fn(),
  getPortfolioUnresolvedOptionActions: vi.fn(),
  getWorkspaceSummaryForPortfolio: vi.fn(),
  getHoldingsWorkspace: vi.fn(),
  updatePortfolioRiskPolicy: vi.fn(),
  updatePortfolioSettings: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
const assistantMocks = vi.hoisted(() => ({ read: vi.fn(), write: vi.fn(), upload: vi.fn() }))
vi.mock('./lib/researchAssistantApi', () => ({
  readResearchAssistant: assistantMocks.read,
  writeResearchAssistant: assistantMocks.write,
  uploadResearchAssistantFile: assistantMocks.upload,
  researchAssistantUrl: (path: string) => path,
}))
const accessMock = vi.hoisted(() => ({ can_edit: true, can_manage: true, role: 'manager' }))
vi.mock('./components/PortfolioAccessProvider', () => ({ usePortfolioAccess: () => accessMock }))
vi.mock('./lib/preload', () => ({
  preloadPortfolioSection: vi.fn(),
}))
vi.mock('./components/PortfolioInstrumentRisk', () => ({
  default: ({ portfolioId, onAskAssistant }: { portfolioId: string; onAskAssistant: (id: string, question: string, reference?: { instrument_id: string; event_case_id: string; event_version_id: string }) => void }) =>
    <section aria-label="Risk content" data-portfolio={portfolioId}><button type="button">Inspect risk</button><button type="button" onClick={() => onAskAssistant('asset-1', '这项风险如何影响组合？', { instrument_id: 'asset-1', event_case_id: 'event-1', event_version_id: 'event-1:2' })}>Ask about risk</button></section>,
}))

function CurrentLocation() {
  const location = useLocation()
  return <span data-testid="current-location">{location.pathname}{location.search}</span>
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function renderLayout(busy = false, researchEnabled = false, query = '', holdingId?: string) {
  return renderPortfolioPage(
    <LanguageProvider enableDomTranslation={false}>
      <PortfolioCapabilitiesContext.Provider value={{ research_enabled: researchEnabled }}>
        <PortfolioWorkspaceLayout activeSection={holdingId ? 'Holdings' : 'Overview'} busy={busy}>
          <div>Portfolio page content</div>
          <CurrentLocation />
        </PortfolioWorkspaceLayout>
      </PortfolioCapabilitiesContext.Provider>
    </LanguageProvider>,
    `/portfolios/3/${holdingId ? `holdings/${holdingId}` : 'overview'}${query}`,
    holdingId ? '/portfolios/:portfolioId/holdings/:holdingId' : '/portfolios/:portfolioId/overview',
  )
}

describe('Portfolio workspace loading contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.assign(accessMock, { can_edit: true, can_manage: true, role: 'manager' })
    apiMocks.getPortfolios.mockResolvedValue([])
    apiMocks.getPortfolioMembers.mockResolvedValue({ members: [] })
    apiMocks.getPortfolioMemberCandidates.mockResolvedValue({ members: [] })
    apiMocks.getPortfolioAccounts.mockResolvedValue({ portfolio_id: '3', accounts: [] })
    apiMocks.getHoldingsWorkspace.mockResolvedValue({ portfolio_id: '3', rows: [], as_of_date: '2026-09-08' })
    apiMocks.getPortfolioUnresolvedOptionActions.mockResolvedValue({
      portfolio_id: '3',
      operational_date: '2026-09-02',
      action_count: 0,
      actions: [],
    })
    const topic = { topic_id: 'chat-3', title: '组合历史对话', portfolio_id: '3', instrument_ids: [], status: 'active', updated_at: '2026-09-08' }
    assistantMocks.read.mockImplementation(async (path: string) => {
      if (path === '/research/topics') return [topic]
      if (path === '/research/catalogue') return { instruments: [{ instrument_id: 'asset-1', name: '持仓标的' }] }
      if (path === '/research/connections') return { assistant_available: true, portfolios: [{ portfolio_id: '3', portfolio_name: 'Contract Portfolio' }] }
      return { topic, entries: [] }
    })
    assistantMocks.write.mockImplementation(async (path: string) => path === '/research/topics' ? topic : {})
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

  it('opens the shared research assistant with the current portfolio and only displayed page dimensions', async () => {
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
    renderLayout(false, false, '?currency=USD&benchmark=spy&start=2026-01-01&end=2026-09-06&tab=unrelated&unrelated=value')
    await screen.findByText('$1,000.00')
    const trigger = screen.getByRole('button', { name: 'Research assistant' })
    expect(trigger).not.toHaveAttribute('href')
    const tools = trigger.closest<HTMLElement>('.portfolio-header-actions')!
    expect(within(tools).queryAllByRole('link')).toHaveLength(0)
    expect(within(tools).getByRole('button', { name: 'Risk alerts' })).toBeInTheDocument()
    expect(within(tools).getByRole('button', { name: 'Portfolio Settings' })).toHaveTextContent('Settings')
    expect(screen.queryByRole('button', { name: '成员权限' })).not.toBeInTheDocument()
    expect(screen.queryByText('管理者')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Portfolio access' })).not.toBeInTheDocument()
    expect(apiMocks.getPortfolioMembers).not.toHaveBeenCalled()
    expect(tools.closest('.portfolio-header-row')?.querySelector('.portfolio-name')).toHaveTextContent('Contract Portfolio')
    expect(trigger.closest('.workspace-app-heading')).toBeNull()

    const currentPage = screen.getByTestId('current-location').textContent
    const user = userEvent.setup()
    await user.click(trigger)
    const assistant = await screen.findByRole('dialog', { name: '研究助手' })
    expect(assistant).toHaveClass('assistant-drawer')
    expect(within(assistant).getByRole('combobox', { name: '关联组合' })).toBeDisabled()
    await user.type(within(assistant).getByRole('textbox', { name: '向研究助手提问' }), '解释当前组合的风险。')
    await user.click(within(assistant).getByRole('button', { name: '发送' }))
    await waitFor(() => expect(assistantMocks.write).toHaveBeenCalledWith('/research/topics/chat-3/analysis', {
      question: '解释当前组合的风险。', watchlist_id: null,
      page_context: { surface: 'portfolio', instrument_id: null, watchlist_id: null, portfolio_id: '3', tab: 'Overview', currency: 'USD', benchmark: 'spy', start: '2026-01-01', end: '2026-09-06' },
    }))
    expect(screen.getByTestId('current-location').textContent).toBe(currentPage)
    await user.click(within(assistant).getByRole('button', { name: '历史对话' }))
    await user.click(within(assistant).getByRole('button', { name: /组合历史对话/ }))
    expect(screen.getByTestId('current-location').textContent).toBe(currentPage)
    await user.click(within(assistant).getByRole('button', { name: '关闭研究助手' }))
    await waitFor(() => expect(trigger).toHaveFocus())
    await user.click(within(tools).getByRole('button', { name: 'Risk alerts' }))
    const drawer = await screen.findByRole('dialog', { name: 'Risk alerts' })
    expect(await within(drawer).findByRole('region', { name: 'Risk content' })).toHaveAttribute('data-portfolio', '3')
    expect(drawer).toHaveClass('portfolio-risk-drawer')
    expect(screen.getByTestId('current-location').textContent).toBe(currentPage)
    expect(screen.getByText('Portfolio page content')).toBeInTheDocument()
    await user.click(within(drawer).getByRole('button', { name: 'Close risk alerts' }))
    expect(screen.queryByRole('dialog', { name: 'Risk alerts' })).not.toBeInTheDocument()
    expect(screen.getByTestId('current-location').textContent).toBe(currentPage)
    expect(screen.getByText('Portfolio page content')).toBeInTheDocument()
  })

  it('closes the assistant with Escape or the backdrop and restores its entry without navigating', async () => {
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
    const { container } = renderLayout(false, false, '?account_id=cash-hkd&account_tab=ledger')
    await screen.findByText('$1,000.00')
    const user = userEvent.setup()
    const trigger = screen.getByRole('button', { name: 'Research assistant' })
    const content = screen.getByText('Portfolio page content')
    const location = screen.getByTestId('current-location').textContent
    await user.click(trigger)
    const input = await screen.findByRole('textbox', { name: '向研究助手提问' })
    await waitFor(() => expect(input).toHaveFocus())
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: '研究助手' })).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
    await user.click(trigger)
    await screen.findByRole('dialog', { name: '研究助手' })
    await user.click(container.querySelector<HTMLElement>('.assistant-backdrop')!)
    expect(screen.queryByRole('dialog', { name: '研究助手' })).not.toBeInTheDocument()
    expect(screen.getByText('Portfolio page content')).toBe(content)
    expect(screen.getByTestId('current-location').textContent).toBe(location)
  })

  it('passes a local derivative holding, account and historical date without inventing an instrument', async () => {
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
    renderLayout(false, false, '?as_of_date=2026-06-30&account_id=fcn-usd&unrelated=value', 'fcn-local-1')
    await screen.findByText('$1,000.00')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Research assistant' }))
    const assistant = await screen.findByRole('dialog', { name: '研究助手' })
    await user.type(within(assistant).getByRole('textbox', { name: '向研究助手提问' }), '解释这张票据的风险。')
    await user.click(within(assistant).getByRole('button', { name: '发送' }))
    await waitFor(() => expect(assistantMocks.write).toHaveBeenCalledWith('/research/topics/chat-3/analysis', {
      question: '解释这张票据的风险。', watchlist_id: null,
      page_context: { surface: 'portfolio', portfolio_id: '3', instrument_id: null, watchlist_id: null,
        tab: 'Holdings', holding_id: 'fcn-local-1', account_id: 'fcn-usd', as_of_date: '2026-06-30' },
    }))
    expect(assistantMocks.write).toHaveBeenCalledWith('/research/topics', expect.objectContaining({ portfolio_id: '3', instrument_ids: [] }))
    expect(screen.getByTestId('current-location')).toHaveTextContent('/portfolios/3/holdings/fcn-local-1?as_of_date=2026-06-30&account_id=fcn-usd&unrelated=value')
  })

  it('opens a risk follow-up in the assistant and returns to the same risk drawer', async () => {
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
    renderLayout(false, false, '?start_date=2026-08-01&end_date=2026-09-01&currency=HKD')
    await screen.findByText('$1,000.00')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Risk alerts' }))
    const risk = await screen.findByRole('dialog', { name: 'Risk alerts' })
    const trigger = await within(risk).findByRole('button', { name: 'Ask about risk' })
    await user.click(trigger)
    const assistant = await screen.findByRole('dialog', { name: '研究助手' })
    expect(within(assistant).getByRole('textbox', { name: '向研究助手提问' })).toHaveValue('这项风险如何影响组合？')
    await user.click(within(assistant).getByRole('button', { name: '发送' }))
    await waitFor(() => expect(assistantMocks.write).toHaveBeenCalledWith('/research/topics/chat-3/analysis', expect.objectContaining({
      page_context: expect.objectContaining({ instrument_id: 'asset-1', start: '2026-08-01', end: '2026-09-01', research_reference: { instrument_id: 'asset-1', event_case_id: 'event-1', event_version_id: 'event-1:2' } }),
    })))
    await user.keyboard('{Escape}')
    expect(assistant).not.toBeInTheDocument()
    expect(risk).toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('keeps the actual risk drawer inside the current page and closes it with Escape or the backdrop', async () => {
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
    const { container } = renderLayout(false, false, '?as_of_date=2026-08-31&currency=HKD')
    await screen.findByText('$1,000.00')
    const user = userEvent.setup()
    const trigger = screen.getByRole('button', { name: 'Risk alerts' })
    const content = screen.getByText('Portfolio page content')
    const originalLocation = screen.getByTestId('current-location').textContent
    expect(trigger).not.toHaveAttribute('href')

    await user.click(trigger)
    const dialog = await screen.findByRole('dialog', { name: 'Risk alerts' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    const closeButton = within(dialog).getByRole('button', { name: 'Close risk alerts' })
    await waitFor(() => expect(closeButton).toHaveFocus())
    await user.click(await within(dialog).findByRole('button', { name: 'Inspect risk' }))
    expect(dialog).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Risk alerts' })).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
    expect(screen.getByText('Portfolio page content')).toBe(content)
    expect(screen.getByTestId('current-location')).toHaveTextContent(originalLocation!)

    await user.click(trigger)
    await screen.findByRole('dialog', { name: 'Risk alerts' })
    await user.click(container.querySelector<HTMLElement>('.portfolio-risk-backdrop')!)
    expect(screen.queryByRole('dialog', { name: 'Risk alerts' })).not.toBeInTheDocument()
    expect(screen.getByText('Portfolio page content')).toBe(content)
    expect(screen.getByTestId('current-location')).toHaveTextContent(originalLocation!)
  })

  it('keeps the workspace structure but never invents zero-valued summary facts while loading', async () => {
    const pendingSummary = deferred<ReturnType<typeof workspaceSummaryFixture>>()
    apiMocks.getWorkspaceSummaryForPortfolio.mockReturnValue(pendingSummary.promise)

    const { container } = renderLayout()
    const workspace = container.querySelector('.portfolio-workspace-page')
    const header = container.querySelector('.portfolio-workspace-shell')

    expect(workspace).toHaveAttribute('aria-busy', 'true')
    expect(header).toHaveAttribute('aria-busy', 'true')
    expect(container.querySelector('.portfolio-topbar > .language-switcher')).toContainElement(
      screen.getByRole('combobox', { name: 'Language' }),
    )
    expect(container.querySelector('.workspace-breadcrumbs .language-switcher')).toBeNull()
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
    expect(within(dialog).getByRole('region', { name: 'Portfolio access' })).toBeInTheDocument()
    await waitFor(() => expect(apiMocks.getPortfolioMembers).toHaveBeenCalledWith('3'))
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

it('keeps assistant available while a viewer cannot open portfolio business settings', async () => {
  Object.assign(accessMock, { can_edit: false, can_manage: false, role: 'viewer' })
  apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
  renderLayout()
  await screen.findByText('$1,000.00')
  expect(screen.getByRole('button', { name: 'Portfolio Settings' })).toBeDisabled()
  expect(screen.queryByText('当前为只读权限，可查看组合资料和向研究助手提问。')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '成员权限' })).not.toBeInTheDocument()
})
