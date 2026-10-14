import { useEffect, useState } from 'react'
import { useLanguage } from '../../../packages/ui/src/i18n'
import { resolveWorkspaceUrl, withLanguage } from '../../../packages/ui/src/navigation'
import { researchStamp } from '../../../packages/ui/src/ResearchRunStatus'
import type { StudioApp } from './StudioHome'
import { readRiskAlerts, riskDestination, type RiskAlerts, type RiskAlert } from './riskAlerts'

export function ImportantRiskEntries({ apps, alerts }: { apps: StudioApp[]; alerts: RiskAlert[] }) {
  const { t, language } = useLanguage()
  const zh = language === 'zh-Hans'
  return <ol>{alerts.map(alert => <li key={alert.notice_id || alert.case_id}><a data-workspace-link href={withLanguage(riskDestination(apps, alert.target), language)} title={`${t('Evidence confidence')}: ${t(alert.confidence || 'Not assessed')} · ${t('Evidence as of')}: ${researchStamp(alert.as_of)} · ${t('Reviewed')}: ${researchStamp(alert.reviewed_at)}`}>
    <span className={`home-risk-level home-risk-level-${alert.level || 'pending'}`}>{alert.level ? ({ high: zh ? '高' : 'High', medium: zh ? '中' : 'Medium', low: zh ? '低' : 'Low' })[alert.level] : (zh ? '等级待评' : 'Priority pending')}</span><span className="home-risk-object" translate="no">{alert.instrument_name}</span><span className="home-risk-title" translate="no">{alert.title}</span>{alert.alert_state === 'assessment_pending' && <span className="home-risk-review-state">{t('New information awaiting review')}</span>}{alert.alert_state === 'unverified_urgent' && <span className="home-risk-review-state">{t('Major lead — unverified')}</span>}<span aria-hidden="true">↗</span>
  </a>{alert.body && <p className="home-risk-body" translate="no">{alert.body}</p>}{alert.status_note && <p className="home-risk-coverage" translate="no">{alert.status_note}</p>}{alert.current_rule_facts?.map((fact, index) => <p key={index} className="home-risk-body" translate="no">{fact.body}</p>)}</li>)}</ol>
}

export default function ImportantRisks({ apps }: { apps: StudioApp[] }) {
  const { t, language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [result, setResult] = useState<RiskAlerts | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [loading, setLoading] = useState(true)
  const app = apps.find(item => item.app_id === 'watchlist')
  const base = app ? resolveWorkspaceUrl(app.url, 'watchlist') : null
  useEffect(() => {
    if (!base) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    void readRiskAlerts(base, 0, controller.signal).then(value => {
      if (!controller.signal.aborted) setResult(value)
    }).catch(reason => {
      if (!controller.signal.aborted) { setResult(null); setError(reason instanceof Error ? reason.message : String(reason)) }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [base, revision])
  if (!base) return null
  return <aside className="home-important-risks" aria-label={t('Important risks')}>
    <h2 title={t('Unresolved major risks retain their last assessment. Material urgent leads awaiting verification are labeled separately.')}>{t('Important risks')}</h2>
    <div className="home-risk-content" aria-live="polite">
      {loading ? <span>{t('Loading…')}</span> : error ? <div className="home-risk-unavailable" role="status">
        <strong>{t('Important risks cannot be read right now.')}</strong>
        <p>{t('The current risk status is unknown; this does not mean there are no important risks.')}</p>
        <div className="home-risk-unavailable-actions"><button type="button" onClick={() => setRevision(value => value + 1)}>{t('Retry')}</button>
          <a data-workspace-link href={withLanguage(`${base}/watchlists/all-instruments`, language)}>{t('Open Watchlist')}</a>
          <details><summary>{t('Technical details')}</summary><code>{error}</code></details>
        </div>
      </div>
        : !result?.alerts.length ? <span>{result?.notice_status === 'assessed' ? (zh ? '当前暂无需要特别提示的风险。' : 'No risks currently require special attention.') : result?.notice_status === 'unavailable' ? (zh ? '相关风险暂无法完整评估。' : 'Some risks cannot currently be fully assessed.') : result?.notice_status === 'assessment_pending' ? (zh ? '当前范围尚未完成风险筛选。' : 'Risk selection is not yet complete for the current scope.') : t('No items currently meet the important-risk criteria.')}</span>
        : <ImportantRiskEntries apps={apps} alerts={result.alerts} />}
      {!loading && !error && !!result?.coverage_notes?.length && <details className="home-risk-coverage"><summary>{zh ? '评估覆盖与限制' : 'Assessment coverage and limitations'}</summary>{result.coverage_notes.map(note => <p key={note} translate="no">{note}</p>)}</details>}
      {!loading && !error && result?.model_work?.paused && <p className="home-risk-coverage">{result.alerts.length ? t('Research paused; retained assessments and pending developments are shown.') : (zh ? '研究已暂停；尚无可展示提示，不代表已经完成无风险检查。' : 'Research is paused. No alerts are available to display; this is not a completed clean risk check.')}</p>}

    </div>
  </aside>
}
