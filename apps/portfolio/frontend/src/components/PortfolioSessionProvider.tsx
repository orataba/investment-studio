import { createContext, useContext, type ReactNode } from 'react'
import type { PortfolioSession } from '../lib/api'
import { HOME_URL } from '../lib/navigation'
import CalculationStatus from './CalculationStatus'
import { usePortfolioBootstrap } from './PortfolioBootstrapProvider'

const SessionContext = createContext<PortfolioSession | null>(null)
export function usePortfolioSession() { return useContext(SessionContext) }
export default function PortfolioSessionProvider({ children }: { children: ReactNode }) {
  const { data: session, error } = usePortfolioBootstrap()
  if (error) return <div role="alert" className="inline-notice inline-notice-error">{error} <a href={`${import.meta.env.BASE_URL.replace(/\/$/, '')}/portfolios`}>返回我的组合</a> · <a href={HOME_URL}>前往账号入口</a></div>
  if (!session) return <CalculationStatus label="正在确认账号与组合权限…" />
  return <SessionContext.Provider key={`${session.user_id}:${session.session_id}:${session.local_unrestricted}`} value={session}>{children}</SessionContext.Provider>
}
