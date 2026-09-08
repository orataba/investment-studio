import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { clearPortfolioApiCache, getPortfolioAccess, type PortfolioAccess } from '../lib/api'
import CalculationStatus from './CalculationStatus'

export const PortfolioAccessContext = createContext<PortfolioAccess | null>(null)
export function usePortfolioAccess() { return useContext(PortfolioAccessContext) }

export default function PortfolioAccessProvider({ children }: { children: ReactNode }) {
  const { portfolioId = '' } = useParams()
  const [access, setAccess] = useState<PortfolioAccess | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    let requestNumber = 0
    let confirmed: PortfolioAccess | null = null
    async function check(invalidate = false) {
      const checkNumber = ++requestNumber
      if (invalidate) {
        confirmed = null
        clearPortfolioApiCache(); setAccess(null); setError(null)
      }
      try {
        const response = await getPortfolioAccess(portfolioId)
        if (!active || checkNumber !== requestNumber) return
        if (confirmed && (confirmed.team_id !== response.team_id || confirmed.role !== response.role || confirmed.can_read !== response.can_read || confirmed.can_edit !== response.can_edit || confirmed.can_manage !== response.can_manage || confirmed.local_unrestricted !== response.local_unrestricted)) clearPortfolioApiCache()
        confirmed = response
        setAccess(response); setError(null)
      } catch (reason) {
        if (active && checkNumber === requestNumber) {
          confirmed = null
          clearPortfolioApiCache(); setAccess(null)
          setError(reason instanceof Error ? reason.message : '无法读取组合权限')
        }
      }
    }
    const checkOnFocus = () => { void check() }
    const checkChangedAuth = () => { void check(true) }
    void check()
    window.addEventListener('focus', checkOnFocus)
    window.addEventListener('studio-auth-changed', checkChangedAuth)
    return () => { active = false; window.removeEventListener('focus', checkOnFocus); window.removeEventListener('studio-auth-changed', checkChangedAuth) }
  }, [portfolioId])
  if (error) return <div role="alert" className="inline-notice inline-notice-error">{error} <Link to="/portfolios">返回我的组合</Link></div>
  if (!access || access.portfolio_id !== portfolioId) return <CalculationStatus label="正在确认组合访问权限…" />
  return <PortfolioAccessContext.Provider value={access}>{children}</PortfolioAccessContext.Provider>
}
