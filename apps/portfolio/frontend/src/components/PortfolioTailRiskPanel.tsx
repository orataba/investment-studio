import { LoadingNotice } from '../../../../../packages/ui/src/NoticeToast'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getPortfolioTailRisk, type PortfolioTailRisk } from '../lib/tailRiskApi'
import InfoHint from './InfoHint'
import './portfolio-tail-risk.css'

const messages: Record<string, [string, string]> = {
  invalid_portfolio_nav: ['组合净值无效，无法计算占比。', 'Portfolio NAV is invalid.'],
  no_modeled_market_exposure: ['没有可用的日频市场敞口样本。', 'No market exposure has usable daily observations.'],
  no_common_daily_periods: ['持仓之间没有起止日期一致的日频情景。', 'Holdings have no common daily observation intervals.'],
  less_than_one_tail_observation: ['所选置信度的尾部不足一个观察样本，暂不显示 VaR / ES。', 'The selected tail contains less than one observation; VaR / ES are unavailable.'],
  partial_market_risk_coverage: ['仅覆盖已建模的市场敞口；未覆盖资产并非零风险。', 'Only modeled market exposures are covered. Excluded assets are not zero risk.'],
  non_daily_or_unverified_intervals_removed: ['已剔除低频或无法确认日频的收益区间，没有填充缺失收益。', 'Non-daily or unverified intervals were removed; missing returns were not filled.'],
  common_period_intersection: ['仅使用所有建模敞口起止日期均一致的共同情景。', 'Only scenarios with identical start and end dates across modeled exposures are used.'],
  requested_history_partially_covered: ['实际共同样本仅覆盖所选回看的一部分，不能视为所选年限的完整历史。', 'The common sample covers only part of the requested lookback, not the full selected number of years.'],
  requested_history_calendar_unverified: ['部分来源无法确认共同的日频交易日历，无法验证所选区间是否完整。仅使用可确认的相邻自然日情景。', 'A common daily calendar cannot be verified for all sources, so full-window coverage cannot be established. Only verified consecutive calendar-day scenarios are used.'],
  unverified_fx_intervals_removed: ['已剔除非日频或无法验证的汇率区间，没有前向填充汇率。', 'Non-daily or unverified FX intervals were removed; FX was not carried forward.'],
  unmatched_security_fx_periods_removed: ['缺少同起止日期汇率冲击的证券区间已剔除，未补零或改用不同周期。', 'Security intervals without FX shocks at identical boundaries were removed, without zero-filling or substituting another period.'],
  derivative_fair_value_unmodeled: ['固定票息票据与期权未纳入日公允价值模型', 'FCN / Option daily fair values are not modeled'],
  missing_base_value: ['缺少本位币账面金额', 'Missing base-currency carrying amount'],
  missing_currency: ['缺少币种', 'Missing currency'],
  non_daily_source: ['非日频数据源', 'Non-daily source'],
  no_verified_daily_returns: ['没有可确认的日频收益', 'No verified daily returns'],
  invalid_return_period: ['收益区间或数值无效', 'Invalid return interval or value'],
  missing_aligned_fx_returns: ['缺少同期汇率收益', 'Missing synchronized FX returns'],
  base_currency_cash_zero_market_shock: ['本位币现金', 'Base-currency cash'],
  zero_exposure: ['当前账面金额为零', 'Zero current carrying amount'],
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
  const prominentLimitations = new Set(['invalid_portfolio_nav', 'no_modeled_market_exposure', 'no_common_daily_periods', 'less_than_one_tail_observation', 'partial_market_risk_coverage'])
  const historyWarning = data?.history_coverage_status === 'partial'
    ? 'requested_history_partially_covered'
    : data?.history_coverage_status === 'unverified' ? 'requested_history_calendar_unverified' : null

  useEffect(() => {
    let cancelled = false
    getPortfolioTailRisk(portfolioId, { asOfDate, confidence, lookbackDays }).then(
      (value) => { if (!cancelled) setLoaded({ key, data: value }) },
      (reason) => { if (!cancelled) setFailure({ key, message: reason instanceof Error ? reason.message : String(reason) }) },
    )
    return () => { cancelled = true }
  }, [portfolioId, asOfDate, confidence, lookbackDays, key])

  const method = [
    zh ? '固定当前仓位，重放历史单日市场冲击；不是组合成立以来的实际业绩，也不是对未来损失的概率保证。' : 'Current positions replay historical one-session shocks. This is not realized portfolio performance or a probability guarantee about future losses.',
    zh ? '所有比例以组合 NAV 为分母。本位币现金不产生市场价格冲击；负值表示历史情景下的收益，不代表不会亏损。' : 'Percentages use full portfolio NAV. Base-currency cash has no market-price shock. Negative values are historical scenario gains, not a guarantee against loss.',
    zh ? '只使用起止日期一致的证券总回报与汇率变化，不填充行情。跨市场日期一致不代表日内收盘时刻一致。' : 'Only observed total-return and FX changes with identical boundaries are replayed, without filling prices. Matching dates do not imply identical intraday closes.',
    zh ? '情景等概率。VaR 使用经验分位数，ES 保留边界情景的部分概率权重；不使用时间平方根缩放。' : 'Scenarios are equally probable. VaR uses the empirical quantile; ES preserves fractional boundary probability. No square-root-of-time scaling is used.',
    ...(data?.limitations.filter((code) => !prominentLimitations.has(code) && code !== historyWarning && code !== 'scenario_history_ends_before_as_of').map(label) ?? []),
  ]
  const tailPrecision = zh
    ? `尾部等效样本数 = 情景数 × (1 − 置信度)。它不是独立尾部事件数，数值可用也不代表统计精度充分。单条情景的最大权重指其在 ES 中的概率权重，不是损益贡献上限。`
    : 'Tail mass = scenario count × (1 − confidence). It is not an independent tail-event count, and an available estimate does not establish statistical precision. Maximum single-scenario weight refers to probability weight in ES, not its share of monetary loss.'

  return <section className="portfolio-section-block portfolio-tail-risk" aria-labelledby="portfolio-tail-risk-title" aria-busy={pending}>
    <header className="portfolio-tail-risk-heading">
      <h2 id="portfolio-tail-risk-title">{zh ? '历史情景损失' : 'Historical scenario losses'}<InfoHint label={zh ? '历史情景方法' : 'Historical scenario method'} detail={method} /></h2>
      <div className="portfolio-tail-risk-controls">
        <label>{zh ? '置信度' : 'Confidence'}
          <select value={confidence} onChange={(event) => setConfidence(Number(event.target.value))}>
            <option value={0.95}>95%</option><option value={0.99}>99%</option>
          </select>
        </label>
        <label>{zh ? '回看上限' : 'Lookback limit'}
          <select value={lookbackDays} onChange={(event) => setLookbackDays(Number(event.target.value))}>
            {[1, 3, 5].map((years) => <option key={years} value={years * 365}>{zh ? `${years} 年` : `${years} year${years > 1 ? 's' : ''}`}</option>)}
          </select>
        </label>
      </div>
    </header>
    {error && <p className="portfolio-tail-risk-error" role="alert">{error} <button type="button" onClick={() => setReload((value) => value + 1)}>{zh ? '重试' : 'Retry'}</button></p>}
    <LoadingNotice active={pending} message={zh ? '正在读取历史情景…' : 'Loading historical scenarios…'} />
    {data && <>
      <div className="portfolio-tail-risk-metrics">
        <div><span className="portfolio-tail-risk-metric-label">VaR · {Math.round(confidence * 100)}%<InfoHint label={zh ? 'VaR 含义' : 'What VaR measures'} detail={zh ? '在所选历史情景中，当前持仓单日损失在该置信度处的分位数。' : 'The selected loss quantile when current holdings replay historical one-session scenarios.'} /></span><strong>{percent(data.var_nav_fraction)}</strong>
          <span>{number(data.var_amount)} {data.base_currency}</span></div>
        <div><span className="portfolio-tail-risk-metric-label">ES / CVaR · {Math.round(confidence * 100)}%<InfoHint label={zh ? 'ES 含义' : 'What ES measures'} detail={zh ? '最差尾部情景的平均单日损失；正确计入边界情景的部分概率权重。' : 'Average one-session loss over the worst tail, including fractional probability at the boundary.'} /></span><strong>{percent(data.expected_shortfall_nav_fraction)}</strong>
          <span>{number(data.expected_shortfall_amount)} {data.base_currency}</span></div>
      </div>
      <div className="portfolio-tail-risk-history">
        <span>{zh ? '回看区间' : 'Requested'} <strong>{data.window_start_date} → {data.window_end_date}</strong></span>
        <span>{zh ? '实际样本' : 'Actual sample'} <strong>{data.first_scenario_start_date ?? '—'} → {data.last_scenario_end_date ?? '—'}</strong></span>
        <span>{zh ? '情景数 / 尾部等效数' : 'Scenarios / Tail mass'} <strong>{number(data.observation_count, 0)} / {number(data.tail_effective_observations)}</strong><InfoHint label={zh ? '尾部样本口径' : 'Tail sample basis'} detail={tailPrecision} /></span>
      </div>
      <p className="portfolio-tail-risk-precision">{zh
        ? `最差 ${number(data.tail_observation_count, 0)} 条情景 · 单条情景在 ES 中的最大概率权重 ${percent(data.tail_max_observation_weight)}`
        : `Worst ${number(data.tail_observation_count, 0)} scenarios · Maximum single-scenario probability weight in ES ${percent(data.tail_max_observation_weight)}`}</p>
      {historyWarning && <div className="portfolio-tail-risk-warning" role="status">
        <p>{zh ? data.history_coverage_status === 'partial' ? '历史样本未覆盖完整回看区间。' : '交易日历尚未核实，无法确认历史覆盖是否完整。' : data.history_coverage_status === 'partial' ? 'Historical sample covers only part of the requested window.' : 'The trading calendar is unverified; full-window coverage cannot be established.'}<InfoHint kind="attention" label={zh ? '历史覆盖限制' : 'Historical coverage limitation'} detail={label(historyWarning)} /></p>
        {data.history_coverage_status === 'partial' && <p className="portfolio-tail-risk-missing">{zh
          ? `应有 ${number(data.expected_common_observation_count, 0)} 条 · 样本前未覆盖 ${number(data.uncovered_leading_observation_count, 0)} 条 · 区间内缺少 ${number(data.missing_internal_observation_count, 0)} 条 · 样本后未覆盖 ${number(data.uncovered_trailing_observation_count, 0)} 条`
          : `${number(data.expected_common_observation_count, 0)} expected · ${number(data.uncovered_leading_observation_count, 0)} uncovered before the sample · ${number(data.missing_internal_observation_count, 0)} missing within it · ${number(data.uncovered_trailing_observation_count, 0)} uncovered after it`}<InfoHint label={zh ? '历史缺口口径' : 'History gap basis'} detail={zh ? '按所有已建模资产和所需汇率的共同交易日历计算。样本前未覆盖可能来自标的成立较晚或来源历史较短，不代表成立前缺失净值。' : 'Counts use the common trading calendar of modeled assets and required FX. Leading absence may reflect younger instruments or shorter source history; it does not imply missing prices before inception.'} /></p>}
      </div>}
      {data.limitations.some((code) => prominentLimitations.has(code)) && <ul className="portfolio-tail-risk-limitations">{data.limitations.filter((code) => prominentLimitations.has(code)).map((code) => <li key={code}>{label(code)}</li>)}</ul>}
      <p className="portfolio-tail-risk-coverage">
        <span>{zh ? '已建模账面总额 / NAV' : 'Modeled Gross Carrying Amount / NAV'}: <strong>{percent(data.modeled_gross_nav_fraction)}</strong></span>
        <span>{zh ? '未建模账面总额 / NAV' : 'Unmodeled Gross Carrying Amount / NAV'}: <strong>{percent(data.excluded_gross_nav_fraction)}</strong></span>
        <span>NAV: {number(data.portfolio_nav)} {data.base_currency}<InfoHint label={zh ? '账面总额口径' : 'Gross carrying amount basis'} detail={zh
          ? '按标的净额化账面金额取绝对值后加总，再除以组合 NAV；不是账户级证券 gross、FCN 本金或期权 delta 等经济敞口。固定票息票据与期权未纳入日公允价值模型。'
          : 'Sum of absolute carrying amounts after netting each instrument, divided by portfolio NAV. This is not account-level security gross exposure, FCN principal, or option delta exposure. FCN / Option daily fair values are not modeled.'} /></span>
      </p>
      <details className="portfolio-tail-risk-details"><summary>{zh ? '各持仓样本与覆盖' : 'Samples and coverage by holding'}</summary>
        <HorizontalTableScroll className="portfolio-tail-risk-table-wrap"><table><thead><tr>
          <th>{zh ? '持仓' : 'Holding'}</th><th>{zh ? '当前权重' : 'Current Weight'}</th><th>{zh ? '有效日频样本 / 区间' : 'Daily samples / Interval'}</th><th>{zh ? '汇率未对齐区间' : 'Unmatched FX intervals'}</th><th>{zh ? '覆盖情况' : 'Coverage'}</th>
        </tr></thead><tbody>{data.rows.map((row, index) => <tr key={`${row.holding_id}:${index}`}>
          <td translate="no">{row.name}</td><td>{percent(row.weight)}</td><td>{row.status === 'modeled' ? <>{number(row.observation_count, 0)}<small>{row.first_scenario_start_date} → {row.last_scenario_end_date}</small></> : '—'}</td>
          <td>{number(row.unmatched_fx_period_count, 0)}{row.fx_rejected_period_count > 0 && <small>{zh ? '未验证的汇率区间' : 'Unverified FX intervals'}: {number(row.fx_rejected_period_count, 0)}</small>}</td>
          <td>{row.reason ? label(row.reason) : zh ? '已建模' : 'Modeled'}</td>
        </tr>)}</tbody></table></HorizontalTableScroll>
      </details>
    </>}
  </section>
}
