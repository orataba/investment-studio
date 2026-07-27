import type { FundChartPoint } from './api'

export const RETURN_WINDOW_POLICY_VERSION = 'return-window/v1'

export type ReturnAnchorMode = 'on_or_before' | 'strictly_before'

export type ReturnWindow = {
  requestedStartDate: string
  requestedEndDate: string
  anchorMode: ReturnAnchorMode
  anchorDate: string
  endDate: string
  points: FundChartPoint[]
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

export function resolveReturnWindow(
  points: FundChartPoint[],
  requestedStartDate: string,
  requestedEndDate: string,
  anchorMode: ReturnAnchorMode = 'on_or_before',
): ReturnWindow | null {
  if (!requestedStartDate || !requestedEndDate || requestedStartDate > requestedEndDate) {
    return null
  }
  const byDate = new Map<string, FundChartPoint>()
  points.forEach((point) => {
    const value = Number(point.value)
    const pointDate = point.date.slice(0, 10)
    if (pointDate && Number.isFinite(value) && value > 0) {
      byDate.set(pointDate, { date: pointDate, value })
    }
  })
  const ordered = [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date))
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

export function cumulativeReturnPercentToGrowthIndex100(points: FundChartPoint[]) {
  return points.map((point) => ({
    date: point.date,
    value: 100 + point.value,
  }))
}

export function periodReturnPercent(window: ReturnWindow) {
  const first = window.points[0]
  const last = window.points[window.points.length - 1]
  return first && last && first.value > 0 ? ((last.value / first.value) - 1) * 100 : null
}

export function annualizedReturnPercent(window: ReturnWindow) {
  const first = window.points[0]
  const last = window.points[window.points.length - 1]
  const start = new Date(`${window.anchorDate}T00:00:00Z`).getTime()
  const end = new Date(`${window.endDate}T00:00:00Z`).getTime()
  const days = Math.round((end - start) / 86_400_000)
  if (!first || !last || first.value <= 0 || days <= 0) {
    return null
  }
  return (Math.pow(last.value / first.value, 365.25 / days) - 1) * 100
}
