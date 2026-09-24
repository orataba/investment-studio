import { useId, useMemo, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import type { PortfolioResearchBacktestSleevePointRecord as SleevePoint } from '../lib/api'
import { formatPercent } from '../lib/format'
import { buildSleeveChartSeries, contiguousChartSegments, divergingSleeveStacks, sleeveChartPoints, type SleeveChartSeries, type SleeveStackPoint } from '../lib/researchSleeveCharts'
import InfoHint from './InfoHint'
import './research-sleeve-charts.css'

export type ResearchSleeveChartsProps = {
  weightPoints: SleevePoint[]
  contributionPoints: SleevePoint[]
}

const WIDTH = 720, HEIGHT = 280, LEFT = 62, RIGHT = 704, TOP = 14, BOTTOM = 242

function percentageAxis(min: number, max: number, fallback: number) {
  const range = max - min || fallback
  const magnitude = 10 ** Math.floor(Math.log10(range / 5))
  const step = ([1, 2, 2.5, 5, 10].find((factor) => factor * magnitude >= range / 5) ?? 10) * magnitude
  const lower = Math.floor(min / step) * step
  const upper = Math.ceil((max === min ? min + fallback : max) / step) * step
  const count = Math.round((upper - lower) / step)
  return { min: lower, max: upper, ticks: Array.from({ length: count + 1 }, (_, index) => lower + step * index) }
}

function sleeveLabel(series: SleeveChartSeries, zh: boolean) {
  if (!zh) return series.label
  if (series.key === '__cash__') return '现金'
  if (series.key === '__derivatives__') return '衍生品'
  if (series.key === '__execution_costs__') return '执行费用'
  return series.label
}

function SleeveChart({ points, series, kind, domain, zh }: {
  points: SleevePoint[]
  series: SleeveChartSeries[]
  kind: 'weight' | 'contribution'
  domain: { min: number; max: number } | null
  zh: boolean
}) {
  const readoutId = useId()
  const [activeDate, setActiveDate] = useState<string | null>(null)
  const ownSeries = useMemo(() => {
    const keys = new Set(points.flatMap((point) => point.sleeves.map((sleeve) => sleeve.top_sleeve_id ?? sleeve.top_sleeve_label)))
    return series.filter((item) => keys.has(item.key))
  }, [points, series])
  const normalized = useMemo(() => sleeveChartPoints(points, ownSeries), [points, ownSeries])
  const stacked = useMemo(() => divergingSleeveStacks(normalized, ownSeries), [normalized, ownSeries])
  const heading = kind === 'weight' ? (zh ? '一级分类权重' : 'Top-Level Weights') : (zh ? '一级分类收益贡献' : 'Top-Level Return Contributions')
  const label = `${heading} · ${zh ? '回测' : 'Backtest'}`
  const hint = kind === 'weight'
    ? (zh ? '回测各日收盘账面金额占当日模拟净资产的比例。正权重与负权重分别堆积在零轴两侧，保留负现金和其他负账面资金。' : 'Backtest end-of-day carrying amounts divided by that day’s simulated NAV. Positive and negative weights stack on opposite sides of zero, retaining negative cash and other signed capital.')
    : (zh ? '回测起点至各日的累计收益贡献，以期初模拟净资产为分母，包括证券损益、现金收益及执行费用。不代表当日收益或风险贡献；各分类贡献相加对应回测累计收益。' : 'Cumulative return contributions from the backtest start, divided by initial simulated NAV, including security P&L, cash income and execution costs. These are neither daily returns nor risk contributions; their sum reconciles to the backtest cumulative return.')
  const hasValues = normalized.some((point) => [...point.values.values()].some((value) => value != null))
  const headingId = useId()
  const selected = normalized.find((point) => point.date === activeDate) ?? normalized[normalized.length - 1]
  let min = 0, max = 0
  if (kind === 'weight') ({ min, max } = stacked)
  else for (const point of normalized) for (const value of point.values.values()) if (value != null) {
    min = Math.min(min, value)
    max = Math.max(max, value)
  }
  const axis = percentageAxis(min, max, kind === 'weight' ? 1 : 0.01)
  const x = (time: number) => domain && domain.max !== domain.min ? LEFT + (time - domain.min) / (domain.max - domain.min) * (RIGHT - LEFT) : (LEFT + RIGHT) / 2
  const y = (value: number) => BOTTOM - (value - axis.min) / (axis.max - axis.min) * (BOTTOM - TOP)
  const areaPath = (segment: SleeveStackPoint[]) => [
    ...segment.map((point, index) => `${index ? 'L' : 'M'}${x(point.time)},${y(point.upper)}`),
    ...[...segment].reverse().map((point) => `L${x(point.time)},${y(point.lower)}`), 'Z',
  ].join(' ')
  const tickTimes = domain ? domain.min === domain.max ? [domain.min] : Array.from({ length: 4 }, (_, index) => domain.min + (domain.max - domain.min) * index / 3) : []

  return <section className="research-sleeve-chart" aria-labelledby={headingId}>
    <div className="research-sleeve-heading">
      <h3 className="panel-title" id={headingId}>{heading}</h3>
      <InfoHint label={zh ? '回测口径' : 'Backtest basis'} detail={hint} />
      <span className="research-sleeve-backtest-label">{zh ? '回测' : 'Backtest'}</span>
    </div>
    {hasValues && domain && selected ? <>
      <div className="research-sleeve-legend" aria-label={zh ? '图例' : 'Legend'}>
        {ownSeries.map((item) => <span key={item.key} data-sleeve-key={item.key}><i style={{ backgroundColor: item.color }} />{sleeveLabel(item, zh)}</span>)}
      </div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" tabIndex={0} aria-label={label} aria-describedby={readoutId}
        onPointerMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect()
          if (!rect.width) return
          const cursor = (event.clientX - rect.left) / rect.width * WIDTH
          const nearest = normalized.reduce((previous, point) => Math.abs(x(point.time) - cursor) < Math.abs(x(previous.time) - cursor) ? point : previous)
          setActiveDate(nearest.date)
        }} onPointerLeave={() => setActiveDate(null)} onBlur={() => setActiveDate(null)} onKeyDown={(event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          const index = normalized.indexOf(selected)
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? normalized.length - 1 : index + (event.key === 'ArrowRight' ? 1 : -1)
          setActiveDate(normalized[Math.max(0, Math.min(normalized.length - 1, next))].date)
        }}>
        {axis.ticks.map((value, index) => <g key={index}>
          <line x1={LEFT} x2={RIGHT} y1={y(value)} y2={y(value)} className={`research-sleeve-gridline${Math.abs(value) < 1e-12 ? ' research-sleeve-zero-line' : ''}`} />
          <text x={LEFT - 9} y={y(value) + 4} textAnchor="end">{formatPercent(value, 1)}</text>
        </g>)}
        {kind === 'weight' ? stacked.stacks.map((item) => <g key={item.key} data-sleeve-key={item.key}>
          {(['positive', 'negative'] as const).map((sign) => contiguousChartSegments(item[sign]).filter((segment) => segment.some((point) => point.upper !== point.lower)).map((segment, index) => <path key={`${sign}-${index}`} data-stack-sign={sign} d={areaPath(segment)} fill={item.color} fillOpacity={0.65} />))}
        </g>) : ownSeries.map((item) => <g key={item.key} data-sleeve-key={item.key}>
          {contiguousChartSegments(normalized.map((point) => {
            const value = point.values.get(item.key)
            return value == null ? null : { ...point, value }
          })).map((segment, index) => <path key={index} d={segment.map((point, position) => `${position ? 'L' : 'M'}${x(point.time)},${y(point.value)}`).join(' ')} fill="none" stroke={item.color} strokeWidth={1.8} />)}
        </g>)}
        <line x1={LEFT} x2={RIGHT} y1={y(0)} y2={y(0)} className="research-sleeve-zero-line" />
        {tickTimes.map((time, index) => <text key={time} x={x(time)} y={BOTTOM + 25} textAnchor={index === 0 ? 'start' : index === tickTimes.length - 1 ? 'end' : 'middle'}>{new Date(time).toISOString().slice(0, 10)}</text>)}
        <line x1={x(selected.time)} x2={x(selected.time)} y1={TOP} y2={BOTTOM} className="research-sleeve-crosshair" />
        {kind === 'contribution' ? ownSeries.map((item) => {
          const value = selected.values.get(item.key)
          return value == null ? null : <circle key={item.key} cx={x(selected.time)} cy={y(value)} r={3} fill={item.color} stroke="white" strokeWidth={1} />
        }) : null}
      </svg>
      <div className="research-sleeve-readout" id={readoutId} aria-live="polite">
        <time dateTime={selected.date}>{selected.date}</time>
        {ownSeries.map((item) => <span key={item.key} style={{ color: item.color }}>{sleeveLabel(item, zh)} {formatPercent(selected.values.get(item.key))}</span>)}
      </div>
    </> : <div className="empty-state">{zh ? '暂无图表数据。' : 'No chart data.'}</div>}
  </section>
}

export default function ResearchSleeveCharts({ weightPoints, contributionPoints }: ResearchSleeveChartsProps) {
  const zh = useLanguage().language === 'zh-Hans'
  const series = useMemo(() => buildSleeveChartSeries(weightPoints, contributionPoints), [weightPoints, contributionPoints])
  const domain = useMemo(() => {
    const times = [...weightPoints, ...contributionPoints].map((point) => Date.parse(`${point.date}T00:00:00Z`)).filter(Number.isFinite)
    return times.length ? times.reduce((range, time) => ({ min: Math.min(range.min, time), max: Math.max(range.max, time) }), { min: times[0], max: times[0] }) : null
  }, [weightPoints, contributionPoints])
  return <div className="research-sleeve-charts">
    <SleeveChart kind="weight" points={weightPoints} series={series} domain={domain} zh={zh} />
    <SleeveChart kind="contribution" points={contributionPoints} series={series} domain={domain} zh={zh} />
  </div>
}
