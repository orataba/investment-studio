export type EmailNavInventoryPayload = {
  limit: number
  counts: {
    candidate_routes: number
    matched_unimported: number
    unresolved_identity_count: number
    unresolved_occurrence_count: number
    folder_count: number
    total_uid_lag: number
    folders_with_lag: number
    folders_with_error: number
    message_retryable: number
    message_dead_letter: number
    parse_retryable: number
    parse_dead_letter: number
    parse_unsupported: number
  }
  routing_statuses: Record<string, number>
  validation_statuses: Record<string, number>
  unresolved_identities: Array<{
    identity_id: string
    instrument_code: string | null
    instrument_name: string | null
    first_nav_date: string | null
    latest_nav_date: string | null
    occurrence_count: number
    routing_statuses: {
      unmatched: number
      ambiguous: number
      rejected: number
    }
    example: {
      folder_name: string | null
      attachment_name: string | null
      reason: string | null
    }
  }>
  folders: Array<{
    folder_id: string
    folder_name: string
    last_committed_uid: number
    last_observed_uid_next: number | null
    uid_lag: number
    last_scan_started_at: string | null
    last_scan_succeeded_at: string | null
    error: null | { at: string | null; code: string | null; reason: string | null }
  }>
  message_failures: Array<{
    failure_id: string
    status: string
    folder_name: string | null
    attempt_count: number
    next_retry_at: string | null
    error_code: string | null
    reason: string | null
  }>
  parse_failures: Array<{
    failure_id: string
    status: string
    folder_name: string | null
    attachment_name: string | null
    source_format: string | null
    attempt_count: number
    next_retry_at: string | null
    error_code: string | null
    reason: string | null
  }>
  automatic_fund_creation: false
}

function formatDateTime(value: string | null) {
  if (!value) return 'Not run yet'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function formatNavRange(first: string | null, latest: string | null) {
  if (!first && !latest) return 'Date unavailable'
  if (!first || first === latest) return first || latest
  return `${first} – ${latest}`
}

function statusSummary(statuses: { unmatched: number; ambiguous: number; rejected: number }) {
  return Object.entries(statuses)
    .filter(([, count]) => count > 0)
    .map(([status, count]) => `${status} ${count}`)
    .join(' · ') || '—'
}

export function EmailNavInventoryPanel({ inventory }: { inventory: EmailNavInventoryPayload }) {
  const failedJobs = inventory.counts.message_dead_letter
    + inventory.counts.message_retryable
    + inventory.counts.parse_dead_letter
    + inventory.counts.parse_retryable
    + inventory.counts.parse_unsupported
  const failureRows = [
    ...inventory.message_failures.map((failure) => ({
      key: failure.failure_id,
      stage: 'Message acquisition',
      status: failure.status,
      location: failure.folder_name || 'Unknown folder',
      attempts: failure.attempt_count,
      reason: failure.reason || failure.error_code || 'No error detail',
    })),
    ...inventory.parse_failures.map((failure) => ({
      key: failure.failure_id,
      stage: 'Attachment parse',
      status: failure.status,
      location: [failure.folder_name, failure.attachment_name].filter(Boolean).join(' / ') || 'Unknown attachment',
      attempts: failure.attempt_count,
      reason: failure.reason || failure.error_code || 'No error detail',
    })),
  ]

  return (
    <>
      <section className="data-ops-panel data-ops-exceptions" aria-label="Email NAV trackable fund review">
        <div className="data-ops-panel-header">
          <div><span>Email NAV inventory</span><h2>Trackable funds needing identity review</h2></div>
          <small>Showing {inventory.unresolved_identities.length} of {inventory.counts.unresolved_identity_count} · review only · automatic fund creation is disabled</small>
        </div>
        <div className="data-ops-metrics">
          <article className={inventory.counts.unresolved_identity_count ? 'data-ops-metric-warning' : ''}>
            <span>Unresolved identities</span><strong>{inventory.counts.unresolved_identity_count}</strong><small>{inventory.counts.unresolved_occurrence_count} durable observations</small>
          </article>
          <article className={inventory.counts.matched_unimported ? 'data-ops-metric-warning' : ''}>
            <span>Matched, not imported</span><strong>{inventory.counts.matched_unimported}</strong><small>Durable publication backlog</small>
          </article>
          <article className={inventory.counts.total_uid_lag ? 'data-ops-metric-warning' : ''}>
            <span>Folder UID lag</span><strong>{inventory.counts.total_uid_lag}</strong><small>{inventory.counts.folders_with_lag} folders behind</small>
          </article>
          <article className={failedJobs ? 'data-ops-metric-warning' : ''}>
            <span>Retry / terminal</span><strong>{failedJobs}</strong><small>{inventory.counts.folders_with_error} folder scan errors</small>
          </article>
        </div>

        {inventory.unresolved_identities.length ? (
          <div className="registry-table-wrap">
            <table className="registry-table registry-table-compact">
              <thead><tr><th>Reported identity</th><th>NAV coverage</th><th>Observations</th><th>Routing status</th><th>Example evidence</th></tr></thead>
              <tbody>{inventory.unresolved_identities.map((identity) => (
                <tr key={identity.identity_id}>
                  <td>{identity.instrument_name || 'Unnamed fund'}<small>{identity.instrument_code || 'No reported code'}</small></td>
                  <td>{formatNavRange(identity.first_nav_date, identity.latest_nav_date)}</td>
                  <td>{identity.occurrence_count}</td>
                  <td>{statusSummary(identity.routing_statuses)}</td>
                  <td>{[identity.example.folder_name, identity.example.attachment_name].filter(Boolean).join(' / ') || 'No file context'}<small>{identity.example.reason || 'No routing reason recorded'}</small></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <div className="data-ops-clear">No unmatched, ambiguous, or rejected email NAV identities.</div>}
      </section>

      <section className="data-ops-grid">
        <article className="data-ops-panel">
          <div className="data-ops-panel-header"><div><span>Folder cursors</span><h2>Incremental scan health</h2></div><small>{inventory.counts.folder_count} tracked folders</small></div>
          {inventory.folders.length ? (
            <div className="registry-table-wrap"><table className="registry-table registry-table-compact"><thead><tr><th>Folder</th><th>UID lag</th><th>Last success</th><th>Error</th></tr></thead><tbody>{inventory.folders.map((folder) => <tr key={folder.folder_id}><td>{folder.folder_name}</td><td>{folder.uid_lag}</td><td>{formatDateTime(folder.last_scan_succeeded_at)}</td><td>{folder.error ? (folder.error.reason || folder.error.code || 'Scan failed') : '—'}</td></tr>)}</tbody></table></div>
          ) : <div className="data-ops-clear">No folder cursor has been recorded yet.</div>}
        </article>

        <article className="data-ops-panel">
          <div className="data-ops-panel-header"><div><span>Pipeline failures</span><h2>Bounded retry review</h2></div><small>Latest operational examples</small></div>
          {failureRows.length ? (
            <div className="registry-table-wrap"><table className="registry-table registry-table-compact"><thead><tr><th>Stage</th><th>Status</th><th>Location</th><th>Detail</th></tr></thead><tbody>{failureRows.map((failure) => <tr key={failure.key}><td>{failure.stage}</td><td>{failure.status}</td><td>{failure.location}</td><td>{failure.reason}<small>{failure.attempts} attempts</small></td></tr>)}</tbody></table></div>
          ) : <div className="data-ops-clear">No retryable or dead-letter email jobs.</div>}
        </article>
      </section>
    </>
  )
}
