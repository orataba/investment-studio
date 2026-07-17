const METRIC_LABELS: Record<string, string> = {
  official_nav: 'Unit NAV',
  nav: 'Unit NAV',
  total_return_nav: 'Dividend-Reinvested Total Return NAV',
  close: 'Market Close',
  adjusted_close: 'Adjusted Close',
  unadjusted_close: 'Market Close',
  unit_nav: 'Unit NAV',
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
