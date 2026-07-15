import {
  getHoldingsWorkspace,
  getPortfolioAccountsWorkspace,
  getPortfolioPerformance,
  getPortfolioResearchWorkbench,
  getPortfolioTaxonomyCatalog,
  getPortfolioTransactionsWorkspace,
} from './api'

export type PortfolioPreloadSection =
  | 'Overview'
  | 'Holdings'
  | 'Performance'
  | 'Risk'
  | 'Transactions'
  | 'Accounts'
  | 'Taxonomies'
  | 'Research'

type PreloadTask = () => Promise<unknown>

const routeModulePreloaders: Record<PortfolioPreloadSection, PreloadTask> = {
  Overview: () => import('../pages/OverviewPage'),
  Holdings: () => import('../pages/PortfolioHomePage'),
  Performance: () => import('../pages/PerformancePage'),
  Risk: () => import('../pages/RiskPage'),
  Transactions: () => import('../pages/TransactionsPage'),
  Accounts: () => import('../pages/AccountsPage'),
  Taxonomies: () => import('../pages/TaxonomiesPage'),
  Research: () => import('../pages/ResearchPage'),
}

const dataPreloaders: Record<PortfolioPreloadSection, (portfolioId: string) => Promise<unknown>> = {
  Overview: (portfolioId) => getPortfolioPerformance(portfolioId),
  Holdings: (portfolioId) => getHoldingsWorkspace(portfolioId),
  Performance: (portfolioId) => getPortfolioPerformance(portfolioId),
  Risk: (portfolioId) => getHoldingsWorkspace(portfolioId, { include_details: true }),
  Transactions: (portfolioId) => getPortfolioTransactionsWorkspace(portfolioId),
  Accounts: (portfolioId) => getPortfolioAccountsWorkspace(portfolioId),
  Taxonomies: (portfolioId) => getPortfolioTaxonomyCatalog(portfolioId),
  Research: (portfolioId) => getPortfolioResearchWorkbench(portfolioId),
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
