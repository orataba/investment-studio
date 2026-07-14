import { describe, expect, it } from 'vitest'

import apiSource from './lib/api.ts?raw'
import historyPresentationSource from './lib/performanceHistoryPresentation.ts?raw'
import overviewPageSource from './pages/OverviewPage.tsx?raw'
import performancePageSource from './pages/PerformancePage.tsx?raw'
import allocationLabPageSource from './pages/AllocationLabPage.tsx?raw'

describe('performance history reliability authority boundary', () => {
  it('requires method50, published, and rounding facts in the published metric contract', () => {
    expect(apiSource).toContain('export type PortfolioDailyPublishedDecimalMetric = {')
    expect(apiSource).toContain('annualized_twr: PortfolioDailyPublishedDecimalMetric | null')
    expect(apiSource).toContain('annualized_volatility: PortfolioDailyPublishedDecimalMetric | null')
    expect(apiSource).toContain('rate: PortfolioDailyPublishedDecimalMetric | null')
    expect(apiSource).toContain('method50: string')
    expect(apiSource).toContain('published: string')
    expect(apiSource).toContain('rounding_adjustment_exact: string')
    expect(apiSource).toContain('elapsed_days: number | null')
    expect(apiSource).toContain('reason_codes: string[]')
  })

  it('renders backend-nullable annualized metrics without a browser eligibility engine', () => {
    expect(performancePageSource).toContain('performance?.annualized_twr?.method50')
    expect(performancePageSource).toContain('xirr.rate?.method50')
    expect(performancePageSource).toContain('statistics?.annualized_volatility?.method50')
    ;[
      'selectPerformanceHistoryReliability',
      'annualizedReturnDisplayEligible',
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

  it('withholds Overview annualized TWR and XIRR when the published metric is null', () => {
    expect(overviewPageSource).toContain(
      'performanceSummary?.annualized_twr?.method50',
    )
    expect(overviewPageSource).toContain('performanceReport.xirr.rate?.method50')
    expect(overviewPageSource).toContain('xirr.annualized_headline_eligible')
    expect(overviewPageSource).toContain(
      "value: annualizedTwr == null ? 'N/A' : signedPercent(annualizedTwr)",
    )
    expect(overviewPageSource).toContain(
      "value: xirr == null ? 'N/A' : signedPercent(xirr)",
    )
    expect(overviewPageSource).not.toContain('annualizedReturnDisplayEligible')
    expect(overviewPageSource).not.toContain('.calmar_ratio')
  })

  it('withholds benchmark and Calmar until they exist in the published report', () => {
    expect(performancePageSource).not.toContain('getPortfolioPerformanceComparison')
    expect(performancePageSource).not.toContain('benchmarkComparison')
    expect(performancePageSource).not.toContain('calmar_ratio')
    expect(performancePageSource).not.toContain('ratioToDrawdown')
    expect(performancePageSource).not.toContain('buildBenchmarkPeriodMetrics')
  })

  it('fails closed policy replay annualized metrics against each column history contract', () => {
    const rowSource = (label: string) => {
      const start = allocationLabPageSource.indexOf(`label: '${label}'`)
      const end = allocationLabPageSource.indexOf('\n    },', start)
      expect(start).toBeGreaterThanOrEqual(0)
      expect(end).toBeGreaterThan(start)
      return allocationLabPageSource.slice(start, end)
    }

    expect(apiSource).toMatch(
      /export type PortfolioPolicyReplayMetricsRecord = \{[\s\S]*?history_reliability: PortfolioPerformanceHistoryReliability/,
    )
    expect(rowSource('Annual Return')).toContain('requiresAnnualizedHistory: true')
    expect(rowSource('Calmar')).toContain('requiresAnnualizedHistory: true')
    ;['Period Return', 'Annual Volatility', 'Sharpe'].forEach((label) => {
      expect(rowSource(label)).not.toContain('requiresAnnualizedHistory: true')
    })
    expect(
      allocationLabPageSource.match(/requiresAnnualizedHistory: true/g),
    ).toHaveLength(2)
    expect(allocationLabPageSource).toMatch(
      /row\.requiresAnnualizedHistory === true,\s*metrics,/,
    )
    expect(allocationLabPageSource).toMatch(
      /row\.requiresAnnualizedHistory === true,\s*benchmarkMetrics,/,
    )
    expect(allocationLabPageSource).toMatch(
      /row\.requiresAnnualizedHistory === true,\s*relative,/,
    )
    expect(allocationLabPageSource).toContain(
      '!annualizedReturnDisplayEligible(record?.history_reliability)',
    )
    expect(allocationLabPageSource).toContain("return 'N/A'")
    expect(allocationLabPageSource).toContain('reliability.sample_label')
    expect(allocationLabPageSource).toContain('reliability.annualization_message')
    ;[
      'MIN_ANNUALIZED_RETURN_HISTORY_DAYS',
      'MILLISECONDS_PER_DAY',
      'Date.parse',
      'minimum_history_days',
    ].forEach((forbidden) => expect(allocationLabPageSource).not.toContain(forbidden))
  })
})
