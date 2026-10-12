import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import type { MonthlyReturnMatrixRow } from '../lib/calendarReturns'
import { formatPercent } from '../lib/format'

const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function heat(value: number | null, max: number) {
  if (value == null) return undefined
  const positive = value >= 0
  const intensity = 0.08 + Math.min(Math.abs(value) / Math.max(max, 1), 1) * (positive ? 0.26 : 0.24)
  return { backgroundColor: `color-mix(in srgb, var(--studio-${positive ? 'positive' : 'negative'}) ${intensity * 100}%, white)`,
    color: `var(--studio-${positive ? 'positive' : 'negative'}-strong)` }
}

/** Display only: periods, values and coverage remain the canonical caller's. */
export default function MonthlyReturnMatrix({ rows, today = new Date().toISOString().slice(0, 10) }: {
  rows: MonthlyReturnMatrixRow[]; today?: string
}) {
  const zh = useLanguage().language === 'zh-Hans'
  const currentMonth = today.slice(0, 7)
  const maximum = Math.max(0, ...rows.flatMap(row => [...row.months, row.ytd]).filter((value): value is number => value != null).map(Math.abs))
  return <HorizontalTableScroll className="table-shell instrument-performance-table-shell">
    <table className="terminal-table terminal-table-compact instrument-heatmap-table">
      <caption>{zh ? '月度收益（%）。MTD 为当前未结束月份，截至单元格所示日期；— 表示不可用，未来月份尚未到期。' : 'Monthly returns (%). MTD is the unfinished current month, through the date shown in its cell. — is unavailable; future months have not begun.'}</caption>
      <thead><tr><th scope="col">{zh ? '年度' : 'Year'}</th>{months.map((month, index) => <th scope="col" key={month}>{zh ? `${index + 1}月` : month}</th>)}<th scope="col">{zh ? '全年 / 年内' : 'Yearly / YTD'}</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.year}>
        <th scope="row" className="instrument-heatmap-row-label">{row.year}</th>
        {row.months.map((value, index) => {
          const bucket = `${row.year}-${String(index + 1).padStart(2, '0')}`
          const window = row.monthWindows[index]
          const current = bucket === currentMonth
          const future = bucket > currentMonth
          const reason = future ? (zh ? '尚未到期' : 'Not yet due') : value == null ? (zh ? '收益不可用：缺少可比或完整观察' : 'Return unavailable: comparable or complete observations are missing') : ''
          const clock = window ? `${window.anchorDate} → ${window.endDate}` : ''
          return <td key={bucket} className={`instrument-heatmap-cell${value == null ? ' instrument-heatmap-cell-empty' : ''}`} style={heat(value, maximum)}
            title={[clock, reason].filter(Boolean).join(' · ')} aria-label={`${bucket}: ${value == null ? reason : formatPercent(value, 2)}${current ? ' MTD' : ''}${clock ? ` · ${clock}` : ''}`}>
            {value == null ? '—' : formatPercent(value, 2)}
            {current && <small className="instrument-month-to-date">MTD{window && <> · {zh ? '截至' : 'as of'} {window.endDate}</>}</small>}
            {future && <small className="instrument-month-to-date">{zh ? '未到期' : 'Not due'}</small>}
          </td>
        })}
        <td className={`instrument-heatmap-cell${row.ytd == null ? ' instrument-heatmap-cell-empty' : ''}`} style={heat(row.ytd, maximum)} title={row.ytdWindow ? `${row.ytdWindow.anchorDate} → ${row.ytdWindow.endDate}` : undefined}>
          {row.ytd == null ? '—' : formatPercent(row.ytd, 2)}
        </td>
      </tr>)}</tbody>
    </table>
  </HorizontalTableScroll>
}
