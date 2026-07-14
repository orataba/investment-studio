import type {
  PortfolioDailyPerformancePoint,
  PortfolioPerformanceCoverageState,
} from './api'

export const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export type MonthlyBucket = {
  bucket_key: string
  start_date: string
  end_date: string
  coverage_state: PortfolioPerformanceCoverageState
  observation_count: number
  start_nav: number | null
  end_nav: number | null
  net_external_inflow: number
  absolute_change: number | null
  delta: number | null
  cumulative_twr: number | null
}

export type MonthlyReturnMatrixRow = {
  year: string
  months: Array<MonthlyBucket | null>
  ytd: number | null
  hasYearStartAnchor: boolean
  observationCount: number
  coverageState: PortfolioPerformanceCoverageState
}

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function compoundReturn(values: number[]) {
  if (!values.length) {
    return null
  }
  return values.reduce((growthIndex, value) => growthIndex * (1 + value), 1) - 1
}

function combineCoverage(states: PortfolioPerformanceCoverageState[]): PortfolioPerformanceCoverageState {
  if (!states.length) {
    return 'unavailable'
  }
  if (states.every((state) => state === 'complete')) {
    return 'complete'
  }
  return 'partial'
}

export function buildMonthlyBuckets(dailySeries: PortfolioDailyPerformancePoint[]) {
  const orderedKeys: string[] = []
  const buckets = new Map<
    string,
    {
      bucket_key: string
      start_date: string
      end_date: string
      coverage_state: PortfolioPerformanceCoverageState
      observation_count: number
      start_nav: number | null
      end_nav: number | null
      net_external_inflow: number
      absolute_change: number
      delta: number
      absolute_change_complete: boolean
      delta_complete: boolean
      growth_index: number
      has_return: boolean
    }
  >()

  dailySeries.forEach((point) => {
    const bucketKey = point.as_of_date.slice(0, 7)
    let bucket = buckets.get(bucketKey)
    if (!bucket) {
      bucket = {
        bucket_key: bucketKey,
        start_date: point.as_of_date,
        end_date: point.as_of_date,
        coverage_state: point.coverage_state,
        observation_count: 0,
        start_nav: point.beginning_nav,
        end_nav: point.ending_nav,
        net_external_inflow: 0,
        absolute_change: 0,
        delta: 0,
        absolute_change_complete: true,
        delta_complete: true,
        growth_index: 1,
        has_return: false,
      }
      buckets.set(bucketKey, bucket)
      orderedKeys.push(bucketKey)
    }

    bucket.end_date = point.as_of_date
    bucket.end_nav = point.ending_nav
    bucket.coverage_state = bucket.coverage_state === 'complete' ? point.coverage_state : bucket.coverage_state
    bucket.observation_count += point.return_observation_eligible ? 1 : 0
    bucket.net_external_inflow += point.net_external_inflow
    if (point.absolute_change == null) {
      bucket.absolute_change_complete = false
    } else {
      bucket.absolute_change += point.absolute_change
    }
    if (point.delta == null) {
      bucket.delta_complete = false
    } else {
      bucket.delta += point.delta
    }
    if (point.daily_twr != null) {
      bucket.growth_index *= 1 + point.daily_twr
      bucket.has_return = true
    }
  })

  return orderedKeys.map((key) => {
    const bucket = buckets.get(key)!
    return {
      bucket_key: bucket.bucket_key,
      start_date: bucket.start_date,
      end_date: bucket.end_date,
      coverage_state: bucket.coverage_state,
      observation_count: bucket.observation_count,
      start_nav: bucket.start_nav,
      end_nav: bucket.end_nav,
      net_external_inflow: bucket.net_external_inflow,
      absolute_change: bucket.absolute_change_complete ? bucket.absolute_change : null,
      delta: bucket.delta_complete ? bucket.delta : null,
      cumulative_twr: bucket.has_return ? bucket.growth_index - 1 : null,
    } satisfies MonthlyBucket
  })
}

export function buildMonthlyReturnMatrixRows(buckets: MonthlyBucket[]): MonthlyReturnMatrixRow[] {
  const rowsByYear = new Map<string, MonthlyReturnMatrixRow>()
  buckets.forEach((bucket) => {
    const year = bucket.bucket_key.slice(0, 4)
    const monthIndex = Number(bucket.bucket_key.slice(5, 7)) - 1
    if (!rowsByYear.has(year)) {
      rowsByYear.set(year, {
        year,
        months: Array.from({ length: 12 }, () => null),
        ytd: null,
        hasYearStartAnchor: false,
        observationCount: 0,
        coverageState: 'unavailable',
      })
    }

    const row = rowsByYear.get(year)!
    if (monthIndex >= 0 && monthIndex < 12) {
      row.months[monthIndex] = bucket
    }
  })

  return [...rowsByYear.values()]
    .map((row) => {
      const populatedMonths = row.months.filter((bucket): bucket is MonthlyBucket => bucket != null)
      const hasYearStartAnchor = populatedMonths.some((bucket) => bucket.start_date <= `${row.year}-01-01`)
      const returns = populatedMonths
        .map((bucket) => finiteNumber(bucket.cumulative_twr))
        .filter((value): value is number => value != null)
      return {
        ...row,
        hasYearStartAnchor,
        ytd: hasYearStartAnchor ? compoundReturn(returns) : null,
        observationCount: populatedMonths.reduce((total, bucket) => total + bucket.observation_count, 0),
        coverageState: combineCoverage(populatedMonths.map((bucket) => bucket.coverage_state)),
      }
    })
    .sort((left, right) => right.year.localeCompare(left.year))
}
