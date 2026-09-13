import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getPortfolioTailRisk, type PortfolioTailRisk } from '../lib/tailRiskApi'
import './portfolio-tail-risk.css'

const messages: Record<string, [string, string]> = {
  invalid_portfolio_nav: ['组合净值无效，无法计算占比。', 'Portfolio NAV is invalid.'],
  no_modeled_market_exposure: ['没有可用的日频市场敞口样本。', 'No market exposure has usable daily observations.'],
  no_common_daily_periods: ['持仓之间没有起止日期一致的日频情景。', 'Holdings have no common daily observation intervals.'],
  less_than_one_tail_observation: ['所选置信度的尾部不足一个观察样本，暂不显示 VaR / ES。', 'The selected tail contains less than one observation; VaR / ES are unavailable.'],
  partial_market_risk_coverage: ['仅覆盖下列可建模部分；未覆盖资产并非零风险。', 'Only the modeled exposures below are covered. Excluded assets are not zero risk.'],
  non_daily_or_unverified_intervals_removed: ['已剔除低频或无法确认日频的收益区间，没有填充缺失收益。', 'Non-daily or unverified intervals were removed; missing returns were not filled.'],
  common_period_intersection: ['仅使用所有建模敞口起止日期均一致的共同情景。', 'Only scenarios with identical start and end dates across modeled exposures are used.'],
  scenario_history_ends_before_as_of: ['最新共同情景早于持仓日期，具体截止日见下方。', 'The latest common scenario predates the holdings date; see the scenario dates below.'],
  derivative_fair_value_unmodeled: ['固定票息票据与期权未纳入日公允价值模型', 'FCN / Option daily fair values are not modeled'],
  missing_base_value: ['缺少本位币市值', 'Missing base-currency value'],
  missing_currency: ['缺少币种', 'Missing currency'],
  non_daily_source: ['非日频数据源', 'Non-daily source'],
  no_verified_daily_returns: ['没有可确认的日频收益', 'No verified daily returns'],
  invalid_return_period: ['收益区间或数值无效', 'Invalid return interval or value'],
  missing_aligned_fx_returns: ['缺少同期汇率收益', 'Missing synchronized FX returns'],
  base_currency_cash_zero_market_shock: ['本位币现金', 'Base-currency cash'],
  zero_exposure: ['无当前敞口', 'No current exposure'],
}

