import { useEffect, useState } from 'react'
import InfoHint from '../../../../../packages/ui/src/InfoHint'
import { LoadingNotice } from '../../../../../packages/ui/src/NoticeToast'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import type { ResearchAssistantReference } from '../../../../../packages/ui/src/researchReference'
import { getInstrumentTailRisk, type InstrumentTailRisk } from '../lib/instrumentTailRiskApi'
import InstrumentRiskPanel from './InstrumentRiskPanel'
import './instrument-risk-monitoring.css'

const reasons: Record<string, [string, string]> = {
  unconfirmed_economic_returns: ['收益口径尚未确认，暂不计算日度风险。', 'Economic return semantics are unconfirmed; daily risk is unavailable.'],
  non_daily_source: ['该资产没有已确认的日频收益，暂不计算日度 VaR / ES。', 'This asset has no confirmed daily returns; daily VaR / ES are unavailable.'],
  unverified_daily_calendar: ['日频交易日历尚未确认，暂不计算日度风险。', 'The daily trading calendar is unverified; daily risk is unavailable.'],
  missing_daily_return_intervals: ['实际样本内部或尾部存在缺失／不可用收益区间，暂不计算 VaR / ES。', 'Missing or unavailable intervals within or after the actual sample prevent VaR / ES.'],
  incomplete_market_session: ['存在尚未收盘的行情日期，暂不使用该收益样本。', 'The return sample contains a market session that has not closed.'],
  invalid_return_interval: ['收益区间存在无效或重复记录，暂不计算日度风险。', 'Invalid or duplicate return intervals prevent a daily estimate.'],
  less_than_one_tail_observation: ['所选置信度的尾部不足一个观察样本，暂不显示 VaR / ES。', 'The selected tail contains less than one observation; VaR / ES are unavailable.'],
  no_verified_daily_returns: ['所选区间没有有效日频收益，暂不计算。', 'No verified daily returns are available in this window.'],
}

