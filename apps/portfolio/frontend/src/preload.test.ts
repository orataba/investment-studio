import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  holdings: vi.fn(() => Promise.resolve({})),
  accounts: vi.fn(() => Promise.resolve({})),
  performance: vi.fn(() => Promise.resolve({})),
  research: vi.fn(() => Promise.resolve({})),
  taxonomy: vi.fn(() => Promise.resolve({})),
  transactions: vi.fn(() => Promise.resolve({})),
}))

vi.mock('./lib/api', () => ({
  getHoldingsWorkspace: apiMocks.holdings,
  getPortfolioAccountsWorkspace: apiMocks.accounts,
  getPortfolioPerformance: apiMocks.performance,
  getPortfolioResearchWorkbench: apiMocks.research,
  getPortfolioTaxonomyCatalog: apiMocks.taxonomy,
  getPortfolioTransactionsWorkspace: apiMocks.transactions,
}))

vi.mock('./pages/TransactionsPage', () => ({ default: () => null }))
vi.mock('./pages/OverviewPage', () => ({ default: () => null }))
vi.mock('./pages/PerformancePage', () => ({ default: () => null }))

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
    expect(apiMocks.performance).not.toHaveBeenCalled()
    expect(apiMocks.research).not.toHaveBeenCalled()
    expect(apiMocks.taxonomy).not.toHaveBeenCalled()
  })

  it('does not prefetch undated reports for pages that resolve their own dates', () => {
    preloadPortfolioSection('Overview', 'portfolio-date-intent')
    preloadPortfolioSection('Performance', 'portfolio-date-intent')

    expect(apiMocks.performance).not.toHaveBeenCalled()
    expect(apiMocks.holdings).not.toHaveBeenCalled()
  })
})
