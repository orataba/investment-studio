import { describe, expect, it } from 'vitest'

import appSource from './App.tsx?raw'
import workspaceLayoutSource from './components/PortfolioWorkspaceLayout.tsx?raw'
import apiSource from './lib/api.ts?raw'
import portfolioIaSource from './lib/portfolioIa.ts?raw'
import allocationLabSource from './pages/AllocationLabPage.tsx?raw'
import portfoliosSource from './pages/PortfoliosPage.tsx?raw'
import riskSource from './pages/RiskPage.tsx?raw'

describe('allocation research canonical frontend semantics', () => {
  it('uses only Allocation Research and Policy Replay contracts and routes', () => {
    expect(apiSource).toContain('export type PortfolioAllocationResearchWorkbenchResponse')
    expect(apiSource).toContain('export type PortfolioPolicyReplayRecord')
    expect(apiSource).toContain('/allocation-research/workbench')
    expect(apiSource).toContain('/policy-replay/benchmark-comparison')
    expect(apiSource).not.toContain('PortfolioResearch')
    expect(apiSource).not.toContain('Backtest')
    expect(apiSource).not.toContain('backtest_')
    expect(apiSource).not.toMatch(/\/research(?:\/|[`'"$])/)

    expect(appSource).toContain('/portfolios/:portfolioId/allocation-research')
    expect(appSource).not.toContain('/portfolios/:portfolioId/research')
    expect(portfolioIaSource).toContain("label: 'Allocation Lab'")
    expect(allocationLabSource).toContain('>Policy Replay</div>')
    expect(riskSource).toContain('>Allocation Policy Drift</div>')
    expect(riskSource).not.toContain('>Current Drift</div>')
  })

  it('requires an explicit operating profile and gates Allocation Lab by that fact', () => {
    expect(apiSource).toContain(
      "export type PortfolioOperatingProfile = 'standard_taxonomy' | 'external_etf_rotation'",
    )
    expect(apiSource).toMatch(
      /export type PortfolioCreatePayload = \{[\s\S]*?operating_profile: PortfolioOperatingProfile/,
    )
    expect(portfoliosSource).toContain("useState<PortfolioOperatingProfile>('standard_taxonomy')")
    expect(portfoliosSource).toContain('operating_profile: createOperatingProfile')
    expect(workspaceLayoutSource).toContain(
      "currentOperatingProfile === 'standard_taxonomy'",
    )
    expect(allocationLabSource).toContain(
      "portfolio.operating_profile === 'external_etf_rotation'",
    )
    expect(allocationLabSource).toContain('Allocation Lab is not applicable')
  })
})
