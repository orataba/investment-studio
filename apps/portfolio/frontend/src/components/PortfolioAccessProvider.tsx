import { createContext, useContext, type ReactNode } from 'react'
import { useParams } from 'react-router'
import type { PortfolioAccess } from '../lib/api'
import CalculationStatus from './CalculationStatus'
import { usePortfolioBootstrap } from './PortfolioBootstrapProvider'

export const PortfolioAccessContext = createContext<PortfolioAccess | null>(null)
export function usePortfolioAccess() { return useContext(PortfolioAccessContext) }

export default function PortfolioAccessProvider({ children }: { children: ReactNode }) {
  const { portfolioId = '' } = useParams()
  const { data } = usePortfolioBootstrap()
  const access = data?.access
  if (!access || access.portfolio_id !== portfolioId || !access.can_read) return <CalculationStatus label="正在确认组合访问权限…" />
  return <PortfolioAccessContext.Provider value={access}>{children}</PortfolioAccessContext.Provider>
}