export default function PortfolioTailRiskPanel({ portfolioId, asOfDate }: {
  portfolioId: string
  asOfDate?: string
}) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [confidence, setConfidence] = useState(0.95)
  const [lookbackDays, setLookbackDays] = useState(1095)
  const [reload, setReload] = useState(0)
  const [loaded, setLoaded] = useState<{ key: string; data: PortfolioTailRisk } | null>(null)
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null)
  const key = JSON.stringify([portfolioId, asOfDate, confidence, lookbackDays, reload])
  const data = loaded?.key === key ? loaded.data : null
  const error = failure?.key === key ? failure.message : null
  const pending = !data && !error
  const label = (code: string) => messages[code]?.[zh ? 0 : 1] ?? code
  const number = (value: number | null | undefined, digits = 2) => value == null ? '—'
    : value.toLocaleString(zh ? 'zh-CN' : 'en-US', { maximumFractionDigits: digits, minimumFractionDigits: digits })
  const percent = (value: number | null | undefined) => value == null ? '—' : `${number(value * 100)}%`

  useEffect(() => {
    let cancelled = false
    getPortfolioTailRisk(portfolioId, { asOfDate, confidence, lookbackDays }).then(
      (value) => { if (!cancelled) setLoaded({ key, data: value }) },
      (reason) => { if (!cancelled) setFailure({ key, message: reason instanceof Error ? reason.message : String(reason) }) },
    )
    return () => { cancelled = true }
  }, [portfolioId, asOfDate, confidence, lookbackDays, key])

  return <section className="panel portfolio-tail-risk" aria-labelledby="portfolio-tail-risk-title" aria-busy={pending}>
    <header className="portfolio-tail-risk-heading">
      <div>
        <h2 id="portfolio-tail-risk-title">{zh ? '尾部风险' : 'Tail risk'}</h2>
        <p>{zh ? '当前持仓 · 1 日共同历史情景 · 本位币损失' : 'Current holdings · Common one-day historical scenarios · Base-currency losses'}</p>
      </div>
      <div className="portfolio-tail-risk-controls">
        <label>{zh ? '置信度' : 'Confidence'}
          <select value={confidence} onChange={(event) => setConfidence(Number(event.target.value))}>
            <option value={0.95}>95%</option><option value={0.99}>99%</option>
          </select>
        </label>
        <label>{zh ? '历史窗口' : 'History'}
          <select value={lookbackDays} onChange={(event) => setLookbackDays(Number(event.target.value))}>
            {[1, 3, 5].map((years) => <option key={years} value={years * 365}>{zh ? `${years} 年` : `${years} year${years > 1 ? 's' : ''}`}</option>)}
          </select>
        </label>
      </div>
    </header>
    {error && <p className="portfolio-tail-risk-error" role="alert">{error} <button type="button" onClick={() => setReload((value) => value + 1)}>{zh ? '重试' : 'Retry'}</button></p>}
    <div className="portfolio-tail-risk-metrics">
      <div><span>VaR · {Math.round(confidence * 100)}%</span><strong>{percent(data?.var_nav_fraction)}</strong>
        <span>{number(data?.var_amount)} {data?.base_currency ?? ''}</span>
        <small>{zh ? '所选置信度对应的损失分位数' : 'Loss quantile at the selected confidence'}</small></div>
      <div><span>ES / CVaR · {Math.round(confidence * 100)}%</span><strong>{percent(data?.expected_shortfall_nav_fraction)}</strong>
        <span>{number(data?.expected_shortfall_amount)} {data?.base_currency ?? ''}</span>
        <small>{zh ? '最差尾部情景的平均损失' : 'Average loss over the worst tail scenarios'}</small></div>
      <div><span>{zh ? '共同情景 / 尾部有效样本' : 'Common scenarios / Tail sample mass'}</span>
        <strong>{data ? `${number(data.observation_count, 0)} / ${number(data.tail_effective_observations)}` : '—'}</strong>
        <span>{pending ? (zh ? '正在读取历史情景…' : 'Loading historical scenarios…') : data ? `${data.first_scenario_start_date ?? '—'} → ${data.last_scenario_end_date ?? '—'}` : '—'}</span>
        <small>{zh ? '尾部样本量 = 情景数 × (1 − 置信度)' : 'Tail mass = Scenarios × (1 − Confidence)'}</small></div>
    </div>
    <p className="portfolio-tail-risk-scope">{zh
      ? '固定票息票据与期权未纳入。所有占比以组合 NAV 为分母；本位币现金不产生市场价格冲击。负值表示历史情景下的收益，不代表不会亏损。'
      : 'FCN / Option are excluded. Percentages use full portfolio NAV; base-currency cash has no market-price shock. Negative values are historical scenario gains, not a guarantee against loss.'}</p>
    {data && <>
      <p className="portfolio-tail-risk-coverage">{zh ? '建模总敞口 / NAV' : 'Modeled gross exposure / NAV'}: {percent(data.modeled_gross_nav_fraction)}
        {' · '}{zh ? '未建模总敞口 / NAV' : 'Unmodeled gross exposure / NAV'}: {percent(data.excluded_gross_nav_fraction)}
        {' · NAV: '}{number(data.portfolio_nav)} {data.base_currency}</p>
      {data.limitations.length > 0 && <ul className="portfolio-tail-risk-limitations">{data.limitations.map((code) => <li key={code}>{label(code)}</li>)}</ul>}
      <details className="portfolio-tail-risk-details"><summary>{zh ? '样本精度与覆盖明细' : 'Sample precision and exposure coverage'}</summary>
        <p>{zh
          ? `ES 平均中，单个情景的概率权重最高为 ${percent(data.tail_max_observation_weight)}。这不是独立尾部事件的数量，也不是样本已充分的保证；历史窗口、市场变化和共同样本缺失都会影响估计。`
          : `In the ES average, one scenario has probability weight up to ${percent(data.tail_max_observation_weight)}. Tail mass is not a count of independent events or an assurance of adequacy; the historical window, changing markets and missing common observations affect precision.`}</p>
        <p>{zh
          ? '仅重放真实同周期的证券总回报及汇率变化，未填充行情。各情景等概率；VaR 使用经验分位数，ES 保留分位边界的部分概率。当前仓位固定，未使用组合实际历史绩效或时间平方根缩放。'
          : 'Only observed, synchronized total-return and FX changes are replayed, without filling prices. Scenarios have equal probabilities; VaR uses the empirical quantile and ES preserves fractional boundary probability. Exposures are fixed today; actual portfolio history and square-root-of-time scaling are not used.'}</p>
        <HorizontalTableScroll className="portfolio-tail-risk-table-wrap"><table><thead><tr>
          <th>{zh ? '持仓' : 'Holding'}</th><th>{zh ? '市值 / NAV' : 'Value / NAV'}</th><th>{zh ? '日频样本' : 'Daily samples'}</th><th>{zh ? '覆盖' : 'Coverage'}</th>
        </tr></thead><tbody>{data.rows.map((row, index) => <tr key={`${row.holding_id}:${index}`}>
          <td>{row.name}</td><td>{percent(row.weight)}</td><td>{row.status === 'modeled' ? number(row.observation_count, 0) : '—'}</td>
          <td>{row.reason ? label(row.reason) : zh ? '已建模' : 'Modeled'}</td>
        </tr>)}</tbody></table></HorizontalTableScroll>
      </details>
    </>}
  </section>
}
