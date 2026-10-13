import { useEffect, useState } from 'react'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { formatPercent, signedValueClass } from '../lib/format'
import { MONTH_LABELS, type MonthlyReturnMatrixRow } from '../lib/monthlyReturns'

export default function MonthlyReturnMatrix({ rows, asOfDate }: { rows: MonthlyReturnMatrixRow[]; asOfDate: string | null }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [detail, setDetail] = useState<string | null>(null)
  useEffect(() => { setDetail(null) }, [rows, asOfDate, language])
  const hint = zh ? 'MTD 为截至所示日期的月内收益。聚焦或悬停单元格查看期间与覆盖。' : 'MTD is the return through the displayed date. Focus or hover a cell for its period and coverage.'
  function percent(value: number | null | undefined) {
    return `${value != null && value > 0 ? '+' : ''}${formatPercent(value)}`
  }
  return <>
    <HorizontalTableScroll className="table-shell">
      <table className="transactions-table performance-return-matrix-table portfolio-monthly-matrix" aria-label={zh ? '月度收益矩阵' : 'Monthly return matrix'}>
        <thead><tr><th>{zh ? '年份' : 'Year'}</th>{MONTH_LABELS.map((month, index) => <th key={month}>{zh ? `${index + 1}月` : month}</th>)}<th>YTD</th></tr></thead>
        <tbody>{rows.length ? rows.map((row) => <tr key={row.year}>
          <th scope="row">{row.year}<span className={`coverage-dot ${row.coverageState === 'complete' ? 'coverage-pill-live' : 'coverage-pill-warning'}`} /></th>
          {row.months.map((bucket, index) => {
            const key = `${row.year}-${String(index + 1).padStart(2, '0')}`
            const future = asOfDate != null && key > asOfDate.slice(0, 7)
            const monthEnd = new Date(Date.UTC(Number(row.year), index + 1, 0)).toISOString().slice(0, 10)
            const mtd = bucket != null && bucket.end_date < monthEnd && key === asOfDate?.slice(0, 7)
            const value = bucket?.cumulative_twr
            const available = value != null && Number.isFinite(value)
            const state = future ? (zh ? '未到期' : 'Not due') : !available ? (bucket ? (zh ? '不完整' : 'Incomplete') : (zh ? '无历史' : 'No history')) : mtd ? 'MTD' : ''
            const description = future
              ? `${key} · ${zh ? '在当前数据截止日之后' : 'After the data cutoff'} ${asOfDate}`
              : bucket
                ? `${key} · ${bucket.start_date} → ${bucket.end_date} · ${mtd ? 'MTD · ' : ''}${percent(value)} · ${bucket.observation_count} ${zh ? '次观察' : 'observations'}${bucket.unavailable_reason ? ` · ${bucket.unavailable_reason}` : ''}`
                : `${key} · ${zh ? '没有可用历史数据' : 'No historical data available'}`
            return <td key={key} tabIndex={0} aria-label={description} title={description}
              onFocus={() => setDetail(description)} onMouseEnter={() => setDetail(description)}
              className={`performance-cell-number performance-return-cell ${available ? signedValueClass(value) : ''} ${future ? 'monthly-return-future' : ''}`}>
              <span>{available ? percent(value) : '—'}</span>
              {state && <small>{state}{mtd && available ? ` · ${bucket!.end_date.slice(5)}` : ''}</small>}
            </td>
          })}
          <td tabIndex={0} className={`performance-cell-number performance-return-cell ${signedValueClass(row.ytd)}`}
            title={row.hasYearStartAnchor ? `${row.year} YTD` : 'No year-start anchor.'}
            onFocus={() => setDetail(row.hasYearStartAnchor ? `${row.year} YTD · ${percent(row.ytd)}` : (zh ? '缺少可靠年初边界，不能计算全年至今收益。' : 'No reliable year-start boundary; YTD is unavailable.'))}>
            {percent(row.ytd)}
          </td>
        </tr>) : <tr><td colSpan={14}>{zh ? '暂无月度收益' : 'No monthly returns.'}</td></tr>}</tbody>
      </table>
    </HorizontalTableScroll>
    <p className="monthly-return-detail" aria-live="polite">{detail ?? `${asOfDate ? `${zh ? '截至' : 'As of'} ${asOfDate} · ` : ''}${hint}`}</p>
  </>
}
