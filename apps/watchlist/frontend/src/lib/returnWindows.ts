import type { InstrumentChartPoint } from './api'

export const RETURN_WINDOW_POLICY_VERSION = 'return-window/v2'

export type ReturnAnchorMode = 'on_or_before' | 'strictly_before'

export type ReturnWindow = {
  requestedStartDate: string
  requestedEndDate: string
  anchorMode: ReturnAnchorMode
  anchorDate: string
  endDate: string
  points: InstrumentChartPoint[]
}

export type AlignedReturnWindows = {
  left: ReturnWindow
  right: ReturnWindow
}

export type ReturnWindowName =
  | '1D'
  | '1W'
  | '1M'
  | '3M'
  | '6M'
  | 'MTD'
  | 'YTD'
  | '1Y'
  | '2Y'
  | '3Y'
  | '5Y'
  | '10Y'

export function shiftIsoDate(
  value: string,
  offset: { years?: number; months?: number; days?: number },
) {
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  if (![year, month, day].every(Number.isFinite)) {
    return null
  }
  const targetMonthIndex = month - 1 + (offset.months || 0) + (offset.years || 0) * 12
  const targetYear = year + Math.floor(targetMonthIndex / 12)
  const normalizedMonthIndex = ((targetMonthIndex % 12) + 12) % 12
  const lastDayOfMonth = new Date(Date.UTC(targetYear, normalizedMonthIndex + 1, 0)).getUTCDate()
  const targetDay = Math.min(day, lastDayOfMonth)
  const shifted = new Date(Date.UTC(targetYear, normalizedMonthIndex, targetDay))
  if (offset.days) {
    shifted.setUTCDate(shifted.getUTCDate() + offset.days)
  }
  return shifted.toISOString().slice(0, 10)
}

export function namedReturnWindowSpec(window: ReturnWindowName, asOfDate: string) {
  const end = asOfDate.slice(0, 10)
  if (window === 'MTD') {
    return { start: `${end.slice(0, 7)}-01`, end, anchorMode: 'strictly_before' as const }
  }
  if (window === 'YTD') {
    return { start: `${end.slice(0, 4)}-01-01`, end, anchorMode: 'strictly_before' as const }
  }
  const start =
    window === '1D'
      ? shiftIsoDate(end, { days: -1 })
      : window === '1W'
        ? shiftIsoDate(end, { days: -7 })
        : window.endsWith('M')
          ? shiftIsoDate(end, { months: -Number(window.slice(0, -1)) })
          : shiftIsoDate(end, { years: -Number(window.slice(0, -1)) })
  return { start: start || '', end, anchorMode: 'on_or_before' as const }
}

function normalizedReturnPoints(points: InstrumentChartPoint[]) {
  const byDate = new Map<string, InstrumentChartPoint>()
  points.forEach((point) => {
    const value = Number(point.value)
    const pointDate = point.date.slice(0, 10)
    if (pointDate && Number.isFinite(value) && value > 0) {
      byDate.set(pointDate, { date: pointDate, value })
    }
  })
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date))
}

export function resolveReturnWindow(
  points: InstrumentChartPoint[],
  requestedStartDate: string,
  requestedEndDate: string,
  anchorMode: ReturnAnchorMode = 'on_or_before',
): ReturnWindow | null {
  if (!requestedStartDate || !requestedEndDate || requestedStartDate > requestedEndDate) {
    return null
  }
  const ordered = normalizedReturnPoints(points)
  let endIndex = -1
  for (let index = ordered.length - 1; index >= 0; index -= 1) {
    if (ordered[index].date <= requestedEndDate) {
      endIndex = index
      break
    }
  }
  if (endIndex < 0) {
    return null
  }
  let anchorIndex = -1
  for (let index = endIndex; index >= 0; index -= 1) {
    const dateMatches =
      anchorMode === 'strictly_before'
        ? ordered[index].date < requestedStartDate
        : ordered[index].date <= requestedStartDate
    if (dateMatches) {
      anchorIndex = index
      break
    }
  }
  if (anchorIndex < 0 || anchorIndex >= endIndex) {
    return null
  }
  const windowPoints = ordered.slice(anchorIndex, endIndex + 1)
  return {
    requestedStartDate,
    requestedEndDate,
    anchorMode,
    anchorDate: windowPoints[0].date,
    endDate: windowPoints[windowPoints.length - 1].date,
    points: windowPoints,
  }
}

