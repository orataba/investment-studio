import { useLanguage } from './i18n'
import { resolveWorkspaceUrl, withLanguage } from './navigation'

/** Recovery never changes the current object or treats a dependency outage as logout. */
export default function RequestRecovery({ error, onRetry, busy = false, requestId, embedded = false }: {
  error: string; onRetry: () => void; busy?: boolean; requestId?: string | null; embedded?: boolean
}) {
  const { t, language } = useLanguage()
  const diagnostic = error.match(/^(.*?) \[request_id=([A-Za-z0-9_-]+)\]$/s)
  const message = diagnostic ? diagnostic[1] : error
  const diagnosticId = requestId || diagnostic?.[2]
  return <div role={embedded ? undefined : "alert"} className="inline-notice inline-notice-error" aria-busy={busy}>
    <p>{t(message)}</p>
    {diagnosticId && <p>{t('Request ID')}: <code translate="no">{diagnosticId}</code></p>}
    <button type="button" disabled={busy} onClick={onRetry}>{t(busy ? 'Retrying…' : 'Retry')}</button>
    {' · '}<a href={withLanguage(resolveWorkspaceUrl(undefined, 'home'), language)}>{t('Home')}</a>
  </div>
}
