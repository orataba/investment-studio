export function isMetricAsOfSensitiveField(fieldKey: string) {
  return (
    fieldKey.startsWith('return_') ||
    fieldKey === 'annualized_return' ||
    fieldKey === 'max_drawdown' ||
    fieldKey === 'volatility' ||
    fieldKey === 'sharpe_ratio' ||
    fieldKey === 'attr.current_drawdown' ||
    fieldKey.startsWith('attr.peer_')
  )
}

function hasNumericMetricValue(value: unknown) {
  if (typeof value === 'number') {
    return Number.isFinite(value)
  }
  return typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))
}

export function summarizeMetricAsOfDates(
  fieldKey: string,
  rows: Array<Record<string, unknown>>,
) {
  if (!isMetricAsOfSensitiveField(fieldKey)) {
    return {
      comparable: true,
      asOfDate: null,
      dates: [] as string[],
      missingDateCount: 0,
    }
  }

  const populatedRows = rows.filter((row) => hasNumericMetricValue(row[fieldKey]))
  const dates = Array.from(
    new Set(
      populatedRows
        .map((row) => String(row.metric_as_of_date || '').slice(0, 10))
        .filter(Boolean),
    ),
  ).sort()
  const missingDateCount = populatedRows.filter(
    (row) => !String(row.metric_as_of_date || '').slice(0, 10),
  ).length

  return {
    comparable: missingDateCount === 0 && dates.length <= 1,
    asOfDate: dates.length === 1 ? dates[0] : null,
    dates,
    missingDateCount,
  }
}
