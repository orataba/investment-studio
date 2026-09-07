import { useEffect, useState } from 'react'
import { getInstrumentReferenceData } from '../lib/api'
import { readWorkbench } from '../lib/workbenchApi'

type Snapshot = { observation_id: string; collected_at: string; collected_at_min: string | null; collected_at_max: string | null }
type EstimateEvidence = {
  supported: boolean; status: string; current_snapshot: Snapshot | null; previous_snapshot: Snapshot | null
  history_available_from?: string
  coverage: { current_company_count?: number; metrics?: Array<{ frequency: string; metric: string; comparable_company_count: number; changed_company_count: number }> }
  changes: Array<{ symbol: string; frequency: string; target_period_end: string; metric: string; currency: string; current_currency_status?: string; previous_value: number; current_value: number; delta_pct: number | null; analyst_count_changed: boolean | null }>
  observations: unknown[]; unmatched: unknown[]; gaps: string[]
}

export default function EstimateHistoryPanel({ instrumentId, language }: { instrumentId: string; language: 'zh-Hans' | 'en' }) {
  const [evidence, setEvidence] = useState<EstimateEvidence | null>(null)
  const [error, setError] = useState('')
  const zh = language === 'zh-Hans'
  useEffect(() => {
    let active = true
    setEvidence(null)
    setError('')
    async function load() {
      try {
        const reference = await getInstrumentReferenceData(instrumentId)
        if (!active) return
        const info = reference.sections.fund_info as Record<string, unknown> | undefined
        // Use disclosed investment categories, never the ticker or fund name.
        if (info?.assetClass === 'Commodities' || info?.invest_type === '黄金现货合约') return
        const value = await readWorkbench<EstimateEvidence>(`/sector-research/estimates?instrument_id=${encodeURIComponent(instrumentId)}`)
        if (active) setEvidence(value)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : (zh ? '读取失败' : 'Unavailable'))
      }
    }
    void load()
    return () => { active = false }
  }, [instrumentId, zh])
  if (!evidence && !error) return null
  const clock = (value: string | null | undefined) => value ? new Date(value).toLocaleString(zh ? 'zh-CN' : 'en-US') : (zh ? '未知' : 'Unknown')
  const state = !evidence ? (zh ? '读取中' : 'Loading') : !evidence.supported ? (zh ? '尚未覆盖' : 'Not covered')
    : evidence.status === 'no_snapshot' ? (zh ? '尚无快照' : 'No snapshot')
      : evidence.status === 'baseline' ? (zh ? '首次基线' : 'First baseline')
        : evidence.status === 'limited' ? (zh ? '比较口径待确认' : 'Comparison limited')
          : evidence.changes.length ? (zh ? `${evidence.changes.length} 项可比变化` : `${evidence.changes.length} comparable changes`)
            : (zh ? '可比范围内未见变化' : 'No changes in comparable data')
  return <details className="panel listed-estimate-history">
    <summary>{zh ? '成份公司预期快照' : 'Constituent estimate history'} · {error ? (zh ? '读取失败' : 'Unavailable') : state}</summary>
    {error ? <p role="alert">{error}</p> : evidence ? <>
      {!evidence.supported ? <p>{zh ? '当前预期快照覆盖 11 只美股行业 ETF。A 股及其他 ETF 仍可结合已取得的行情、披露和材料分析，成份及预期数据须逐项核实。' : 'Estimate snapshots currently cover 11 US sector ETFs. For other ETFs, use available prices and disclosures and verify constituent coverage separately.'}</p> : <>
        <p>{zh ? `已留存 ${evidence.coverage.current_company_count ?? 0} 家成份公司资料。` : `${evidence.coverage.current_company_count ?? 0} constituent company records retained.`}</p>
        <p>{zh ? '本次采集' : 'Current collection'}：{clock(evidence.current_snapshot?.collected_at)}<br />
          {zh ? '上次采集' : 'Previous collection'}：{evidence.previous_snapshot ? clock(evidence.previous_snapshot.collected_at) : (zh ? '尚无历史基线' : 'No earlier baseline')}<br />
          {zh ? '采集历史起点' : 'Collection history begins'}：{clock(evidence.history_available_from)}<br />
          {zh ? '源数据采集范围' : 'Source collection range'}：{clock(evidence.current_snapshot?.collected_at_min)} — {clock(evidence.current_snapshot?.collected_at_max)}</p>
        {evidence.gaps.length ? <ul>{evidence.gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul> : null}
        {evidence.status === 'no_snapshot' ? <p>{zh ? '后台首次采集资料后建立基线，此后每次采集都会保留历史，与是否运行研究无关。' : 'The first data collection establishes a baseline. Every later collection is retained, independently of research runs.'}</p> : null}
        <p>{zh ? `另有 ${evidence.observations.length} 项待核实数值差异、${evidence.unmatched.length} 项财期或覆盖变化，未计为预期上修或下修。` : `${evidence.observations.length} unverified differences and ${evidence.unmatched.length} period or coverage changes are excluded from revisions.`}</p>
        {evidence.changes.some((row) => row.current_currency_status === 'inferred_from_reporting_currency')
          ? <p>{zh ? '部分预期币种按公司财报币种推定，来源已留存；预期接口本身未直接披露币种。' : 'Some estimate currencies are inferred from company financial statements, with sources retained. The estimates endpoint does not disclose currency directly.'}</p> : null}
        {evidence.changes.length ? <div className="listed-estimate-table"><table><thead><tr>{(zh ? ['公司 / 财期', '指标', '前值 → 当前', '变化'] : ['Company / period', 'Metric', 'Previous → current', 'Change']).map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>
          {evidence.changes.map((row) => <tr key={`${row.symbol}-${row.frequency}-${row.target_period_end}-${row.metric}`}><td>{row.symbol}<br />{row.target_period_end} · {row.frequency === 'annual' ? (zh ? '年度' : 'Annual') : (zh ? '季度' : 'Quarterly')}</td><td>{row.metric === 'eps_avg' ? 'EPS' : (zh ? '营收' : 'Revenue')} ({row.currency})</td><td>{row.previous_value.toLocaleString()} → {row.current_value.toLocaleString()}{row.analyst_count_changed ? <small>{zh ? ' · 分析师样本数也有变化' : ' · Analyst count changed'}</small> : null}</td><td>{row.delta_pct == null ? (zh ? '基数非正，不计算百分比' : 'Nonpositive base') : `${row.delta_pct > 0 ? '+' : ''}${row.delta_pct.toFixed(2)}%`}</td></tr>)}
        </tbody></table></div> : null}
      </>}
      <p>{zh ? '每次后台采集保留独立观察，源采集时间和预测财期分别记录；快照差异不等于精确调整时点，也不自动构成投资机会。' : 'Each data collection is retained as a separate observation. Source collection time and forecast period are distinct. Differences do not establish an exact revision time or an investment opportunity.'}</p>
    </> : null}
  </details>
}
