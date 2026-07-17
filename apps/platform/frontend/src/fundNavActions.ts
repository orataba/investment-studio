export type FundNavEventType = 'cash_distribution' | 'unit_split'
export type FundNavRevisionKind = 'original' | 'correction' | 'cancellation'
export type FundNavEvidenceKind = '' | 'provider_notice' | 'manual_verified'

export type FundNavActionCandidate = {
  fund_nav_action_candidate_id: string
  instrument_id: string
  candidate_type: 'cash_distribution_signal' | 'cash_balance_discontinuity'
  interval_start_date: string
  interval_end_date: string
  observed_cash_balance_before: string
  observed_cash_balance_after: string
  observed_cash_delta: string
  expected_cash_balance: string | null
  measurement_uncertainty: string
  status: 'open' | 'confirming' | 'superseded' | 'resolved' | 'rejected'
  source_provider: string
  source_revision: string
  source_evidence: Record<string, unknown>
  resolved_fund_nav_event_id: string | null
  rejection_reason: string | null
  decision_by: string | null
  confirmation_client_mutation_id: string | null
  confirmation_request_fingerprint: string | null
  created_at: string
  updated_at: string
}

export type FundNavEventRevision = {
  fund_nav_event_id: string
  fund_nav_action_id: string
  revision_number: number
  revision_kind: FundNavRevisionKind
  supersedes_fund_nav_event_id: string | null
  instrument_id: string
  event_type: FundNavEventType
  announcement_date: string | null
  record_date: string | null
  effective_date: string
  payable_date: string | null
  sequence_order: number | null
  cash_per_unit: string | null
  unit_ratio: string | null
  evidence_kind: Exclude<FundNavEvidenceKind, ''>
  source: string
  external_event_id: string | null
  provenance: Record<string, unknown>
  recorded_by: string
  revision_reason: string
  created_at: string
  updated_at: string
}

export type FundNavReinvestmentEvidenceRevision = {
  fund_nav_reinvestment_evidence_id: string
  instrument_id: string
  fund_nav_event_id: string
  revision_number: number
  revision_kind: FundNavRevisionKind
  supersedes_fund_nav_reinvestment_evidence_id: string | null
  reinvestment_nav: string
  evidence_kind: Exclude<FundNavEvidenceKind, ''>
  source: string
  external_evidence_id: string | null
  provenance: Record<string, unknown>
  recorded_by: string
  revision_reason: string
  created_at: string
  updated_at: string
}

export type FundNavProjectionRun = {
  fund_nav_projection_run_id: string
  instrument_id: string
  input_fingerprint: string
  source_observation_fingerprint: string
  projection_kind: 'provider_explicit' | 'event_derived' | 'hybrid_reanchored'
  projection_status: 'complete' | 'partial' | 'unavailable'
  method_version: string
  anchor_date: string | null
  source_provider: string
  evidence: Record<string, unknown>
  created_by: string
  fund_nav_event_ids: string[]
  fund_nav_reinvestment_evidence_ids: string[]
  created_at: string
}