export default function InstrumentRiskMonitoringPanel({ instrumentId, onAskAssistant }: {
  instrumentId: string
  onAskAssistant?: (id: string, question: string, reference?: ResearchAssistantReference) => void
}) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [confidence, setConfidence] = useState(.95)
  const [lookbackDays, setLookbackDays] = useState(1095)
  const [reload, setReload] = useState(0)
  const [loaded, setLoaded] = useState<{ key: string; data: InstrumentTailRisk } | null>(null)
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null)
  const key = JSON.stringify([instrumentId, confidence, lookbackDays, reload])
  const data = loaded?.key === key ? loaded.data : null
  const error = failure?.key === key ? failure.message : null
  const pct = (n: number | null | undefined) => n == null ? '—' : `${(n * 100).toFixed(2)}%`
  useEffect(() => {
    const controller = new AbortController()
    setLoaded(null)
    setFailure(null)
    getInstrumentTailRisk(instrumentId, confidence, lookbackDays, controller.signal).then(
      value => { if (!controller.signal.aborted) setLoaded({ key, data: value }) },
      reason => { if (!controller.signal.aborted) setFailure({ key, message: reason instanceof Error ? reason.message : String(reason) }) },
    )
    return () => controller.abort()
  }, [instrumentId, confidence, lookbackDays, key])
  const method = zh ? [
    '按单一资产、不加杠杆的多头敞口，使用真实相邻交易日的普通收益率；以资产报价币种计量，不包含组合仓位与汇率敞口。',
    '历史模拟法：损失等于收益率的相反数。VaR 是损失分位数；ES 是最差尾部的平均损失，边界情景按实际概率权重计入。负值表示历史情景收益。',
    '股票、ETF 和基金使用已确认的分红再投收益；指数保留已确认的价格或全收益口径，加密资产使用价格收益。复权显示方式不改变风险结果。',
    '不填补缺失收益，不把低频收益缩放为日频。尾部不足一个等效样本时不展示；超过一个不代表估计已经稳定。历史情景不保证未来损失范围。',
  ] : [
    'An unlevered long asset in its quote currency, using simple returns between observed adjacent trading sessions. Portfolio position sizes and FX exposure are excluded.',
    'Historical loss is minus return. VaR is its empirical quantile; ES integrates the worst tail with fractional boundary mass. Negative values represent historical gains.',
    'Equities, ETFs and funds require reinvested total returns. Indices retain their confirmed price/total-return basis; crypto uses price returns. Display adjustments do not change risk.',
    'No filling or time scaling. Tails below one effective observation are withheld; larger samples do not guarantee stable estimates or future loss bounds.',
  ]
  return <div className="instrument-risk-monitoring">
    <section className="instrument-tail-risk" aria-label={zh ? '日度尾部风险' : 'Daily tail risk'}>
      <div className="instrument-tail-risk-heading"><h2>{zh ? '日度尾部风险' : 'Daily tail risk'}<InfoHint label={zh ? '风险计算口径' : 'Risk methodology'} detail={method} /></h2>
        <div className="instrument-tail-risk-controls">
          <label>{zh ? '置信度' : 'Confidence'}<select value={confidence} onChange={e => setConfidence(Number(e.target.value))}><option value={.95}>95%</option><option value={.99}>99%</option></select></label>
          <label>{zh ? '回看' : 'Lookback'}<select value={lookbackDays} onChange={e => setLookbackDays(Number(e.target.value))}>{[[365, 1], [1095, 3], [1825, 5]].map(([days, years]) => <option key={days} value={days}>{years}{zh ? ' 年' : ' years'}</option>)}</select></label>
        </div>
      </div>
      <LoadingNotice active={!data && !error} message={zh ? '正在计算风险…' : 'Calculating risk…'} />
      {error && <div role="alert">{error} <button onClick={() => setReload(v => v + 1)}>{zh ? '重试' : 'Retry'}</button></div>}
      {data && <>
        <div className="instrument-tail-risk-metrics">
          <div><span>VaR · {Math.round(confidence * 100)}%</span><strong>{pct(data.var)}</strong></div>
          <div><span>ES / CVaR · {Math.round(confidence * 100)}%</span><strong>{pct(data.expected_shortfall)}</strong></div>
          <div><span>{zh ? '日频样本 / 尾部等效数' : 'Daily samples / Tail mass'}</span><strong>{data.observation_count} / {data.tail_effective_observations.toFixed(2)}</strong></div>
        </div>
        {data.reason && <p className="instrument-tail-risk-warning" role="status">{reasons[data.reason]?.[zh ? 0 : 1] ?? data.reason}</p>}
        {data.source_stale && <p className="instrument-tail-risk-warning" role="status">{zh ? `有效收益尚未覆盖应到日期 ${data.latest_expected_date}，结果不能代表最新风险。` : `Valid returns do not cover the latest due date ${data.latest_expected_date}; this is not a current estimate.`}</p>}
        {data.freshness?.data_freshness_status === 'pending_recalc' && <p className="instrument-tail-risk-warning" role="status">{zh ? '收益数据正在更新，请以更新完成后的结果为准。' : 'Return data is being updated; use the completed result when available.'}</p>}
        {data.freshness?.data_freshness_status === 'stale' && <p className="instrument-tail-risk-warning" role="status">{zh ? '收益计算尚未更新至最新数据或设置，以下保留上次结果。' : 'The calculation has not caught up with the latest data or settings; these are retained results.'}</p>}
        <p className="instrument-tail-risk-sample">{zh ? '实际样本' : 'Actual sample'}: {data.first_observation_start_date ?? '—'} → {data.last_observation_end_date ?? '—'} · {data.currency ?? '—'} · {data.return_kind === 'total_return' ? (zh ? '分红再投收益' : 'Total returns') : data.return_kind === 'price_return' ? (zh ? '价格收益' : 'Price returns') : '—'}</p>
        {data.window_start_date && <p className="instrument-tail-risk-sample">{zh ? '回看区间' : 'Requested window'}: {data.window_start_date} → {data.window_end_date}
          <InfoHint label={zh ? '尾部样本说明' : 'Tail sample precision'} detail={zh ? `最差 ${data.tail_observation_count} 条情景；单条情景在 ES 中的最大权重 ${pct(data.tail_max_observation_weight)}。` : `Worst ${data.tail_observation_count} scenarios; maximum single-scenario ES weight ${pct(data.tail_max_observation_weight)}.`} />
        </p>}
        {data.history_coverage_status === 'partial' && <p className="instrument-tail-risk-warning" role="status">{zh ? `样本未覆盖完整回看区间：前段 ${data.uncovered_leading_count} 条、区间内 ${data.missing_internal_count} 条、尾段 ${data.uncovered_trailing_count} 条未覆盖。` : `Partial history: ${data.uncovered_leading_count} leading, ${data.missing_internal_count} internal and ${data.uncovered_trailing_count} trailing intervals uncovered.`}<InfoHint label={zh ? '历史覆盖说明' : 'Historical coverage'} detail={zh ? '按已确认交易日历核对。样本前未覆盖可能来自成立较晚或来源历史较短；未覆盖日期没有按零收益处理。' : 'Checked against the confirmed calendar. Leading absence may reflect inception or limited source history. Missing returns are never zero-filled.'} /></p>}
        {!!data.missing_intervals?.length && <details className="instrument-tail-risk-warning"><summary>{zh ? '缺失收益区间' : 'Missing return intervals'} ({data.missing_intervals.length})</summary><ul>{data.missing_intervals.map(p => <li key={`${p.start_date}:${p.end_date}`}>{p.start_date} → {p.end_date}</li>)}</ul></details>}
        {data.excluded_interval_count > 0 && <p className="instrument-tail-risk-warning">{zh ? `发现 ${data.excluded_interval_count} 条非日频或不可用收益区间，风险估计不可用。` : `${data.excluded_interval_count} non-daily or unavailable intervals prevent a risk estimate.`}</p>}
      </>}
    </section>
    <InstrumentRiskPanel key={instrumentId} instrumentId={instrumentId} heading={zh ? '风险事项与跟踪' : 'Risk cases and follow-up'} onAskAssistant={onAskAssistant} />
  </div>
}
