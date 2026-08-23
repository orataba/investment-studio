import type { InstrumentChartPoint } from './api'

/**
 * Calendar-period return helpers used by the Watchlist detail risk/performance
 * views.
 *
 * A period return is a close-to-close observation: the last valid observation
 * in the previous calendar period is compared with the last valid observation
 * in the current calendar period.  In particular, we must not turn a
 * January-31 -> March-31 move into a (mislabelled) March return when February
 * is missing from the source series.
 */

function normalizePoints(points: InstrumentChartPoint[]) {
  const byDate = new Map<string, InstrumentChartPoint>()
  points.forEach((point) => {
    const date = String(point.date || '').slice(0, 10)
    const value = Number(point.value)
    if (!date || !Number.isFinite(value) || value <= 0) {
      return
    }
    byDate.set(date, { date, value })
  })
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date))
}

export function monthBucket(value: string) {
  return value.slice(0, 7)
}

export function yearBucket(value: string) {
  return value.slice(0, 4)
}

function monthIndex(value: string) {
  const year = Number(value.slice(0, 4))
  const month = Number(value.slice(5, 7))
  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
    return null
  }
  return year * 12 + month - 1
}

export function areAdjacentCalendarMonths(leftDate: string, rightDate: string) {
  const left = monthIndex(leftDate)
  const right = monthIndex(rightDate)
  return left != null && right != null && right - left === 1
}

export function areAdjacentCalendarYears(leftDate: string, rightDate: string) {
  const left = Number(leftDate.slice(0, 4))
  const right = Number(rightDate.slice(0, 4))
  return Number.isInteger(left) && Number.isInteger(right) && right - left === 1
}

/** Return the last valid observation in each calendar month. */
export function buildMonthlyCloseSeries(points: InstrumentChartPoint[]) {
  const closes = new Map<string, InstrumentChartPoint>()
  normalizePoints(points).forEach((point) => {
    closes.set(monthBucket(point.date), point)
  })
  return [...closes.values()].sort((left, right) => left.date.localeCompare(right.date))
}

/** Return the last valid observation in each calendar year. */
export function buildAnnualCloseSeries(points: InstrumentChartPoint[]) {
  const closes = new Map<string, InstrumentChartPoint>()
  normalizePoints(points).forEach((point) => {
    closes.set(yearBucket(point.date), point)
  })
  return [...closes.values()].sort((left, right) => left.date.localeCompare(right.date))
}

/**
 * Build close-to-close monthly returns.  A return is emitted only when both
 * adjacent calendar months have an observation; gaps are never bridged.
 */
export type CalendarReturnWindow = {
  anchorDate: string
  endDate: string
}

export function buildMonthlyReturnWindows(
  points: InstrumentChartPoint[],
): Array<InstrumentChartPoint & CalendarReturnWindow> {
  const closes = buildMonthlyCloseSeries(points)
  const returns: Array<InstrumentChartPoint & CalendarReturnWindow> = []
  for (let index = 1; index < closes.length; index += 1) {
    const previous = closes[index - 1]
    const current = closes[index]
    if (!areAdjacentCalendarMonths(previous.date, current.date) || previous.value <= 0) {
      continue
    }
    returns.push({
      date: current.date,
      anchorDate: previous.date,
      endDate: current.date,
      value: (current.value / previous.value - 1) * 100,
    })
  }
  return returns
}

export function buildMonthlyReturnSeries(points: InstrumentChartPoint[]) {
  return buildMonthlyReturnWindows(points).map(({ date, value }) => ({ date, value }))
}

/**
 * Build close-to-close calendar-year returns.  The same adjacency rule keeps
 * an older observation from being silently reused as a missing prior-year
 * close.
 */
export function buildAnnualReturnSeries(points: InstrumentChartPoint[]) {
  const closes = buildAnnualCloseSeries(points)
  const returns: Array<InstrumentChartPoint & { year: string; anchorDate: string }> = []
  for (let index = 1; index < closes.length; index += 1) {
    const previous = closes[index - 1]
    const current = closes[index]
    if (!areAdjacentCalendarYears(previous.date, current.date) || previous.value <= 0) {
      continue
    }
    returns.push({
      year: yearBucket(current.date),
      date: current.date,
      anchorDate: previous.date,
      value: (current.value / previous.value - 1) * 100,
    })
  }
  return returns
}

export type MonthlyReturnMatrixRow = {
  year: string
  months: Array<number | null>
  ytd: number | null
  monthWindows: Array<CalendarReturnWindow | null>
  ytdWindow: CalendarReturnWindow | null
}

/**
 * Build the calendar return matrix used by the Performance tab.
 *
 * Rows are sourced from both monthly and annual return series.  This matters
 * for sparse annual NAV data: a valid adjacent-year return can exist even
 * when there are no adjacent calendar-month closes to populate the month
 * cells.
 */
export function buildMonthlyReturnMatrix(points: InstrumentChartPoint[]): MonthlyReturnMatrixRow[] {
  const monthlyReturns = buildMonthlyReturnWindows(points)
  const annualReturns = buildAnnualReturnSeries(points)
  const annualReturnByYear = new Map(
    annualReturns.map((point) => [point.year, point] as const),
  )
  const rows = new Map<number, MonthlyReturnMatrixRow>()

  monthlyReturns.forEach((point) => {
    const year = Number(point.date.slice(0, 4))
    const month = Number(point.date.slice(5, 7))
    if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
      return
    }
    const row = rows.get(year) || {
      year: String(year),
      months: Array.from({ length: 12 }, () => null),
      ytd: null,
      monthWindows: Array.from({ length: 12 }, () => null),
      ytdWindow: null,
    }
    row.months[month - 1] = point.value
    row.monthWindows[month - 1] = {
      anchorDate: point.anchorDate,
      endDate: point.endDate,
    }
    rows.set(year, row)
  })

  annualReturns.forEach((point) => {
    const year = Number(point.year)
    if (!Number.isInteger(year) || rows.has(year)) {
      return
    }
    rows.set(year, {
      year: String(year),
      months: Array.from({ length: 12 }, () => null),
      ytd: null,
      monthWindows: Array.from({ length: 12 }, () => null),
      ytdWindow: null,
    })
  })

  return Array.from(rows.entries())
    .sort((left, right) => right[0] - left[0])
    .map(([year, row]) => {
      const annualReturn = annualReturnByYear.get(String(year))
      return {
        ...row,
        ytd: annualReturn?.value ?? null,
        ytdWindow: annualReturn
          ? { anchorDate: annualReturn.anchorDate, endDate: annualReturn.date }
          : null,
      }
    })
}
