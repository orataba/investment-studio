import type { ResearchQuantChart, ResearchQuantTable, SavedResearchSource } from '../lib/researchDossierApi'
import ResearchReadingAside from './ResearchReadingAside'

// Display labels are separate from retained computation keys; unknown fields stay in evidence.
const metricLabels: Record<string, string> = {
  correlation_to_csi1000: '与中证1000相关系数', beta_to_csi1000: '中证1000 Beta', down_day_capture: '下跌日捕获率', up_day_capture: '上涨日捕获率',
  nav_cum_return_pct: '产品累计净值收益（%）', index_cum_return_pct: '指数价格收益（%）', nav_max_drawdown_pct: '产品最大回撤（%）', index_max_drawdown_pct: '指数最大回撤（%）', nav_current_drawdown_pct: '产品当前回撤（%）', lag1_autocorr: '一期自相关',
  common_observations: '共同观察日', n_returns: '收益样本数', observations: '观察数',
  correlation: '与基准相关系数', corr: '与基准相关系数', correlation_to_benchmark: '与基准相关系数', beta_ols: 'Beta（OLS）', beta: 'Beta',
  up_capture: '上涨捕获', down_capture: '下跌捕获', upside_capture: '上涨捕获', downside_capture: '下跌捕获',
  up_capture_pct: '上涨捕获（%）', down_capture_pct: '下跌捕获（%）',
  return_pct: '区间收益（%）', annualized_return_pct: '年化收益（%）', max_drawdown_pct: '最大回撤（%）', volatility_pct: '年化波动率（%）',
  sharpe: '夏普比率', sharpe_ratio: '夏普比率', r_squared: '拟合优度 R²', r2: '拟合优度 R²', alpha_ols: 'Alpha（OLS）',
  variance: '方差', missing: '未取得指标', sample_start: '样本开始', sample_end: '样本截止', start_date: '样本开始', end_date: '样本截止',
  breadth_pct: '市场广度（%）', change_pp: '变化（百分点）',
}
function metricLabel(key: string, columnLabel?: string): string | null {
  if (columnLabel && columnLabel !== key) return columnLabel
  if (metricLabels[key]) return metricLabels[key]
  const match = /^(full|recent|benchmark|fund)_(.+)$/.exec(key)
  if (match && metricLabels[match[2]]) return `${({ full: '全样本', recent: '近期样本', benchmark: '基准', fund: '产品' } as Record<string, string>)[match[1]]} · ${metricLabels[match[2]]}`
  return key.includes('_') ? null : key
}

const colors = ['var(--studio-chart-primary)', 'var(--studio-chart-secondary)', 'var(--studio-chart-tertiary)', 'var(--studio-chart-coral)', 'var(--studio-chart-blue)']
const numeric = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const number = (value: number) => value.toLocaleString('zh-CN', { maximumSignificantDigits: 6 })
const axisNumber = (value: number) => value !== 0 && (Math.abs(value) < 0.0001 || Math.abs(value) >= 1e9)
  ? value.toExponential(2) : number(value)

export const isVolatilityFigure = (source: SavedResearchSource) =>
  (typeof source.methodology === 'object' && source.methodology?.metric === 'ewma_volatility') || Boolean(source.data?.current && 'volatility_pct' in source.data.current)

function metricValue(key: string, value: unknown, unit?: string) {
  if (value === null || value === undefined) return '—'
  if (!numeric(value)) return typeof value === 'object' ? JSON.stringify(value) : String(value)
  if (['%', 'percent', 'pct'].includes(unit || '') || /(?:_pct|[（(]%[）)]|%)$/.test(key)) return `${value.toFixed(2)}%`
  if (['pp', 'percentage_points', '个百分点'].includes(unit || '') || /(?:_pp|[（(]个百分点[）)]|百分点)$/.test(key)) return `${value.toFixed(2)} 个百分点`
  return value.toLocaleString('zh-CN', { maximumSignificantDigits: 21 })
}

