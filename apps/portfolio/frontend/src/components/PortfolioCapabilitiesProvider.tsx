import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { getPortfolioCapabilities, type PortfolioCapabilities } from '../lib/api'
import CalculationStatus from './CalculationStatus'

export const PortfolioCapabilitiesContext = createContext<PortfolioCapabilities>({ research_enabled: false })

export function usePortfolioCapabilities() {
  return useContext(PortfolioCapabilitiesContext)
}

export default function PortfolioCapabilitiesProvider({ children }: { children: ReactNode }) {
  const [capabilities, setCapabilities] = useState<PortfolioCapabilities | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getPortfolioCapabilities()
      .then((response) => {
        if (!cancelled) setCapabilities(response)
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Request failed.')
      })
    return () => { cancelled = true }
  }, [])

  if (error) return <div className="inline-notice inline-notice-error" role="alert">{error}</div>
  if (!capabilities) return <CalculationStatus label="Loading settings…" />

  return (
    <PortfolioCapabilitiesContext.Provider value={capabilities}>
      {children}
    </PortfolioCapabilitiesContext.Provider>
  )
}
