import { Fragment, useMemo, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import type { PortfolioResearchBacktestPointRecord as Point, PortfolioResearchBacktestRecord, PortfolioResearchCurrentContextRecord } from '../lib/api'
import { buildResearchComparison, reliableResearchPoints, researchChartSeries, buildResearchReturnMatrix, type ResearchCalendarReturn, type ResearchSeriesSummary } from '../lib/researchComparison'
import { formatNumber, formatPercent } from '../lib/format'
import { MONTH_LABELS } from '../lib/monthlyReturns'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import InfoHint from './InfoHint'
import './research-comparison.css'

const COLORS = ['#16815d', '#2563eb', '#a16a21']
type ChartLine = { label: string; points: Array<{ date: string; value: number }> }

function ComparisonChart({ lines, zh, inception }: { lines: ChartLine[]; zh: boolean; inception?: string | null }) {
  const [mode, setMode] = useState<'growth' | 'drawdown'>('growth')
  const [hoverDate, setHoverDate] = useState<string | null>(null)
  const chartLines = lines.map((line) => {
    let peak = 0
    return { ...line, points: line.points.map((point) => {
      peak = Math.max(peak, point.value)
      return { date: point.date, value: mode === 'drawdown' ? point.value / peak - 1 : point.value }
    }) }
  })
  const all = chartLines.flatMap((line) => line.points)
  if (all.length < 2) return <div className="empty-state">{zh ? '暂无可用收益序列。' : 'No return series available.'}</div>
  const width = 840, height = 320, left = 58, right = 817, top = 20, bottom = 270
  const timestamp = (date: string) => Date.parse(`${date}T00:00:00Z`)
  const dates = [...new Set(all.map((point) => point.date))].sort()
  const minTime = timestamp(dates[0]), maxTime = timestamp(dates[dates.length - 1])
  const minValue = Math.min(...all.map((point) => point.value)), maxValue = Math.max(...all.map((point) => point.value))
  const padding = Math.max((maxValue - minValue) * 0.12, mode === 'drawdown' ? 0.005 : 0.01)
  const yMin = minValue - padding, yMax = mode === 'drawdown' ? 0 : maxValue + padding
  const x = (date: string) => left + (timestamp(date) - minTime) / Math.max(maxTime - minTime, 1) * (right - left)
  const y = (value: number) => bottom - (value - yMin) / (yMax - yMin) * (bottom - top)
  const selectedDate = hoverDate && dates.includes(hoverDate) ? hoverDate : dates[dates.length - 1]
  const labelValue = (value: number) => mode === 'drawdown' ? formatPercent(value) : formatNumber(value, 3)
  return <div className="research-comparison-chart">
    <div className="research-comparison-chart-toolbar">
      <div className="research-comparison-legend">
        {chartLines.map((line, index) => line.points.length ? <span key={line.label}><i style={{ background: COLORS[index] }} />{line.label}</span> : null)}
      </div>
      <div className="research-chart-toggle" role="group" aria-label={zh ? '图表指标' : 'Chart metric'}>
        <button type="button" aria-pressed={mode === 'growth'} onClick={() => setMode('growth')}>{zh ? '收益指数' : 'Return Index'}</button>
        <button type="button" aria-pressed={mode === 'drawdown'} onClick={() => setMode('drawdown')}>{zh ? '回撤' : 'Drawdown'}</button>
      </div>
    </div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0} aria-label={zh ? '真实组合、回测与基准收益曲线' : 'Actual portfolio, backtest and benchmark return curves'}
      onPointerMove={(event) => {
        const rect = event.currentTarget.getBoundingClientRect()
        const cursor = (event.clientX - rect.left) / rect.width * width
        setHoverDate(dates.reduce((nearest, date) => Math.abs(x(date) - cursor) < Math.abs(x(nearest) - cursor) ? date : nearest))
      }} onPointerLeave={() => setHoverDate(null)} onKeyDown={(event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
        event.preventDefault()
        const next = dates.indexOf(selectedDate) + (event.key === 'ArrowRight' ? 1 : -1)
        setHoverDate(dates[Math.max(0, Math.min(dates.length - 1, next))])
      }}>
      {[0, 1, 2, 3, 4].map((tick) => {
        const value = yMin + (yMax - yMin) * tick / 4
        return <g key={tick}><line x1={left} x2={right} y1={y(value)} y2={y(value)} className="research-comparison-gridline" />
          <text x={left - 9} y={y(value) + 4} textAnchor="end">{mode === 'drawdown' ? formatPercent(value, 1) : formatNumber(value, 2)}</text></g>
      })}
      {[0, 1, 2, 3].map((tick) => {
        const date = dates[Math.round((dates.length - 1) * tick / 3)]
        return <text key={tick} x={x(date)} y={bottom + 24} textAnchor={tick === 0 ? 'start' : tick === 3 ? 'end' : 'middle'}>{inception && date < inception ? inception : date}</text>
      })}
      {chartLines.map((line, index) => <path key={line.label} d={line.points.map((point, i) => `${i ? 'L' : 'M'}${x(point.date)},${y(point.value)}`).join(' ')} fill="none" stroke={COLORS[index]} strokeWidth={2} />)}
      <line x1={x(selectedDate)} x2={x(selectedDate)} y1={top} y2={bottom} className="research-comparison-crosshair" />
    </svg>
    <div className="research-comparison-readout">
      <time>{inception && selectedDate < inception ? `${inception} · ${zh ? '期初锚点' : 'Opening anchor'}` : selectedDate}</time>
      {chartLines.map((line, index) => {
        const point = line.points.find((item) => item.date === selectedDate)
        return line.points.length ? <span key={line.label} style={{ color: COLORS[index] }}>{line.label} {point ? labelValue(point.value) : '—'}</span> : null
      })}
    </div>
  </div>
}

