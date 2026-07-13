import { describe, expect, it } from 'vitest'

import instrumentChartSource from './components/InstrumentPriceChart.tsx?raw'
import performanceNavChartSource from './components/PerformanceNavChart.tsx?raw'
import portfolioWorkspaceLayoutSource from './components/PortfolioWorkspaceLayout.tsx?raw'
import apiSource from './lib/api.ts?raw'
import formatSource from './lib/format.ts?raw'
import performanceSeriesSource from './lib/performanceSeries.ts?raw'
import overviewSource from './pages/OverviewPage.tsx?raw'
import performanceSource from './pages/PerformancePage.tsx?raw'
import portfolioHomeSource from './pages/PortfolioHomePage.tsx?raw'
import portfolioSecurityDetailSource from './pages/PortfolioSecurityDetailPage.tsx?raw'
import portfoliosSource from './pages/PortfoliosPage.tsx?raw'
import researchSource from './pages/ResearchPage.tsx?raw'
import riskSource from './pages/RiskPage.tsx?raw'

describe('portfolio frontend calculation authority boundary', () => {
  it('uses the backend return calendar and has no daily-return calendar engine', () => {
    expect(apiSource).toContain('/performance/calendar')
    expect(overviewSource).toContain('getPortfolioReturnCalendar')
    expect(overviewSource).not.toContain("from '../lib/monthlyReturns'")
    expect(overviewSource).not.toContain('buildMonthlyBuckets')
    expect(overviewSource).not.toContain('buildMonthlyReturnMatrixRows')
  })

  it('does not rebuild overview return, benchmark, risk, cash, or FX metrics', () => {
    ;[
      'periodReturnFromTwr',
      'buildPortfolioReturnMetrics',
      'sampleStandardDeviation',
      'trailingAnnualizedVolatility',
      'buildPortfolioRiskMetrics',
      'buildBenchmarkMetrics',
      'cashValueRaw',
      'daily_twr',
    ].forEach((forbidden) => expect(overviewSource).not.toContain(forbidden))
    expect(overviewSource).not.toMatch(/market_value_base\s*\?\?\s*row\.market_value/)
    expect(overviewSource).not.toMatch(/cost_basis_base\s*\?\?\s*row\.cost_basis/)
    expect(overviewSource).not.toMatch(/day_change_value_base\s*\?\?\s*row\.day_change_value/)
  })

  it('builds the TWR display index exclusively from cumulative TWR', () => {
    expect(performanceSeriesSource).toContain('point.cumulative_twr')
    expect(performanceSeriesSource).not.toContain('daily_twr')
    expect(performanceSeriesSource).not.toContain('compounded')
  })

  it('does not derive chart returns or drawdowns from plotted points', () => {
    ;['buildDrawdownPoints', 'activePeriodTwr', 'windowReturn', 'benchmarkPeriodReturn', 'changePct'].forEach(
      (forbidden) => expect(performanceNavChartSource).not.toContain(forbidden),
    )
    expect(instrumentChartSource).toContain('chart?.summary.change_value')
    expect(instrumentChartSource).toContain('chart?.summary.high')
    expect(instrumentChartSource).not.toContain('activePoint.value - firstPoint.value')
  })

  it('does not synthesize missing research metrics from backtest points', () => {
    ;[
      'drawdownPoints',
      'currentDrawdownFromPoints',
      'returnMapByDate',
      'activeCurrentDrawdown',
      'metricDifference',
      'excessReturnMetric',
    ].forEach((forbidden) => expect(researchSource).not.toContain(forbidden))
  })

  it('renders Risk exclusively from the backend-authoritative risk workspace', () => {
    expect(apiSource).toContain('/risk/workspace')
    expect(riskSource).toContain('getPortfolioRiskWorkspace')
    ;[
      'alignReturnSeriesToFrequency',
      'buildCurrentWeightedPortfolioReturnPoints',
      'buildRollingMetricPoints',
      'buildCorrelationMatrix',
      'buildRiskContributionRows',
      'sampleCovariance',
      'ewmaCovariance',
      'estimateCovarianceFromValues',
      'annualizedCovarianceFromValues',
      'annualizedVarianceFromValues',
      'commonReturnDateKeys',
      'returnWindowCoverage',
      'riskMinObservationsForWindow',
    ].forEach((forbidden) => expect(riskSource).not.toContain(forbidden))
    expect(riskSource).not.toContain('getHoldingsWorkspace')
    expect(riskSource).not.toContain('getPortfolioInstrumentPriceChart')
    expect(riskSource).not.toContain('getPortfolioTaxonomyCatalog')
  })

  it('does not calculate benchmark or relative performance in the browser', () => {
    expect(apiSource).toContain('/performance/comparison')
    expect(performanceSource).toContain('getPortfolioPerformanceComparison')
    expect(performanceSource).toContain('comparison.differences')
    expect(performanceSource).toContain('summary.calmar_ratio')
    expect(performanceSource).toContain(
      'annualizedReturnDisplayEligible(historyReliability)',
    )
    expect(performanceSource).toContain('comparison?.history_reliability')
    expect(performanceSource).toContain('benchmarkComparison.coverage.benchmark_quote_basis')
    expect(performanceSource).toContain('benchmarkComparison.unavailable_reasons')
    ;[
      'sampleStddev',
      'sampleCovariance',
      'sampleCorrelation',
      'annualizationPeriodsPerYear',
      'annualizedVolatility',
      'annualizedDownsideVolatility',
      'compoundReturn',
      'ratioToDrawdown',
      'buildBenchmarkPeriodMetrics',
      'buildRelativePerformanceMetrics',
      'benchmarkAlignedDailyReturns',
      'getPortfolioInstrumentPriceChart',
      'buildPerformanceHistoryReliability',
      'MIN_ANNUALIZED_RETURN_HISTORY_DAYS',
      'Date.parse',
    ].forEach((forbidden) => expect(performanceSource).not.toContain(forbidden))
    expect(performanceSource).not.toMatch(/summary\.[a-z_]+\s*-\s*benchmark/)
  })

  it('does not synthesize portfolio or group investment metrics from current holdings', () => {
    ;[
      'weightedHoldingMetric',
      'groupedReturnSeries',
      'sampleStddev',
      'groupedAnnualizedVolatility',
      'drawdownFromReturns',
      'groupedCurrentDrawdown',
      'groupedMaxDrawdown',
      'chartReturnForColumn',
      'priorMarketValue',
    ].forEach((forbidden) => expect(portfolioHomeSource).not.toContain(forbidden))
    expect(portfolioHomeSource).toContain('row.unrealized_pnl')
    expect(portfolioHomeSource).toContain('workspace.totals.unrealized_pnl_base')
    expect(portfolioHomeSource).not.toMatch(/marketValue\s*-\s*costBasis/)
    expect(portfolioSecurityDetailSource).toContain('selectedRow?.unrealized_pnl_base')
    expect(portfolioSecurityDetailSource).not.toMatch(/market_value_base\s*-\s*selectedRow\.cost_basis_base/)
  })

  it('never assigns USD when a display currency fact is unavailable', () => {
    expect(formatSource).not.toMatch(/currency\s*=\s*['"]USD['"]/)
    expect(portfolioWorkspaceLayoutSource).not.toContain("base_currency: 'USD'")
    expect(overviewSource).not.toMatch(/base_currency[^\n]*\?\?\s*['"]USD['"]/)
    expect(performanceSource).not.toMatch(/baseCurrency[^\n]*\?\?\s*['"]USD['"]/)
    expect(performanceSource).not.toMatch(/base_currency[^\n]*\?\?\s*['"]USD['"]/)
    expect(portfolioSecurityDetailSource).not.toMatch(/\?\?\s*['"]USD['"]/)
  })

  it('does not label a mixed-currency portfolio sum with the first currency', () => {
    expect(portfoliosSource).toContain('groupPortfolioTotalsByBaseCurrency')
    expect(portfoliosSource).not.toContain("resolvedPortfolios[0]?.base_currency")
  })

  it('keeps the ordinary planning-taxonomy path strategy-neutral', () => {
    expect(overviewSource).toContain("assignment.target_scope === 'instrument'")
    expect(overviewSource).toContain('defaultPlanningTaxonomyId')
    expect(overviewSource).not.toMatch(/instrument_type\s*===\s*['\"]etf['\"]/i)
    expect(overviewSource).not.toMatch(/rotation/i)
  })
})
