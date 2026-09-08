import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { clearPortfolioApiCache, getPortfolioSession, type PortfolioSession } from '../lib/api'
import { HOME_URL } from '../lib/navigation'
import CalculationStatus from './CalculationStatus'

const SessionContext = createContext<PortfolioSession | null>(null)
export function usePortfolioSession() { return useContext(SessionContext) }
export default function PortfolioSessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<PortfolioSession | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    let requestNumber = 0
    let confirmed: PortfolioSession | null = null
    clearPortfolioApiCache()
    async function check(invalidate = false) {
      const checkNumber = ++requestNumber
      if (invalidate) {
        confirmed = null
        setSession(null); clearPortfolioApiCache(); setError(null)
      }
      try {
        const result = await getPortfolioSession()
        if (!active || checkNumber !== requestNumber) return
        if (confirmed && (confirmed.user_id !== result.user_id || confirmed.session_id !== result.session_id || confirmed.local_unrestricted !== result.local_unrestricted)) clearPortfolioApiCache()
        confirmed = result
        setSession(result); setError(null)
      } catch (reason) {
        if (active && checkNumber === requestNumber) {
          confirmed = null
          clearPortfolioApiCache(); setSession(null)
          setError(reason instanceof Error ? reason.message : '请先登录')
        }
      }
    }
    const checkOnFocus = () => { void check() }
    const checkChangedAuth = () => { void check(true) }
    void check()
    window.addEventListener('focus', checkOnFocus)
    window.addEventListener('studio-auth-changed', checkChangedAuth)
    return () => { active = false; window.removeEventListener('focus', checkOnFocus); window.removeEventListener('studio-auth-changed', checkChangedAuth) }
  }, [])
  if (error) return <div role="alert" className="inline-notice inline-notice-error">{error} <a href={HOME_URL}>前往账号入口</a></div>
  if (!session) return <CalculationStatus label="正在确认登录账号…" />
  return <SessionContext.Provider key={`${session.user_id}:${session.session_id}:${session.local_unrestricted}`} value={session}>{children}</SessionContext.Provider>
}
