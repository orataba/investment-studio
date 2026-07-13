import type { PortfolioReturnCalendarBucket } from './api'

export const RETURN_CALENDAR_MONTH_LABELS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
]

export type ReturnCalendarMatrixRow = {
  year: string
  months: Array<PortfolioReturnCalendarBucket | null>
}

/**
 * Arrange authoritative calendar buckets for display only. This presenter does
 * not link, compound, annualize, or otherwise reinterpret backend returns.
 */
export function buildReturnCalendarMatrixRows(
  buckets: readonly PortfolioReturnCalendarBucket[],
): ReturnCalendarMatrixRow[] {
  const rowsByYear = new Map<string, ReturnCalendarMatrixRow>()

  buckets.forEach((bucket) => {
    const match = /^(\d{4})-(\d{2})$/.exec(bucket.bucket_key)
    if (!match || bucket.frequency !== 'monthly') {
      return
    }
    const year = match[1]
    const monthIndex = Number(match[2]) - 1
    if (monthIndex < 0 || monthIndex >= RETURN_CALENDAR_MONTH_LABELS.length) {
      return
    }
    const row = rowsByYear.get(year) ?? {
      year,
      months: Array.from({ length: RETURN_CALENDAR_MONTH_LABELS.length }, () => null),
    }
    row.months[monthIndex] = bucket
    rowsByYear.set(year, row)
  })

  return [...rowsByYear.values()].sort((left, right) => right.year.localeCompare(left.year))
}
