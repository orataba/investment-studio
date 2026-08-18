function normalizeConfiguredUrl(value: string | undefined) {
  const normalized = (value || '').trim().replace(/\/$/, '')
  return normalized || null
}

function isLoopbackUrl(value: string) {
  try {
    const url = new URL(value)
    return (
      url.hostname === '127.0.0.1' ||
      url.hostname === 'localhost' ||
      url.hostname === '::1' ||
      url.hostname === '[::1]'
    )
  } catch {
    return false
  }
}

function buildSiblingAppUrl(port: string) {
  if (typeof window === 'undefined') {
    return `http://127.0.0.1:${port}`
  }
  const hostname = window.location.hostname.includes(':')
    ? `[${window.location.hostname}]`
    : window.location.hostname
  return `${window.location.protocol}//${hostname}:${port}`
}

function normalizeAppUrl(value: string | undefined, port: string) {
  const normalized = normalizeConfiguredUrl(value)
  if (!normalized || isLoopbackUrl(normalized)) {
    return buildSiblingAppUrl(port)
  }
  return normalized
}

export const PLATFORM_HOME_URL = normalizeAppUrl(import.meta.env.VITE_PLATFORM_URL, '5172')

export const PLATFORM_API_URL = normalizeAppUrl(import.meta.env.VITE_PLATFORM_API_URL, '8002')

export const WATCHLIST_URL = normalizeAppUrl(import.meta.env.VITE_WATCHLIST_URL, '5173')

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
