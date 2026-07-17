import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  addFundNavReinvestmentEvidence,
  buildCandidateRejectPayload,
  buildFundNavActionPayload,
  buildFundNavEvidencePayload,
  confirmFundNavActionCandidate,
  createFundNavAction,
  createMutationId,
  emptyFundNavActionDraft,
  emptyFundNavEvidenceDraft,
  fundNavEventToDraft,
  fundNavEvidenceToDraft,
  loadFundNavReviewData,
  rejectFundNavActionCandidate,
  resumeFundNavActionCandidateConfirmation,
  reviseFundNavAction,
  reviseFundNavReinvestmentEvidence,
  type FundNavActionCandidate,
  type FundNavActionDraft,
  type FundNavActionRequest,
  type FundNavAdjustmentFactor,
  type FundNavAuditDraft,
  type FundNavEventRevision,
  type FundNavEvidenceDraft,
  type FundNavEvidenceKind,
  type FundNavInstrumentDetail,
  type FundNavMutationResponse,
  type FundNavReinvestmentEvidenceRevision,
} from './fundNavActions'

function candidateTypeLabel(candidateType: FundNavActionCandidate['candidate_type']) {
  return candidateType === 'cash_distribution_signal'
    ? 'Potential cash distribution'
    : 'Cash-balance discontinuity'
}

function eventTypeLabel(eventType: FundNavEventRevision['event_type']) {
  return eventType === 'cash_distribution' ? 'Cash distribution' : 'Unit split'
}

function revisionKindLabel(kind: FundNavEventRevision['revision_kind']) {
  if (kind === 'correction') return 'Correction'
  if (kind === 'cancellation') return 'Cancellation'
  return 'Original'
}

function eventValue(event: FundNavEventRevision) {
  return event.event_type === 'cash_distribution'
    ? `${event.cash_per_unit ?? '—'} per unit`
    : `${event.unit_ratio ?? '—'}× units`
}

