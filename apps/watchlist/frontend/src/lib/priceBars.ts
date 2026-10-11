import type { InstrumentPriceBar, InstrumentPriceBarsResponse } from './api'

export type PriceAdjustmentMode = 'front' | 'back' | 'raw'
export type PriceRange = '1M' | '3M' | '6M' | '1Y' | 'ALL'

export type DisplayPriceBar = {
  segment_id?: string | null
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
  anchors?: InstrumentPriceBarsResponse['adjustment_anchors'],
): DisplayPriceBar[] {
  const parsed = sourceBars
    .map(parseBar)
    .filter((bar): bar is DisplayPriceBar => bar !== null)
    .sort((left, right) => left.date.localeCompare(right.date))
  if (mode === 'raw') {
    return parsed
  }

  const anchor = anchors?.[mode]
  const anchorFactor = Number(anchor?.factor)
  if (!anchor || !Number.isFinite(anchorFactor) || anchorFactor <= 0) return []
  const segmentBars = parsed.filter(bar => bar.date >= anchor.start_date && bar.date <= anchor.end_date)
  if (segmentBars.some(bar => bar.adjustmentFactor === null || bar.adjustmentFactor <= 0)) return []

  return segmentBars.map((bar, index) => {
    const scale = (bar.adjustmentFactor as number) / anchorFactor
    const previous = segmentBars[index - 1]
    return {
      ...bar,
      open: bar.open * scale,
      high: bar.high * scale,
      low: bar.low * scale,
      close: bar.close * scale,
      previousClose:
        previous ? previous.close * (previous.adjustmentFactor as number) / anchorFactor : null,
    }
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
