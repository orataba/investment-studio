import { useEffect, useState } from 'react'
import { searchPortfolioSecurities, type SecurityCatalogResponse } from './api'

export function useSecurityCatalog(portfolioId: string | undefined, query: string, enabled: boolean) {
  enabled = enabled && Boolean(portfolioId)
  const normalized = query.trim()
  const [state, setState] = useState<{
    key: string; response?: SecurityCatalogResponse; error?: string
  } | null>(null)
  const key = JSON.stringify([portfolioId, normalized])
  useEffect(() => {
    if (!enabled || !portfolioId || !normalized) {
      setState(null)
      return
    }
    let cancelled = false
    setState(null)
    const timer = window.setTimeout(() => {
      searchPortfolioSecurities(portfolioId, normalized).then((response) => {
        if (!cancelled) setState({ key, response })
      }).catch((error: unknown) => {
        if (!cancelled) setState({ key, error: error instanceof Error ? error.message : 'Security catalog search failed.' })
      })
    }, 250)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [portfolioId, normalized, enabled, key])
  const active = enabled && normalized && state?.key === key ? state : null
  return {
    results: active?.response?.results ?? [],
    loading: Boolean(enabled && normalized && !active),
    error: active?.error ?? (Object.values(active?.response?.catalog_errors ?? {}).join(' ') || null),
  }
}
