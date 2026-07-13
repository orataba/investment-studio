import { describe, expect, it } from 'vitest'

import apiSource from './lib/api.ts?raw'
import historyPresentationSource from './lib/performanceHistoryPresentation.ts?raw'
import overviewPageSource from './pages/OverviewPage.tsx?raw'
import performancePageSource from './pages/PerformancePage.tsx?raw'
import researchPageSource from './pages/ResearchPage.tsx?raw'

describe('performance history reliability authority boundary', () => {
  it('requires backend eligibility, span, reason, and display facts in the API contract', () => {
    expect(
      apiSource.match(
        /history_reliability: PortfolioPerformanceHistoryReliability/g,
      ),
    ).toHaveLength(3)
    expect(apiSource).toContain('elapsed_days: number | null')
    expect(apiSource).toContain('calendar_span_days: number | null')
    expect(apiSource).toContain('minimum_history_days: number')
    expect(apiSource).toContain('annualized_return_eligible: boolean')
    expect(apiSource).toContain('annualized_return_reason_codes:')
    expect(apiSource).toContain('sample_label: string')
    expect(apiSource).toContain('annualization_message: string | null')
  })

  it('renders backend history policy without a browser date or eligibility engine', () => {
    expect(performancePageSource).toContain(
      'selectPerformanceHistoryReliability(',
    )
    expect(performancePageSource).toContain(
      'comparison?.history_reliability',
    )
    expect(performancePageSource).toContain(
      'annualizedReturnDisplayEligible(historyReliability)',
    )
    expect(performancePageSource).toContain(
      'performanceHistoryReliability?.sample_label',
    )
    expect(performancePageSource).toContain(
      'performanceHistoryReliability?.annualization_message',
    )
    expect(performancePageSource).toContain(
      "annualizedReturnEligible ? signedPercent(portfolioAnnualizedReturn) : 'N/A'",
    )
    expect(performancePageSource).toContain(
      "annualizedReturnEligible ? signedPercent(irr) : 'N/A'",
    )
    expect(performancePageSource).toContain(
      "annualizedReturnEligible ? formatRatio(calmarRatio) : 'N/A'",
    )
    ;[
      'buildPerformanceHistoryReliability',
      'MIN_ANNUALIZED_RETURN_HISTORY_DAYS',
      'MILLISECONDS_PER_DAY',
      'Date.parse',
      'annualizationMessage',
      'sampleLabel',
    ].forEach((forbidden) => expect(performancePageSource).not.toContain(forbidden))
    ;[
      'MIN_ANNUALIZED_RETURN_HISTORY_DAYS',
      'MILLISECONDS_PER_DAY',
      'Date.parse',
    ].forEach((forbidden) => expect(historyPresentationSource).not.toContain(forbidden))
  })

  it('withholds Overview annualized TWR and money-weighted returns unless eligible', () => {
    expect(overviewPageSource).toContain(
      'annualizedReturnDisplayEligible(',
    )
    expect(overviewPageSource).toContain(
      "overviewAnnualizedReturnEligible\n            ? signedPercent(performanceWorkspace?.summary.annualized_twr)\n            : 'N/A'",
    )
    expect(overviewPageSource).toContain(
      "overviewAnnualizedReturnEligible\n            ? `${signedPercent(performanceWorkspace?.summary.irr)} / ${signedPercent(performanceWorkspace?.summary.mwror)}`\n            : 'N/A'",
    )
    expect(overviewPageSource).not.toContain('.calmar_ratio')
  })

  it('keeps Calmar and benchmark metrics on backend-authoritative fields', () => {
    expect(performancePageSource).toContain('portfolioComparisonMetrics?.annualized_return')
    expect(performancePageSource).toContain('summary.calmar_ratio')
    expect(performancePageSource).not.toContain('ratioToDrawdown')
    expect(performancePageSource).not.toContain('buildBenchmarkPeriodMetrics')
  })

  it('fails closed research annualized metrics against each column history contract', () => {
    const rowSource = (label: string) => {
      const start = researchPageSource.indexOf(`label: '${label}'`)
      const end = researchPageSource.indexOf('\n    },', start)
      expect(start).toBeGreaterThanOrEqual(0)
      expect(end).toBeGreaterThan(start)
      return researchPageSource.slice(start, end)
    }

    expect(apiSource).toMatch(
      /export type PortfolioResearchBacktestMetricsRecord = \{[\s\S]*?history_reliability: PortfolioPerformanceHistoryReliability/,
    )
    expect(rowSource('Annual Return')).toContain('requiresAnnualizedHistory: true')
    expect(rowSource('Calmar')).toContain('requiresAnnualizedHistory: true')
    ;['Period Return', 'Annual Volatility', 'Sharpe'].forEach((label) => {
      expect(rowSource(label)).not.toContain('requiresAnnualizedHistory: true')
    })
    expect(
      researchPageSource.match(/requiresAnnualizedHistory: true/g),
    ).toHaveLength(2)
    expect(researchPageSource).toMatch(
      /row\.requiresAnnualizedHistory === true,\s*metrics,/,
    )
    expect(researchPageSource).toMatch(
      /row\.requiresAnnualizedHistory === true,\s*benchmarkMetrics,/,
    )
    expect(researchPageSource).toMatch(
      /row\.requiresAnnualizedHistory === true,\s*relative,/,
    )
    expect(researchPageSource).toContain(
      '!annualizedReturnDisplayEligible(record?.history_reliability)',
    )
    expect(researchPageSource).toContain("return 'N/A'")
    expect(researchPageSource).toContain('reliability.sample_label')
    expect(researchPageSource).toContain('reliability.annualization_message')
    ;[
      'MIN_ANNUALIZED_RETURN_HISTORY_DAYS',
      'MILLISECONDS_PER_DAY',
      'Date.parse',
      'minimum_history_days',
    ].forEach((forbidden) => expect(researchPageSource).not.toContain(forbidden))
  })
})
