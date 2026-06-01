import {
  getHoldingsWorkspace,
  getPortfolioAccounts,
  getPortfolioAccountsWorkspace,
  getPortfolioFxRates,
  getPortfolioInstruments,
  getPortfolioPerformance,
  getPortfolioPerformanceBoundaryHoldings,
  getPortfolioPerformanceCalculation,
  getPortfolioPerformanceCalculationGroups,
  getPortfolioPerformanceContribution,
  getPortfolioResearchWorkbench,
  getPortfolioTaxonomyCatalog,
  getPortfolioTransactionsWorkspace,
  getPortfolios,
  getWorkspaceSummaryForPortfolio,
  preloadPortfolioWorkspace,
} from './api'

type PreloadTask = () => Promise<unknown>
type IdleCallback = () => void
type IdleWindow = Window & {
  requestIdleCallback?: (callback: IdleCallback, options?: { timeout: number }) => number
}

let routeModulesPreloaded = false
const dataPreloadPortfolioIds = new Set<string>()

export function preloadPortfolioRouteModules() {
  if (routeModulesPreloaded) {
    return
  }

  routeModulesPreloaded = true
  void Promise.allSettled([
    import('../pages/AccountsPage'),
    import('../pages/OverviewPage'),
    import('../pages/PerformancePage'),
    import('../pages/PortfolioHomePage'),
    import('../pages/PortfolioSecurityDetailPage'),
    import('../pages/ResearchPage'),
    import('../pages/RiskPage'),
    import('../pages/TaxonomiesPage'),
    import('../pages/TransactionsPage'),
  ])
}

function schedulePortfolioPreload(callback: () => void) {
  if (typeof window === 'undefined') {
    callback()
    return
  }

  const idleWindow = window as IdleWindow
  if (typeof idleWindow.requestIdleCallback === 'function') {
    idleWindow.requestIdleCallback(callback, { timeout: 2_000 })
    return
  }

  window.setTimeout(callback, 250)
}

async function runPreloadQueue(tasks: PreloadTask[], concurrency = 3) {
  let nextIndex = 0
  const workerCount = Math.min(concurrency, tasks.length)
  const workers = Array.from({ length: workerCount }, async () => {
    while (nextIndex < tasks.length) {
      const task = tasks[nextIndex]
      nextIndex += 1
      try {
        await task()
      } catch {
        // Preload failures should not affect the foreground route.
      }
    }
  })
  await Promise.all(workers)
}

export function preloadPortfolioTabData(portfolioId: string) {
  if (!portfolioId || dataPreloadPortfolioIds.has(portfolioId)) {
    return
  }

  dataPreloadPortfolioIds.add(portfolioId)
  void preloadPortfolioWorkspace(portfolioId).catch(() => undefined)

  schedulePortfolioPreload(() => {
    const tasks: PreloadTask[] = [
      () => getPortfolios(),
      () => getWorkspaceSummaryForPortfolio(portfolioId),
      () => getHoldingsWorkspace(portfolioId),
      () => getPortfolioTaxonomyCatalog(portfolioId),
      () => getPortfolioPerformance(portfolioId),
      () => getPortfolioPerformanceCalculation(portfolioId),
      () => getPortfolioPerformanceContribution(portfolioId, { axis: 'instrument' }),
      () => getPortfolioPerformanceContribution(portfolioId, { axis: 'account' }),
      () => getPortfolioPerformanceCalculationGroups(portfolioId, { axis: 'instrument' }),
      () => getPortfolioPerformanceBoundaryHoldings(portfolioId),
      () => getPortfolioAccounts(portfolioId),
      () => getPortfolioAccountsWorkspace(portfolioId),
      () => getPortfolioInstruments(portfolioId),
      () => getPortfolioFxRates(portfolioId),
      () => getPortfolioTransactionsWorkspace(portfolioId),
      () => getPortfolioResearchWorkbench(portfolioId),
    ]

    void runPreloadQueue(tasks)
  })
}
