import type { ResearchQuantChart, ResearchQuantTable, SavedResearchSource } from '../lib/researchDossierApi'

const colors = ['#2563a6', '#17776b', '#8a5b9f', '#8b6870']
const numeric = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const number = (value: number) => value.toLocaleString('zh-CN', { maximumSignificantDigits: 6 })
const axisNumber = (value: number) => value !== 0 && (Math.abs(value) < 0.0001 || Math.abs(value) >= 1e9)
  ? value.toExponential(2) : number(value)

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
  const upper = chart.kind === 'bar' && dataMax === 0 ? 0 : dataMax + padding
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
      {yTicks.map(value => <g key={value}><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} stroke="#e3e8ef" /><text x={left - 9} y={y(value) + 4} textAnchor="end">{axisNumber(value)}</text></g>)}
      {chart.kind === 'bar' && <line x1={left} x2={width - right} y1={y(0)} y2={y(0)} stroke="#97a6b5" />}
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

export default function ResearchQuantFigure({ source, captioned = false }: { source: SavedResearchSource; captioned?: boolean }) {
  const data = source.data
  const tables = data?.tables || []
  return <div className="research-quant-result">
    {data?.summary && <p translate="no">{data.summary}</p>}
    {data?.metrics && Object.keys(data.metrics).length > 0 && <dl className="research-quant-metrics">{Object.entries(data.metrics).map(([key, value]) => <div key={key}><dt translate="no">{key}</dt><dd translate="no">{value === null ? '—' : numeric(value) ? number(value) : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>}
    {data?.charts?.map(chart => { const table = tables.find(item => item.key === chart.table_key); return table ? <Chart key={chart.key} chart={chart} table={table} showTitle={!captioned || chart.title !== source.title} /> : <p key={chart.key} className="sector-research-note">图表所引用的留存表格不可用。</p> })}
    {tables.map(table => <details className="research-quant-table" key={table.key}><summary>{table.title} · {table.rows.length} 条观测</summary><div className="research-table-scroll"><table><thead><tr>{table.columns.map(column => <th key={column.key}>{column.label}{column.unit && `（${column.unit}）`}</th>)}</tr></thead><tbody>{table.rows.map((row, index) => <tr key={index}>{table.columns.map(column => <td key={column.key}>{row[column.key] === null || row[column.key] === undefined ? '—' : numeric(row[column.key]) ? number(row[column.key] as number) : String(row[column.key])}</td>)}</tr>)}</tbody></table></div></details>)}
  </div>
}
