export const PLATFORM_HOME_URL = 'http://127.0.0.1:5172'

export const PORTFOLIO_ENTRY_PATH = '/portfolios'
export const DEFAULT_PORTFOLIO_ID = 'yungu'

export function buildPortfolioBasePath(portfolioId: string) {
  return `${PORTFOLIO_ENTRY_PATH}/${portfolioId}`
}

export function buildPortfolioSectionPath(portfolioId: string, sectionPath: string) {
  return `${buildPortfolioBasePath(portfolioId)}${sectionPath}`
}
