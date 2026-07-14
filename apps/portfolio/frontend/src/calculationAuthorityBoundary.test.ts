import { describe, expect, it } from 'vitest'

import instrumentChartSource from './components/InstrumentPriceChart.tsx?raw'
import performanceNavChartSource from './components/PerformanceNavChart.tsx?raw'
import portfolioWorkspaceLayoutSource from './components/PortfolioWorkspaceLayout.tsx?raw'
import apiSource from './lib/api.ts?raw'
import formatSource from './lib/format.ts?raw'
import overviewPublicationSource from './lib/overviewPublication.ts?raw'
import positionLotAggregationSource from './lib/positionLotAggregation.ts?raw'
import accountsSource from './pages/AccountsPage.tsx?raw'
import overviewSource from './pages/OverviewPage.tsx?raw'
import performanceSource from './pages/PerformancePage.tsx?raw'
import portfolioHomeSource from './pages/PortfolioHomePage.tsx?raw'
import portfolioSecurityDetailSource from './pages/PortfolioSecurityDetailPage.tsx?raw'
import portfoliosSource from './pages/PortfoliosPage.tsx?raw'
import allocationLabSource from './pages/AllocationLabPage.tsx?raw'
import riskSource from './pages/RiskPage.tsx?raw'
import transactionsSource from './pages/TransactionsPage.tsx?raw'

describe('portfolio frontend calculation authority boundary', () => {
  it('uses one published report for Overview performance and its return calendar', () => {
    expect(apiSource).toContain('/performance/report')
    expect(overviewSource).toContain('loadOverviewPublishedBundle')
    expect(overviewPublicationSource).toContain('getPortfolioPerformanceReport')
    expect(overviewPublicationSource).toContain("axis: 'instrument'")
    expect(overviewPublicationSource).toContain("frequency: 'monthly'")
    expect(overviewSource).not.toContain('getPortfolioPerformance(')
    expect(overviewSource).not.toContain('getPortfolioReturnCalendar')
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

  it('plots only published NAV and bounded method50 wealth/drawdown facts', () => {
    expect(overviewSource).toContain('point.closing_nav')
    expect(overviewSource).toContain('point.wealth_index_method50')
    expect(overviewSource).toContain('point.drawdown_method50')
    expect(overviewSource).toContain(
      'performanceReport?.portfolio_bridge?.economic_pnl_exact',
    )
    expect(overviewSource).toContain('holdingsWorkspace?.totals.exact_values?.nav')
    expect(overviewSource).not.toContain(
      'performanceReport?.portfolio_bridge?.closing_nav_exact',
    )
    expect(overviewSource).toContain('exactDecimalToDisplayNumber')
    expect(overviewSource).not.toContain('buildTwrIndexPoints')
    expect(overviewSource).not.toContain("from '../lib/performanceSeries'")
  })

  it('does not derive chart returns or drawdowns from plotted points', () => {
    ;['buildDrawdownPoints', 'activePeriodTwr', 'windowReturn', 'benchmarkPeriodReturn', 'changePct'].forEach(
      (forbidden) => expect(performanceNavChartSource).not.toContain(forbidden),
    )
    expect(instrumentChartSource).toContain('chart?.summary.change_value')
    expect(instrumentChartSource).toContain('chart?.summary.high')
    expect(instrumentChartSource).not.toContain('activePoint.value - firstPoint.value')
  })

  it('does not synthesize missing allocation research metrics from policy replay points', () => {
    ;[
      'drawdownPoints',
      'currentDrawdownFromPoints',
      'returnMapByDate',
      'activeCurrentDrawdown',
      'metricDifference',
      'excessReturnMetric',
    ].forEach((forbidden) => expect(allocationLabSource).not.toContain(forbidden))
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

  it('renders authoritative Decimal performance facts without browser-side benchmark math', () => {
    expect(performanceSource).toContain('getPortfolioPerformanceReport')
    expect(performanceSource).toContain('performance?.cumulative_twr?.method50')
    expect(performanceSource).toContain('statistics?.annualized_volatility?.method50')
    expect(performanceSource).toContain('xirr.rate?.method50')
    expect(performanceSource).toContain('bridge?.economic_pnl_exact')
    expect(performanceSource).toContain('performance?.effective_return_start_date')
    expect(performanceSource).toContain('performance?.effective_return_end_date')
    expect(performanceSource).toContain('this bridge is not the latest selected-date NAV')
    expect(performanceSource).toContain('exactDecimalToDisplayNumber')
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
      'getPortfolioPerformanceComparison',
      'comparison.differences',
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

  it('uses only the taxonomy snapshot sealed with Overview holdings', () => {
    expect(overviewSource).toContain('sealed_display_config.taxonomy')
    expect(overviewSource).not.toContain('getPortfolioTaxonomyCatalog')
    expect(overviewSource).not.toContain('getWorkspaceSummaryForPortfolio')
    expect(overviewPublicationSource).not.toContain('getPortfolioTaxonomyCatalog')
    expect(overviewPublicationSource).not.toContain('getWorkspaceSummaryForPortfolio')
  })

  it('keeps Accounts on the exact current Portfolio Daily publication', () => {
    expect(accountsSource).toContain('workspace.publication.publication_id')
    expect(accountsSource).toContain('account_value_base_exact')
    expect(accountsSource).toContain('settled_cash_local')
    expect(accountsSource).toContain('cost_basis_local_exact')
    expect(accountsSource).not.toContain('derived_cash_balance')
    expect(accountsSource).not.toContain('ledger_postings')
    expect(accountsSource).not.toContain('LedgerPosting')
  })

  it('does not live-replay lots or postings in the transaction workspace', () => {
    expect(transactionsSource).not.toContain('position-preview')
    expect(transactionsSource).not.toContain('positionPreview')
    expect(transactionsSource).not.toContain('ledger_postings')
    expect(transactionsSource).not.toContain("inspectorTab === 'lots'")
    expect(transactionsSource).not.toContain("inspectorTab === 'postings'")
  })

  it('keeps market quotes as references and transaction evidence as exact strings', () => {
    expect(apiSource).toContain('suggested_transaction_price: string | null')
    expect(apiSource).toContain('rate: string')
    expect(transactionsSource).toContain('exactDecimalMultiply')
    expect(transactionsSource).toContain('counter_amount: actualCounterAmount')
    expect(transactionsSource).not.toContain('computedCounterAmount')
    expect(transactionsSource).not.toContain('exactDecimalQuantizeHalfEven')
    expect(transactionsSource).not.toContain('sharedFxRate.rate.toFixed')
    expect(transactionsSource).not.toMatch(/Math\.abs\(targetAmount\s*-\s*sourceAmount\s*\*/)
  })

  it('models published positions and open lots as exact decimal strings', () => {
    expect(apiSource).toContain('type PortfolioDailyPublishedPositionRecord')
    expect(apiSource).toContain('type PortfolioDailyPublishedLotRecord')
    expect(apiSource).toContain('open_quantity_exact: string')
    expect(apiSource).toContain('cost_basis_local_exact: string')
    expect(positionLotAggregationSource).toContain('exactDecimalSum')
    ;['remaining_quantity: number', 'realizations:', 'current_market_value', 'status: \'closed\''].forEach(
      (forbidden) => expect(apiSource).not.toContain(forbidden),
    )
  })
})
