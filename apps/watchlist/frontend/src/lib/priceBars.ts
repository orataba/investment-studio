import type { InstrumentPriceBar } from './api'

export type PriceAdjustmentMode = 'qfq' | 'raw'
export type PriceRange = '1M' | '3M' | '6M' | '1Y' | 'ALL'

export type DisplayPriceBar = {
  date: string
  open: number
  high: number
  low: number
  close: number
  previousClose: number | null
  volume: number | null
  turnover: number | null
  adjustmentFactor: number | null
  currency: string
  volumeUnit: string | null
  turnoverUnit: string | null
  provider: string
  status: 'complete' | 'partial'
}

const RANGE_MONTHS: Record<Exclude<PriceRange, 'ALL'>, number> = {
  '1M': 1,
  '3M': 3,
  '6M': 6,
  '1Y': 12,
}

function shiftIsoMonth(isoDate: string, months: number): string {
  const source = new Date(`${isoDate}T00:00:00Z`)
  if (Number.isNaN(source.getTime())) return isoDate
  const day = source.getUTCDate()
  source.setUTCDate(1)
  source.setUTCMonth(source.getUTCMonth() - months)
  const lastDay = new Date(
    Date.UTC(source.getUTCFullYear(), source.getUTCMonth() + 1, 0),
  ).getUTCDate()
  source.setUTCDate(Math.min(day, lastDay))
  return source.toISOString().slice(0, 10)
}

function finiteNumber(value: string | null): number | null {
  if (value === null || value.trim() === '') {
    return null
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function parseBar(bar: InstrumentPriceBar): DisplayPriceBar | null {
  const open = finiteNumber(bar.open)
  const high = finiteNumber(bar.high)
  const low = finiteNumber(bar.low)
  const close = finiteNumber(bar.close)
  if (
    open === null ||
    high === null ||
    low === null ||
    close === null ||
    open <= 0 ||
    high < Math.max(open, close) ||
    low > Math.min(open, close)
  ) {
    return null
  }
  return {
    date: bar.date,
    open,
    high,
    low,
    close,
    previousClose: finiteNumber(bar.previous_close),
    volume: finiteNumber(bar.volume),
    turnover: finiteNumber(bar.turnover),
    adjustmentFactor: finiteNumber(bar.adjustment_factor),
    currency: bar.currency,
    volumeUnit: bar.volume_unit,
    turnoverUnit: bar.turnover_unit,
    provider: bar.provider,
    status: bar.status,
  }
}

export function adjustPriceBars(
  sourceBars: InstrumentPriceBar[],
  mode: PriceAdjustmentMode,
): DisplayPriceBar[] {
  const parsed = sourceBars
    .map(parseBar)
    .filter((bar): bar is DisplayPriceBar => bar !== null)
    .sort((left, right) => left.date.localeCompare(right.date))
  if (mode === 'raw') {
    return parsed
  }

  const latestFactor = [...parsed]
    .reverse()
    .find((bar) => bar.adjustmentFactor !== null && bar.adjustmentFactor > 0)
    ?.adjustmentFactor
  if (!latestFactor) {
    return parsed
  }

  return parsed.flatMap((bar) => {
    if (bar.adjustmentFactor === null || bar.adjustmentFactor <= 0) {
      return []
    }
    const scale = bar.adjustmentFactor / latestFactor
    return [
      {
        ...bar,
        open: bar.open * scale,
        high: bar.high * scale,
        low: bar.low * scale,
        close: bar.close * scale,
        previousClose:
          bar.previousClose === null ? null : bar.previousClose * scale,
      },
    ]
  })
}

export function slicePriceBars(
  bars: DisplayPriceBar[],
  range: PriceRange,
): DisplayPriceBar[] {
  if (range === 'ALL') {
    return bars
  }
  const latestDate = bars[bars.length - 1]?.date
  if (!latestDate) return []
  const cutoff = shiftIsoMonth(latestDate, RANGE_MONTHS[range])
  return bars.filter((bar) => bar.date >= cutoff)
}

function calendarReturn(bars: DisplayPriceBar[], months: number): number | null {
  if (bars.length < 2) {
    return null
  }
  const targetDate = shiftIsoMonth(bars[bars.length - 1].date, months)
  const anchor = [...bars].reverse().find((bar) => bar.date <= targetDate)?.close
  const latest = bars[bars.length - 1]?.close
  return anchor && latest ? (latest / anchor - 1) * 100 : null
}

export function priceReturnStats(bars: DisplayPriceBar[]) {
  const latest = bars[bars.length - 1]
  const previous = bars[bars.length - 2]
  const latestYear = latest?.date.slice(0, 4)
  const firstCurrentYearIndex = latestYear
    ? bars.findIndex((bar) => bar.date.startsWith(latestYear))
    : -1
  const ytdAnchorIndex =
    firstCurrentYearIndex > 0 ? firstCurrentYearIndex - 1 : -1
  const ytdAnchor = ytdAnchorIndex >= 0 ? bars[ytdAnchorIndex]?.close : null
  const ytd = latest && ytdAnchor ? (latest.close / ytdAnchor - 1) * 100 : null
  const dailyChange =
    latest && previous ? (latest.close / previous.close - 1) * 100 : null

  return {
    dailyChange,
    oneMonth: calendarReturn(bars, 1),
    threeMonth: calendarReturn(bars, 3),
    sixMonth: calendarReturn(bars, 6),
    oneYear: calendarReturn(bars, 12),
    ytd,
    sinceStart:
      bars.length > 1
        ? (bars[bars.length - 1].close / bars[0].close - 1) * 100
        : null,
  }
}

export function priceRiskStats(bars: DisplayPriceBar[]) {
  const returns: number[] = []
  let peak = 0
  let maximumDrawdown = 0
  for (let index = 0; index < bars.length; index += 1) {
    const close = bars[index].close
    peak = Math.max(peak, close)
    maximumDrawdown = Math.min(maximumDrawdown, close / peak - 1)
    if (index > 0) {
      returns.push(close / bars[index - 1].close - 1)
    }
  }
  const mean = returns.length
    ? returns.reduce((sum, value) => sum + value, 0) / returns.length
    : null
  const variance =
    mean !== null && returns.length > 1
      ? returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) /
        (returns.length - 1)
      : null
  const latest = bars[bars.length - 1]?.close ?? null
  const currentPeak = bars.reduce((value, bar) => Math.max(value, bar.close), 0)
  return {
    annualizedVolatility: variance === null ? null : Math.sqrt(variance * 252) * 100,
    maximumDrawdown: maximumDrawdown * 100,
    currentDrawdown:
      latest === null || currentPeak <= 0 ? null : (latest / currentPeak - 1) * 100,
    observationCount: bars.length,
  }
}
