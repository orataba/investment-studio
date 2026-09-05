import { resolveWorkspaceUrl } from '../../../../../packages/ui/src/navigation'

export const HOME_URL = resolveWorkspaceUrl(import.meta.env.VITE_HOME_URL, 'home')

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
