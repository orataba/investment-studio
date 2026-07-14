import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  holdings: vi.fn(() => Promise.resolve({})),
  accounts: vi.fn(() => Promise.resolve({})),
  performanceReport: vi.fn(() => Promise.resolve({})),
  portfolios: vi.fn((options?: { includeArchived?: boolean }) =>
    Promise.resolve([
      {
        portfolio_id: options?.includeArchived ? 'portfolio-standard' : 'portfolio-active',
        operating_profile: 'standard_taxonomy',
      },
      {
        portfolio_id: 'portfolio-external',
        operating_profile: 'external_etf_rotation',
      },
    ]),
  ),
  allocationResearch: vi.fn(() => Promise.resolve({})),
  taxonomy: vi.fn(() => Promise.resolve({})),
  transactions: vi.fn(() => Promise.resolve({})),
  overviewBundle: vi.fn(() => Promise.resolve({})),
  workspaceSummary: vi.fn(() => Promise.resolve({})),
}))

vi.mock('./lib/overviewPublication', () => ({
  loadOverviewPublishedBundle: apiMocks.overviewBundle,
}))

vi.mock('./lib/api', () => ({
  getHoldingsWorkspace: apiMocks.holdings,
  getPortfolioAccountsWorkspace: apiMocks.accounts,
  getPortfolioPerformanceReport: apiMocks.performanceReport,
  getPortfolios: apiMocks.portfolios,
  getPortfolioAllocationResearchWorkbench: apiMocks.allocationResearch,
  getPortfolioTaxonomyCatalog: apiMocks.taxonomy,
  getPortfolioTransactionsWorkspace: apiMocks.transactions,
  getWorkspaceSummaryForPortfolio: apiMocks.workspaceSummary,
}))

vi.mock('./pages/TransactionsPage', () => ({ default: () => null }))

import { preloadPortfolioSection } from './lib/preload'

describe('portfolio intent preloading', () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockClear())
  })

  it('warms only the intended route and does not recreate the global API fan-out', () => {
    preloadPortfolioSection('Transactions', 'portfolio-intent-test')
    preloadPortfolioSection('Transactions', 'portfolio-intent-test')

    expect(apiMocks.transactions).toHaveBeenCalledTimes(1)
    expect(apiMocks.transactions).toHaveBeenCalledWith('portfolio-intent-test')
    expect(apiMocks.holdings).not.toHaveBeenCalled()
    expect(apiMocks.accounts).not.toHaveBeenCalled()
    expect(apiMocks.performanceReport).not.toHaveBeenCalled()
    expect(apiMocks.allocationResearch).not.toHaveBeenCalled()
    expect(apiMocks.taxonomy).not.toHaveBeenCalled()
  })

  it('warms Overview with the same published report shape used by the page', () => {
    preloadPortfolioSection('Overview', 'portfolio-overview-preload')
    preloadPortfolioSection('Overview', 'portfolio-overview-preload')

    expect(apiMocks.overviewBundle).toHaveBeenCalledTimes(1)
    expect(apiMocks.overviewBundle).toHaveBeenCalledWith('portfolio-overview-preload')
    expect(apiMocks.taxonomy).not.toHaveBeenCalled()
  })

  it('preloads Allocation Lab only for the standard taxonomy operating profile', async () => {
    preloadPortfolioSection('Allocation Lab', 'portfolio-standard')

    await vi.waitFor(() => {
      expect(apiMocks.allocationResearch).toHaveBeenCalledWith('portfolio-standard')
    })

    preloadPortfolioSection('Allocation Lab', 'portfolio-external')
    await vi.waitFor(() => {
      expect(apiMocks.portfolios).toHaveBeenCalledWith({ includeArchived: true })
    })
    expect(apiMocks.allocationResearch).toHaveBeenCalledTimes(1)
  })
})
