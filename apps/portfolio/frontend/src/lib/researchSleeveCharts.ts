import type { PortfolioResearchBacktestSleevePointRecord as SleevePoint } from './api'

const COLORS = ['#2563eb', '#16815d', '#7c3aed', '#0891b2', '#be185d', '#64748b', '#4f46e5', '#047857', '#a855f7', '#0e7490', '#9f1239', '#475569']

export type SleeveChartSeries = { key: string; label: string; color: string }
export type SleeveChartPoint = { date: string; time: number; values: Map<string, number | null> }
export type SleeveStackPoint = { date: string; time: number; lower: number; upper: number }

export function buildSleeveChartSeries(...datasets: SleevePoint[][]): SleeveChartSeries[] {
  const series = new Map<string, SleeveChartSeries>()
  for (const points of datasets) for (const point of points) for (const sleeve of point.sleeves) {
    const key = sleeve.top_sleeve_id ?? sleeve.top_sleeve_label
    if (!series.has(key)) series.set(key, { key, label: sleeve.top_sleeve_label, color: COLORS[series.size % COLORS.length] })
  }
  return [...series.values()]
}

export function sleeveChartPoints(points: SleevePoint[], series: SleeveChartSeries[]): SleeveChartPoint[] {
  return points.map((point) => {
    const recorded = new Map(point.sleeves.map((sleeve) => [sleeve.top_sleeve_id ?? sleeve.top_sleeve_label, sleeve.value]))
    return {
      date: point.date,
      time: Date.parse(`${point.date}T00:00:00Z`),
      values: new Map(series.map(({ key }) => {
        const value = recorded.get(key)
        // Replay omits zero sleeves. An explicitly missing value remains unknown.
        return [key, recorded.has(key) ? typeof value === 'number' && Number.isFinite(value) ? value : null : 0]
      })),
    }
  }).filter((point) => Number.isFinite(point.time)).sort((a, b) => a.time - b.time)
}

export function divergingSleeveStacks(points: SleeveChartPoint[], series: SleeveChartSeries[]) {
  const stacks = series.map((item) => ({ ...item, positive: [] as Array<SleeveStackPoint | null>, negative: [] as Array<SleeveStackPoint | null> }))
  const vertices: SleeveChartPoint[] = []
  points.forEach((point, index) => {
    const previous = points[index - 1]
    if (previous && series.every(({ key }) => previous.values.get(key) != null && point.values.get(key) != null)) {
      const crossings = new Set<number>()
      for (const { key } of series) {
        const before = previous.values.get(key)!, after = point.values.get(key)!
        if (before * after < 0) crossings.add(-before / (after - before))
      }
      // Add only drawing vertices at sign changes: a sleeve must not appear
      // on both sides of zero between two observed dates. Readouts stay raw.
      for (const fraction of [...crossings].sort((a, b) => a - b)) {
        const time = previous.time + (point.time - previous.time) * fraction
        vertices.push({ date: new Date(time).toISOString(), time, values: new Map(series.map(({ key }) => {
          const before = previous.values.get(key)!, after = point.values.get(key)!
          return [key, before + (after - before) * fraction]
        })) })
      }
    }
    vertices.push(point)
  })
  let min = 0, max = 0
  for (const point of vertices) {
    // No exact stack is available if any component is explicitly unknown.
    const complete = series.every(({ key }) => point.values.get(key) != null)
    let positive = 0, negative = 0
    for (const stack of stacks) {
      if (!complete) {
        stack.positive.push(null)
        stack.negative.push(null)
        continue
      }
      const value = point.values.get(stack.key)!
      const positiveValue = Math.max(value, 0), negativeValue = Math.min(value, 0)
      stack.positive.push({ date: point.date, time: point.time, lower: positive, upper: positive + positiveValue })
      stack.negative.push({ date: point.date, time: point.time, lower: negative + negativeValue, upper: negative })
      positive += positiveValue
      negative += negativeValue
    }
    min = Math.min(min, negative)
    max = Math.max(max, positive)
  }
  return { stacks, min, max }
}

export function contiguousChartSegments<T>(points: Array<T | null>): T[][] {
  const result: T[][] = []
  let current: T[] = []
  for (const point of points) {
    if (point == null) {
      if (current.length) result.push(current)
      current = []
    } else current.push(point)
  }
  if (current.length) result.push(current)
  return result
}
