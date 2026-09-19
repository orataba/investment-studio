import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { clearPortfolioApiCache } from '../lib/api'
import { getPortfolioBootstrap, type PortfolioBootstrap } from '../lib/bootstrap'

type BootstrapState = { data: PortfolioBootstrap | null; error: string | null; portfolioId: string | null }
const BootstrapContext = createContext<BootstrapState>({ data: null, error: null, portfolioId: null })
export function usePortfolioBootstrap() { return useContext(BootstrapContext) }

function authorityKey(value: PortfolioBootstrap) {
  return JSON.stringify([value.user_id, value.session_id, value.local_unrestricted,
    value.can_create, value.can_write_team_research, value.is_team_owner, value.access])
}

/** Fetch session, deployment settings and current portfolio access as one identity-bound snapshot. */
export default function PortfolioBootstrapProvider({ portfolioId, children }: { portfolioId: string | null; children: ReactNode }) {
  const [state, setState] = useState<BootstrapState>({ data: null, error: null, portfolioId })
  useEffect(() => {
    let active = true
    let requestNumber = 0
    let confirmed: PortfolioBootstrap | null = null
    let controller: AbortController | null = null
    clearPortfolioApiCache()
    setState({ data: null, error: null, portfolioId })
    async function check(invalidate = false) {
      const number = ++requestNumber
      controller?.abort()
      const requestController = new AbortController()
      controller = requestController
      const timeout = window.setTimeout(() => requestController.abort(), 120_000)
      if (invalidate) {
        confirmed = null
        clearPortfolioApiCache()
        setState({ data: null, error: null, portfolioId })
      }
      try {
        const result = await getPortfolioBootstrap(portfolioId, requestController.signal)
        if (!active || number !== requestNumber) return
        if (portfolioId && (result.access?.portfolio_id !== portfolioId || result.access.user_id !== result.user_id || !result.access.can_read)) {
          throw new Error('组合不存在或无权访问')
        }
        if (confirmed && authorityKey(confirmed) !== authorityKey(result)) clearPortfolioApiCache()
        confirmed = result
        setState({ data: result, error: null, portfolioId })
      } catch (reason) {
        if (active && number === requestNumber) {
          confirmed = null
          clearPortfolioApiCache()
          setState({ data: null, portfolioId, error: requestController.signal.aborted
            ? '账号校验等待超时，请稍后刷新。' : reason instanceof Error ? reason.message : '请先登录' })
        }
      } finally { window.clearTimeout(timeout) }
    }
    const onFocus = () => { void check() }
    const onAuthChanged = () => { void check(true) }
    void check()
    window.addEventListener('focus', onFocus)
    window.addEventListener('studio-auth-changed', onAuthChanged)
    return () => {
      active = false
      controller?.abort()
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('studio-auth-changed', onAuthChanged)
    }
  }, [portfolioId])
  // The new route must never render with the prior portfolio's authority.
  const value = state.portfolioId === portfolioId ? state : { data: null, error: null, portfolioId }
  return <BootstrapContext.Provider value={value}>{children}</BootstrapContext.Provider>
}
