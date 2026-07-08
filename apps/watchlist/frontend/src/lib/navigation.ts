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

export const WATCHLIST_ENTRY_PATH = '/watchlists'

export function buildWatchlistPath(watchlistId: string) {
  return `${WATCHLIST_ENTRY_PATH}/${watchlistId}`
}

export function buildInstrumentDetailPath(instrumentId: string, watchlistId?: string | null) {
  const normalizedWatchlistId = (watchlistId || '').trim()
  const search = normalizedWatchlistId
    ? `?${new URLSearchParams({ watchlist: normalizedWatchlistId }).toString()}`
    : ''
  return `/instruments/${instrumentId}${search}`
}
