import { useEffect, useState } from 'react'
import { getInstrumentReferenceData, type InstrumentReferenceData } from '../lib/api'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { dateLabel } from './ResearchEvidence'

type Row = Record<string, unknown>
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((row): row is Row => Boolean(row) && typeof row === 'object') : []
const text = (value: unknown) => value == null || value === '' ? '—' : typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(value)

export default function ResearchFundamentalNumbers({ instrumentId }: { instrumentId: string }) {
  const [state, setState] = useState<{ instrumentId: string; data?: InstrumentReferenceData; error?: string } | null>(null)
  useEffect(() => {
    let active = true
    void getInstrumentReferenceData(instrumentId).then(data => { if (active) setState({ instrumentId, data }) })
      .catch(reason => { if (active) setState({ instrumentId, error: reason instanceof Error ? reason.message : '基本面数据读取失败。' }) })
    return () => { active = false }
  }, [instrumentId])
  const current = state?.instrumentId === instrumentId ? state : null
  const data = current?.data
  const financials = rows(data?.sections.financials)
  const ratios = rows(data?.sections.ratios)
  const metrics = rows(data?.sections.key_metrics)
  const holdings = rows(data?.sections.holdings).filter(row => typeof row.weightPercentage === 'number').sort((a, b) => Number(b.weightPercentage) - Number(a.weightPercentage)).slice(0, 10)
  const availableRatios = [
    ['priceToEarningsRatio', '市盈率（倍）'], ['priceToBookRatio', '市净率（倍）'], ['priceToSalesRatio', '市销率（倍）'],
    ['netProfitMargin', '净利率（原值）'], ['grossProfitMargin', '毛利率（原值）'], ['debtToEquityRatio', '负债权益比（原值）'],
  ].filter(([key]) => ratios.some(row => row[key] != null))
  const availableMetrics = [
    ['returnOnEquity', 'ROE（原值）'], ['returnOnInvestedCapital', 'ROIC（原值）'], ['freeCashFlowYield', '自由现金流收益率（原值）'], ['earningsYield', '盈利收益率（原值）'], ['enterpriseValue', '企业价值（报表币种）'],
  ].filter(([key]) => metrics.some(row => row[key] != null))
  const table = (title: string, values: Row[], columns: string[][]) => values.length > 0 && <section className="research-fundamental-table"><h4>{title}</h4><HorizontalTableScroll><table><thead><tr><th>财期</th><th>币种</th>{columns.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead><tbody>{values.map((row, index) => <tr key={`${row.date}:${row.period}:${index}`}><td>{text(row.date)} · {text(row.period)}</td><td>{text(row.reportedCurrency)}</td>{columns.map(([key]) => <td key={key}>{text(row[key])}</td>)}</tr>)}</tbody></table></HorizontalTableScroll></section>
  return <div className="research-fundamental-numbers">
    {!current && <p role="status" className="sector-research-note">正在读取基本面数据…</p>}
    {current?.error && <p role="alert">{current.error}</p>}
    {data && <p className="sector-research-note">数据来源 <span translate="no">{data.provider}</span> · 采集于 {dateLabel(data.fetched_at)}。财期与采集时间分别保留。</p>}
    {table('年度利润表历史', financials, [['revenue', '营收（报表币种）'], ['operatingIncome', '经营利润（报表币种）'], ['netIncome', '净利润（报表币种）'], ['eps', '每股收益（币种/股）']])}
    {availableRatios.length > 0 && table('估值与财务比率历史', ratios, availableRatios)}
    {availableMetrics.length > 0 && table('重要基本面指标历史', metrics, availableMetrics)}
    {holdings.length > 0 && <section className="research-fundamental-table"><h4>主要成份 · Top 10</h4><HorizontalTableScroll><table><thead><tr><th>成份</th><th className="research-numeric-cell">披露权重（%）</th><th>快照日期</th></tr></thead><tbody>{holdings.map((row, index) => <tr key={index}><td translate="no">{text(row.asset)} · {text(row.name)}</td><td className="research-numeric-cell" title={text(row.weightPercentage)}>{Number(row.weightPercentage).toFixed(2)}</td><td>{text(row.snapshotDate)}</td></tr>)}</tbody></table></HorizontalTableScroll><p className="sector-research-note">披露权重未重新归一化；主要成份不能代表全部持仓。快照日期不代表持仓报告期。</p></section>}
    {data && !financials.length && !availableRatios.length && !availableMetrics.length && !holdings.length && <p className="sector-research-note">当前未取得可展示的基本面历史或成份数据。</p>}
    {data && Object.entries(data.section_errors || {}).filter(([key]) => ['financials', 'key_metrics', 'ratios', 'holdings'].includes(key)).map(([key, value]) => <p key={key} className="sector-research-limitation" translate="no">{value}</p>)}
  </div>
}
