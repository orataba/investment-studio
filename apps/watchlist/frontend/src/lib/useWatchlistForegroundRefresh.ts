import { useEffect, useRef } from 'react'
import { getWatchlists, type WatchlistRecord } from './api'

export function useWatchlistForegroundRefresh(
  scopeKey: string | null,
  onSynchronized: (watchlists: WatchlistRecord[]) => void,
  onError: (message: string) => void,
) {
  const callbacks = useRef({ onSynchronized, onError })
  callbacks.current = { onSynchronized, onError }

  useEffect(() => {
    if (!scopeKey) return
    let cancelled = false
    let inFlight = false

    async function refresh() {
      if (document.visibilityState !== 'visible' || inFlight) return
      inFlight = true
      try {
        // Reading the directory reconciles Registry membership before rows load.
        const watchlists = await getWatchlists()
        if (!cancelled) callbacks.current.onSynchronized(watchlists)
      } catch (error) {
        if (!cancelled) callbacks.current.onError(
          error instanceof Error ? error.message : 'Failed to refresh watchlists.',
        )
      } finally {
        inFlight = false
      }
    }

    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      cancelled = true
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [scopeKey])
}
