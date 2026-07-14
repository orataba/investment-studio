import {
  clearPortfolioApiCache,
  getHoldingsWorkspace,
  getPortfolioPerformanceReport,
  type HoldingsWorkspaceResponse,
  type PortfolioDailyPublishedPerformanceReportResponse,
} from './api'

export const OVERVIEW_PUBLICATION_MAX_ATTEMPTS = 3
export const OVERVIEW_PUBLICATION_CHANGED_CODE = 'publication_changed_retry'

export type OverviewPublishedBundle = {
  holdings: HoldingsWorkspaceResponse
  performance: PortfolioDailyPublishedPerformanceReportResponse
  publication_id: string
  attempt_count: number
}

type OverviewPublicationLoaderDependencies = {
  clearCache: () => void
  getHoldings: (portfolioId: string) => Promise<HoldingsWorkspaceResponse>
  getPerformance: (
    portfolioId: string,
  ) => Promise<PortfolioDailyPublishedPerformanceReportResponse>
}

const defaultDependencies: OverviewPublicationLoaderDependencies = {
  clearCache: clearPortfolioApiCache,
  getHoldings: getHoldingsWorkspace,
  getPerformance: (portfolioId) =>
    getPortfolioPerformanceReport(portfolioId, {
      axis: 'instrument',
      frequency: 'monthly',
    }),
}

function publicationId(
  surface: string,
  response: { publication?: { publication_id?: unknown } | null },
) {
  const value = response.publication?.publication_id
  if (typeof value !== 'string' || !value.trim() || value !== value.trim()) {
    throw new Error(`${surface}.publication.publication_id is required.`)
  }
  return value
}

export async function loadOverviewPublishedBundle(
  portfolioId: string,
  dependencies: OverviewPublicationLoaderDependencies = defaultDependencies,
): Promise<OverviewPublishedBundle> {
  if (!portfolioId.trim()) {
    throw new Error('Portfolio id is required.')
  }

  const mismatches: string[] = []
  for (let attempt = 1; attempt <= OVERVIEW_PUBLICATION_MAX_ATTEMPTS; attempt += 1) {
    // A publication mismatch must not be retried from the one-minute GET cache.
    dependencies.clearCache()
    const [holdings, performance] = await Promise.all([
      dependencies.getHoldings(portfolioId),
      dependencies.getPerformance(portfolioId),
    ])
    const holdingsPublicationId = publicationId('holdings', holdings)
    const performancePublicationId = publicationId('performance', performance)
    if (holdingsPublicationId === performancePublicationId) {
      return {
        holdings,
        performance,
        publication_id: holdingsPublicationId,
        attempt_count: attempt,
      }
    }
    mismatches.push(`${holdingsPublicationId}/${performancePublicationId}`)
  }

  throw new Error(
    `${OVERVIEW_PUBLICATION_CHANGED_CODE}: holdings and performance did not resolve to one immutable publication after ${OVERVIEW_PUBLICATION_MAX_ATTEMPTS} attempts (${mismatches.join(', ')}).`,
  )
}