function formatTimestamp(value: string) {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function shortId(value: string | null) {
  if (!value) return '—'
  return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value
}

function evidenceReason(value: Record<string, unknown>) {
  const reason = value.unavailable_reason
  return typeof reason === 'string' && reason.trim() ? reason : 'not provable from current evidence'
}

export function FundNavActionCandidateFacts({
  candidate,
}: {
  candidate: FundNavActionCandidate
}) {
  return (
    <div className="fund-nav-candidate-facts">
      <div><span>Signal</span><strong>{candidateTypeLabel(candidate.candidate_type)}</strong></div>
      <div>
        <span>Review state</span>
        <strong>{candidate.status === 'confirming' ? 'Confirmation reserved' : candidate.status}</strong>
        {candidate.confirmation_client_mutation_id
          ? <small>{candidate.confirmation_client_mutation_id}</small>
          : null}
      </div>
      <div>
        <span>Observed interval</span>
        <strong>{candidate.interval_start_date} → {candidate.interval_end_date}</strong>
      </div>
      <div><span>Cash before</span><strong>{candidate.observed_cash_balance_before}</strong></div>
      <div><span>Cash after</span><strong>{candidate.observed_cash_balance_after}</strong></div>
      <div><span>Observed change</span><strong>{candidate.observed_cash_delta}</strong></div>
      <div><span>Measurement uncertainty</span><strong>± {candidate.measurement_uncertainty}</strong></div>
      <div><span>Expected cash</span><strong>{candidate.expected_cash_balance ?? 'Not available'}</strong></div>
      <div>
        <span>Source</span>
        <strong>{candidate.source_provider}</strong>
        <small>{candidate.source_revision}</small>
      </div>
    </div>
  )
}

function AuditFields({
  actor,
  reason,
  onActorChange,
  onReasonChange,
}: {
  actor: string
  reason: string
  onActorChange: (value: string) => void
  onReasonChange: (value: string) => void
}) {
  return (
    <div className="registry-action-form-grid fund-nav-audit-fields">
      <label>
        <span>Operator identity</span>
        <input
          type="text"
          value={actor}
          onChange={(event) => onActorChange(event.target.value)}
          placeholder="Name or operations account"
          maxLength={320}
          required
        />
      </label>
      <label className="registry-field-wide">
        <span>Revision / decision reason</span>
        <textarea
          value={reason}
          onChange={(event) => onReasonChange(event.target.value)}
          placeholder="State why this exact record is being created, corrected, cancelled, or rejected"
          maxLength={4096}
          required
        />
      </label>
    </div>
  )
}

function ActionFactsFields({
  draft,
  onChange,
  lockEventType,
  allowReinvestmentEvidence,
}: {
  draft: FundNavActionDraft
  onChange: (draft: FundNavActionDraft) => void
  lockEventType: boolean
  allowReinvestmentEvidence: boolean
}) {
  function set<K extends keyof FundNavActionDraft>(key: K, value: FundNavActionDraft[K]) {
    onChange({ ...draft, [key]: value })
  }

  return (
    <div className="registry-action-form-grid">
      <label>
        <span>Action type</span>
        <select
          value={draft.eventType}
          onChange={(event) => onChange({
            ...emptyFundNavActionDraft(event.target.value as FundNavActionDraft['eventType']),
            evidenceKind: draft.evidenceKind,
            evidenceSource: draft.evidenceSource,
          })}
          disabled={lockEventType}
        >
          <option value="cash_distribution">Cash distribution</option>
          <option value="unit_split">Unit split</option>
        </select>
      </label>
      <label>
        <span>Effective date</span>
        <input type="date" value={draft.effectiveDate} onChange={(event) => set('effectiveDate', event.target.value)} required />
      </label>
      <label>
        <span>{draft.eventType === 'cash_distribution' ? 'Cash per unit' : 'New / old unit ratio'}</span>
        <input
          type="text"
          inputMode="decimal"
          value={draft.amount}
          onChange={(event) => set('amount', event.target.value)}
          placeholder="Exact positive decimal"
          required
        />
      </label>
      <label>
        <span>Same-day sequence</span>
        <input
          type="text"
          inputMode="numeric"
          value={draft.sequenceOrder}
          onChange={(event) => set('sequenceOrder', event.target.value)}
          placeholder="Required if actions share a date"
        />
      </label>
      <label>
        <span>Announcement date</span>
        <input type="date" value={draft.announcementDate} onChange={(event) => set('announcementDate', event.target.value)} />
      </label>
      <label>
        <span>Record date</span>
        <input type="date" value={draft.recordDate} onChange={(event) => set('recordDate', event.target.value)} />
      </label>
      <label>
        <span>Payable date</span>
        <input type="date" value={draft.payableDate} onChange={(event) => set('payableDate', event.target.value)} />
      </label>
      <label>
        <span>Evidence type</span>
        <select
          value={draft.evidenceKind}
          onChange={(event) => set('evidenceKind', event.target.value as FundNavEvidenceKind)}
          required
        >
          <option value="">Select evidence type</option>
          <option value="provider_notice">Provider notice</option>
          <option value="manual_verified">Manually verified evidence</option>
        </select>
      </label>
      <label className="registry-field-wide">
        <span>Exact evidence source / document reference</span>
        <input
          type="text"
          value={draft.evidenceSource}
          onChange={(event) => set('evidenceSource', event.target.value)}
          placeholder="Provider notice, statement, or verified document reference"
          maxLength={1024}
          required
        />
      </label>
      <label className="registry-field-wide">
        <span>External event ID</span>
        <input
          type="text"
          value={draft.externalEventId}
          onChange={(event) => set('externalEventId', event.target.value)}
          placeholder="Optional provider event or notice identifier"
          maxLength={1024}
        />
      </label>
      {allowReinvestmentEvidence && draft.eventType === 'cash_distribution' ? (
        <div className="registry-field-wide fund-nav-nested-evidence">
          <label>
            <span>Dividend reinvestment NAV</span>
            <input
              type="text"
              inputMode="decimal"
              value={draft.reinvestmentNav}
              onChange={(event) => set('reinvestmentNav', event.target.value)}
              placeholder="Optional; enter only with exact evidence"
            />
            <small className="fund-nav-na-warning">
              Blank means the unit NAV remains available while dividend-reinvested total-return NAV is NA.
              A correction never inherits evidence from the superseded action revision.
            </small>
          </label>
          {draft.reinvestmentNav.trim() ? (
            <div className="registry-action-form-grid">
              <label>
                <span>Reinvestment evidence type</span>
                <select
                  value={draft.reinvestmentEvidenceKind}
                  onChange={(event) => set('reinvestmentEvidenceKind', event.target.value as FundNavEvidenceKind)}
                  required
                >
                  <option value="">Select evidence type</option>
                  <option value="provider_notice">Provider notice</option>
                  <option value="manual_verified">Manually verified evidence</option>
                </select>
              </label>
              <label>
                <span>External reinvestment evidence ID</span>
                <input
                  type="text"
                  value={draft.externalReinvestmentEvidenceId}
                  onChange={(event) => set('externalReinvestmentEvidenceId', event.target.value)}
                  maxLength={1024}
                  placeholder="Optional provider evidence identifier"
                />
              </label>
              <label className="registry-field-wide">
                <span>Exact reinvestment evidence source / document reference</span>
                <input
                  type="text"
                  value={draft.reinvestmentEvidenceSource}
                  onChange={(event) => set('reinvestmentEvidenceSource', event.target.value)}
                  maxLength={1024}
                  placeholder="The specific notice, statement, page, or verified source for this NAV"
                  required
                />
              </label>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function EvidenceFactsFields({
  draft,
  onChange,
}: {
  draft: FundNavEvidenceDraft
  onChange: (draft: FundNavEvidenceDraft) => void
}) {
  function set<K extends keyof FundNavEvidenceDraft>(key: K, value: FundNavEvidenceDraft[K]) {
    onChange({ ...draft, [key]: value })
  }
  return (
    <div className="registry-action-form-grid">
      <label>
        <span>Dividend reinvestment NAV</span>
        <input
          type="text"
          inputMode="decimal"
          value={draft.reinvestmentNav}
          onChange={(event) => set('reinvestmentNav', event.target.value)}
          placeholder="Exact positive decimal"
          required
        />
      </label>
      <label>
        <span>Evidence type</span>
        <select
          value={draft.evidenceKind}
          onChange={(event) => set('evidenceKind', event.target.value as FundNavEvidenceKind)}
          required
        >
          <option value="">Select evidence type</option>
          <option value="provider_notice">Provider notice</option>
          <option value="manual_verified">Manually verified evidence</option>
        </select>
      </label>
      <label className="registry-field-wide">
        <span>Exact evidence source / document reference</span>
        <input
          type="text"
          value={draft.evidenceSource}
          onChange={(event) => set('evidenceSource', event.target.value)}
          maxLength={1024}
          required
        />
      </label>
      <label className="registry-field-wide">
        <span>External evidence ID</span>
        <input
          type="text"
          value={draft.externalEvidenceId}
          onChange={(event) => set('externalEvidenceId', event.target.value)}
          maxLength={1024}
          placeholder="Optional provider evidence identifier"
        />
      </label>
    </div>
  )
}

function FactorRows({ factors }: { factors: FundNavAdjustmentFactor[] }) {
  return (
    <div className="registry-table-wrap">
      <table className="registry-table registry-table-compact fund-nav-factor-table">
        <thead>
          <tr>
            <th>Date</th><th>Factor</th><th>Kind / evidence</th><th>Event</th><th>Reinvestment evidence</th><th>Previous</th><th>Logical key</th>
          </tr>
        </thead>
        <tbody>
          {factors.length ? factors.map((factor) => (
            <tr key={factor.fund_nav_adjustment_factor_id}>
              <td>{factor.as_of_date}</td>
              <td>{factor.factor_level}</td>
              <td>{factor.factor_kind}<small>{factor.evidence_kind}</small></td>
              <td title={factor.fund_nav_event_id ?? undefined}>{shortId(factor.fund_nav_event_id)}</td>
              <td title={factor.fund_nav_reinvestment_evidence_id ?? undefined}>{shortId(factor.fund_nav_reinvestment_evidence_id)}</td>
              <td title={factor.previous_fund_nav_adjustment_factor_id ?? undefined}>{shortId(factor.previous_fund_nav_adjustment_factor_id)}</td>
              <td title={factor.factor_logical_key}>{shortId(factor.factor_logical_key)}</td>
            </tr>
          )) : <tr><td colSpan={7}>No factors belong to this projection.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function FactorHistory({ factors }: { factors: FundNavAdjustmentFactor[] }) {
  const [open, setOpen] = useState(false)
  const [limit, setLimit] = useState(100)
  const newestFirst = useMemo(
    () => [...factors].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [factors],
  )
  return (
    <details
      className="fund-nav-history-disclosure"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>All immutable factor records ({factors.length})</summary>
      {open ? (
        <>
          <FactorRows factors={newestFirst.slice(0, limit)} />
          {limit < newestFirst.length ? (
            <button type="button" className="registry-submit secondary" onClick={() => setLimit((value) => value + 100)}>
              Show 100 more
            </button>
          ) : null}
        </>
      ) : null}
    </details>
  )
}

export function FundNavProjectionAudit({ detail }: { detail: FundNavInstrumentDetail }) {
  const currentRun = detail.fund_nav_projection_runs.find(
    (run) => run.fund_nav_projection_run_id === detail.current_fund_nav_projection_run_id,
  ) ?? null
  const runs = [...detail.fund_nav_projection_runs].sort((a, b) => b.created_at.localeCompare(a.created_at))

  return (
    <section className="fund-nav-ledger-section" aria-labelledby={`fund-nav-projection-${detail.instrument_id}`}>
      <div className="registry-detail-header">
        <div>
          <div className="registry-form-title" id={`fund-nav-projection-${detail.instrument_id}`}>Current Total-Return Projection</div>
          <div className="registry-table-meta">Immutable run, input set, and cumulative adjustment-factor lineage</div>
        </div>
        <span className={`coverage-badge coverage-badge-${currentRun?.projection_status ?? 'unavailable'}`}>
          {currentRun?.projection_status ?? 'unavailable'}
        </span>
      </div>
      {currentRun ? (
        <>
          <div className="fund-nav-candidate-facts fund-nav-projection-facts">
            <div><span>Projection</span><strong>{currentRun.projection_kind}</strong></div>
            <div><span>Method</span><strong>{currentRun.method_version}</strong></div>
            <div><span>Anchor</span><strong>{currentRun.anchor_date ?? 'NA'}</strong></div>
            <div><span>Source</span><strong>{currentRun.source_provider}</strong></div>
            <div><span>Created by</span><strong>{currentRun.created_by}</strong><small>{formatTimestamp(currentRun.created_at)}</small></div>
            <div title={currentRun.fund_nav_projection_run_id}><span>Run ID</span><strong>{shortId(currentRun.fund_nav_projection_run_id)}</strong></div>
          </div>
          {currentRun.projection_status === 'unavailable' ? (
            <div className="fund-nav-na-panel" role="status">
              Dividend-reinvested total-return NAV is NA: {evidenceReason(currentRun.evidence)}. Unit NAV remains available and no cash-added pseudo-cumulative value is published.
            </div>
          ) : null}
          <FactorRows factors={[...detail.fund_nav_adjustment_factors].sort((a, b) => a.as_of_date.localeCompare(b.as_of_date))} />
          <details className="fund-nav-source-evidence">
            <summary>Current projection evidence and exact input IDs</summary>
            <pre>{JSON.stringify({
              evidence: currentRun.evidence,
              fund_nav_event_ids: currentRun.fund_nav_event_ids,
              fund_nav_reinvestment_evidence_ids: currentRun.fund_nav_reinvestment_evidence_ids,
              source_observation_fingerprint: currentRun.source_observation_fingerprint,
              input_fingerprint: currentRun.input_fingerprint,
            }, null, 2)}</pre>
          </details>
        </>
      ) : (
        <div className="fund-nav-na-panel">No projection has been published. Unit NAV may remain available; total-return NAV is NA.</div>
      )}
      <details className="fund-nav-history-disclosure">
        <summary>All immutable projection runs ({runs.length})</summary>
        <div className="registry-table-wrap">
          <table className="registry-table registry-table-compact">
            <thead><tr><th>Created</th><th>Status</th><th>Kind</th><th>Anchor</th><th>Method</th><th>Operator</th><th>Run ID</th></tr></thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.fund_nav_projection_run_id}>
                  <td>{formatTimestamp(run.created_at)}</td><td>{run.projection_status}</td><td>{run.projection_kind}</td><td>{run.anchor_date ?? 'NA'}</td><td>{run.method_version}</td><td>{run.created_by}</td><td title={run.fund_nav_projection_run_id}>{shortId(run.fund_nav_projection_run_id)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <FactorHistory factors={detail.fund_nav_adjustment_factor_history} />
    </section>
  )
}

export function FundNavRevisionHistory({ detail }: { detail: FundNavInstrumentDetail }) {
  const actionHistory = [...detail.fund_nav_event_revisions].sort((a, b) => (
    b.effective_date.localeCompare(a.effective_date) || b.revision_number - a.revision_number
  ))
  const evidenceHistory = [...detail.fund_nav_reinvestment_evidence_revisions].sort((a, b) => (
    b.created_at.localeCompare(a.created_at) || b.revision_number - a.revision_number
  ))
  return (
    <section className="fund-nav-ledger-section">
      <div className="registry-form-title">Immutable Administrator Revision History</div>
      <details className="fund-nav-history-disclosure" open>
        <summary>Action revisions ({actionHistory.length})</summary>
        <div className="registry-table-wrap">
          <table className="registry-table registry-table-compact">
            <thead><tr><th>Date</th><th>Action / value</th><th>Revision</th><th>Operator</th><th>Reason</th><th>Source</th><th>Revision ID</th></tr></thead>
            <tbody>
              {actionHistory.length ? actionHistory.map((event) => (
                <tr key={event.fund_nav_event_id}>
                  <td>{event.effective_date}</td>
                  <td>{eventTypeLabel(event.event_type)}<small>{eventValue(event)}</small></td>
                  <td>{revisionKindLabel(event.revision_kind)} #{event.revision_number}<small>supersedes {shortId(event.supersedes_fund_nav_event_id)}</small></td>
                  <td>{event.recorded_by}<small>{formatTimestamp(event.created_at)}</small></td>
                  <td>{event.revision_reason}</td>
                  <td>{event.source}<details className="fund-nav-inline-evidence"><summary>provenance</summary><pre>{JSON.stringify(event.provenance, null, 2)}</pre></details></td>
                  <td title={event.fund_nav_event_id}>{shortId(event.fund_nav_event_id)}</td>
                </tr>
              )) : <tr><td colSpan={7}>No action revisions recorded.</td></tr>}
            </tbody>
          </table>
        </div>
      </details>
      <details className="fund-nav-history-disclosure">
        <summary>Reinvestment evidence revisions ({evidenceHistory.length})</summary>
        <div className="registry-table-wrap">
          <table className="registry-table registry-table-compact">
            <thead><tr><th>NAV</th><th>Revision</th><th>Bound event</th><th>Operator</th><th>Reason</th><th>Source</th><th>Evidence ID</th></tr></thead>
            <tbody>
              {evidenceHistory.length ? evidenceHistory.map((evidence) => (
                <tr key={evidence.fund_nav_reinvestment_evidence_id}>
                  <td>{evidence.reinvestment_nav}</td>
                  <td>{revisionKindLabel(evidence.revision_kind)} #{evidence.revision_number}<small>supersedes {shortId(evidence.supersedes_fund_nav_reinvestment_evidence_id)}</small></td>
                  <td title={evidence.fund_nav_event_id}>{shortId(evidence.fund_nav_event_id)}</td>
                  <td>{evidence.recorded_by}<small>{formatTimestamp(evidence.created_at)}</small></td>
                  <td>{evidence.revision_reason}</td>
                  <td>{evidence.source}<details className="fund-nav-inline-evidence"><summary>provenance</summary><pre>{JSON.stringify(evidence.provenance, null, 2)}</pre></details></td>
                  <td title={evidence.fund_nav_reinvestment_evidence_id}>{shortId(evidence.fund_nav_reinvestment_evidence_id)}</td>
                </tr>
              )) : <tr><td colSpan={7}>No reinvestment evidence revisions recorded.</td></tr>}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  )
}

type ActionEditor = {
  entity: 'action'
  operation: 'create' | 'confirm' | 'correct' | 'cancel'
  mutationId: string
  draft: FundNavActionDraft
  reason: string
  candidateId?: string
  event?: FundNavEventRevision
}

type EvidenceEditor = {
  entity: 'evidence'
  operation: 'add' | 'correct' | 'cancel'
  mutationId: string
  draft: FundNavEvidenceDraft
  reason: string
  event: FundNavEventRevision
  evidence?: FundNavReinvestmentEvidenceRevision
}

type RejectEditor = {
  entity: 'candidate-reject'
  candidateId: string
  reason: string
}

type ReviewEditor = ActionEditor | EvidenceEditor | RejectEditor | null

export function FundNavActionReview({
  instrumentId,
  request,
  onMutationCommitted,
}: {
  instrumentId: string
  request: FundNavActionRequest
  onMutationCommitted: (instrumentId: string) => Promise<void>
}) {
  const [detail, setDetail] = useState<FundNavInstrumentDetail | null>(null)
  const [candidates, setCandidates] = useState<FundNavActionCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [operator, setOperator] = useState('')
  const [editor, setEditor] = useState<ReviewEditor>(null)
  const [submitting, setSubmitting] = useState(false)
  const requestVersionRef = useRef(0)

  const loadReview = useCallback(async () => {
    const requestVersion = ++requestVersionRef.current
    setLoading(true)
    setError(null)
    try {
      const loaded = await loadFundNavReviewData(request, instrumentId)
      if (requestVersion !== requestVersionRef.current) return
      setDetail(loaded.detail)
      setCandidates(loaded.candidates)
    } catch (requestError) {
      if (requestVersion !== requestVersionRef.current) return
      setDetail(null)
      setCandidates([])
      const message = requestError instanceof Error ? requestError.message : 'Failed to load the fund NAV audit ledger.'
      setError(message)
      throw requestError
    } finally {
      if (requestVersion === requestVersionRef.current) setLoading(false)
    }
  }, [instrumentId, request])

  useEffect(() => {
    setDetail(null)
    setCandidates([])
    setEditor(null)
    setNotice(null)
    void loadReview().catch(() => undefined)
    return () => { requestVersionRef.current += 1 }
  }, [loadReview])

  const currentEvidenceByEvent = useMemo(() => new Map(
    (detail?.fund_nav_reinvestment_evidence ?? []).map((item) => [item.fund_nav_event_id, item]),
  ), [detail])

  function updateEditorReason(reason: string) {
    setEditor((current) => current ? { ...current, reason } : current)
  }

  function openCreate() {
    setEditor({ entity: 'action', operation: 'create', mutationId: createMutationId(), draft: emptyFundNavActionDraft(), reason: '' })
    setError(null); setNotice(null)
  }

  function openConfirm(candidateId: string) {
    setEditor({ entity: 'action', operation: 'confirm', candidateId, mutationId: createMutationId(), draft: emptyFundNavActionDraft('cash_distribution'), reason: '' })
    setError(null); setNotice(null)
  }

  function openActionRevision(event: FundNavEventRevision, operation: 'correct' | 'cancel') {
    setEditor({ entity: 'action', operation, event, mutationId: createMutationId(), draft: fundNavEventToDraft(event), reason: '' })
    setError(null); setNotice(null)
  }

  function openEvidenceEditor(event: FundNavEventRevision, evidence?: FundNavReinvestmentEvidenceRevision, operation: 'add' | 'correct' | 'cancel' = 'add') {
    setEditor({
      entity: 'evidence', operation, event, evidence, mutationId: createMutationId(),
      draft: evidence ? fundNavEvidenceToDraft(evidence) : emptyFundNavEvidenceDraft(), reason: '',
    })
    setError(null); setNotice(null)
  }

  async function finishCommittedMutation(message: string) {
    setEditor(null)
    setNotice(message)
    await Promise.all([loadReview(), onMutationCommitted(instrumentId)])
  }

  function committedMutationMessage(
    result: FundNavMutationResponse,
    successMessage: string,
  ) {
    const primary = result.candidate_confirmation_status === 'confirming'
      ? 'The Registry action committed, but the candidate confirmation remains durably reserved. Use Resume Pending Confirmation to finish it.'
      : successMessage
    return result.operational_warnings.length
      ? `${primary} ${result.operational_warnings.join(' ')}`
      : primary
  }

  async function submitAction(event: FormEvent<HTMLFormElement>, active: ActionEditor) {
    event.preventDefault(); setSubmitting(true); setError(null); setNotice(null)
    let committed = false
    try {
      const audit: FundNavAuditDraft = { actor: operator, reason: active.reason }
      const payload = buildFundNavActionPayload({
        draft: active.draft,
        audit,
        mutationId: active.mutationId,
        revisionKind: active.operation === 'create' || active.operation === 'confirm' ? 'original' : active.operation === 'correct' ? 'correction' : 'cancellation',
        supersedesEventId: active.event?.fund_nav_event_id ?? null,
      })
      let result: FundNavMutationResponse
      if (active.operation === 'create') {
        result = await createFundNavAction(request, instrumentId, payload)
      } else if (active.operation === 'confirm') {
        result = await confirmFundNavActionCandidate(request, instrumentId, active.candidateId as string, payload)
      } else {
        result = await reviseFundNavAction(request, instrumentId, active.event?.fund_nav_action_id as string, payload)
      }
      committed = true
      const message = active.operation === 'cancel'
        ? 'Action cancellation was appended; downstream recalculation is durably reconciled from the Registry generation.'
        : active.draft.reinvestmentNav.trim()
          ? 'The evidence-backed action revision was appended; downstream recalculation is durably reconciled from the Registry generation.'
          : 'The action revision was appended. Total-return NAV remains NA wherever reinvestment evidence is missing.'
      await finishCommittedMutation(committedMutationMessage(result, message))
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to write the fund NAV action.'
      setError(committed ? `The immutable revision was committed, but refreshing the view failed: ${message}` : message)
    } finally { setSubmitting(false) }
  }

  async function submitEvidence(event: FormEvent<HTMLFormElement>, active: EvidenceEditor) {
    event.preventDefault(); setSubmitting(true); setError(null); setNotice(null)
    let committed = false
    try {
      const payload = buildFundNavEvidencePayload({
        draft: active.draft,
        audit: { actor: operator, reason: active.reason },
        mutationId: active.mutationId,
        revisionKind: active.operation === 'add' ? 'original' : active.operation === 'correct' ? 'correction' : 'cancellation',
        supersedesEvidenceId: active.evidence?.fund_nav_reinvestment_evidence_id ?? null,
      })
      let result: FundNavMutationResponse
      if (active.operation === 'add') {
        result = await addFundNavReinvestmentEvidence(request, instrumentId, active.event.fund_nav_event_id, payload)
      } else {
        result = await reviseFundNavReinvestmentEvidence(request, instrumentId, active.evidence?.fund_nav_reinvestment_evidence_id as string, payload)
      }
      committed = true
      await finishCommittedMutation(committedMutationMessage(
        result,
        active.operation === 'cancel'
          ? 'Reinvestment evidence cancellation was appended; affected total-return NAV will be NA after recalculation.'
          : 'Reinvestment evidence revision was appended; downstream recalculation is durably reconciled from the Registry generation.',
      ))
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to write reinvestment evidence.'
      setError(committed ? `The immutable evidence revision was committed, but refreshing the view failed: ${message}` : message)
    } finally { setSubmitting(false) }
  }

  async function submitReject(event: FormEvent<HTMLFormElement>, active: RejectEditor) {
    event.preventDefault(); setSubmitting(true); setError(null); setNotice(null)
    let committed = false
    try {
      await rejectFundNavActionCandidate(
        request, instrumentId, active.candidateId,
        buildCandidateRejectPayload({ actor: operator, reason: active.reason }),
      )
      committed = true
      await finishCommittedMutation('The signal was rejected with an immutable operator decision and reason.')
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to reject the candidate.'
      setError(committed ? `The decision was committed, but refreshing the view failed: ${message}` : message)
    } finally { setSubmitting(false) }
  }

  async function resumeCandidateConfirmation(candidateId: string) {
    setSubmitting(true); setError(null); setNotice(null)
    let committed = false
    try {
      const result = await resumeFundNavActionCandidateConfirmation(
        request,
        instrumentId,
        candidateId,
      )
      committed = true
      await finishCommittedMutation(committedMutationMessage(
        result,
        'The reserved candidate confirmation is resolved and downstream recalculation is coordinated.',
      ))
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to resume the candidate confirmation.'
      setError(committed
        ? `The confirmation completed, but refreshing the view failed: ${message}`
        : message)
    } finally { setSubmitting(false) }
  }

  return (
    <section className="registry-detail-section fund-nav-action-review" aria-labelledby={`fund-nav-actions-${instrumentId}`}>
      <div className="registry-detail-header">
        <div>
          <div className="registry-form-title" id={`fund-nav-actions-${instrumentId}`}>Fund NAV Actions &amp; Total-Return Audit</div>
          <div className="registry-table-meta">{loading ? 'Loading exact ledger…' : `${candidates.length} open signal(s)`}</div>
        </div>
        <div className="registry-form-actions">
          <button type="button" className="registry-submit" onClick={openCreate} disabled={loading || submitting}>Record Fund Action</button>
          <button type="button" className="registry-submit secondary" onClick={() => void loadReview().catch(() => undefined)} disabled={loading || submitting}>Reload Ledger</button>
        </div>
      </div>

      <div className="fund-nav-na-panel">
        Canonical fund data publishes only unit NAV and provable dividend-reinvested total-return NAV. Cash-added “cumulative NAV” is never substituted. When reinvestment evidence is absent or invalid, total-return NAV is NA and unit NAV remains available.
      </div>
      <label className="fund-nav-operator-field">
        <span>Current operator identity — required for every confirmation and revision</span>
        <input type="text" value={operator} onChange={(event) => setOperator(event.target.value)} maxLength={320} placeholder="Name or operations account" />
      </label>

      {error ? <div className="registry-error" role="alert">{error}</div> : null}
      {notice ? <div className="registry-notice" role="status">{notice}</div> : null}

      {editor ? (
        <div className="fund-nav-editor-shell">
          <div className="registry-detail-header">
            <div>
              <div className="registry-form-title">
                {editor.entity === 'action'
                  ? editor.operation === 'create' ? 'Record Original Fund Action'
                    : editor.operation === 'confirm' ? 'Confirm Signal as Exact Cash Distribution'
                      : editor.operation === 'correct' ? 'Append Action Correction'
                        : 'Append Action Cancellation'
                  : editor.entity === 'evidence'
                    ? editor.operation === 'add' ? 'Add Reinvestment Evidence'
                      : editor.operation === 'correct' ? 'Append Evidence Correction'
                        : 'Append Evidence Cancellation'
                    : 'Reject Candidate Signal'}
              </div>
              {'mutationId' in editor ? <div className="registry-table-meta">Mutation ID: {editor.mutationId}</div> : null}
            </div>
          </div>
          {editor.entity === 'action' ? (
            <form className="fund-nav-review-form" onSubmit={(event) => void submitAction(event, editor)}>
              {editor.operation === 'cancel' ? (
                <div className="fund-nav-cancellation-warning">
                  This appends a cancellation revision for {shortId(editor.event?.fund_nav_event_id ?? null)}. Historical rows remain immutable; current projections will be rebuilt without this action.
                </div>
              ) : (
                <ActionFactsFields
                  draft={editor.draft}
                  onChange={(draft) => setEditor((current) => current?.entity === 'action' ? { ...current, draft } : current)}
                  lockEventType={editor.operation !== 'create'}
                  allowReinvestmentEvidence
                />
              )}
              <AuditFields actor={operator} reason={editor.reason} onActorChange={setOperator} onReasonChange={updateEditorReason} />
              <div className="registry-form-actions">
                <button type="submit" className={`registry-submit ${editor.operation === 'cancel' ? 'danger' : ''}`} disabled={submitting}>{submitting ? 'Writing…' : 'Append Immutable Revision'}</button>
                <button type="button" className="registry-submit secondary" onClick={() => setEditor(null)} disabled={submitting}>Close</button>
              </div>
            </form>
          ) : editor.entity === 'evidence' ? (
            <form className="fund-nav-review-form" onSubmit={(event) => void submitEvidence(event, editor)}>
              {editor.operation === 'cancel' ? (
                <div className="fund-nav-cancellation-warning">
                  This appends an evidence cancellation. The old evidence remains in history; affected total-return NAV becomes NA unless another valid evidence chain proves it.
                </div>
              ) : <EvidenceFactsFields draft={editor.draft} onChange={(draft) => setEditor((current) => current?.entity === 'evidence' ? { ...current, draft } : current)} />}
              <AuditFields actor={operator} reason={editor.reason} onActorChange={setOperator} onReasonChange={updateEditorReason} />
              <div className="registry-form-actions">
                <button type="submit" className={`registry-submit ${editor.operation === 'cancel' ? 'danger' : ''}`} disabled={submitting}>{submitting ? 'Writing…' : 'Append Immutable Evidence Revision'}</button>
                <button type="button" className="registry-submit secondary" onClick={() => setEditor(null)} disabled={submitting}>Close</button>
              </div>
            </form>
          ) : (
            <form className="fund-nav-review-form" onSubmit={(event) => void submitReject(event, editor)}>
              <AuditFields actor={operator} reason={editor.reason} onActorChange={setOperator} onReasonChange={updateEditorReason} />
              <div className="registry-form-actions">
                <button type="submit" className="registry-submit danger" disabled={submitting}>{submitting ? 'Rejecting…' : 'Reject With Operator Reason'}</button>
                <button type="button" className="registry-submit secondary" onClick={() => setEditor(null)} disabled={submitting}>Close</button>
              </div>
            </form>
          )}
        </div>
      ) : null}

      <section className="fund-nav-ledger-section">
        <div className="registry-detail-header"><div><div className="registry-form-title">Open Evidence Signals</div><div className="registry-table-meta">Signals never invent an event date, amount, or reinvestment NAV</div></div></div>
        {!loading && !candidates.length ? <div className="fund-nav-action-empty">No open NAV action signals for this fund.</div> : null}
        <div className="fund-nav-candidate-list">
          {candidates.map((candidate) => (
            <article className="fund-nav-candidate" key={candidate.fund_nav_action_candidate_id}>
              <FundNavActionCandidateFacts candidate={candidate} />
              <details className="fund-nav-source-evidence"><summary>Source evidence</summary><pre>{JSON.stringify(candidate.source_evidence, null, 2)}</pre></details>
              <div className="registry-form-actions">
                {candidate.status === 'confirming' ? (
                  <button type="button" className="registry-submit" onClick={() => void resumeCandidateConfirmation(candidate.fund_nav_action_candidate_id)} disabled={submitting}>Resume Pending Confirmation</button>
                ) : (
                  <>
                    <button type="button" className="registry-submit secondary" onClick={() => openConfirm(candidate.fund_nav_action_candidate_id)} disabled={submitting}>Confirm Exact Cash Distribution</button>
                    <button type="button" className="registry-submit danger" onClick={() => { setEditor({ entity: 'candidate-reject', candidateId: candidate.fund_nav_action_candidate_id, reason: '' }); setError(null); setNotice(null) }} disabled={submitting}>Reject Signal</button>
                  </>
                )}
              </div>
            </article>
          ))}
        </div>
      </section>

      {detail ? (
        <>
          <section className="fund-nav-ledger-section">
            <div className="registry-detail-header"><div><div className="registry-form-title">Current Effective Actions &amp; Evidence</div><div className="registry-table-meta">Only non-cancelled heads are used by the current projection</div></div></div>
            <div className="fund-nav-current-actions">
              {detail.fund_nav_events.length ? [...detail.fund_nav_events].sort((a, b) => b.effective_date.localeCompare(a.effective_date)).map((action) => {
                const evidence = currentEvidenceByEvent.get(action.fund_nav_event_id)
                return (
                  <article className="fund-nav-current-action" key={action.fund_nav_event_id}>
                    <div className="fund-nav-candidate-facts">
                      <div><span>Effective</span><strong>{action.effective_date}</strong></div>
                      <div><span>Action</span><strong>{eventTypeLabel(action.event_type)}</strong><small>{eventValue(action)}</small></div>
                      <div><span>Sequence</span><strong>{action.sequence_order ?? '—'}</strong></div>
                      <div><span>Current revision</span><strong>#{action.revision_number}</strong><small>{action.recorded_by}: {action.revision_reason}</small></div>
                      <div><span>Reinvestment NAV</span><strong>{evidence?.reinvestment_nav ?? 'NA'}</strong><small>{evidence ? `${evidence.recorded_by}: ${evidence.revision_reason}` : 'No current evidence'}</small></div>
                    </div>
                    <div className="registry-form-actions">
                      <button type="button" className="registry-submit secondary" onClick={() => openActionRevision(action, 'correct')} disabled={submitting}>Correct Action</button>
                      <button type="button" className="registry-submit danger" onClick={() => openActionRevision(action, 'cancel')} disabled={submitting}>Cancel Action</button>
                      {action.event_type === 'cash_distribution' ? evidence ? (
                        <>
                          <button type="button" className="registry-submit secondary" onClick={() => openEvidenceEditor(action, evidence, 'correct')} disabled={submitting}>Correct Reinvestment Evidence</button>
                          <button type="button" className="registry-submit danger" onClick={() => openEvidenceEditor(action, evidence, 'cancel')} disabled={submitting}>Cancel Reinvestment Evidence</button>
                        </>
                      ) : <button type="button" className="registry-submit secondary" onClick={() => openEvidenceEditor(action)} disabled={submitting}>Add Reinvestment Evidence</button> : null}
                    </div>
                  </article>
                )
              }) : <div className="fund-nav-action-empty">No current fund actions.</div>}
            </div>
          </section>
          <FundNavProjectionAudit detail={detail} />
          <FundNavRevisionHistory detail={detail} />
        </>
      ) : null}
    </section>
  )
}