export function resolveAlignedReturnWindows(
  leftPoints: InstrumentChartPoint[],
  rightPoints: InstrumentChartPoint[],
  requestedStartDate: string | null,
  requestedEndDate: string,
  anchorMode: ReturnAnchorMode = 'on_or_before',
): AlignedReturnWindows | null {
  if (
    !requestedEndDate ||
    (requestedStartDate != null &&
      (!requestedStartDate || requestedStartDate > requestedEndDate))
  ) {
    return null
  }
  const leftByDate = new Map(
    normalizedReturnPoints(leftPoints).map((point) => [point.date, point]),
  )
  const rightByDate = new Map(
    normalizedReturnPoints(rightPoints).map((point) => [point.date, point]),
  )
  const commonDates = [...leftByDate.keys()]
    .filter((pointDate) => rightByDate.has(pointDate) && pointDate <= requestedEndDate)
    .sort()
  if (commonDates.length < 2) {
    return null
  }
  const endDate = commonDates[commonDates.length - 1]
  let anchorDate: string | null = requestedStartDate == null ? commonDates[0] : null
  if (requestedStartDate != null) {
    for (let index = commonDates.length - 1; index >= 0; index -= 1) {
      const pointDate = commonDates[index]
      const matches =
        anchorMode === 'strictly_before'
          ? pointDate < requestedStartDate
          : pointDate <= requestedStartDate
      if (matches) {
        anchorDate = pointDate
        break
      }
    }
  }
  if (anchorDate == null || anchorDate >= endDate) {
    return null
  }
  const alignedDates = commonDates.filter(
    (pointDate) => pointDate >= anchorDate && pointDate <= endDate,
  )
  if (alignedDates.length < 2) {
    return null
  }
  const resolvedRequestedStartDate = requestedStartDate ?? anchorDate
  const buildWindow = (
    pointsByDate: Map<string, InstrumentChartPoint>,
  ): ReturnWindow => ({
    requestedStartDate: resolvedRequestedStartDate,
    requestedEndDate,
    anchorMode,
    anchorDate,
    endDate,
    points: alignedDates.map((pointDate) => pointsByDate.get(pointDate)!),
  })
  return {
    left: buildWindow(leftByDate),
    right: buildWindow(rightByDate),
  }
}

export function normalizeCumulativeReturn(window: ReturnWindow) {
  const anchorValue = window.points[0]?.value
  if (!(anchorValue > 0)) {
    return []
  }
  return window.points.map((point) => ({
    date: point.date,
    value: ((point.value / anchorValue) - 1) * 100,
  }))
}

export function cumulativeReturnPercentToGrowthIndex100(points: InstrumentChartPoint[]) {
  return points.map((point) => ({
    date: point.date,
    value: 100 + point.value,
  }))
}

export function commonObservationDateWindow(
  leftPoints: InstrumentChartPoint[],
  rightPoints: InstrumentChartPoint[],
) {
  const rightDates = new Set(
    normalizedReturnPoints(rightPoints).map((point) => point.date),
  )
  const commonDates = normalizedReturnPoints(leftPoints)
    .map((point) => point.date)
    .filter((pointDate) => rightDates.has(pointDate))
  return commonDates.length
    ? { start: commonDates[0], end: commonDates[commonDates.length - 1] }
    : null
}

export function periodReturnPercent(window: ReturnWindow) {
  const first = window.points[0]
  const last = window.points[window.points.length - 1]
  return first && last && first.value > 0 ? ((last.value / first.value) - 1) * 100 : null
}

export function actualYearFraction(startDate: string, endDate: string): number {
  if (endDate < startDate) {
    return -actualYearFraction(endDate, startDate)
  }
  const startYear = Number(startDate.slice(0, 4))
  const endYear = Number(endDate.slice(0, 4))
  const anniversary = (years: number) =>
    shiftIsoDate(startDate, { years }) || startDate
  let wholeYears = Math.max(endYear - startYear, 0)
  while (wholeYears > 0 && anniversary(wholeYears) > endDate) {
    wholeYears -= 1
  }
  const currentAnniversary = anniversary(wholeYears)
  if (currentAnniversary === endDate) {
    return wholeYears
  }
  const nextAnniversary = anniversary(wholeYears + 1)
  const current = new Date(`${currentAnniversary}T00:00:00Z`).getTime()
  const next = new Date(`${nextAnniversary}T00:00:00Z`).getTime()
  const end = new Date(`${endDate}T00:00:00Z`).getTime()
  return wholeYears + ((end - current) / (next - current))
}

export function annualizedReturnPercent(window: ReturnWindow) {
  const first = window.points[0]
  const last = window.points[window.points.length - 1]
  const start = new Date(`${window.anchorDate}T00:00:00Z`).getTime()
  const end = new Date(`${window.endDate}T00:00:00Z`).getTime()
  const days = Math.round((end - start) / 86_400_000)
  const years = actualYearFraction(window.anchorDate, window.endDate)
  if (
    !first ||
    !last ||
    first.value <= 0 ||
    days <= 0 ||
    years < 1
  ) {
    return null
  }
  return (Math.pow(last.value / first.value, 1 / years) - 1) * 100
}
