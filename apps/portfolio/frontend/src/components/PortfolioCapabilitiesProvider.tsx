import { createContext, useContext, type ReactNode } from 'react'
import type { PortfolioCapabilities } from '../lib/api'
import CalculationStatus from './CalculationStatus'
import { usePortfolioBootstrap } from './PortfolioBootstrapProvider'

export const PortfolioCapabilitiesContext = createContext<PortfolioCapabilities>({ research_enabled: false })
export function usePortfolioCapabilities() { return useContext(PortfolioCapabilitiesContext) }

export default function PortfolioCapabilitiesProvider({ children }: { children: ReactNode }) {
  const { data } = usePortfolioBootstrap()
  if (!data) return <CalculationStatus label="Loading settings…" />
  return <PortfolioCapabilitiesContext.Provider value={data.capabilities}>{children}</PortfolioCapabilitiesContext.Provider>
}
