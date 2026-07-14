import {
  getHoldingsWorkspace,
  getPortfolioAccountsWorkspace,
  getPortfolioPerformanceReport,
  getPortfolioAllocationResearchWorkbench,
  getPortfolioTaxonomyCatalog,
  getPortfolioTransactionsWorkspace,
  getPortfolios,
  getWorkspaceSummaryForPortfolio,
} from './api'
import { loadOverviewPublishedBundle } from './overviewPublication'

export type PortfolioPreloadSection =
  | 'Overview'
  | 'Holdings'
  | 'Performance'
  | 'Risk'
  | 'Transactions'
  | 'Accounts'
  | 'Taxonomies'
  | 'Allocation Lab'

type PreloadTask = () => Promise<unknown>

const routeModulePreloaders: Record<PortfolioPreloadSection, PreloadTask> = {
  Overview: () => import('../pages/OverviewPage'),
  Holdings: () => import('../pages/PortfolioHomePage'),
  Performance: () => import('../pages/PerformancePage'),
  Risk: () => import('../pages/RiskPage'),
  Transactions: () => import('../pages/TransactionsPage'),
  Accounts: () => import('../pages/AccountsPage'),
  Taxonomies: () => import('../pages/TaxonomiesPage'),
  'Allocation Lab': () => import('../pages/AllocationLabPage'),
}

const dataPreloaders: Record<PortfolioPreloadSection, (portfolioId: string) => Promise<unknown>> = {
  Overview: (portfolioId) => loadOverviewPublishedBundle(portfolioId),
  Holdings: (portfolioId) => getHoldingsWorkspace(portfolioId),
  Performance: (portfolioId) =>
    getPortfolioPerformanceReport(portfolioId, { axis: 'instrument', frequency: 'monthly' }),
  Risk: (portfolioId) => getWorkspaceSummaryForPortfolio(portfolioId),
  Transactions: (portfolioId) => getPortfolioTransactionsWorkspace(portfolioId),
  Accounts: (portfolioId) => getPortfolioAccountsWorkspace(portfolioId),
  Taxonomies: (portfolioId) => getPortfolioTaxonomyCatalog(portfolioId),
  'Allocation Lab': async (portfolioId) => {
    const portfolios = await getPortfolios({ includeArchived: true })
    const portfolio = portfolios.find((item) => item.portfolio_id === portfolioId)
    if (!portfolio || portfolio.operating_profile !== 'standard_taxonomy') {
      return portfolio
    }
    return getPortfolioAllocationResearchWorkbench(portfolioId)
  },
}

const loadedRouteModules = new Set<PortfolioPreloadSection>()
const loadedDataIntents = new Set<string>()

export const PORTFOLIO_PRELOAD_SECTIONS = Object.freeze(
  Object.keys(routeModulePreloaders) as PortfolioPreloadSection[],
)

export function isPortfolioPreloadSection(value: string): value is PortfolioPreloadSection {
  return PORTFOLIO_PRELOAD_SECTIONS.includes(value as PortfolioPreloadSection)
}

/**
 * Warm only the route the user has shown intent to open. The previous global
 * idle fan-out fetched every portfolio surface (including multi-megabyte
 * holdings/contribution payloads) and competed with the foreground route.
 */
export function preloadPortfolioSection(section: string, portfolioId: string) {
  if (!portfolioId || !isPortfolioPreloadSection(section)) {
    return
  }

  if (!loadedRouteModules.has(section)) {
    loadedRouteModules.add(section)
    void routeModulePreloaders[section]().catch(() => {
      loadedRouteModules.delete(section)
    })
  }

  const dataIntentKey = `${portfolioId}:${section}`
  if (loadedDataIntents.has(dataIntentKey)) {
    return
  }
  loadedDataIntents.add(dataIntentKey)
  void dataPreloaders[section](portfolioId).catch(() => {
    loadedDataIntents.delete(dataIntentKey)
  })
}
