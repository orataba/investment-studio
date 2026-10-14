import { useEffect, useState } from 'react'
import { useLanguage } from './i18n'
import { PriceRuleCountsLine, PriceRuleDetails } from './PriceRiskRules'
import { type PriceRiskPeriod, type RiskAsset, type RiskRequest, percent } from './instrumentRisk'

type RulePreview = { metric_snapshot_id: string | null; rule_version: string | null; as_of: string | null; status: string; note: string; rules: Array<{ key: string; previous_limit_pct: number | null; proposed_limit_pct: number | null; evaluable_observations: number; unavailable_observations: number; previous_trigger_count: number; proposed_trigger_count: number; new_trigger_dates: string[]; removed_trigger_dates: string[] }> }
const pricePeriods: Array<[PriceRiskPeriod, string]> = [
  ['day', '日跌幅'],
  ['week', '周跌幅'],
  ['month', '月跌幅'],
  ['quarter', '季度跌幅'],
]

export default function PriceRiskSettings({
  canWrite,
  asset,
  request,
  onSaved,
}: {
  asset: RiskAsset
  request: RiskRequest
  onSaved: () => Promise<void>
  canWrite: boolean
}) {
  const zh = useLanguage().language === 'zh-Hans'
  const initial = () => ({
    drawdown: asset.drawdown_limit?.toString() || '',
    ...Object.fromEntries(
      pricePeriods.map(([key]) => [
        key,
        asset.period_limits?.[key]?.toString() || '',
      ]),
    ),
  })
  const [values, setValues] = useState<Record<string, string>>(initial)
  const [severities, setSeverities] = useState<Record<string, 'attention' | 'severe'>>(asset.price_risk_calibration?.severities || {})
  const [reason, setReason] = useState('')
  const [preview, setPreview] = useState<RulePreview | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    setValues(initial())
    setSeverities(asset.price_risk_calibration?.severities || {})
  }, [asset.drawdown_limit, asset.period_limits, asset.price_risk_calibration?.severities])
  useEffect(() => { setPreview(null); setPreviewError('') }, [values, severities])
  const proposed = { expected_rule_version: asset.rule_version ?? null, severities, change_reason: reason.trim(), drawdown_limit: values.drawdown ? Number(values.drawdown) : null, period_limits: Object.fromEntries(pricePeriods.map(([key]) => [key, values[key] ? Number(values[key]) : null])) }
  const calibration = asset.price_risk_calibration
  return (
    <details className="risk-price-settings" open>
      <summary>指标规则</summary>
      {asset.price_rule_summary && <>
        <PriceRuleCountsLine counts={asset.price_rule_summary.counts} />
        <details><summary>当前规则与观察值</summary><PriceRuleDetails summary={asset.price_rule_summary} /></details>
      </>}
      <p className="research-muted">
        下跌幅度达到复核线才生成事项；填正数，留空关闭。严重规则触发后直接保留事实提示，普通规则先经筛选。设置在不同观察列表和组合中共用。
      </p>
      <form
        onSubmit={async (event) => {
          event.preventDefault()
          if (!canWrite) return
          setBusy(true)
          setMessage('')
          setFailed(false)
          try {
            await request(`/risk/rules/${asset.instrument_id}`, {
              method: 'PUT',
              body: JSON.stringify(proposed),
            })
            setReason('')
            setMessage('指标规则已保存')
            try { await onSaved() } catch (failure) {
              setFailed(true)
              setMessage(zh ? `指标规则已保存，但最新结果读取失败，请刷新页面。${failure instanceof Error ? failure.message : ''}` : `Metric rules were saved, but the updated result could not be read. Refresh the page. ${failure instanceof Error ? failure.message : ''}`)
            }
          } catch (error) {
            setFailed(true)
            setMessage(error instanceof Error ? error.message : '保存失败')
          } finally {
            setBusy(false)
          }
        }}
      >
        <div className="risk-price-inputs">
          {[...pricePeriods, ['drawdown', '高点回撤']].map(([key, label]) => (
            <label key={key}>
              {label}（%）
              <input
                disabled={!canWrite || busy || previewBusy}
                aria-label={`${asset.name} ${label}复核线`}
                type="number"
                min="0.01"
                max="100"
                step="any"
                placeholder="未设置"
                value={values[key] || ''}
                onChange={(event) =>
                  setValues({ ...values, [key]: event.target.value })
                }
              />
              <select aria-label={`${asset.name} ${label} ${zh ? '规则级别' : 'Rule severity'}`} disabled={!canWrite || busy || previewBusy} value={severities[key] || 'attention'} onChange={event => setSeverities({ ...severities, [key]: event.target.value as 'attention' | 'severe' })}><option value="attention">{zh ? '普通复核' : 'Standard review'}</option><option value="severe">{zh ? '严重 · 事实直达' : 'Severe · mandatory fact alert'}</option></select>
            </label>
          ))}
        </div>
        <details className="risk-rule-preview"><summary>{zh ? '保存前核对当前观察与新阈值' : 'Compare current observations with proposed thresholds'}</summary><p>{zh ? '以下只比较当前观察，不代表历史命中次数或交易建议。' : 'This compares current observations only; it is not a historical trigger count or trading recommendation.'}</p><table><thead><tr><th>{zh ? '规则' : 'Rule'}</th><th>{zh ? '当前值' : 'Current value'}</th><th>{zh ? '原阈值' : 'Saved threshold'}</th><th>{zh ? '新阈值' : 'Proposed threshold'}</th></tr></thead><tbody>{[...pricePeriods, ['drawdown', '高点回撤']].map(([key, label]) => { const rule = asset.price_rule_summary?.rules.find(item => item.key === key); return <tr key={key}><td>{label}</td><td>{rule?.value_pct == null ? '—' : `${rule.value_pct}%`}</td><td>{rule?.limit_pct == null ? '—' : `${rule.limit_pct}%`}</td><td>{values[key] ? `${values[key]}%` : (zh ? '未配置' : 'Unconfigured')}</td></tr> })}</tbody></table></details>
        <div className="risk-history-preview"><button type="button" disabled={previewBusy} onClick={async () => { setPreviewBusy(true); setPreviewError(''); try { setPreview(await request<RulePreview>(`/risk/rules/${asset.instrument_id}/preview`, { method: 'POST', body: JSON.stringify(proposed) })) } catch (failure) { setPreviewError(failure instanceof Error ? failure.message : String(failure)) } finally { setPreviewBusy(false) } }}>{previewBusy ? (zh ? '正在核对历史…' : 'Checking history…') : (zh ? '预览历史命中差异' : 'Preview historical trigger changes')}</button>
          {previewError && <p role="alert">{previewError}</p>}{preview && <><p className="research-muted" translate="no">{preview.note}</p><p>{zh ? '截至' : 'As of'} {preview.as_of || '—'} · {zh ? '历史按本次留存路径重新计算，不代表当时可得信息。' : 'History is recomputed from the retained path and does not represent information available at each historical date.'}</p>{preview.status === 'unavailable' ? <p>{zh ? '历史样本暂不可用。' : 'Historical observations are unavailable.'}</p> : <table><thead><tr><th>{zh ? '规则' : 'Rule'}</th><th>{zh ? '可计算 / 缺口' : 'Evaluable / Gaps'}</th><th>{zh ? '原命中' : 'Saved triggers'}</th><th>{zh ? '新命中' : 'Proposed triggers'}</th><th>{zh ? '变化日期' : 'Changed dates'}</th></tr></thead><tbody>{preview.rules.map(rule => <tr key={rule.key}><td>{[...pricePeriods, ['drawdown', '高点回撤']].find(([key]) => key === rule.key)?.[1] || rule.key}</td><td>{rule.evaluable_observations} / {rule.unavailable_observations}</td><td>{rule.previous_trigger_count}</td><td>{rule.proposed_trigger_count}</td><td><details><summary>+{rule.new_trigger_dates.length} / −{rule.removed_trigger_dates.length}</summary><p>+ {rule.new_trigger_dates.join(', ') || '—'}</p><p>− {rule.removed_trigger_dates.join(', ') || '—'}</p></details></td></tr>)}</tbody></table>}</>}
        </div>
        {canWrite && <label className="risk-rule-reason">{zh ? '修改原因' : 'Reason for change'}<input required value={reason} onChange={event => setReason(event.target.value)} /></label>}
        <div className="risk-settings-save">
          {canWrite && <button disabled={busy || previewBusy || !reason.trim()}>{busy ? '保存中…' : '保存指标规则'}</button>}
          <span role={failed ? 'alert' : 'status'}>{message}</span>
        </div>
      </form>
      {asset.rule_version && <p className="research-muted">{zh ? '生效版本' : 'Active version'} <code translate="no">{asset.rule_version}</code></p>}
      {!!asset.price_rule_summary?.rule_history?.length && <details><summary>{zh ? '规则修改记录' : 'Rule change history'}</summary>{asset.price_rule_summary.rule_history.map(version => <div key={version.revision}><p>{zh ? '版本' : 'Version'} {version.revision} · {version.effective_at} · {typeof version.changed_by === 'string' ? version.changed_by : version.changed_by?.display_name}</p><p translate="no">{version.reason}</p><details><summary>{zh ? '原值与新值' : 'Before and after'}</summary><pre>{JSON.stringify({ previous: version.previous, current: version.current }, null, 2)}</pre></details></div>)}</details>}
      <details className="risk-initial-method">
        <summary>初值依据</summary>
        <p className="research-muted">
          {calibration?.sample_end
            ? <><span>校准样本</span> {calibration.sample_start} → {calibration.sample_end} · {calibration.observations} <span>个日收益观察值</span> · <span>日波动率</span> {percent(calibration.daily_volatility_pct)}。{calibration.manually_edited && <span>当前复核线已手动调整。</span>}</>
            : '日度样本完整且足够时，才按自身历史波动生成初值。'}
        </p>
        <p className="research-muted">常规市场使用最近最多 252、至少 63 个日对数收益；24/7 市场最多 365、至少 90 个。</p>
        <p className="research-muted">日、周、月、季度分别取 3／2.5／2／1.5 倍区间波动，最低跌幅为 0.5%／1%／2%／3%，向上取整到 0.5 个百分点。</p>
        <p className="research-muted">保存后固定，不随波动升高自动放宽。这是初始复核容忍度，不是亏损概率或买卖指令；高点回撤线另行设置。</p>
      </details>
    </details>
  )
}
