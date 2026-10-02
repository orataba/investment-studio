import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import RiskSourceCoverage from './RiskSourceCoverage'
import { holdingFixture, holdingsWorkspaceFixture, instrumentFixture } from '../test/portfolioFixtures'

function workspace(gapCount: number, dates: string[], completeDates?: string[]) {
  const value = holdingsWorkspaceFixture({
    as_of_date: '2026-09-30',
    rows: [holdingFixture({ instrument_core: instrumentFixture({ instrument_id: 'fund', instrument_name: 'Test Fund' }),
      instrument_return_series_all: completeDates ? { points: [], observation_coverage: {
        start_date: '2025-01-01', end_date: '2026-09-30', gap_dates: completeDates, gap_detection_basis: 'calendar',
      } } : null,
    })],
  })
  value.risk_policy = { ...value.risk_policy!, lookback_days: 30 }
  value.risk_basis = { ...value.risk_basis!, gap_details: [{ instrument_id: 'fund', gap_count: gapCount, gap_date_sample: dates, gap_detection_basis: 'calendar' }] }
  return value
}

describe('source coverage relevance', () => {
  it('omits fully known gaps outside the active model return window', () => {
    const { container } = render(<LanguageProvider enableDomTranslation={false}><RiskSourceCoverage workspace={workspace(2, ['2026-08-30', '2026-10-01'])} /></LanguageProvider>)
    expect(container).toBeEmptyDOMElement()
  })
  it('retains in-window gaps using complete dates rather than a truncated sample', () => {
    render(<LanguageProvider enableDomTranslation={false}><RiskSourceCoverage workspace={workspace(2, ['2025-01-01'], ['2025-01-01', '2026-09-10'])} /></LanguageProvider>)
    expect(screen.getByText(/2026-09-10/)).toBeInTheDocument()
    expect(screen.queryByText(/2025-01-01/)).not.toBeInTheDocument()
  })
  it('does not infer complete coverage from a truncated out-of-window sample', () => {
    render(<LanguageProvider enableDomTranslation={false}><RiskSourceCoverage workspace={workspace(4, ['2025-01-01'])} /></LanguageProvider>)
    expect(screen.getByText('Test Fund')).toBeInTheDocument()
    expect(screen.getByText(/4 in total/)).toBeInTheDocument()
  })
  it('retains a pre-boundary source gap crossed by a return used inside the window', () => {
    const value = workspace(2, ['2026-08-27', '2026-08-29'], ['2026-08-27', '2026-08-29'])
    value.rows[0].instrument_return_series_all!.points = [
      { date: '2026-09-01', start_date: '2026-08-28', value: 0.01 },
    ]
    render(<LanguageProvider enableDomTranslation={false}><RiskSourceCoverage workspace={value} /></LanguageProvider>)
    expect(screen.getByText('Test Fund')).toBeInTheDocument()
    expect(screen.getByText(/2026-08-29/)).toBeInTheDocument()
    expect(screen.queryByText(/2026-08-27/)).not.toBeInTheDocument()
  })
})
