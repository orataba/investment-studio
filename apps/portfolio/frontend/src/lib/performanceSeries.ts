import type { PerformanceNavChartPoint } from '../components/PerformanceNavChart'
import type { PortfolioDailyPerformancePoint } from './api'

function isEligibleChartObservation(point: PortfolioDailyPerformancePoint) {
  return point.return_observation_eligible
}

export function buildPortfolioValueChartPoints(
  points: PortfolioDailyPerformancePoint[],
): PerformanceNavChartPoint[] {
  return points
    .filter(
      (point) =>
        isEligibleChartObservation(point) &&
        point.ending_nav != null &&
        Number.isFinite(point.ending_nav),
    )
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
    .map((point) => ({
      date: point.as_of_date,
      value: point.ending_nav as number,
    }))
}

export function rebasePerformanceSeriesTo100(
  points: PerformanceNavChartPoint[],
): PerformanceNavChartPoint[] {
  const baseValue = points[0]?.value
  if (baseValue == null || !Number.isFinite(baseValue) || baseValue === 0) {
    return []
  }
  return points.map((point) => ({
    date: point.date,
    value: (point.value / baseValue) * 100,
  }))
}

export function buildTwrIndexPoints(points: PortfolioDailyPerformancePoint[]): PerformanceNavChartPoint[] {
  let compoundedGrowth = 1
  const chartPoints: PerformanceNavChartPoint[] = []

  points
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
    .forEach((point) => {
      if (point.cumulative_twr != null && Number.isFinite(point.cumulative_twr)) {
        compoundedGrowth = 1 + point.cumulative_twr
      } else if (point.daily_twr != null && Number.isFinite(point.daily_twr)) {
        compoundedGrowth *= 1 + point.daily_twr
      } else {
        return
      }

      if (!isEligibleChartObservation(point)) {
        return
      }

      const value = compoundedGrowth * 100
      if (Number.isFinite(value)) {
        chartPoints.push({
          date: point.as_of_date,
          value,
        })
      }
    })

  return chartPoints
}
