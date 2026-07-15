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
  unavailable_reason: string | null
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

function pointReturnCoverage(point: PortfolioDailyPerformancePoint) {
  return point.return_coverage_state
}

function pointValuationCoverage(point: PortfolioDailyPerformancePoint) {
  return point.valuation_coverage_state
}

function monthEndDate(bucketKey: string) {
  const [year, month] = bucketKey.split('-').map(Number)
  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
    return null
  }
  return new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10)
}

function calendarDayGap(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00Z`)
  const rightTime = Date.parse(`${right}T00:00:00Z`)
  return Number.isFinite(leftTime) && Number.isFinite(rightTime)
    ? Math.round((rightTime - leftTime) / 86_400_000)
    : null
}

export function buildMonthlyBuckets(dailySeries: PortfolioDailyPerformancePoint[]) {
  const sortedSeries = dailySeries
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
  const latestSeriesDate = sortedSeries[sortedSeries.length - 1]?.as_of_date ?? null
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
      start_boundary_complete: boolean
      end_boundary_complete: boolean
      return_path_complete: boolean
      previous_date: string | null
    }
  >()

  sortedSeries.forEach((point) => {
    const bucketKey = point.as_of_date.slice(0, 7)
    let bucket = buckets.get(bucketKey)
    if (!bucket) {
      bucket = {
        bucket_key: bucketKey,
        start_date: point.as_of_date,
        end_date: point.as_of_date,
        coverage_state: pointReturnCoverage(point),
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
        start_boundary_complete:
          point.as_of_date === `${bucketKey}-01` &&
          point.beginning_nav != null &&
          pointValuationCoverage(point) === 'complete',
        end_boundary_complete: false,
        return_path_complete: true,
        previous_date: null,
      }
      buckets.set(bucketKey, bucket)
      orderedKeys.push(bucketKey)
    }

    bucket.end_date = point.as_of_date
    bucket.end_nav = point.ending_nav
    bucket.coverage_state =
      bucket.coverage_state === 'complete' ? pointReturnCoverage(point) : bucket.coverage_state
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
    if (pointReturnCoverage(point) !== 'complete' || point.daily_twr == null || !Number.isFinite(point.daily_twr)) {
      bucket.return_path_complete = false
    } else {
      bucket.growth_index *= 1 + point.daily_twr
      bucket.has_return = true
    }
    bucket.end_boundary_complete =
      point.ending_nav != null && pointValuationCoverage(point) === 'complete'
    if (bucket.previous_date != null && calendarDayGap(bucket.previous_date, point.as_of_date) !== 1) {
      bucket.return_path_complete = false
    }
    bucket.previous_date = point.as_of_date
  })

  return orderedKeys.map((key) => {
    const bucket = buckets.get(key)!
    const requiredEndDate = key === latestSeriesDate?.slice(0, 7) ? latestSeriesDate : monthEndDate(key)
    const complete =
      bucket.start_boundary_complete &&
      bucket.end_boundary_complete &&
      bucket.return_path_complete &&
      requiredEndDate != null &&
      bucket.end_date === requiredEndDate
    const unavailableReason = !bucket.start_boundary_complete
      ? 'Missing reliable month-start boundary.'
      : !bucket.end_boundary_complete || requiredEndDate == null || bucket.end_date !== requiredEndDate
        ? 'Missing reliable month-end boundary.'
        : !bucket.return_path_complete
          ? 'Return coverage is not continuous.'
          : null
    return {
      bucket_key: bucket.bucket_key,
      start_date: bucket.start_date,
      end_date: bucket.end_date,
      coverage_state: complete ? 'complete' : bucket.has_return ? 'partial' : 'unavailable',
      observation_count: bucket.observation_count,
      start_nav: bucket.start_nav,
      end_nav: bucket.end_nav,
      net_external_inflow: bucket.net_external_inflow,
      absolute_change: bucket.absolute_change_complete ? bucket.absolute_change : null,
      delta: bucket.delta_complete ? bucket.delta : null,
      cumulative_twr: complete && bucket.has_return ? bucket.growth_index - 1 : null,
      unavailable_reason: unavailableReason,
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
      const januaryBucket = row.months[0]
      const hasYearStartAnchor =
        januaryBucket?.start_date === `${row.year}-01-01` && januaryBucket.coverage_state === 'complete'
      const latestMonthIndex = row.months.reduce(
        (latest, bucket, index) => (bucket == null ? latest : index),
        -1,
      )
      const continuousMonths =
        latestMonthIndex >= 0 &&
        row.months
          .slice(0, latestMonthIndex + 1)
          .every((bucket) => bucket != null && bucket.coverage_state === 'complete')
      const returns = row.months
        .slice(0, latestMonthIndex + 1)
        .filter((bucket): bucket is MonthlyBucket => bucket != null)
        .map((bucket) => finiteNumber(bucket.cumulative_twr))
        .filter((value): value is number => value != null)
      return {
        ...row,
        hasYearStartAnchor,
        ytd: hasYearStartAnchor && continuousMonths ? compoundReturn(returns) : null,
        observationCount: populatedMonths.reduce((total, bucket) => total + bucket.observation_count, 0),
        coverageState: combineCoverage(populatedMonths.map((bucket) => bucket.coverage_state)),
      }
    })
    .sort((left, right) => right.year.localeCompare(left.year))
}
