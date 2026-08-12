export type CalculationFrequency = 'daily'

export type ReturnPoint = {
  date: string
  value: number
  start_date?: string | null
}

export type GroupReturnSeries = {
  groupKey: string
  groupLabel: string
  returnsByDate: Map<string, number>
  periodStartByDate: Map<string, string | null>
  endingWeightByDate: Map<string, number>
  latestWeight: number | null
  observationCount: number
  sourceMembers?: GroupReturnSeries[]
}

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

const RISK_WINDOW_MONTHS_BY_LOOKBACK_DAYS: Record<number, number> = {
  30: 1,
  90: 3,
  180: 6,
  366: 12,
  730: 24,
}

export function shiftIsoCalendarMonths(isoDate: string, months: number) {
  const [year, month, day] = isoDate.split('-').map(Number)
  const sourceMonthIndex = (month || 1) - 1
  const targetMonthStart = new Date(year, sourceMonthIndex + months, 1)
  const targetYear = targetMonthStart.getFullYear()
  const targetMonthIndex = targetMonthStart.getMonth()
  const targetMonthLastDay = new Date(targetYear, targetMonthIndex + 1, 0).getDate()
  return localDateIso(new Date(targetYear, targetMonthIndex, Math.min(day || 1, targetMonthLastDay)))
}

export function riskWindowStart(asOfDate: string, lookbackDays: number) {
  const calendarMonths = RISK_WINDOW_MONTHS_BY_LOOKBACK_DAYS[lookbackDays]
  if (calendarMonths) {
    return shiftIsoCalendarMonths(asOfDate, -calendarMonths)
  }
  return shiftIsoDate(asOfDate, -Math.max(lookbackDays - 1, 0))
}

export function dayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00`)
  const rightTime = Date.parse(`${right}T00:00:00`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.max(0, (rightTime - leftTime) / 86_400_000)
}

export function absoluteDayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00`)
  const rightTime = Date.parse(`${right}T00:00:00`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.abs((rightTime - leftTime) / 86_400_000)
}

export function alignReturnSeriesToFrequency(
  series: GroupReturnSeries[],
  frequency: CalculationFrequency,
  finalDate: string,
) {
  void frequency
  void finalDate
  return series
}

export function alignReturnPointsToFrequency(
  returnPoints: ReturnPoint[],
  frequency: CalculationFrequency,
  finalDate: string,
) {
  void frequency
  void finalDate
  return returnPoints
}

export function commonReturnDateKeys(
  series: GroupReturnSeries[],
  startDate = '',
  endDate = '',
  options: { includeZeroWeight?: boolean } = {},
) {
  const activeSeries = options.includeZeroWeight
    ? series
    : series.filter((item) => Math.abs(item.latestWeight ?? 0) > 1e-9)
  if (!activeSeries.length) {
    return []
  }
  const firstSeries = activeSeries[0]
  if (!firstSeries) {
    return []
  }
  return [...firstSeries.returnsByDate.keys()]
    .filter((dateKey) => (!startDate || dateKey > startDate) && (!endDate || dateKey <= endDate))
    .filter((dateKey) =>
      activeSeries.every((item) => {
        const value = item.returnsByDate.get(dateKey)
        return value != null && Number.isFinite(value)
      }),
    )
    .sort()
}

export function returnPointsInWindow(returnPoints: ReturnPoint[], asOfDate: string, lookbackDays: number) {
  if (!asOfDate) {
    return []
  }
  const startDate = riskWindowStart(asOfDate, lookbackDays)
  return returnPoints.filter((point) => point.date > startDate && point.date <= asOfDate)
}

export function pairWindowReturns(
  left: Map<string, number>,
  right: Map<string, number>,
  asOfDate: string,
  lookbackDays: number,
) {
  const startDate = riskWindowStart(asOfDate, lookbackDays)
  const pairs: Array<{ date: string; left: number; right: number }> = []
  left.forEach((leftValue, dateKey) => {
    if (dateKey <= startDate || dateKey > asOfDate) {
      return
    }
    const rightValue = right.get(dateKey)
    if (rightValue != null && Number.isFinite(leftValue) && Number.isFinite(rightValue)) {
      pairs.push({ date: dateKey, left: leftValue, right: rightValue })
    }
  })
  return pairs.sort((leftPair, rightPair) => leftPair.date.localeCompare(rightPair.date))
}

export function windowReturnPoints(series: GroupReturnSeries, asOfDate: string, lookbackDays: number) {
  const startDate = riskWindowStart(asOfDate, lookbackDays)
  const points: ReturnPoint[] = []
  series.returnsByDate.forEach((value, dateKey) => {
    if (dateKey > startDate && dateKey <= asOfDate && Number.isFinite(value)) {
      points.push({
        date: dateKey,
        value,
        start_date: series.periodStartByDate.get(dateKey) ?? null,
      })
    }
  })
  return points.sort((left, right) => left.date.localeCompare(right.date))
}
