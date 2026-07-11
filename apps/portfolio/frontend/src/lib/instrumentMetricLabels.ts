const METRIC_LABELS: Record<string, string> = {
  official_nav: 'Official NAV',
  nav: 'NAV',
  total_return_nav: 'Total Return NAV',
  close: 'Market Close',
  adjusted_close: 'Adjusted Close',
  unadjusted_close: 'Market Close',
  unit_nav: 'Unit NAV',
  cumulative_nav: 'Cumulative NAV',
  cum_nav: 'Cumulative NAV',
  accumulated_nav: 'Accumulated NAV',
  dividend_adjusted_nav: 'Dividend-adjusted NAV',
  reinvested_nav: 'Total Return NAV',
}

export function instrumentMetricLabel(metricFamily?: string | null, fallback = 'Market quote') {
  const normalizedMetricFamily = metricFamily?.trim().toLocaleLowerCase()
  if (!normalizedMetricFamily) {
    return fallback
  }
  return METRIC_LABELS[normalizedMetricFamily] ?? normalizedMetricFamily
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toLocaleUpperCase() + part.slice(1))
    .join(' ')
}

export function valuationQuoteLabel(metricFamily?: string | null) {
  return instrumentMetricLabel(metricFamily, 'Valuation quote')
}

export function performanceSeriesLabel(metricFamily?: string | null) {
  return instrumentMetricLabel(metricFamily, 'Performance series')
}
