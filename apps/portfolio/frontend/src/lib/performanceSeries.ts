import type { PerformanceNavChartPoint } from '../components/PerformanceNavChart'
import type { PortfolioDailyPerformancePoint } from './api'

export function buildTwrIndexPoints(points: PortfolioDailyPerformancePoint[]): PerformanceNavChartPoint[] {
  return points
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
    .map((point) => ({
      date: point.as_of_date,
      value:
        point.cumulative_twr != null && Number.isFinite(point.cumulative_twr)
          ? (1 + point.cumulative_twr) * 100
          : null,
    }))
}
