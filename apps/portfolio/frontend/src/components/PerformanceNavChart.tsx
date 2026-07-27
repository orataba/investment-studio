import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent } from 'react'

import { formatCurrency, formatNumber, formatPercent, formatSignedCurrency } from '../lib/format'
import { rebasePerformanceSeriesTo100 } from '../lib/performanceSeries'

export type PerformanceNavChartPoint = {
  date: string
  value: number
}

type ChartDisplayStyle = 'mountain' | 'line' | 'dot'
type ChartSeriesMode = 'portfolio_value' | 'twr_index'

type PerformanceNavChartProps = {
  points: PerformanceNavChartPoint[]
  currency: string
  showRangeControls?: boolean
  variant?: 'nav' | 'overview'
  twrPoints?: PerformanceNavChartPoint[]
  benchmarkPoints?: PerformanceNavChartPoint[]
  benchmarkLabel?: string | null
}

const CHART_WIDTH = 960
const NAV_CHART_HEIGHT = 318
const DRAWDOWN_CHART_HEIGHT = 120
const NAV_CHART_PADDING = { top: 24, right: 18, bottom: 40, left: 58 }
const DRAWDOWN_CHART_PADDING = { top: 18, right: 18, bottom: 30, left: 58 }
const CHART_BAND_SEGMENTS = 10
const MONTH_SHORT_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

type RangeKey = '1M' | '3M' | '6M' | 'YTD' | '1Y' | '3Y' | 'MAX'

const RANGE_OPTIONS: Array<{ key: RangeKey; label: string }> = [
  { key: '1M', label: '1M' },
  { key: '3M', label: '3M' },
  { key: '6M', label: '6M' },
  { key: 'YTD', label: 'YTD' },
  { key: '1Y', label: '1Y' },
  { key: '3Y', label: '3Y' },
  { key: 'MAX', label: 'MAX' },
]

type Coordinate = {
  x: number
  y: number
}

type ChartBounds = {
  yMin: number
  yMax: number
  startTime: number | null
  endTime: number | null
}

