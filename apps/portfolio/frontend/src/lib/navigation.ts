import { resolveWorkspaceUrl } from '../../../../../packages/ui/src/navigation'

export const HOME_URL = resolveWorkspaceUrl(import.meta.env.VITE_HOME_URL, 'home')

export const WATCHLIST_URL = resolveWorkspaceUrl(import.meta.env.VITE_WATCHLIST_URL, 'watchlist')

export const PORTFOLIO_ENTRY_PATH = '/portfolios'

export function buildPortfolioBasePath(portfolioId: string) {
  return `${PORTFOLIO_ENTRY_PATH}/${portfolioId}`
}

export function buildPortfolioSectionPath(portfolioId: string, sectionPath: string) {
  return `${buildPortfolioBasePath(portfolioId)}${sectionPath}`
}

export function buildPortfolioHoldingDetailPath(portfolioId: string, instrumentId: string) {
  return `${buildPortfolioSectionPath(portfolioId, '/holdings')}/${encodeURIComponent(instrumentId)}`
}

export function buildWatchlistInstrumentDetailUrl(instrumentId: string, params: Record<string, string | null | undefined> = {}) {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value) {
      query.set(key, value)
    }
  })
  const search = query.toString()
  return `${WATCHLIST_URL}/instruments/${encodeURIComponent(instrumentId)}${search ? `?${search}` : ''}`
}
