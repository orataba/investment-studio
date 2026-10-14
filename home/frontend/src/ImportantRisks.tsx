import { useEffect, useState } from 'react'
import { useLanguage } from '../../../packages/ui/src/i18n'
import { resolveWorkspaceUrl, withLanguage } from '../../../packages/ui/src/navigation'
import { researchStamp } from '../../../packages/ui/src/ResearchRunStatus'
import type { StudioApp } from './StudioHome'
import { readRiskAlerts, riskDestination, type RiskAlerts, type RiskAlert } from './riskAlerts'

export function ImportantRiskEntries({ apps, alerts }: { apps: StudioApp[]; alerts: RiskAlert[] }) {
  const { t, language } = useLanguage()
  return <ul>{alerts.map(alert => <li key={alert.case_id}><a data-workspace-link href={withLanguage(riskDestination(apps, alert.target), language)} title={`${t('Evidence confidence')}: ${t(alert.confidence || 'Not assessed')} · ${t('Evidence as of')}: ${researchStamp(alert.as_of)} · ${t('Reviewed')}: ${researchStamp(alert.reviewed_at)}`}>
    <span className="home-risk-object" translate="no">{alert.instrument_name}</span><span className="home-risk-title" translate="no">{alert.title}</span>{alert.alert_state === 'assessment_pending' && <span className="home-risk-review-state">{t('New information awaiting review')}</span>}{alert.alert_state === 'unverified_urgent' && <span className="home-risk-review-state">{t('Major lead — unverified')}</span>}<span aria-hidden="true">↗</span>
  </a></li>)}</ul>
}

export default function ImportantRisks({ apps }: { apps: StudioApp[] }) {
  const { t, language } = useLanguage()
  const [result, setResult] = useState<RiskAlerts | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const app = apps.find(item => item.app_id === 'watchlist')
  const base = app ? resolveWorkspaceUrl(app.url, 'watchlist') : null
  useEffect(() => {
    if (!base) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    void readRiskAlerts(base, offset, controller.signal).then(value => {
      if (!controller.signal.aborted && offset > 0 && !value.alerts.length) { setOffset(0); return }
      if (!controller.signal.aborted) setResult(value)
    }).catch(reason => {
      if (!controller.signal.aborted) { setResult(null); setError(reason instanceof Error ? reason.message : String(reason)) }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [base, revision, offset])
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
        : !result?.alerts.length ? <span>{t('No items currently meet the important-risk criteria.')}</span>
        : <ImportantRiskEntries apps={apps} alerts={result.alerts} />}
      {!loading && !error && result?.model_work?.paused && <p className="home-risk-coverage">{t('Research paused; retained assessments and pending developments are shown.')}</p>}
      {!loading && !error && result && (offset > 0 || result.has_more) && <div className="home-risk-pages">
        {offset > 0 && <button type="button" onClick={() => setOffset(value => Math.max(0, value - 3))}>{t('Previous risks')}</button>}
        <span>{offset + 1}–{offset + result.alerts.length} / {result.total}</span>
        {result.has_more && <button type="button" onClick={() => setOffset(value => value + 3)}>{t('Next risks')}</button>}
      </div>}
    </div>
  </aside>
}