export type FundNavAdjustmentFactor = {
  fund_nav_adjustment_factor_id: string
  factor_logical_key: string
  instrument_id: string
  fund_nav_projection_run_id: string
  as_of_date: string
  factor_level: string
  factor_kind: 'provider_implied' | 'event_derived'
  fund_nav_event_id: string | null
  fund_nav_reinvestment_evidence_id: string | null
  previous_fund_nav_adjustment_factor_id: string | null
  evidence_kind:
    | 'provider_total_return'
    | 'fund_nav_event'
    | 'zero_cash_anchor'
    | 'window_normalized_anchor'
  method_version: string
  anchor_date: string
  source_provider: string
  evidence: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type FundNavInstrumentDetail = {
  instrument_id: string
  fund_nav_events: FundNavEventRevision[]
  fund_nav_event_revisions: FundNavEventRevision[]
  fund_nav_reinvestment_evidence: FundNavReinvestmentEvidenceRevision[]
  fund_nav_reinvestment_evidence_revisions: FundNavReinvestmentEvidenceRevision[]
  fund_nav_projection_runs: FundNavProjectionRun[]
  current_fund_nav_projection_run_id: string | null
  fund_nav_adjustment_factors: FundNavAdjustmentFactor[]
  fund_nav_adjustment_factor_history: FundNavAdjustmentFactor[]
}

export type FundNavActionDraft = {
  eventType: FundNavEventType
  announcementDate: string
  recordDate: string
  effectiveDate: string
  payableDate: string
  sequenceOrder: string
  amount: string
  evidenceKind: FundNavEvidenceKind
  evidenceSource: string
  externalEventId: string
  reinvestmentNav: string
  reinvestmentEvidenceKind: FundNavEvidenceKind
  reinvestmentEvidenceSource: string
  externalReinvestmentEvidenceId: string
}

export type FundNavEvidenceDraft = {
  reinvestmentNav: string
  evidenceKind: FundNavEvidenceKind
  evidenceSource: string
  externalEvidenceId: string
}

export type FundNavAuditDraft = {
  actor: string
  reason: string
}

export type FundNavActionFacts = {
  event_type: FundNavEventType
  announcement_date?: string
  record_date?: string
  effective_date: string
  payable_date?: string
  sequence_order?: number
  cash_per_unit?: string
  unit_ratio?: string
  evidence_kind: Exclude<FundNavEvidenceKind, ''>
  source: string
  external_event_id?: string
  provenance: { evidence_source: string }
}

export type FundNavReinvestmentEvidenceFacts = {
  reinvestment_nav: string
  evidence_kind: Exclude<FundNavEvidenceKind, ''>
  source: string
  external_evidence_id?: string
  provenance: { evidence_source: string }
}

type FundNavMutationAudit = {
  client_mutation_id: string
  recorded_by: string
  revision_reason: string
}

export type FundNavActionCreatePayload = FundNavMutationAudit & {
  action: FundNavActionFacts
  reinvestment_evidence?: FundNavReinvestmentEvidenceFacts
}

export type FundNavActionRevisionPayload = FundNavMutationAudit & {
  revision_kind: 'correction' | 'cancellation'
  predecessor_fund_nav_event_id: string
  action?: FundNavActionFacts
  reinvestment_evidence?: FundNavReinvestmentEvidenceFacts
}

export type FundNavActionPayload =
  | FundNavActionCreatePayload
  | FundNavActionRevisionPayload

export type FundNavEvidenceCreatePayload = FundNavMutationAudit & {
  evidence: FundNavReinvestmentEvidenceFacts
}

export type FundNavEvidenceRevisionPayload = FundNavMutationAudit & {
  revision_kind: 'correction' | 'cancellation'
  predecessor_fund_nav_reinvestment_evidence_id: string
  evidence?: FundNavReinvestmentEvidenceFacts
}

export type FundNavReinvestmentEvidencePayload =
  | FundNavEvidenceCreatePayload
  | FundNavEvidenceRevisionPayload

export type FundNavMutationResponse = {
  changed: boolean
  dirty_from: string | null
  candidate_confirmation_status: 'confirming' | 'resolved' | null
  candidate_projection_synchronized: boolean
  operational_warnings: string[]
}

export type FundNavActionRequest = <T>(path: string, init?: RequestInit) => Promise<T>

const POSITIVE_DECIMAL_PATTERN = /^(?:0|[1-9]\d*)(?:\.\d+)?$/
const POSITIVE_INTEGER_PATTERN = /^[1-9]\d*$/
const MAX_SOURCE_LENGTH = 1024
const MAX_REASON_LENGTH = 4096
const MAX_ACTOR_LENGTH = 320

export function createMutationId() {
  return crypto.randomUUID()
}

export function emptyFundNavActionDraft(
  eventType: FundNavEventType = 'cash_distribution',
): FundNavActionDraft {
  return {
    eventType,
    announcementDate: '',
    recordDate: '',
    effectiveDate: '',
    payableDate: '',
    sequenceOrder: '',
    amount: '',
    evidenceKind: '',
    evidenceSource: '',
    externalEventId: '',
    reinvestmentNav: '',
    reinvestmentEvidenceKind: '',
    reinvestmentEvidenceSource: '',
    externalReinvestmentEvidenceId: '',
  }
}

export function emptyFundNavEvidenceDraft(): FundNavEvidenceDraft {
  return {
    reinvestmentNav: '',
    evidenceKind: '',
    evidenceSource: '',
    externalEvidenceId: '',
  }
}

export function fundNavEventToDraft(event: FundNavEventRevision): FundNavActionDraft {
  return {
    eventType: event.event_type,
    announcementDate: event.announcement_date ?? '',
    recordDate: event.record_date ?? '',
    effectiveDate: event.effective_date,
    payableDate: event.payable_date ?? '',
    sequenceOrder: event.sequence_order === null ? '' : String(event.sequence_order),
    amount: event.event_type === 'cash_distribution'
      ? event.cash_per_unit ?? ''
      : event.unit_ratio ?? '',
    evidenceKind: event.evidence_kind,
    evidenceSource: event.source,
    externalEventId: event.external_event_id ?? '',
    reinvestmentNav: '',
    reinvestmentEvidenceKind: '',
    reinvestmentEvidenceSource: '',
    externalReinvestmentEvidenceId: '',
  }
}

export function fundNavEvidenceToDraft(
  evidence: FundNavReinvestmentEvidenceRevision,
): FundNavEvidenceDraft {
  return {
    reinvestmentNav: evidence.reinvestment_nav,
    evidenceKind: evidence.evidence_kind,
    evidenceSource: evidence.source,
    externalEvidenceId: evidence.external_evidence_id ?? '',
  }
}

function requireIsoDate(value: string, label: string) {
  const normalized = value.trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized)) {
    throw new Error(`Enter an exact ${label}.`)
  }
  const parsed = new Date(`${normalized}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== normalized) {
    throw new Error(`Enter a valid ${label}.`)
  }
  return normalized
}

function optionalIsoDate(value: string, label: string) {
  return value.trim() ? requireIsoDate(value, label) : undefined
}

function requirePositiveDecimal(value: string, label: string) {
  const normalized = value.trim()
  if (!POSITIVE_DECIMAL_PATTERN.test(normalized) || !/[1-9]/.test(normalized)) {
    throw new Error(`${label} must be an exact positive decimal.`)
  }
  return normalized
}

function requireNonUnitRatio(value: string) {
  const ratio = requirePositiveDecimal(value, 'Unit split ratio')
  if (/^1(?:\.0+)?$/.test(ratio)) {
    throw new Error('Unit split ratio must change the unit count and cannot equal 1.')
  }
  return ratio
}

function requireEvidenceKind(value: FundNavEvidenceKind) {
  if (value !== 'provider_notice' && value !== 'manual_verified') {
    throw new Error('Select the evidence type used for this record.')
  }
  return value
}

function requireBoundedText(value: string, label: string, maxLength: number) {
  const normalized = value.trim()
  if (!normalized) {
    throw new Error(`Enter ${label}.`)
  }
  if (normalized.length > maxLength) {
    throw new Error(`${label} cannot exceed ${maxLength} characters.`)
  }
  return normalized
}

function normalizeAudit(audit: FundNavAuditDraft) {
  return {
    actor: requireBoundedText(audit.actor, 'the operator identity', MAX_ACTOR_LENGTH),
    reason: requireBoundedText(audit.reason, 'the revision reason', MAX_REASON_LENGTH),
  }
}

function normalizedOptionalText(value: string, label: string, maxLength: number) {
  const normalized = value.trim()
  if (normalized.length > maxLength) {
    throw new Error(`${label} cannot exceed ${maxLength} characters.`)
  }
  return normalized || undefined
}

function validateDateOrder(draft: FundNavActionDraft, effectiveDate: string) {
  const announcementDate = optionalIsoDate(draft.announcementDate, 'announcement date')
  const recordDate = optionalIsoDate(draft.recordDate, 'record date')
  const payableDate = optionalIsoDate(draft.payableDate, 'payable date')
  if (recordDate && recordDate > effectiveDate) {
    throw new Error('Record date cannot be after effective date.')
  }
  return { announcementDate, recordDate, payableDate }
}

export function buildFundNavActionPayload({
  draft,
  audit,
  mutationId,
  revisionKind = 'original',
  supersedesEventId = null,
}: {
  draft: FundNavActionDraft
  audit: FundNavAuditDraft
  mutationId: string
  revisionKind?: FundNavRevisionKind
  supersedesEventId?: string | null
}): FundNavActionPayload {
  const { actor, reason } = normalizeAudit(audit)
  const mutationAudit: FundNavMutationAudit = {
    client_mutation_id: requireBoundedText(mutationId, 'the mutation ID', 200),
    recorded_by: actor,
    revision_reason: reason,
  }
  if (revisionKind === 'cancellation') {
    if (!supersedesEventId?.trim()) {
      throw new Error('A cancellation must identify the revision it supersedes.')
    }
    return {
      ...mutationAudit,
      revision_kind: 'cancellation',
      predecessor_fund_nav_event_id: supersedesEventId,
    }
  }
  const effectiveDate = requireIsoDate(draft.effectiveDate, 'effective date')
  const { announcementDate, recordDate, payableDate } = validateDateOrder(draft, effectiveDate)
  const evidenceKind = requireEvidenceKind(draft.evidenceKind)
  const source = requireBoundedText(draft.evidenceSource, 'the exact evidence source', MAX_SOURCE_LENGTH)
  const externalEventId = normalizedOptionalText(
    draft.externalEventId,
    'External event ID',
    MAX_SOURCE_LENGTH,
  )
  const sequenceOrder = draft.sequenceOrder.trim()
  if (sequenceOrder && !POSITIVE_INTEGER_PATTERN.test(sequenceOrder)) {
    throw new Error('Sequence order must be a positive integer.')
  }
  if (revisionKind === 'original' && supersedesEventId !== null) {
    throw new Error('An original action cannot supersede an event revision.')
  }
  if (revisionKind === 'correction' && !supersedesEventId?.trim()) {
    throw new Error('A correction must identify the revision it supersedes.')
  }

  const action: FundNavActionFacts = {
    event_type: draft.eventType,
    ...(announcementDate ? { announcement_date: announcementDate } : {}),
    ...(recordDate ? { record_date: recordDate } : {}),
    effective_date: effectiveDate,
    ...(payableDate ? { payable_date: payableDate } : {}),
    ...(sequenceOrder ? { sequence_order: Number(sequenceOrder) } : {}),
    ...(draft.eventType === 'cash_distribution'
      ? { cash_per_unit: requirePositiveDecimal(draft.amount, 'Cash distribution per unit') }
      : { unit_ratio: requireNonUnitRatio(draft.amount) }),
    evidence_kind: evidenceKind,
    source,
    ...(externalEventId ? { external_event_id: externalEventId } : {}),
    provenance: { evidence_source: source },
  }

  let reinvestmentEvidence: FundNavReinvestmentEvidenceFacts | undefined
  const reinvestmentNav = draft.reinvestmentNav.trim()
  if (reinvestmentNav) {
    if (draft.eventType !== 'cash_distribution') {
      throw new Error('Reinvestment NAV can only be attached to a cash distribution.')
    }
    const reinvestmentSource = requireBoundedText(
      draft.reinvestmentEvidenceSource,
      'the exact reinvestment evidence source',
      MAX_SOURCE_LENGTH,
    )
    const externalReinvestmentEvidenceId = normalizedOptionalText(
      draft.externalReinvestmentEvidenceId,
      'External reinvestment evidence ID',
      MAX_SOURCE_LENGTH,
    )
    reinvestmentEvidence = {
      reinvestment_nav: requirePositiveDecimal(reinvestmentNav, 'Reinvestment NAV'),
      evidence_kind: requireEvidenceKind(draft.reinvestmentEvidenceKind),
      source: reinvestmentSource,
      ...(externalReinvestmentEvidenceId
        ? { external_evidence_id: externalReinvestmentEvidenceId }
        : {}),
      provenance: { evidence_source: reinvestmentSource },
    }
  }
  if (revisionKind === 'correction') {
    return {
      ...mutationAudit,
      revision_kind: 'correction',
      predecessor_fund_nav_event_id: supersedesEventId as string,
      action,
      ...(reinvestmentEvidence ? { reinvestment_evidence: reinvestmentEvidence } : {}),
    }
  }
  return {
    ...mutationAudit,
    action,
    ...(reinvestmentEvidence ? { reinvestment_evidence: reinvestmentEvidence } : {}),
  }
}

export function buildFundNavEvidencePayload({
  draft,
  audit,
  mutationId,
  revisionKind = 'original',
  supersedesEvidenceId = null,
}: {
  draft: FundNavEvidenceDraft
  audit: FundNavAuditDraft
  mutationId: string
  revisionKind?: FundNavRevisionKind
  supersedesEvidenceId?: string | null
}): FundNavReinvestmentEvidencePayload {
  if (revisionKind === 'original' && supersedesEvidenceId !== null) {
    throw new Error('Original reinvestment evidence cannot supersede another revision.')
  }
  if (revisionKind !== 'original' && !supersedesEvidenceId?.trim()) {
    throw new Error('A correction or cancellation must identify the evidence it supersedes.')
  }
  const { actor, reason } = normalizeAudit(audit)
  const mutationAudit: FundNavMutationAudit = {
    client_mutation_id: requireBoundedText(mutationId, 'the mutation ID', 200),
    recorded_by: actor,
    revision_reason: reason,
  }
  if (revisionKind === 'cancellation') {
    return {
      ...mutationAudit,
      revision_kind: 'cancellation',
      predecessor_fund_nav_reinvestment_evidence_id: supersedesEvidenceId as string,
    }
  }
  const source = requireBoundedText(draft.evidenceSource, 'the exact evidence source', MAX_SOURCE_LENGTH)
  const externalEvidenceId = normalizedOptionalText(
    draft.externalEvidenceId,
    'External evidence ID',
    MAX_SOURCE_LENGTH,
  )
  const evidence: FundNavReinvestmentEvidenceFacts = {
    reinvestment_nav: requirePositiveDecimal(draft.reinvestmentNav, 'Reinvestment NAV'),
    evidence_kind: requireEvidenceKind(draft.evidenceKind),
    source,
    ...(externalEvidenceId ? { external_evidence_id: externalEvidenceId } : {}),
    provenance: { evidence_source: source },
  }
  if (revisionKind === 'correction') {
    return {
      ...mutationAudit,
      revision_kind: 'correction',
      predecessor_fund_nav_reinvestment_evidence_id: supersedesEvidenceId as string,
      evidence,
    }
  }
  return { ...mutationAudit, evidence }
}

export function normalizeRejectionReason(value: string) {
  return requireBoundedText(value, 'a reason for rejecting this signal', MAX_REASON_LENGTH)
}

export function buildCandidateRejectPayload({
  actor,
  reason,
}: {
  actor: string
  reason: string
}) {
  return {
    decision_by: requireBoundedText(actor, 'the operator identity', MAX_ACTOR_LENGTH),
    reason: normalizeRejectionReason(reason),
  }
}

function instrumentBasePath(instrumentId: string) {
  return `/api/instruments/${encodeURIComponent(instrumentId)}`
}

export function instrumentDetailPath(instrumentId: string) {
  return instrumentBasePath(instrumentId)
}

export function fundNavActionCandidatesPath(instrumentId: string) {
  return `${instrumentBasePath(instrumentId)}/nav-action-candidates`
}

export function fundNavActionCandidateConfirmPath(instrumentId: string, candidateId: string) {
  return `${fundNavActionCandidatesPath(instrumentId)}/${encodeURIComponent(candidateId)}/confirm`
}

export function fundNavActionCandidateRejectPath(instrumentId: string, candidateId: string) {
  return `${fundNavActionCandidatesPath(instrumentId)}/${encodeURIComponent(candidateId)}/reject`
}

export function fundNavActionCandidateResumePath(instrumentId: string, candidateId: string) {
  return `${fundNavActionCandidatesPath(instrumentId)}/${encodeURIComponent(candidateId)}/resume-confirmation`
}

export function fundNavActionsPath(instrumentId: string) {
  return `${instrumentBasePath(instrumentId)}/fund-nav-actions`
}

export function fundNavActionRevisionsPath(instrumentId: string, actionId: string) {
  return `${fundNavActionsPath(instrumentId)}/${encodeURIComponent(actionId)}/revisions`
}

export function fundNavEventReinvestmentEvidencePath(instrumentId: string, eventId: string) {
  return `${instrumentBasePath(instrumentId)}/fund-nav-events/${encodeURIComponent(eventId)}/reinvestment-evidence`
}

export function fundNavEvidenceRevisionsPath(instrumentId: string, evidenceId: string) {
  return `${instrumentBasePath(instrumentId)}/fund-nav-reinvestment-evidence/${encodeURIComponent(evidenceId)}/revisions`
}

function postJson<T>(request: FundNavActionRequest, path: string, payload: object) {
  return request<T>(path, { method: 'POST', body: JSON.stringify(payload) })
}

export function loadFundNavReviewData(request: FundNavActionRequest, instrumentId: string) {
  return Promise.all([
    request<FundNavInstrumentDetail>(instrumentDetailPath(instrumentId)),
    request<FundNavActionCandidate[]>(fundNavActionCandidatesPath(instrumentId)),
  ]).then(([detail, candidates]) => ({ detail, candidates }))
}

export function createFundNavAction(
  request: FundNavActionRequest,
  instrumentId: string,
  payload: FundNavActionPayload,
) {
  return postJson<FundNavMutationResponse>(request, fundNavActionsPath(instrumentId), payload)
}

export function reviseFundNavAction(
  request: FundNavActionRequest,
  instrumentId: string,
  actionId: string,
  payload: FundNavActionPayload,
) {
  return postJson<FundNavMutationResponse>(request, fundNavActionRevisionsPath(instrumentId, actionId), payload)
}

export function confirmFundNavActionCandidate(
  request: FundNavActionRequest,
  instrumentId: string,
  candidateId: string,
  payload: FundNavActionPayload,
) {
  return postJson<FundNavMutationResponse>(
    request,
    fundNavActionCandidateConfirmPath(instrumentId, candidateId),
    payload,
  )
}

export function resumeFundNavActionCandidateConfirmation(
  request: FundNavActionRequest,
  instrumentId: string,
  candidateId: string,
) {
  return postJson<FundNavMutationResponse>(
    request,
    fundNavActionCandidateResumePath(instrumentId, candidateId),
    {},
  )
}

export function rejectFundNavActionCandidate(
  request: FundNavActionRequest,
  instrumentId: string,
  candidateId: string,
  payload: { decision_by: string; reason: string },
) {
  return postJson<FundNavActionCandidate>(
    request,
    fundNavActionCandidateRejectPath(instrumentId, candidateId),
    payload,
  )
}

export function addFundNavReinvestmentEvidence(
  request: FundNavActionRequest,
  instrumentId: string,
  eventId: string,
  payload: FundNavReinvestmentEvidencePayload,
) {
  return postJson<FundNavMutationResponse>(
    request,
    fundNavEventReinvestmentEvidencePath(instrumentId, eventId),
    payload,
  )
}

export function reviseFundNavReinvestmentEvidence(
  request: FundNavActionRequest,
  instrumentId: string,
  evidenceId: string,
  payload: FundNavReinvestmentEvidencePayload,
) {
  return postJson<FundNavMutationResponse>(
    request,
    fundNavEvidenceRevisionsPath(instrumentId, evidenceId),
    payload,
  )
}