function Chart({ chart, table, showTitle }: { chart: ResearchQuantChart; table: ResearchQuantTable; showTitle: boolean }) {
  const columns = new Set(table.columns.map(column => column.key))
  if (!columns.has(chart.x_key) || !chart.series.length || chart.series.some(series => !columns.has(series.key))) return <p className="sector-research-note">图表引用的列不在留存数据中。</p>
  const rows = table.rows.filter(row => row[chart.x_key] !== null && row[chart.x_key] !== undefined)
  const values = rows.flatMap(row => chart.series.flatMap(series => numeric(row[series.key]) ? [row[series.key] as number] : []))
  if (!rows.length || !values.length) return <p className="sector-research-note">本次分析没有可绘制的数值。</p>
  const width = 720; const height = 265; const left = 68; const right = 24; const top = 18; const bottom = 48
  const plotWidth = width - left - right; const plotHeight = height - top - bottom
  const rawX = rows.map(row => row[chart.x_key])
  const dateX = rawX.every(value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(value) && Number.isFinite(Date.parse(value)))
  const continuousX = dateX || rawX.every(numeric)
  const xValues = rawX.map((value, index) => dateX ? Date.parse(String(value)) : numeric(value) && continuousX ? value : index)
  const xMin = Math.min(...xValues); const xMax = Math.max(...xValues)
  const x = (index: number) => left + (xMax === xMin ? 0.5 : (xValues[index] - xMin) / (xMax - xMin)) * plotWidth
  const dataMin = Math.min(...values, ...(chart.kind === 'bar' ? [0] : [])); const dataMax = Math.max(...values, ...(chart.kind === 'bar' ? [0] : []))
  const padding = (dataMax - dataMin || Math.abs(dataMax) || 1) * 0.08
  const lower = chart.kind === 'bar' && dataMin === 0 ? 0 : dataMin - padding
  const upper = chart.kind === 'bar' && dataMax === 0 && dataMin < 0 ? 0 : dataMax + padding
  const roughStep = (upper - lower) / 4
  const magnitude = 10 ** Math.floor(Math.log10(roughStep))
  const step = ([1, 2, 2.5, 5, 10].find(value => value >= roughStep / magnitude) || 10) * magnitude
  const yMin = Math.floor(lower / step) * step; const yMax = Math.ceil(upper / step) * step
  const yTicks = Array.from({ length: Math.round((yMax - yMin) / step) + 1 }, (_, index) => Number((yMin + index * step).toPrecision(12)))
  const y = (value: number) => top + (yMax - value) / (yMax - yMin) * plotHeight
  const sorted = rows.map((_, index) => index).sort((a, b) => xValues[a] - xValues[b])
  // A middle observation can sit next to an endpoint on an irregular date axis.
  const xTicks = continuousX ? [...new Set([sorted[0], sorted[rows.length - 1]])] : sorted
  const barX = (index: number) => continuousX ? x(index) : left + (index + 0.5) / rows.length * plotWidth
  const barWidth = Math.min(26, plotWidth / Math.max(1, rows.length) / (chart.series.length + 1))
  return <div className="research-quant-chart">
    {showTitle && <h4>{chart.title}</h4>}
    <div className="research-quant-plot" role="region" aria-label={chart.title} tabIndex={0}><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={chart.title}>
      <title>{chart.title}</title>
      {yTicks.map(value => <g key={value}><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} stroke="var(--studio-chart-grid)" /><text x={left - 9} y={y(value) + 4} textAnchor="end">{axisNumber(value)}</text></g>)}
      {chart.kind === 'bar' && <line x1={left} x2={width - right} y1={y(0)} y2={y(0)} className="research-quant-zero-axis" stroke="var(--studio-chart-axis)" />}
      {chart.series.map((series, seriesIndex) => {
        const color = colors[seriesIndex % colors.length]
        if (chart.kind === 'bar') return <g key={series.key}>{rows.map((row, index) => numeric(row[series.key]) ? <rect key={index} x={barX(index) + (seriesIndex - chart.series.length / 2) * barWidth} y={Math.min(y(0), y(row[series.key] as number))} width={barWidth * 0.85} height={Math.abs(y(row[series.key] as number) - y(0))} fill={color}><title>{String(row[chart.x_key])} · {series.label}: {number(row[series.key] as number)}</title></rect> : null)}</g>
        const segments: number[][] = []; let current: number[] = []
        sorted.forEach(index => { if (numeric(rows[index][series.key])) current.push(index); else if (current.length) { segments.push(current); current = [] } }); if (current.length) segments.push(current)
        return <g key={series.key}>{segments.map((segment, index) => <polyline key={index} points={segment.map(point => `${x(point)},${y(rows[point][series.key] as number)}`).join(' ')} fill="none" stroke={color} strokeWidth="2" />)}{sorted.filter(index => numeric(rows[index][series.key])).map(index => <circle key={index} cx={x(index)} cy={y(rows[index][series.key] as number)} r={2.2} fill={color}><title>{String(rows[index][chart.x_key])} · {series.label}: {number(rows[index][series.key] as number)}</title></circle>)}</g>
      })}
      {xTicks.map(index => <text key={index} x={chart.kind === 'bar' ? barX(index) : x(index)} y={height - 25} textAnchor={chart.kind === 'bar' && !continuousX ? 'middle' : index === sorted[0] ? 'start' : index === sorted[rows.length - 1] ? 'end' : 'middle'}>{String(rows[index][chart.x_key])}</text>)}
    </svg></div>
    <div className="research-chart-legend">{chart.series.map((series, index) => <span key={series.key}><i style={{ background: colors[index % colors.length] }} />{series.label}</span>)}</div>
    {(chart.x_label || chart.y_label) && <p className="sector-research-note">{[chart.x_label, chart.y_label].filter(Boolean).join(' · ')}</p>}
  </div>
}

