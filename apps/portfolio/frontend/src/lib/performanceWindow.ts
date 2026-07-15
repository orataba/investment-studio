export type PerformanceWindowMode = 'dates' | 'since_inception'
export type PerformancePeriodPreset = 'mtd' | 'qtd' | 'ytd' | '1y' | 'si'

export type PerformanceWindowSelection = {
  startDate: string
  endDate: string
  mode?: PerformanceWindowMode
}

export const PERFORMANCE_PERIOD_PRESETS: Array<{
  key: PerformancePeriodPreset
  label: string
}> = [
  { key: 'mtd', label: 'MTD' },
  { key: 'qtd', label: 'QTD' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1Y' },
  { key: 'si', label: 'SI' },
]

export function localDateIso(input = new Date()) {
  const year = input.getFullYear()
  const month = `${input.getMonth() + 1}`.padStart(2, '0')
  const day = `${input.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function shiftIsoDate(isoDate: string, days: number) {
  const [year, month, day] = isoDate.split('-').map(Number)
  const nextDate = new Date(year, (month || 1) - 1, day || 1)
  nextDate.setDate(nextDate.getDate() + days)
  return localDateIso(nextDate)
}

export function validIsoDate(value: string | null | undefined) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : ''
}

export function performancePresetStartDate(
  preset: Exclude<PerformancePeriodPreset, 'si'>,
  endDate: string,
) {
  const [year, month, day] = endDate.split('-').map(Number)
  if (!year || !month || !day) {
    return ''
  }
  if (preset === 'mtd') {
    return `${year}-${String(month).padStart(2, '0')}-01`
  }
  if (preset === 'qtd') {
    const quarterStartMonth = Math.floor((month - 1) / 3) * 3 + 1
    return `${year}-${String(quarterStartMonth).padStart(2, '0')}-01`
  }
  if (preset === 'ytd') {
    return `${year}-01-01`
  }
  const previousYear = year - 1
  const finalDayOfTargetMonth = new Date(previousYear, month, 0).getDate()
  return `${previousYear}-${String(month).padStart(2, '0')}-${String(
    Math.min(day, finalDayOfTargetMonth),
  ).padStart(2, '0')}`
}

export function normalizePerformanceWindowSelection(
  value: unknown,
): PerformanceWindowSelection | null {
  if (!value || typeof value !== 'object') {
    return null
  }
  const record = value as { startDate?: unknown; endDate?: unknown; mode?: unknown }
  const startDate = typeof record.startDate === 'string' ? validIsoDate(record.startDate) : ''
  const endDate = typeof record.endDate === 'string' ? validIsoDate(record.endDate) : ''
  const mode: PerformanceWindowMode =
    record.mode === 'since_inception' ? 'since_inception' : 'dates'
  return startDate || endDate || mode === 'since_inception'
    ? { startDate, endDate, mode }
    : null
}

export function resolvePerformanceWindow({
  queryStartDate,
  queryEndDate,
  querySinceInception,
  storedSelection,
  portfolioAsOfDate,
  todayDate,
  portfolioSummarySettled,
  defaultLookbackDays,
}: {
  queryStartDate?: string | null
  queryEndDate?: string | null
  querySinceInception: boolean
  storedSelection?: PerformanceWindowSelection | null
  portfolioAsOfDate?: string | null
  todayDate: string
  portfolioSummarySettled: boolean
  defaultLookbackDays: number
}) {
  const normalizedStoredSelection = normalizePerformanceWindowSelection(storedSelection)
  const appliedSinceInception =
    querySinceInception || normalizedStoredSelection?.mode === 'since_inception'
  const appliedStartDate =
    validIsoDate(queryStartDate) || normalizedStoredSelection?.startDate || ''
  const appliedEndDate = validIsoDate(queryEndDate) || normalizedStoredSelection?.endDate || ''
  const waitingForDefaultEndDate = !appliedEndDate && !portfolioSummarySettled
  const effectiveEndDate =
    appliedEndDate || validIsoDate(portfolioAsOfDate) || validIsoDate(todayDate)
  const defaultStartDate = shiftIsoDate(
    effectiveEndDate,
    -(defaultLookbackDays - 1),
  )
  const effectiveStartDate = appliedSinceInception
    ? ''
    : appliedStartDate || defaultStartDate

  return {
    appliedSinceInception,
    appliedStartDate,
    appliedEndDate,
    waitingForDefaultEndDate,
    effectiveStartDate,
    effectiveEndDate,
  }
}

export function buildPerformanceWindowFilters(startDate: string, endDate: string) {
  const normalizedStartDate = validIsoDate(startDate)
  const normalizedEndDate = validIsoDate(endDate)
  return {
    ...(normalizedStartDate ? { start_date: normalizedStartDate } : {}),
    ...(normalizedEndDate ? { end_date: normalizedEndDate } : {}),
  }
}
