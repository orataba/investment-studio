import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import type { CorrelationHistoryObservation } from '../lib/correlationHistory'
import { holdingsWorkspaceFixture } from '../test/portfolioFixtures'
import CorrelationObservation from './CorrelationObservation'
import PortfolioRiskDrawer from './PortfolioRiskDrawer'

const getHoldingsWorkspace = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ getHoldingsWorkspace }))
vi.mock('./PortfolioInstrumentRisk', () => ({ default: () => <p>Existing assessed risk remains visible</p> }))

const renderWithLanguage = (element: ReactElement) => render(element, {
  wrapper: ({ children }) => <LanguageProvider enableDomTranslation={false}>{children}</LanguageProvider>,
})

function observation(overrides: Partial<CorrelationHistoryObservation> = {}): CorrelationHistoryObservation {
  return {
    current: { asOfDate: '2026-09-15', windowStartDate: '2026-08-15', observedStartDate: '2026-08-16', observedEndDate: '2026-09-15', firstPeriodStartDate: '2026-08-15', averageCorrelation: 0.200009, observationCount: 22, pairs: [] },
    previous: { asOfDate: '2026-08-15', windowStartDate: '2026-07-15', observedStartDate: '2026-07-16', observedEndDate: '2026-08-15', firstPeriodStartDate: '2026-07-15', averageCorrelation: 0.200002, observationCount: 23, pairs: [] },
    memberCount: 3, pairCount: 3, risingPairCount: 3, averageChange: 0.000007,
    status: 'available', unavailableReason: null, attention: true,
    referenceWindowCount: 12, referenceChangeCount: 11, levelUpperFence: 0.2000041875, changeUpperFence: 0.00000475,
    ...overrides,
  }
}

beforeEach(() => getHoldingsWorkspace.mockResolvedValue(holdingsWorkspaceFixture()))

describe('Correlation observation presentation', () => {
  it('keeps a small flagged change readable instead of rounding it to zero', () => {
    renderWithLanguage(<CorrelationObservation scopeLabel="Current classifications" observation={observation()} />)
    expect(screen.getByText('+7.00e-6')).toBeInTheDocument()
    expect(screen.queryByText('+0.000')).not.toBeInTheDocument()
    expect(screen.getByText('Unusual correlation rise — diversification may weaken')).toBeInTheDocument()
  })

  it('preserves the sign of a small decline without inventing an attention cue', () => {
    renderWithLanguage(<CorrelationObservation scopeLabel="Current classifications" observation={observation({ averageChange: -0.000007, attention: false, risingPairCount: 0 })} />)
    expect(screen.getByText('-7.00e-6')).toBeInTheDocument()
    expect(screen.queryByText(/Unusual correlation rise/)).not.toBeInTheDocument()
  })

  it('omits descriptive non-alert observations from the risk drawer while preserving assessed risks', async () => {
    renderWithLanguage(<PortfolioRiskDrawer portfolioId="3" onClose={vi.fn()} onAskAssistant={vi.fn()}
      correlationObservation={{ scopeLabel: 'Quiet basket', observation: observation({ attention: false }) }} />)
    expect(await screen.findByText('Existing assessed risk remains visible')).toBeInTheDocument()
    expect(screen.queryByText(/Latest monthly mean correlation/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Quiet basket/)).not.toBeInTheDocument()
  })

  it('replaces an open drawer observation when scope or date changes and removes it when the cue clears', async () => {
    const props = { portfolioId: '3', onClose: vi.fn(), onAskAssistant: vi.fn() }
    const { rerender } = renderWithLanguage(<PortfolioRiskDrawer {...props}
      correlationObservation={{ scopeLabel: 'Old basket', observation: observation() }} />)
    await screen.findByText('Existing assessed risk remains visible')
    expect(screen.getByText('Old basket').closest('p')).toHaveTextContent('Old basket · 2026-09-15')

    const next = observation()
    next.current = { ...next.current, asOfDate: '2026-08-15', windowStartDate: '2026-07-15' }
    rerender(<PortfolioRiskDrawer {...props} correlationObservation={{ scopeLabel: 'New basket', observation: next }} />)
    expect(screen.getByText('New basket').closest('p')).toHaveTextContent('New basket · 2026-08-15')
    expect(screen.queryByText(/Old basket/)).not.toBeInTheDocument()

    rerender(<PortfolioRiskDrawer {...props} correlationObservation={{ scopeLabel: 'New basket', observation: { ...next, attention: false } }} />)
    expect(screen.queryByText(/Unusual correlation rise/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Latest monthly mean correlation/)).not.toBeInTheDocument()
    expect(screen.getByText('Existing assessed risk remains visible')).toBeInTheDocument()

    rerender(<PortfolioRiskDrawer {...props} />)
    expect(screen.queryByText(/New basket/)).not.toBeInTheDocument()
    expect(screen.getByText('Existing assessed risk remains visible')).toBeInTheDocument()
  })
})
