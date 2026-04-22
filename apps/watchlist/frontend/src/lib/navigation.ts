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

export function buildWatchlistInstrumentPath(watchlistId: string, assetId: string) {
  return `${buildWatchlistPath(watchlistId)}/instruments/${assetId}`
}
