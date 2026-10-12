import { useEffect, useState } from 'react'
import { useLanguage } from '../../../packages/ui/src/i18n'
import { resolveWorkspaceUrl, withLanguage } from '../../../packages/ui/src/navigation'
import { researchStamp } from '../../../packages/ui/src/ResearchRunStatus'
import type { StudioApp } from './StudioHome'
import { readRiskAlerts, riskDestination, type RiskAlerts } from './riskAlerts'

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
    <h2 title={t('Only major or urgent risks confirmed by the latest risk review and still requiring attention.')}>{t('Important risks')}</h2>
    <div className="home-risk-content" aria-live="polite">
      {loading ? <span>{t('Loading…')}</span> : error ? <span>{t('Risk status unavailable.')} <button type="button" title={error} onClick={() => setRevision(value => value + 1)}>{t('Retry')}</button></span>
        : !result?.alerts.length ? <span>{t('No published major or urgent items requiring attention.')}</span>
        : <ul>{result.alerts.map(alert => <li key={alert.case_id}><a data-workspace-link href={withLanguage(riskDestination(apps, alert.target), language)} title={`${t('Evidence confidence')}: ${t(alert.confidence || 'Not assessed')} · ${t('Evidence as of')}: ${researchStamp(alert.as_of)} · ${t('Reviewed')}: ${researchStamp(alert.reviewed_at)}`}>
          <span className="home-risk-object" translate="no">{alert.instrument_name}</span><span className="home-risk-title" translate="no">{alert.title}</span><span aria-hidden="true">↗</span>
        </a></li>)}</ul>}
      {!loading && !error && result && (offset > 0 || result.has_more) && <div className="home-risk-pages">
        {offset > 0 && <button type="button" onClick={() => setOffset(value => Math.max(0, value - 3))}>{t('Previous risks')}</button>}
        <span>{offset + 1}–{offset + result.alerts.length} / {result.total}</span>
        {result.has_more && <button type="button" onClick={() => setOffset(value => value + 3)}>{t('Next risks')}</button>}
      </div>}
    </div>
  </aside>
}