type Props = {
  context: PortfolioResearchCurrentContextRecord
  points: Point[]
  endDate?: string | null
  benchmarkPoints: Point[]
  benchmarkLabel?: string | null
  benchmarkLoading: boolean
  benchmarkError: string | null
  currentTargets: boolean
  initialState?: PortfolioResearchBacktestRecord['initial_state']
  selectedScope?: string | null
}

export default function ResearchComparisonPanel({ context, points, endDate, benchmarkPoints, benchmarkLabel, benchmarkLoading, benchmarkError, currentTargets, initialState, selectedScope }: Props) {
  const zh = useLanguage().language === 'zh-Hans'
  const inception = context.portfolio_inception_date
  // Preserve only a declared beginning-of-day anchor before economic inception.
  const trim = (series: Point[]) => series.filter((point) => (!inception || point.date >= inception || point.is_start_anchor && point.date === new Date(Date.parse(`${inception}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10)) && (!endDate || point.date <= endDate))
  const actual = reliableResearchPoints(trim(context.chart_points))
  const backtest = reliableResearchPoints(trim(points))
  const actualAnchors = new Set(context.chart_points.filter((point) => point.is_start_anchor).map((point) => point.date))
  const sharedAnchors = new Set(points.filter((point) => point.is_start_anchor && actualAnchors.has(point.date)).map((point) => point.date))
  // Older benchmark projections omit the marker; retain an existing value at a
  // verified shared BOD anchor without inventing a price or an earlier history.
  const benchmark = reliableResearchPoints(trim(benchmarkPoints.map((point) => sharedAnchors.has(point.date) ? { ...point, is_start_anchor: true } : point)))
  const comparison = buildResearchComparison(actual, backtest, benchmark, context.performance_valuation_basis !== 'operational_carrying_basis')
  const chart = researchChartSeries(actual, backtest, benchmark)
  const matrix = buildResearchReturnMatrix(actual, backtest, benchmark)
  const matrixSeries = [
    { key: 'actual' as const, label: zh ? '真实组合' : 'Actual' },
    { key: 'backtest' as const, label: zh ? '回测' : 'Backtest' },
    { key: 'difference' as const, label: zh ? '差值' : 'Difference' },
    ...(benchmarkPoints.length ? [{ key: 'benchmark' as const, label: benchmarkLabel ?? (zh ? '基准' : 'Benchmark') }] : []),
  ]
  const matrixCell = (value: ResearchCalendarReturn | null, annual = false) => <td className={[
    annual ? 'research-matrix-annual' : '',
    value && value.value > 0 ? 'positive-cell' : value && value.value < 0 ? 'negative-cell' : '',
  ].filter(Boolean).join(' ')}>
    {formatPercent(value?.value)}
    {value?.partial ? <InfoHint label={zh ? '区间未满' : 'Partial period'} detail={`${value.startDate} – ${value.endDate}`} /> : null}
  </td>
  const metricRows = useMemo<Array<{ key: keyof ResearchSeriesSummary; label: string; kind?: 'number' | 'days' | 'date' }>>(() => [
    { key: 'periodReturn', label: zh ? '区间收益' : 'Period Return' },
    { key: 'annualizedReturn', label: zh ? '年化收益' : 'Annual Return' },
    { key: 'annualizedVolatility', label: zh ? '年化波动率' : 'Annualized Volatility' },
    { key: 'sharpe', label: 'Sharpe', kind: 'number' },
    { key: 'sortino', label: 'Sortino', kind: 'number' },
    { key: 'calmar', label: 'Calmar', kind: 'number' },
    { key: 'maxDrawdown', label: zh ? '最大回撤' : 'Max Drawdown' },
    { key: 'currentDrawdown', label: zh ? '当前回撤' : 'Current Drawdown' },
    { key: 'maxDrawdownStart', label: zh ? '回撤起点' : 'Drawdown Peak', kind: 'date' },
    { key: 'maxDrawdownEnd', label: zh ? '回撤谷底' : 'Drawdown Trough', kind: 'date' },
    { key: 'maxDrawdownDays', label: zh ? '回撤天数' : 'Drawdown Days', kind: 'days' },
    { key: 'recoveryDays', label: zh ? '谷底至修复天数' : 'Recovery Days', kind: 'days' },
  ], [zh])
  const fmt = (value: number | string | null | undefined, kind?: string) => value == null ? '—' : typeof value === 'string' ? value : kind === 'number' ? formatNumber(value, 2) : kind === 'days' ? formatNumber(value, 0) : formatPercent(value)
  const benchmarkVisible = benchmarkPoints.length > 0
  const lateBacktest = inception && backtest[0] && backtest[0].date > inception
  const actualEnd = actual[actual.length - 1]?.date
  const lateActual = inception && actual[0] && actual[0].date > inception
  const shortActual = endDate && actualEnd && actualEnd < endDate
  return <section className="panel research-performance-panel">
    <div className="panel-header">
      <div className="research-comparison-heading"><h2 className="panel-title">{zh ? '真实组合与回测' : 'Actual vs Backtest'}</h2>
        <InfoHint label={zh ? '收益口径' : 'Return basis'} detail={[
          zh ? '真实组合采用业绩页的时间加权收益，剔除入金、出金影响。' : 'Actual uses canonical time-weighted returns, excluding external deposits and withdrawals.',
          currentTargets ? (zh ? '回测将本次保存的目标配置应用于历史，不代表当时已知这些目标与标的。' : 'The backtest applies this run’s saved current targets to history; it does not imply those targets or assets were known then.') : (zh ? '此回测保留原运行的方法与数据口径。' : 'This saved backtest retains its original methodology.'),
          initialState ? (zh ? '回测以组合成立日收盘的真实持仓与现金为期初状态，此后才模拟收益与目标调仓；数据不足只推迟首次调仓。初始持仓持续估值，不改成现金，也不复刻之后的实际证券交易与申赎。' : 'Simulation starts from actual inception end-of-day holdings and cash, then simulates returns and target rebalances. Insufficient risk data delays the first rebalance. Initial holdings retain their returns; later actual security trades and external flows are not replayed.') : (zh ? '旧回测保留保存时的起点与初始状态；重新运行才使用真实期初持仓。' : 'Saved backtests retain their original start and initial state. Rerun to use actual inception holdings.'),
          zh ? '真实曲线从首个可靠锚点计为1；回测、基准在首次共同收盘与真实曲线对齐，不填补缺失历史。' : 'Actual starts at 1 at its first reliable anchor. Backtest and benchmark align to actual at their first common close, without filling missing history.',
          zh ? '图中回撤基于各自完整可用历史；右侧指标仅在标示的共同区间内重新计算。' : 'Chart drawdowns use each full available history; the metric table recalculates over its labeled common window.',
          zh ? 'FCN与期权在回测中按不交易、零收益账面资金处理，不模拟票息或期权损益。' : 'FCN and options are no-trade, zero-return capital in the backtest; coupons and option payoffs are not simulated.',
        ]} />
      </div>
      <span className="portfolio-detail-meta">{inception ? `${zh ? '成立' : 'Inception'} ${inception}` : ''}{endDate ? ` · ${zh ? '截至' : 'Through'} ${endDate}` : ''}</span>
    </div>
    {initialState?.status === 'unavailable' ? <div className="inline-notice inline-notice-warning">{zh ? '真实期初持仓无法可靠估值，回测暂不可用。' : 'Actual inception holdings cannot be reliably valued; the backtest is unavailable.'}<InfoHint label={zh ? '期初状态' : 'Initial state'} detail={initialState.unavailable_reason ?? (zh ? '需要补齐成立日的持仓、现金及估值数据。' : 'Inception holdings, cash and valuation data are required.')} /></div> : null}
    {selectedScope ? <div className="inline-notice inline-notice-warning">{zh ? `回测仅覆盖“${selectedScope}”，实际收益覆盖全组合；差值不代表同一组合的配置效果。` : `Backtest covers “${selectedScope}”; actual returns cover the full portfolio. Differences are not like-for-like allocation effects.`}</div> : null}
    {lateBacktest ? <div className="inline-notice inline-notice-warning">{zh ? `可用回测从 ${backtest[0].date} 开始；成立日至此前没有模拟结果。` : `Available backtest starts ${backtest[0].date}; the earlier inception period has no simulation.`}</div> : null}
    {lateActual || shortActual ? <div className="inline-notice inline-notice-warning">{zh ? `实际收益有效区间：${context.performance_start_date ?? actual[0]?.date} 至 ${actualEnd}；其余日期不补算。` : `Available actual returns: ${context.performance_start_date ?? actual[0]?.date} to ${actualEnd}; missing dates are not filled.`}</div> : null}
    {context.performance_coverage_state && context.performance_coverage_state !== 'complete' ? <div className="inline-notice inline-notice-warning">{zh ? '实际收益历史不完整，仅显示连续可靠区间。' : 'Actual return coverage is incomplete; only the continuous reliable window is shown.'}</div> : null}
    {context.performance_valuation_basis === 'operational_carrying_basis' ? <div className="inline-notice inline-notice-warning">{zh ? '实际收益含账面估值，不能视作完整公允价值业绩。' : 'Actual returns include carrying valuations and are not complete fair-value performance.'}</div> : null}
    <div className="research-performance-main">
      <div className="research-performance-chart-block">
        <ComparisonChart zh={zh} inception={inception} lines={[
          { label: zh ? '真实组合' : 'Actual', points: chart.actualPoints },
          { label: zh ? '回测' : 'Backtest', points: chart.backtestPoints },
          { label: benchmarkLabel ?? (zh ? '基准' : 'Benchmark'), points: chart.benchmarkPoints },
        ]} />
        {benchmarkLoading ? <div className="research-comparison-status" role="status">{zh ? '正在更新基准…' : 'Updating benchmark...'}</div> : null}
        {benchmarkError ? <div className="research-comparison-status research-comparison-status-error" role="alert">{benchmarkError}</div> : null}
      </div>
      <div className="portfolio-section-block research-metrics-panel">
        <div className="research-comparison-heading"><h3 className="panel-title">{zh ? '同区间指标' : 'Comparable Metrics'}</h3>
          <InfoHint label={zh ? '指标口径' : 'Metric basis'} detail={zh
            ? '指标仅使用两条收益路径的共同收盘日期，差值为真实减回测。满一个周年才显示年化收益与Calmar；风险年化按实际观测日期频率，Sharpe无风险收益、Sortino最低可接受收益均为0。修复天数从最大回撤谷底起算，未修复显示空。基准须覆盖整个共同区间。'
            : 'Metrics use the same close dates; differences are actual minus backtest. Annual return and Calmar require a full calendar anniversary. Risk annualization uses observed date frequency; Sharpe risk-free rate and Sortino minimum return are zero. Recovery days start at the drawdown trough; unrecovered drawdowns remain blank. Benchmark must cover the entire common window.'} />
        </div>
        {comparison ? <HorizontalTableScroll className="table-shell"><table className="performance-summary-table research-metric-table research-actual-comparison-table">
          <thead><tr><th>{zh ? '指标' : 'Metric'}</th><th>{zh ? '真实' : 'Actual'}</th><th>{zh ? '回测' : 'Backtest'}</th><th>{zh ? '差值' : 'Difference'}</th>{benchmarkVisible ? <th>{zh ? '基准' : 'Benchmark'}</th> : null}</tr></thead>
          <tbody><tr className="research-metric-period-row"><th>{zh ? '共同区间' : 'Common Window'}</th><td colSpan={benchmarkVisible ? 4 : 3}>{inception && comparison.startDate < inception ? inception : comparison.startDate} – {comparison.endDate}</td></tr>
            {metricRows.map((row) => {
              const a = comparison.actual[row.key], b = comparison.backtest[row.key]
              const diff = typeof a === 'number' && typeof b === 'number' ? a - b : null
              return <tr key={row.key}><th>{row.label}</th><td>{fmt(a, row.kind)}</td><td>{fmt(b, row.kind)}</td><td>{row.kind === 'date' ? '—' : fmt(diff, row.kind)}</td>{benchmarkVisible ? <td>{fmt(comparison.benchmark?.[row.key], row.kind)}</td> : null}</tr>
            })}
          </tbody></table></HorizontalTableScroll> : <div className="empty-state">{zh ? '暂无两条路径共同的有效收益区间。' : 'No common eligible return window.'}</div>}
        {comparison && benchmarkVisible && !comparison.benchmark ? <div className="research-comparison-status">{zh ? '基准未覆盖完整共同区间，比较指标留空。' : 'Benchmark does not cover the full common window; comparison metrics are unavailable.'}</div> : null}
      </div>
    </div>
    <div className="research-monthly-section">
      <div className="panel-header panel-header-inline"><div className="research-comparison-heading"><h3 className="panel-title">{zh ? '月度收益矩阵' : 'Monthly Return Matrix'}</h3>
        <InfoHint label={zh ? '月度与年度口径' : 'Monthly and annual basis'} detail={[
          zh ? '每年按真实组合、回测及差值排列；各自保留全部可用历史。差值为真实减回测的百分点，仅在起止日期相同时显示。' : 'Each year shows actual, backtest and difference, retaining each series’ available history. Differences are actual minus backtest in percentage points and require matching period boundaries.',
          zh ? '年度列为该年累计收益（当年截至当前日期），按收益指数复合计算，不是月收益相加或年化收益。' : 'The annual column is the cumulative calendar-year return (year to date for the current year), compounded from the return index; it is neither a sum of monthly returns nor an annualized return.',
          zh ? '成立不足整期或缺少完整期末观察时，用圆圈提示标明实际区间；没有月末边界的跨月收益不分配到某一个月，缺失月份留空。' : 'Partial inception or end periods show their observed dates in the info hint. A return spanning a missing month-end is not assigned to one month; unavailable months remain blank.',
        ]} />
      </div></div>
      <HorizontalTableScroll className="table-shell research-monthly-scroll"><table className="transactions-table research-monthly-table" aria-label={zh ? '月度收益矩阵' : 'Monthly return matrix'}>
        <colgroup><col className="research-matrix-year-col" /><col className="research-matrix-series-col" />{MONTH_LABELS.map((month) => <col key={month} />)}<col className="research-matrix-annual-col" /></colgroup>
        <thead><tr><th>{zh ? '年度' : 'Year'}</th><th>{zh ? '序列' : 'Series'}</th>{MONTH_LABELS.map((month, index) => <th key={month}>{zh ? `${index + 1}月` : month}</th>)}<th>{zh ? '年度收益' : 'Year / YTD'}</th></tr></thead>
        {matrix.length ? matrix.map((year) => <tbody key={year.year} className="research-matrix-year">
          {matrixSeries.map((series, index) => <tr key={series.key} className={series.key === 'difference' ? 'research-matrix-difference' : undefined}>
            {index === 0 ? <th scope="rowgroup" rowSpan={matrixSeries.length}>{year.year}</th> : null}<th scope="row">{series.label}</th>
            {year.months.map((month, index) => <Fragment key={index}>{matrixCell(month[series.key])}</Fragment>)}
            {matrixCell(year.annual[series.key], true)}
          </tr>)}
        </tbody>) : <tbody><tr><td colSpan={15} className="empty-state-cell">{zh ? '暂无有效收益区间。' : 'No eligible return periods.'}</td></tr></tbody>}
      </table></HorizontalTableScroll>
    </div>
  </section>
}
