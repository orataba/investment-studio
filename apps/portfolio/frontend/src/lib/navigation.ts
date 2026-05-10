function normalizeConfiguredUrl(value: string | undefined, fallback: string) {
  const normalized = (value || '').trim().replace(/\/$/, '')
  return normalized || fallback
}

export const PLATFORM_HOME_URL = normalizeConfiguredUrl(
  import.meta.env.VITE_PLATFORM_URL,
  'http://127.0.0.1:5172',
)

export const WATCHLIST_URL = normalizeConfiguredUrl(
  import.meta.env.VITE_WATCHLIST_URL,
  'http://127.0.0.1:5173',
)

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
