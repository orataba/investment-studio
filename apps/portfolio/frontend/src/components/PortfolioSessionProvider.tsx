import { createContext, useContext, type ReactNode } from 'react'
import type { PortfolioSession } from '../lib/api'
import RequestRecovery from '../../../../../packages/ui/src/RequestRecovery'
import CalculationStatus from './CalculationStatus'
import { usePortfolioBootstrap } from './PortfolioBootstrapProvider'

const SessionContext = createContext<PortfolioSession | null>(null)
export function usePortfolioSession() { return useContext(SessionContext) }
export default function PortfolioSessionProvider({ children }: { children: ReactNode }) {
  const { data: session, error, retry } = usePortfolioBootstrap()
  if (error) return <RequestRecovery error={error} onRetry={retry} />
  if (!session) return <CalculationStatus label="正在确认账号与组合权限…" />
  return <SessionContext.Provider key={`${session.user_id}:${session.session_id}:${session.local_unrestricted}`} value={session}>{children}</SessionContext.Provider>
}
