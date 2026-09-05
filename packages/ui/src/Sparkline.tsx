export type SparklineDatum = {
  date?: string | null
  value?: number | null
}

type SparklineTrend = 'positive' | 'negative' | 'neutral'

type SparklineProps = {
  values?: SparklineDatum[] | null
  maxPoints?: number
  ariaLabel?: string
  className?: string
}

const SPARKLINE_WIDTH = 96
const SPARKLINE_HEIGHT = 24
const SPARKLINE_TOP_PADDING = 2
const SPARKLINE_BOTTOM_PADDING = 4

function normalizePoints(values: SparklineDatum[] | null | undefined) {
  return (values ?? []).flatMap((point) => {
    if (point.value == null) {
      return []
    }
    const value = Number(point.value)
    return Number.isFinite(value) ? [{ ...point, value }] : []
  })
}

function trendForPoints(points: Array<SparklineDatum & { value: number }>): SparklineTrend {
  if (points.length < 2) {
    return 'neutral'
  }
  const first = points[0]?.value
  const last = points[points.length - 1]?.value
  if (last > first) {
    return 'positive'
  }
  if (last < first) {
    return 'negative'
  }
  return 'neutral'
}

export default function Sparkline({ values, maxPoints, ariaLabel, className }: SparklineProps) {
  const normalized = normalizePoints(values)
  const points =
    maxPoints && maxPoints > 0 && normalized.length > maxPoints
      ? normalized.slice(-maxPoints)
      : normalized

  if (points.length < 2) {
    return <span className="investment-studio-sparkline-empty">--</span>
  }

  const min = Math.min(...points.map((point) => point.value))
  const max = Math.max(...points.map((point) => point.value))
  const span = max - min || 1
  const drawableHeight = SPARKLINE_HEIGHT - SPARKLINE_TOP_PADDING - SPARKLINE_BOTTOM_PADDING
  const line = points
    .map((point, index) => {
      const x = (index / (points.length - 1)) * (SPARKLINE_WIDTH - 1)
      const y = SPARKLINE_TOP_PADDING + (1 - (point.value - min) / span) * drawableHeight
      return `${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' L ')
  const area = `${line} L ${SPARKLINE_WIDTH - 1} ${SPARKLINE_HEIGHT} L 0 ${SPARKLINE_HEIGHT} Z`
  const trend = trendForPoints(points)
  const cellClassName = ['investment-studio-sparkline-cell', className].filter(Boolean).join(' ')

  return (
    <span className={cellClassName} data-trend={trend}>
      <svg
        className="investment-studio-sparkline"
        viewBox={`0 0 ${SPARKLINE_WIDTH} ${SPARKLINE_HEIGHT}`}
        role={ariaLabel ? 'img' : undefined}
        aria-label={ariaLabel}
        aria-hidden={ariaLabel ? undefined : true}
      >
        <path className="investment-studio-sparkline-area" d={`M ${area}`} />
        <path className="investment-studio-sparkline-path" d={`M ${line}`} />
      </svg>
    </span>
  )
}
