import type { PerformanceNavChartPoint } from '../components/PerformanceNavChart'
import type { PortfolioDailyPerformancePoint } from './api'

export function buildTwrIndexPoints(points: PortfolioDailyPerformancePoint[]): PerformanceNavChartPoint[] {
  let compoundedGrowth = 1
  let hasBasePoint = false

  return points
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
    .map((point) => {
      if (!hasBasePoint && point.cumulative_twr == null && point.daily_twr == null && point.ending_nav != null) {
        hasBasePoint = true
        return {
          date: point.as_of_date,
          value: 100,
        }
      }

      if (point.cumulative_twr != null) {
        compoundedGrowth = 1 + point.cumulative_twr
      } else if (point.daily_twr != null) {
        compoundedGrowth *= 1 + point.daily_twr
      } else {
        return null
      }

      hasBasePoint = true
      return {
        date: point.as_of_date,
        value: compoundedGrowth * 100,
      }
    })
    .filter((point): point is PerformanceNavChartPoint => point != null && Number.isFinite(point.value))
}
