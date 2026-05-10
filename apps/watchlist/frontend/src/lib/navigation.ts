function normalizeConfiguredUrl(value: string | undefined, fallback: string) {
  const normalized = (value || '').trim().replace(/\/$/, '')
  return normalized || fallback
}

export const PLATFORM_HOME_URL = normalizeConfiguredUrl(
  import.meta.env.VITE_PLATFORM_URL,
  'http://127.0.0.1:5172',
)

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