export default function ResearchQuantFigure({ source, captioned = false, hideSummary = false }: { source: SavedResearchSource; captioned?: boolean; hideSummary?: boolean }) {
  const data = source.data
  const volatility = isVolatilityFigure(source)
  const history: ResearchQuantTable | null = volatility && data?.history?.length ? {
    key: 'ewma-history', title: '波动率历史', columns: [{ key: 'date', label: '观测日期' }, { key: 'volatility_pct', label: '年化波动率', unit: '%' }],
    rows: data.history.map(row => ({ date: row.date, volatility_pct: row.volatility_pct })),
  } : null
  const tables = [...(data?.tables || []), ...(history ? [history] : [])]
  const charts: ResearchQuantChart[] = [...(data?.charts || []), ...(history ? [{ key: 'ewma-history', title: '年化波动率', kind: 'line' as const, table_key: history.key, x_key: 'date', series: [{ key: 'volatility_pct', label: 'EWMA年化波动率' }], y_label: '%' }] : [])]
  const metrics = Object.entries(data?.metrics || {}).map(([key, value]) => {
    const column = tables.flatMap(table => table.columns).find(item => item.key === key)
    return { key, value, label: metricLabel(key, column?.label), unit: column?.unit }
  })
  const dates = metrics.filter(item => /(?:sample_start|sample_end|start_date|end_date)$/.test(item.key))
  const retainedSample = tables.flatMap(table => table.rows).find(row => ['样本区间', '共同样本区间', 'Sample period', 'Common sample'].includes(String(row.metric)))?.value
  const keyMetrics = metrics.filter(item => item.label && !dates.includes(item)).slice(0, 6)
  const displayValue = ({ key, value, unit }: typeof metrics[number]) => data?.analysis_kind === 'watchlist_observations'
    && ['股票Top 10平均相关性（63交易日）', 'Top 10平均相关性（63交易日）'].includes(key) && numeric(value) ? value.toFixed(3) : metricValue(key, value, unit)
  return <div className="research-quant-result">
    {data?.summary && !hideSummary && <p translate="no">{data.summary}</p>}
    {retainedSample != null && <p className="research-quant-sample"><span>样本区间 <span translate="no">{String(retainedSample)}</span></span></p>}
    {hideSummary && data?.summary && !metrics.length && <ResearchReadingAside label="来源计算摘要"><p translate="no">{data.summary}</p></ResearchReadingAside>}
    {dates.length > 0 && <p className="research-quant-sample">{dates.map(item => <span key={item.key}>{item.label || '样本日期'} <time>{String(item.value ?? '—')}</time></span>)}</p>}
    {volatility && <dl className="research-quant-metrics">{[
      { label: '当前年化波动率', value: data?.current?.volatility_pct, unit: '%', date: data?.current?.date },
      { label: '前次年化波动率', value: data?.previous?.volatility_pct, unit: '%', date: data?.previous?.date },
      { label: '较前次变化', value: data?.change_pp, unit: '个百分点' },
      { label: '近5次交易观察变化', value: data?.five_session_change_pp, unit: '个百分点' },
    ].map(item => <div key={item.label}><dt>{item.label}</dt><dd>{numeric(item.value) ? `${item.unit === '个百分点' && item.value > 0 ? '+' : ''}${item.value.toFixed(2)}` : '未取得'}{numeric(item.value) && <span>{item.unit}</span>}{item.date && <small><time dateTime={item.date}>{item.date}</time></small>}</dd></div>)}</dl>}
    {keyMetrics.length > 0 && <dl className="research-quant-metrics">{keyMetrics.map(item => <div key={item.key}><dt>{item.label}</dt><dd translate="no">{displayValue(item)}</dd></div>)}</dl>}
    {metrics.length > 0 && <ResearchReadingAside label="查看全部指标和计算依据">
      {hideSummary && data?.summary && <p translate="no">{data.summary}</p>}
      <div className="research-table-scroll"><table><thead><tr><th>指标</th><th>留存值</th><th>单位</th><th>原始字段</th></tr></thead><tbody>{metrics.map(item => <tr key={item.key}><th>{item.label || '其他计算指标'}</th><td translate="no">{item.value == null ? '—' : typeof item.value === 'object' ? JSON.stringify(item.value) : String(item.value)}</td><td>{item.unit || '以原始记录为准'}</td><td><code translate="no">{item.key}</code></td></tr>)}</tbody></table></div>
      <details><summary>完整计算记录与输入依据</summary><pre className="research-dossier-text" translate="no">{JSON.stringify(source, null, 2)}</pre></details>
    </ResearchReadingAside>}
    {charts.map(chart => { const table = tables.find(item => item.key === chart.table_key); return table ? <Chart key={chart.key} chart={chart} table={table} showTitle={!captioned || chart.title !== source.title} /> : <p key={chart.key} className="sector-research-note">图表所引用的留存表格不可用。</p> })}
    {tables.map(table => <div className="research-quant-table" key={table.key}><ResearchReadingAside label={`${table.title} · ${table.rows.length} 条观测`} title={`${table.title} · 留存数据`}><div className="research-table-scroll"><table><thead><tr>{table.columns.map(column => <th key={column.key}>{column.label}{column.unit && `（${column.unit}）`}</th>)}</tr></thead><tbody>{table.rows.map((row, index) => <tr key={index}>{table.columns.map(column => <td key={column.key}>{row[column.key] === null || row[column.key] === undefined ? '—' : numeric(row[column.key]) ? number(row[column.key] as number) : String(row[column.key])}</td>)}</tr>)}</tbody></table></div></ResearchReadingAside></div>)}
  </div>
}
