function normalizeConfiguredUrl(value: string | undefined, fallback: string) {
  const normalized = (value || '').trim().replace(/\/$/, '')
  return normalized || fallback
}

export const PLATFORM_HOME_URL = normalizeConfiguredUrl(
  import.meta.env.VITE_PLATFORM_URL,
  'http://127.0.0.1:5172',
)

export const PORTFOLIO_ENTRY_PATH = '/portfolios'

export function buildPortfolioBasePath(portfolioId: string) {
  return `${PORTFOLIO_ENTRY_PATH}/${portfolioId}`
}

export function buildPortfolioSectionPath(portfolioId: string, sectionPath: string) {
  return `${buildPortfolioBasePath(portfolioId)}${sectionPath}`
}