function toDateMs(date: string) {
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T00:00:00` : date
  const time = Date.parse(normalized)
  return Number.isNaN(time) ? null : time
}

function formatLocalDateKey(time: number) {
  const date = new Date(time)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function getYAxisStubEndX(padding: typeof NAV_CHART_PADDING) {
  return padding.left - 14
}

function getYAxisLabelTextY(lineY: number, chartHeight: number, placement: 'below' | 'above' = 'below') {
  if (placement === 'above') {
    return Math.max(lineY - 6, 12)
  }
  return Math.min(lineY + 18, chartHeight - 4)
}

function getXAxisTickTopY(chartHeight: number, padding: typeof NAV_CHART_PADDING) {
  return chartHeight - padding.bottom + 5
}

function getXAxisLabelY(chartHeight: number) {
  return chartHeight - 10
}

function formatDateTickLabel(dateKey: string, previousDateKey: string | null, spanDays: number) {
  const currentTime = toDateMs(dateKey)
  if (currentTime == null) {
    return dateKey
  }
  const currentDate = new Date(currentTime)
  const previousTime = previousDateKey ? toDateMs(previousDateKey) : null
  const previousDate = previousTime == null ? null : new Date(previousTime)
  const month = MONTH_SHORT_LABELS[currentDate.getMonth()]
  if (!previousDate || currentDate.getFullYear() !== previousDate.getFullYear()) {
    return String(currentDate.getFullYear())
  }
  if (spanDays <= 120) {
    return `${month} ${currentDate.getDate()}`
  }
  return month
}

function buildDateTicks(points: PerformanceNavChartPoint[], padding: typeof NAV_CHART_PADDING, count = 7) {
  const startTime = toDateMs(points[0]?.date)
  const endTime = toDateMs(points[points.length - 1]?.date)
  if (startTime == null || endTime == null) {
    return []
  }
  const drawableWidth = CHART_WIDTH - padding.left - padding.right
  const spanDays = Math.max(0, Math.round((endTime - startTime) / 86_400_000))
  const targetCount = Math.min(Math.max(2, count), spanDays + 1)
  const ticks = Array.from({ length: targetCount }, (_, index) => {
    const ratio = targetCount <= 1 ? 0 : index / (targetCount - 1)
    const date = formatLocalDateKey(startTime + (endTime - startTime) * ratio)
    return {
      date,
      x: padding.left + ratio * drawableWidth,
      label: '',
    }
  })
  return ticks.map((tick, index) => ({
    ...tick,
    label: formatDateTickLabel(tick.date, ticks[index - 1]?.date ?? null, spanDays),
  }))
}

function buildPlotBands(padding: typeof NAV_CHART_PADDING, chartHeight: number, segments = CHART_BAND_SEGMENTS) {
  const plottingWidth = CHART_WIDTH - padding.left - padding.right
  const bandWidth = plottingWidth / segments
  return Array.from({ length: segments }, (_, index) => {
    if (index % 2 === 0) {
      return null
    }
    return {
      x: padding.left + bandWidth * index,
      y: padding.top,
      width: bandWidth,
      height: chartHeight - padding.top - padding.bottom,
    }
  }).filter((band): band is { x: number; y: number; width: number; height: number } => band != null)
}

function addMonths(date: Date, months: number) {
  const nextDate = new Date(date)
  nextDate.setMonth(nextDate.getMonth() + months)
  return nextDate
}

function rangeStartDate(latestDate: string | undefined, rangeKey: RangeKey) {
  if (!latestDate) {
    return null
  }

  const latestTime = toDateMs(latestDate)
  if (latestTime == null || rangeKey === 'MAX') {
    return null
  }

  const latest = new Date(latestTime)

  if (rangeKey === 'YTD') {
    return new Date(latest.getFullYear(), 0, 1)
  }

  const monthOffsetByRange: Record<Exclude<RangeKey, 'YTD' | 'MAX'>, number> = {
    '1M': -1,
    '3M': -3,
    '6M': -6,
    '1Y': -12,
    '3Y': -36,
  }

  return addMonths(latest, monthOffsetByRange[rangeKey])
}

function getRangeStartIndex(points: PerformanceNavChartPoint[], rangeKey: RangeKey) {
  const targetDate = rangeStartDate(points[points.length - 1]?.date, rangeKey)
  if (!targetDate) {
    return 0
  }

  const targetTime = targetDate.getTime()
  const startIndex = points.findIndex((point) => {
    const pointTime = toDateMs(point.date)
    return pointTime != null && pointTime >= targetTime
  })
  return startIndex === -1 ? 0 : startIndex
}

function buildLinePath(coordinates: Coordinate[]) {
  return coordinates
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(' ')
}

function buildAreaPath(coordinates: Coordinate[], baselineY: number) {
  if (!coordinates.length) {
    return ''
  }
  return `${buildLinePath(coordinates)} L ${coordinates[coordinates.length - 1].x.toFixed(2)} ${baselineY} L ${coordinates[0].x.toFixed(2)} ${baselineY} Z`
}

function buildBounds(points: PerformanceNavChartPoint[], comparisonPoints: PerformanceNavChartPoint[] = []) {
  const allPoints = [...points, ...comparisonPoints]
  const values = allPoints.map((point) => point.value)
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const rawSpan = maxValue - minValue
  const cushion = rawSpan > 0 ? rawSpan * 0.08 : Math.max(Math.abs(maxValue) * 0.02, 1)
  const yMin = minValue - cushion
  const yMax = maxValue + cushion
  const startTime = toDateMs(points[0]?.date)
  const endTime = toDateMs(points[points.length - 1]?.date)

  return {
    yMin,
    yMax,
    startTime,
    endTime,
  }
}

function projectCoordinates(
  points: PerformanceNavChartPoint[],
  bounds: ChartBounds,
  chartHeight: number,
  padding: typeof NAV_CHART_PADDING,
) {
  const span = bounds.yMax - bounds.yMin || 1
  const drawableWidth = CHART_WIDTH - padding.left - padding.right
  const drawableHeight = chartHeight - padding.top - padding.bottom
  const useDateScale =
    bounds.startTime != null && bounds.endTime != null && bounds.endTime > bounds.startTime

  return points.map((point, index) => {
    const pointTime = toDateMs(point.date)
    const xRatio =
      useDateScale && pointTime != null
        ? (pointTime - bounds.startTime!) / (bounds.endTime! - bounds.startTime!)
        : points.length > 1
          ? index / (points.length - 1)
          : 0
    const x = padding.left + Math.min(Math.max(xRatio, 0), 1) * drawableWidth
    const y = padding.top + drawableHeight - ((point.value - bounds.yMin) / span) * drawableHeight
    return { x, y }
  })
}

function buildNavGeometry(points: PerformanceNavChartPoint[], comparisonPoints: PerformanceNavChartPoint[] = []) {
  const bounds = buildBounds(points, comparisonPoints)
  const coordinates = projectCoordinates(points, bounds, NAV_CHART_HEIGHT, NAV_CHART_PADDING)
  const comparisonCoordinates = projectCoordinates(comparisonPoints, bounds, NAV_CHART_HEIGHT, NAV_CHART_PADDING)
  const baselineY = NAV_CHART_HEIGHT - NAV_CHART_PADDING.bottom
  const span = bounds.yMax - bounds.yMin || 1
  const guideValues = [bounds.yMax, bounds.yMin + span * 0.67, bounds.yMin + span * 0.33, bounds.yMin]

  return {
    coordinates,
    comparisonCoordinates,
    linePath: buildLinePath(coordinates),
    comparisonLinePath: buildLinePath(comparisonCoordinates),
    areaPath: buildAreaPath(coordinates, baselineY),
    guideValues,
    yMin: bounds.yMin,
    yMax: bounds.yMax,
  }
}

function buildDrawdownPoints(points: PerformanceNavChartPoint[], baselineValue?: number) {
  let runningHigh = baselineValue ?? points[0]?.value ?? 0
  return points.map((point) => {
    runningHigh = Math.max(runningHigh, point.value)
    const drawdown = runningHigh > 0 ? point.value / runningHigh - 1 : 0
    return { date: point.date, drawdown }
  })
}

function buildDrawdownGeometry(points: Array<{ date: string; drawdown: number }>) {
  const minDrawdown = Math.min(...points.map((point) => point.drawdown), 0)
  const yMin = minDrawdown < 0 ? minDrawdown : -0.01
  const drawableWidth = CHART_WIDTH - DRAWDOWN_CHART_PADDING.left - DRAWDOWN_CHART_PADDING.right
  const drawableHeight = DRAWDOWN_CHART_HEIGHT - DRAWDOWN_CHART_PADDING.top - DRAWDOWN_CHART_PADDING.bottom
  const startTime = toDateMs(points[0]?.date)
  const endTime = toDateMs(points[points.length - 1]?.date)
  const useDateScale = startTime != null && endTime != null && endTime > startTime

  const coordinates = points.map((point, index) => {
    const pointTime = toDateMs(point.date)
    const xRatio =
      useDateScale && pointTime != null ? (pointTime - startTime!) / (endTime! - startTime!) : index / (points.length - 1)
    const x = DRAWDOWN_CHART_PADDING.left + Math.min(Math.max(xRatio, 0), 1) * drawableWidth
    const y = DRAWDOWN_CHART_PADDING.top + ((0 - point.drawdown) / (0 - yMin)) * drawableHeight
    return { x, y }
  })

  const linePath = buildLinePath(coordinates)
  const zeroY = DRAWDOWN_CHART_PADDING.top
  const areaPath = buildAreaPath(coordinates, zeroY)
  const guideValues = [0, yMin / 2, yMin]

  return {
    coordinates,
    linePath,
    areaPath,
    guideValues,
    yMin,
  }
}

function normalizePoints(points: PerformanceNavChartPoint[]) {
  return points
    .filter((point) => point.date && Number.isFinite(point.value))
    .slice()
    .sort((firstPoint, secondPoint) => firstPoint.date.localeCompare(secondPoint.date))
}

function filterPointsByDateWindow(points: PerformanceNavChartPoint[], startDate?: string, endDate?: string) {
  if (!startDate || !endDate) {
    return []
  }
  return points.filter((point) => point.date >= startDate && point.date <= endDate)
}

function findPointAtOrBefore(points: PerformanceNavChartPoint[], targetDate: string) {
  let selected: PerformanceNavChartPoint | null = null
  points.forEach((point) => {
    if (point.date <= targetDate) {
      selected = point
    }
  })
  return selected ?? points[0] ?? null
}

function findPointBefore(points: PerformanceNavChartPoint[], targetDate: string) {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].date < targetDate) {
      return points[index]
    }
  }
  return null
}

function findDrawdownAtOrBefore(points: Array<{ date: string; drawdown: number }>, targetDate: string) {
  let selected: { date: string; drawdown: number } | null = null
  points.forEach((point) => {
    if (point.date <= targetDate) {
      selected = point
    }
  })
  return selected ?? points[0] ?? null
}

function formatSignedPercent(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return '-'
  }
  const absolute = formatPercent(Math.abs(value))
  if (value > 0) {
    return `+${absolute}`
  }
  if (value < 0) {
    return `-${absolute}`
  }
  return absolute
}

function toneClassName(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return ''
  }
  if (value > 0) {
    return 'portfolio-nav-chart-change-positive'
  }
  if (value < 0) {
    return 'portfolio-nav-chart-change-negative'
  }
  return ''
}

function formatSeriesValue(value: number | null | undefined, mode: ChartSeriesMode, currency: string) {
  if (mode === 'portfolio_value') {
    return formatCurrency(value, currency)
  }
  return value == null || Number.isNaN(value) ? '-' : formatNumber(value, 2)
}

function getYAxisUnit(values: number[], mode: ChartSeriesMode, currency: string) {
  if (mode !== 'portfolio_value') {
    return { label: 'Index', divisor: 1, digits: 2 }
  }

  const maxAbsValue = Math.max(...values.map((value) => Math.abs(value)), 0)
  if (maxAbsValue >= 1_000_000_000) {
    return { label: `${currency} bn`, divisor: 1_000_000_000, digits: 2 }
  }
  if (maxAbsValue >= 1_000_000) {
    return { label: `${currency} mn`, divisor: 1_000_000, digits: 2 }
  }
  if (maxAbsValue >= 1_000) {
    return { label: `${currency} k`, divisor: 1_000, digits: 1 }
  }
  return { label: currency, divisor: 1, digits: 0 }
}

function formatAxisValue(
  value: number | null | undefined,
  axisUnit: ReturnType<typeof getYAxisUnit>,
) {
  if (value == null || Number.isNaN(value)) {
    return '-'
  }
  return formatNumber(value / axisUnit.divisor, axisUnit.digits)
}

function formatValueTag(value: number | null | undefined, mode: ChartSeriesMode, currency: string) {
  if (value == null || Number.isNaN(value)) {
    return '-'
  }
  if (mode !== 'portfolio_value') {
    return formatNumber(value, 2)
  }
  const absoluteValue = Math.abs(value)
  if (absoluteValue >= 1_000_000_000) {
    return `${formatCurrency(value / 1_000_000_000, currency)}B`
  }
  if (absoluteValue >= 1_000_000) {
    return `${formatCurrency(value / 1_000_000, currency)}M`
  }
  if (absoluteValue >= 1_000) {
    return `${formatCurrency(value / 1_000, currency)}K`
  }
  return formatCurrency(value, currency)
}

function buildValueTagGeometry(
  coordinate: Coordinate | undefined,
  label: string,
  padding: typeof NAV_CHART_PADDING,
  chartHeight: number,
) {
  if (!coordinate) {
    return null
  }
  const width = Math.min(Math.max(label.length * 6.4 + 16, 52), 132)
  const height = 22
  const x = Math.min(
    CHART_WIDTH - padding.right - width,
    Math.max(padding.left, coordinate.x - width - 8),
  )
  const y = Math.min(
    chartHeight - padding.bottom - height,
    Math.max(padding.top, coordinate.y - height / 2),
  )
  return {
    x,
    y,
    width,
    height,
    textX: x + width / 2,
    textY: y + 14.5,
  }
}

function seriesLabel(mode: ChartSeriesMode) {
  return mode === 'portfolio_value' ? 'Value' : 'TWR Return'
}

function chartUnitLabel(mode: ChartSeriesMode, axisUnit: ReturnType<typeof getYAxisUnit>) {
  return mode === 'portfolio_value' ? axisUnit.label : 'Index'
}

export default function PerformanceNavChart({
  points,
  currency,
  showRangeControls = true,
  variant = 'nav',
  twrPoints = [],
  benchmarkPoints = [],
  benchmarkLabel = null,
}: PerformanceNavChartProps) {
  const isOverview = variant === 'overview'
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [selectedRange, setSelectedRange] = useState<RangeKey | 'CUSTOM'>('MAX')
  const [windowStartIndex, setWindowStartIndex] = useState(0)
  const [windowEndIndex, setWindowEndIndex] = useState<number | null>(null)
  const [seriesMode, setSeriesMode] = useState<ChartSeriesMode>(isOverview ? 'twr_index' : 'portfolio_value')
  const [displayStyle, setDisplayStyle] = useState<ChartDisplayStyle>('mountain')
  const [showDrawdownPanel, setShowDrawdownPanel] = useState(true)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsMenuRef = useRef<HTMLDivElement | null>(null)

  const sortedValuePoints = useMemo(() => normalizePoints(points), [points])
  const sortedTwrPoints = useMemo(() => normalizePoints(twrPoints), [twrPoints])
  const sortedBenchmarkPoints = useMemo(() => normalizePoints(benchmarkPoints), [benchmarkPoints])
  const hasTwrSeries = sortedTwrPoints.length > 1
  const effectiveSeriesMode = isOverview && seriesMode === 'twr_index' && hasTwrSeries ? 'twr_index' : 'portfolio_value'
  const sortedMainPoints = effectiveSeriesMode === 'twr_index' ? sortedTwrPoints : sortedValuePoints
  const useTwrDrawdown = hasTwrSeries

  useEffect(() => {
    if (!settingsOpen) {
      return undefined
    }

    function handleDocumentPointerDown(event: PointerEvent) {
      if (!settingsMenuRef.current?.contains(event.target as Node)) {
        setSettingsOpen(false)
      }
    }

    document.addEventListener('pointerdown', handleDocumentPointerDown)
    return () => document.removeEventListener('pointerdown', handleDocumentPointerDown)
  }, [settingsOpen])

  const maxIndex = Math.max(0, sortedMainPoints.length - 1)
  const rawEndIndex = windowEndIndex ?? maxIndex
  const clampedWindowEndIndex = Math.min(Math.max(rawEndIndex, 1), maxIndex)
  const clampedWindowStartIndex = Math.min(
    Math.max(windowStartIndex, 0),
    Math.max(0, clampedWindowEndIndex - 1),
  )
  const visibleRawPoints = sortedMainPoints.slice(clampedWindowStartIndex, clampedWindowEndIndex + 1)
  const visibleStartDate = visibleRawPoints[0]?.date
  const visibleEndDate = visibleRawPoints[visibleRawPoints.length - 1]?.date
  const visibleTwrRawPoints = filterPointsByDateWindow(sortedTwrPoints, visibleStartDate, visibleEndDate)
  const visiblePoints =
    effectiveSeriesMode === 'twr_index'
      ? rebasePerformanceSeriesTo100(visibleRawPoints)
      : visibleRawPoints
  const visibleTwrPoints =
    effectiveSeriesMode === 'twr_index'
      ? visiblePoints
      : visibleTwrRawPoints
  const firstVisibleTwrDate = visibleTwrRawPoints[0]?.date
  const twrWindowBaseValue = firstVisibleTwrDate
    ? findPointBefore(sortedTwrPoints, firstVisibleTwrDate)?.value ?? 100
    : 100
  const visibleBenchmarkRawPoints =
    isOverview && effectiveSeriesMode === 'twr_index'
      ? filterPointsByDateWindow(sortedBenchmarkPoints, visibleStartDate, visibleEndDate)
      : []
  const visibleBenchmarkPoints =
    visibleBenchmarkRawPoints.length > 1 && visiblePoints.length
      ? rebasePerformanceSeriesTo100(visibleBenchmarkRawPoints)
      : []

  const drawdownSourcePoints = useTwrDrawdown ? visibleTwrPoints : visiblePoints

  const chartState = useMemo(() => {
    if (visiblePoints.length < 2) {
      return null
    }

    const drawdownPoints =
      drawdownSourcePoints.length > 1
        ? buildDrawdownPoints(
            drawdownSourcePoints,
            useTwrDrawdown && effectiveSeriesMode !== 'twr_index'
              ? twrWindowBaseValue
              : undefined,
          )
        : []

    return {
      navGeometry: buildNavGeometry(visiblePoints, visibleBenchmarkPoints),
      drawdownGeometry: drawdownPoints.length > 1 ? buildDrawdownGeometry(drawdownPoints) : null,
      drawdownPoints,
    }
  }, [
    drawdownSourcePoints,
    effectiveSeriesMode,
    twrWindowBaseValue,
    useTwrDrawdown,
    visibleBenchmarkPoints,
    visiblePoints,
  ])

  if (!chartState) {
    return <div className="price-chart-empty">Insufficient data.</div>
  }

  const activeIndex = Math.min(hoveredIndex ?? visiblePoints.length - 1, visiblePoints.length - 1)
  const activePoint = visiblePoints[activeIndex]
  const activeCoordinate = chartState.navGeometry.coordinates[activeIndex]
  const firstPoint = visiblePoints[0]
  const lastPoint = visiblePoints[visiblePoints.length - 1]
  const activePortfolioValuePoint = findPointAtOrBefore(sortedValuePoints, activePoint.date)
  const activeTwrPoint = findPointAtOrBefore(sortedTwrPoints, activePoint.date)
  const activePeriodTwr =
    effectiveSeriesMode === 'twr_index'
      ? activePoint.value / 100 - 1
      : activeTwrPoint && twrWindowBaseValue !== 0
        ? activeTwrPoint.value / twrWindowBaseValue - 1
        : null
  const activeDrawdown = findDrawdownAtOrBefore(chartState.drawdownPoints, activePoint.date)?.drawdown ?? null
  const changeValue = activePoint.value - firstPoint.value
  const changePct = firstPoint.value !== 0 ? changeValue / firstPoint.value : null
  const activePeriodReturn = effectiveSeriesMode === 'twr_index' ? activePeriodTwr : changePct
  const activePeriodLabel = effectiveSeriesMode === 'twr_index' ? 'Period TWR' : 'Period Value'
  const activeChangeClassName = toneClassName(isOverview ? activePeriodReturn : changeValue)
  const benchmarkPeriodReturn =
    visibleBenchmarkPoints.length > 1
      ? visibleBenchmarkPoints[visibleBenchmarkPoints.length - 1].value / 100 - 1
      : null

  const windowHigh = Math.max(...visiblePoints.map((point) => point.value))
  const windowLow = Math.min(...visiblePoints.map((point) => point.value))
  const maxDrawdown = chartState.drawdownPoints.length
    ? Math.min(...chartState.drawdownPoints.map((point) => point.drawdown))
    : null
  const latestMainCoordinate = chartState.navGeometry.coordinates[chartState.navGeometry.coordinates.length - 1]
  const latestMainTagLabel = formatValueTag(lastPoint.value, effectiveSeriesMode, currency)
  const latestMainTag = buildValueTagGeometry(
    latestMainCoordinate,
    latestMainTagLabel,
    NAV_CHART_PADDING,
    NAV_CHART_HEIGHT,
  )
  const drawdownCoordinates = chartState.drawdownGeometry?.coordinates ?? []
  const latestDrawdownCoordinate = drawdownCoordinates[drawdownCoordinates.length - 1]
  const latestDrawdownPoint = chartState.drawdownPoints[chartState.drawdownPoints.length - 1]
  const latestDrawdownTagLabel = latestDrawdownPoint ? formatPercent(latestDrawdownPoint.drawdown) : '-'
  const latestDrawdownTag = buildValueTagGeometry(
    latestDrawdownCoordinate,
    latestDrawdownTagLabel,
    DRAWDOWN_CHART_PADDING,
    DRAWDOWN_CHART_HEIGHT,
  )
  const mainBands = buildPlotBands(NAV_CHART_PADDING, NAV_CHART_HEIGHT)
  const drawdownBands = buildPlotBands(DRAWDOWN_CHART_PADDING, DRAWDOWN_CHART_HEIGHT)
  const mainDateTicks = buildDateTicks(visiblePoints, NAV_CHART_PADDING, 8)
  const windowReturn = firstPoint.value !== 0 ? (lastPoint.value - firstPoint.value) / firstPoint.value : null
  const windowReturnClassName = toneClassName(isOverview ? activePeriodReturn : windowReturn)
  const yAxisUnit = getYAxisUnit(chartState.navGeometry.guideValues, effectiveSeriesMode, currency)
  const chartUnit = chartUnitLabel(effectiveSeriesMode, yAxisUnit)
  const zoomStartPct = maxIndex > 0 ? (clampedWindowStartIndex / maxIndex) * 100 : 0
  const zoomEndPct = maxIndex > 0 ? (clampedWindowEndIndex / maxIndex) * 100 : 100
  const zoomSliderStyle = {
    '--portfolio-nav-zoom-start': `${zoomStartPct}%`,
    '--portfolio-nav-zoom-end': `${zoomEndPct}%`,
  } as CSSProperties
  const showDrawdown = showDrawdownPanel && chartState.drawdownGeometry && chartState.drawdownPoints.length > 1

  function handleRangeSelect(rangeKey: RangeKey) {
    setSelectedRange(rangeKey)
    setHoveredIndex(null)
    setWindowStartIndex(getRangeStartIndex(sortedMainPoints, rangeKey))
    setWindowEndIndex(maxIndex)
  }

  function handleZoomStartChange(nextStartIndex: number) {
    setSelectedRange('CUSTOM')
    setHoveredIndex(null)
    setWindowStartIndex(Math.min(nextStartIndex, clampedWindowEndIndex - 1))
  }

  function handleZoomEndChange(nextEndIndex: number) {
    setSelectedRange('CUSTOM')
    setHoveredIndex(null)
    setWindowEndIndex(Math.max(nextEndIndex, clampedWindowStartIndex + 1))
  }

  function handleSeriesModeChange(nextMode: ChartSeriesMode) {
    setSeriesMode(nextMode)
    setSelectedRange('MAX')
    setWindowStartIndex(0)
    setWindowEndIndex(null)
    setHoveredIndex(null)
  }

  function handlePointerMove(event: MouseEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const chartX = bounds.width > 0 ? ((event.clientX - bounds.left) / bounds.width) * CHART_WIDTH : 0
    const drawableWidth = CHART_WIDTH - NAV_CHART_PADDING.left - NAV_CHART_PADDING.right
    const ratio = drawableWidth > 0 ? (chartX - NAV_CHART_PADDING.left) / drawableWidth : 0
    const nextIndex = Math.min(
      visiblePoints.length - 1,
      Math.max(0, Math.round(ratio * (visiblePoints.length - 1))),
    )
    setHoveredIndex(nextIndex)
  }

  const rangeControls =
    showRangeControls ? (
      <div
        className="portfolio-nav-range-strip"
        role="group"
        aria-label={isOverview ? 'Portfolio chart time range' : 'Portfolio NAV time range'}
      >
        {RANGE_OPTIONS.map((option) => (
          <button
            type="button"
            className={`portfolio-nav-range-button ${selectedRange === option.key ? 'portfolio-nav-range-button-active' : ''}`}
            key={option.key}
            onClick={() => handleRangeSelect(option.key)}
          >
            {option.label}
          </button>
        ))}
      </div>
    ) : null

  const settingsMenu =
    isOverview && settingsOpen ? (
      <div className="portfolio-nav-settings-panel">
        <div className="portfolio-nav-settings-layout">
          <section className="portfolio-nav-settings-block">
            <div className="portfolio-nav-settings-block-head">
              <span>Series</span>
              <strong>{seriesLabel(effectiveSeriesMode)}</strong>
            </div>
            <div className="portfolio-nav-settings-control-group">
              <span>Data Type</span>
              <div className="portfolio-nav-settings-option-grid">
                {(['portfolio_value', 'twr_index'] as ChartSeriesMode[]).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    className={
                      effectiveSeriesMode === mode
                        ? 'portfolio-nav-option portfolio-nav-option-active'
                        : 'portfolio-nav-option'
                    }
                    disabled={mode === 'twr_index' && !hasTwrSeries}
                    onClick={() => handleSeriesModeChange(mode)}
                  >
                    {seriesLabel(mode)}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="portfolio-nav-settings-block">
            <div className="portfolio-nav-settings-block-head">
              <span>Display</span>
              <strong>{chartUnit}</strong>
            </div>
            <div className="portfolio-nav-settings-control-group">
              <span>Chart Style</span>
              <div className="portfolio-nav-settings-option-grid">
                {(['mountain', 'line', 'dot'] as ChartDisplayStyle[]).map((style) => (
                  <button
                    key={style}
                    type="button"
                    className={
                      displayStyle === style ? 'portfolio-nav-option portfolio-nav-option-active' : 'portfolio-nav-option'
                    }
                    onClick={() => setDisplayStyle(style)}
                  >
                    {style.charAt(0).toUpperCase() + style.slice(1)}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="portfolio-nav-settings-block portfolio-nav-settings-block-data">
            <div className="portfolio-nav-settings-block-head">
              <span>Events & Data</span>
              <strong>Chart overlays</strong>
            </div>
            <div className="portfolio-nav-settings-option-grid">
              <button
                type="button"
                className={
                  showDrawdownPanel ? 'portfolio-nav-option portfolio-nav-option-active' : 'portfolio-nav-option'
                }
                onClick={() => setShowDrawdownPanel((current) => !current)}
              >
                Drawdown
              </button>
            </div>
          </section>
        </div>
      </div>
    ) : null

  return (
    <section className={`portfolio-nav-chart ${isOverview ? 'portfolio-nav-chart-overview' : ''}`}>
      {isOverview ? (
        <div className="portfolio-nav-chart-series-head">
          <div className="portfolio-series-legend">
            <div className="portfolio-series-label">
              <strong>Portfolio</strong>
              <span>{seriesLabel(effectiveSeriesMode)}</span>
              {effectiveSeriesMode === 'portfolio_value' ? (
                <em>{formatCurrency(activePoint.value, currency)}</em>
              ) : null}
              <em className={activeChangeClassName}>{activePeriodLabel} {formatSignedPercent(activePeriodReturn)}</em>
            </div>
            {benchmarkLabel && effectiveSeriesMode === 'twr_index' ? (
              <div className="portfolio-series-label portfolio-series-label-benchmark-row">
                <strong>{benchmarkLabel}</strong>
                <span>Benchmark</span>
                <em className={toneClassName(benchmarkPeriodReturn)}>
                  Period {formatSignedPercent(benchmarkPeriodReturn)}
                </em>
              </div>
            ) : null}
          </div>
          <div className="portfolio-nav-chart-series-meta">
            <span className="portfolio-nav-chart-currency">{chartUnit}</span>
            <div className="portfolio-nav-chart-menu" ref={settingsMenuRef}>
              <button
                type="button"
                className={
                  settingsOpen
                    ? 'portfolio-nav-settings-trigger portfolio-nav-settings-trigger-active'
                    : 'portfolio-nav-settings-trigger'
                }
                aria-label="Chart settings"
                onClick={() => setSettingsOpen((current) => !current)}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M4 7h4" />
                  <path d="M14 7h6" />
                  <circle cx="11" cy="7" r="2.25" />
                  <path d="M4 17h7" />
                  <path d="M17 17h3" />
                  <circle cx="14" cy="17" r="2.25" />
                </svg>
              </button>
              {settingsMenu}
            </div>
          </div>
        </div>
      ) : (
        <div className="portfolio-nav-chart-header">
          <div className="portfolio-nav-chart-readout">
            <strong>{formatCurrency(activePoint.value, currency)}</strong>
            <span className={toneClassName(changeValue)}>
              {changeValue === 0 ? formatCurrency(changeValue, currency) : formatSignedCurrency(changeValue, currency)} /{' '}
              {formatPercent(changePct)}
            </span>
            <span>{activePoint.date}</span>
          </div>
          <div className="portfolio-nav-chart-actions">
            <span className="portfolio-nav-chart-currency">{currency}</span>
            {rangeControls}
          </div>
        </div>
      )}

      <div className="portfolio-nav-chart-plot">
        {hoveredIndex != null ? (
          <div
            className={`portfolio-nav-chart-tooltip ${activeCoordinate.x > CHART_WIDTH * 0.72 ? 'portfolio-nav-chart-tooltip-left' : ''}`}
            style={{ left: `${(activeCoordinate.x / CHART_WIDTH) * 100}%` }}
          >
            <span>{activePoint.date}</span>
            <strong>{formatSeriesValue(activePoint.value, effectiveSeriesMode, currency)}</strong>
            {isOverview ? (
              <>
                {effectiveSeriesMode === 'twr_index' ? (
                  <span>Value {formatCurrency(activePortfolioValuePoint?.value, currency)}</span>
                ) : null}
                <span>{activePeriodLabel} {formatSignedPercent(activePeriodReturn)}</span>
                {effectiveSeriesMode === 'portfolio_value' && hasTwrSeries ? (
                  <span>Period TWR {formatSignedPercent(activePeriodTwr)}</span>
                ) : null}
              </>
            ) : (
              <span>Period {formatPercent(changePct)}</span>
            )}
            <span>DD {formatPercent(activeDrawdown)}</span>
          </div>
        ) : null}
        <svg
          className="portfolio-nav-chart-svg portfolio-nav-main-svg"
          viewBox={`0 0 ${CHART_WIDTH} ${NAV_CHART_HEIGHT}`}
          role="img"
          aria-label={isOverview ? `${seriesLabel(effectiveSeriesMode)} trend` : 'Portfolio NAV trend'}
          onMouseMove={handlePointerMove}
          onMouseLeave={() => setHoveredIndex(null)}
        >
          {isOverview
            ? mainBands.map((band) => (
                <rect
                  key={`main-band-${band.x.toFixed(2)}`}
                  className="portfolio-nav-chart-band"
                  x={band.x}
                  y={band.y}
                  width={band.width}
                  height={band.height}
                />
              ))
            : null}
          {chartState.navGeometry.guideValues.map((guideValue, guideIndex) => {
            const guideRatio =
              chartState.navGeometry.yMax === chartState.navGeometry.yMin
                ? 0.5
                : (guideValue - chartState.navGeometry.yMin) /
                  (chartState.navGeometry.yMax - chartState.navGeometry.yMin)
            const y =
              NAV_CHART_PADDING.top +
              (NAV_CHART_HEIGHT - NAV_CHART_PADDING.top - NAV_CHART_PADDING.bottom) * (1 - guideRatio)
            const isBottomTick = guideIndex === chartState.navGeometry.guideValues.length - 1
            return (
              <g key={`${guideIndex}:${guideValue}`}>
                {isOverview ? (
                  <line
                    className={`portfolio-nav-grid-line portfolio-nav-grid-line-stub ${
                      isBottomTick ? 'portfolio-nav-grid-line-emphasis' : ''
                    }`}
                    x1="8"
                    x2={getYAxisStubEndX(NAV_CHART_PADDING)}
                    y1={y}
                    y2={y}
                  />
                ) : null}
                <line
                  className={`portfolio-nav-grid-line ${isBottomTick ? 'portfolio-nav-grid-line-emphasis' : ''}`}
                  x1={NAV_CHART_PADDING.left}
                  x2={CHART_WIDTH - NAV_CHART_PADDING.right}
                  y1={y}
                  y2={y}
                />
                <text
                  className="portfolio-nav-axis-label"
                  x={isOverview ? getYAxisStubEndX(NAV_CHART_PADDING) : CHART_WIDTH - 4}
                  y={
                    isOverview
                      ? getYAxisLabelTextY(y, NAV_CHART_HEIGHT, isBottomTick ? 'above' : 'below')
                      : y - 6
                  }
                  textAnchor="end"
                >
                  {isOverview
                    ? formatAxisValue(guideValue, yAxisUnit)
                    : formatSeriesValue(guideValue, effectiveSeriesMode, currency)}
                </text>
              </g>
            )
          })}

          {isOverview
            ? mainDateTicks.map((tick) => (
                <g key={`main-date-${tick.date}`}>
                  <line
                    className="portfolio-nav-x-axis-tick"
                    x1={tick.x}
                    x2={tick.x}
                    y1={getXAxisTickTopY(NAV_CHART_HEIGHT, NAV_CHART_PADDING)}
                    y2={getXAxisTickTopY(NAV_CHART_HEIGHT, NAV_CHART_PADDING) + 6}
                  />
                  <text className="portfolio-nav-x-axis-label" x={tick.x} y={getXAxisLabelY(NAV_CHART_HEIGHT)}>
                    {tick.label}
                  </text>
                </g>
              ))
            : null}

          {displayStyle === 'mountain' ? <path className="portfolio-nav-area" d={chartState.navGeometry.areaPath} /> : null}
          {displayStyle !== 'dot' ? <path className="portfolio-nav-line" d={chartState.navGeometry.linePath} /> : null}
          {visibleBenchmarkPoints.length > 1 && chartState.navGeometry.comparisonLinePath ? (
            <path className="portfolio-nav-line portfolio-nav-line-benchmark" d={chartState.navGeometry.comparisonLinePath} />
          ) : null}
          {displayStyle === 'dot'
            ? chartState.navGeometry.coordinates.map((coordinate, index) => (
                <circle
                  key={`main-point-${visiblePoints[index]?.date ?? index}`}
                  className="portfolio-nav-point"
                  cx={coordinate.x}
                  cy={coordinate.y}
                  r={3.2}
                />
              ))
            : null}
          <line
            className="portfolio-nav-guide-line"
            x1={activeCoordinate.x}
            x2={activeCoordinate.x}
            y1={NAV_CHART_PADDING.top}
            y2={NAV_CHART_HEIGHT - NAV_CHART_PADDING.bottom}
          />
          <circle className="portfolio-nav-point" cx={activeCoordinate.x} cy={activeCoordinate.y} r={4.5} />
          {isOverview && latestMainTag ? (
            <g className="portfolio-nav-value-tag">
              <rect
                x={latestMainTag.x}
                y={latestMainTag.y}
                width={latestMainTag.width}
                height={latestMainTag.height}
                rx="0"
              />
              <text x={latestMainTag.textX} y={latestMainTag.textY}>
                {latestMainTagLabel}
              </text>
            </g>
          ) : null}
        </svg>
      </div>

      {showDrawdown ? (
        <div className="portfolio-nav-drawdown-shell">
          {isOverview ? (
            <div className="portfolio-nav-drawdown-header">
              <span>Drawdown</span>
              <strong className="portfolio-nav-chart-change-negative">{formatPercent(activeDrawdown)}</strong>
              <em>
                {useTwrDrawdown ? 'Based on TWR' : 'Based on NAV'} · Max {formatPercent(maxDrawdown)}
              </em>
            </div>
          ) : useTwrDrawdown ? (
            <div className="portfolio-nav-drawdown-header">
              <span>Drawdown</span>
              <strong>{useTwrDrawdown ? 'Based on TWR' : 'Based on NAV'}</strong>
              <em>
                Current {formatPercent(activeDrawdown)} / Max {formatPercent(maxDrawdown)}
              </em>
            </div>
          ) : null}
          <div className="portfolio-nav-drawdown-plot">
            <svg
              className="portfolio-nav-chart-svg"
              viewBox={`0 0 ${CHART_WIDTH} ${DRAWDOWN_CHART_HEIGHT}`}
              role="img"
              aria-label={isOverview || useTwrDrawdown ? 'Portfolio TWR drawdown' : 'Portfolio NAV drawdown'}
              onMouseMove={handlePointerMove}
              onMouseLeave={() => setHoveredIndex(null)}
            >
              {isOverview
                ? drawdownBands.map((band) => (
                    <rect
                      key={`drawdown-band-${band.x.toFixed(2)}`}
                      className="portfolio-nav-chart-band"
                      x={band.x}
                      y={band.y}
                      width={band.width}
                      height={band.height}
                    />
                  ))
                : null}
              {chartState.drawdownGeometry!.guideValues.map((guideValue, guideIndex) => {
                const y =
                  DRAWDOWN_CHART_PADDING.top +
                  ((0 - guideValue) / (0 - chartState.drawdownGeometry!.yMin)) *
                    (DRAWDOWN_CHART_HEIGHT - DRAWDOWN_CHART_PADDING.top - DRAWDOWN_CHART_PADDING.bottom)
                const isBottomTick = guideIndex === chartState.drawdownGeometry!.guideValues.length - 1
                return (
                  <g key={`${guideIndex}:${guideValue}`}>
                    {isOverview ? (
                      <line
                        className={`portfolio-nav-grid-line portfolio-nav-grid-line-stub ${
                          isBottomTick ? 'portfolio-nav-grid-line-emphasis' : ''
                        }`}
                        x1="8"
                        x2={getYAxisStubEndX(DRAWDOWN_CHART_PADDING)}
                        y1={y}
                        y2={y}
                      />
                    ) : null}
                    <line
                      className={`portfolio-nav-grid-line ${isBottomTick ? 'portfolio-nav-grid-line-emphasis' : ''}`}
                      x1={DRAWDOWN_CHART_PADDING.left}
                      x2={CHART_WIDTH - DRAWDOWN_CHART_PADDING.right}
                      y1={y}
                      y2={y}
                    />
                    <text
                      className="portfolio-nav-axis-label"
                      x={isOverview ? getYAxisStubEndX(DRAWDOWN_CHART_PADDING) : CHART_WIDTH - 4}
                      y={
                        isOverview
                          ? getYAxisLabelTextY(y, DRAWDOWN_CHART_HEIGHT, isBottomTick ? 'above' : 'below')
                          : y - 4
                      }
                      textAnchor="end"
                    >
                      {formatPercent(guideValue)}
                    </text>
                  </g>
                )
              })}
              <path className="portfolio-nav-drawdown-area" d={chartState.drawdownGeometry!.areaPath} />
              <path className="portfolio-nav-drawdown-line" d={chartState.drawdownGeometry!.linePath} />
              <line
                className="portfolio-nav-guide-line"
                x1={activeCoordinate.x}
                x2={activeCoordinate.x}
                y1={DRAWDOWN_CHART_PADDING.top}
                y2={DRAWDOWN_CHART_HEIGHT - DRAWDOWN_CHART_PADDING.bottom}
              />
              {isOverview && latestDrawdownTag ? (
                <g className="portfolio-nav-value-tag portfolio-nav-value-tag-drawdown">
                  <rect
                    x={latestDrawdownTag.x}
                    y={latestDrawdownTag.y}
                    width={latestDrawdownTag.width}
                    height={latestDrawdownTag.height}
                    rx="0"
                  />
                  <text x={latestDrawdownTag.textX} y={latestDrawdownTag.textY}>
                    {latestDrawdownTagLabel}
                  </text>
                </g>
              ) : null}
            </svg>
          </div>
        </div>
      ) : null}

      {!isOverview ? (
        <div className="portfolio-nav-chart-stats" aria-label="Portfolio NAV window statistics">
          <div>
            <span>Window Return</span>
            <strong className={windowReturnClassName}>{formatPercent(windowReturn)}</strong>
          </div>
          <div>
            <span>Max DD</span>
            <strong className="portfolio-nav-chart-change-negative">{formatPercent(maxDrawdown)}</strong>
          </div>
          <div>
            <span>High</span>
            <strong>{formatSeriesValue(windowHigh, effectiveSeriesMode, currency)}</strong>
          </div>
          <div>
            <span>Low</span>
            <strong>{formatSeriesValue(windowLow, effectiveSeriesMode, currency)}</strong>
          </div>
        </div>
      ) : null}

      <div className="portfolio-nav-chart-footer">
        {isOverview ? (
          <div className="portfolio-nav-period-meta">
            <span>Period</span>
            <strong>
              {firstPoint.date} - {lastPoint.date}
            </strong>
          </div>
        ) : null}
        <div className="portfolio-nav-zoom">
          <span>{firstPoint.date}</span>
          <div className="portfolio-nav-zoom-slider" style={zoomSliderStyle}>
            <input
              type="range"
              min={0}
              max={maxIndex}
              value={clampedWindowStartIndex}
              aria-label={isOverview ? 'Portfolio chart zoom start date' : 'Portfolio NAV zoom start date'}
              onChange={(event) => handleZoomStartChange(Number(event.currentTarget.value))}
            />
            <input
              type="range"
              min={0}
              max={maxIndex}
              value={clampedWindowEndIndex}
              aria-label={isOverview ? 'Portfolio chart zoom end date' : 'Portfolio NAV zoom end date'}
              onChange={(event) => handleZoomEndChange(Number(event.currentTarget.value))}
            />
          </div>
          <span>{lastPoint.date}</span>
        </div>
      </div>
    </section>
  )
}
