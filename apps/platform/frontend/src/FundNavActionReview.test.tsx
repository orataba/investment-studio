import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import {
  FundNavActionCandidateFacts,
  FundNavProjectionAudit,
  FundNavRevisionHistory,
} from './FundNavActionReview'
import {
  addFundNavReinvestmentEvidence,
  buildCandidateRejectPayload,
  buildFundNavActionPayload,
  buildFundNavEvidencePayload,
  confirmFundNavActionCandidate,
  createFundNavAction,
  fundNavActionCandidateConfirmPath,
  fundNavActionCandidateRejectPath,
  fundNavActionCandidateResumePath,
  fundNavActionCandidatesPath,
  fundNavActionRevisionsPath,
  fundNavActionsPath,
  fundNavEventReinvestmentEvidencePath,
  fundNavEvidenceRevisionsPath,
  instrumentDetailPath,
  loadFundNavReviewData,
  resumeFundNavActionCandidateConfirmation,
  reviseFundNavAction,
  reviseFundNavReinvestmentEvidence,
  type FundNavActionCandidate,
  type FundNavActionDraft,
  type FundNavActionRequest,
  type FundNavEventRevision,
  type FundNavInstrumentDetail,
  type FundNavReinvestmentEvidenceRevision,
} from './fundNavActions'

const actionDraft: FundNavActionDraft = {
  eventType: 'cash_distribution',
  announcementDate: '2026-07-09',
  recordDate: '2026-07-10',
  effectiveDate: '2026-07-10',
  payableDate: '2026-07-12',
  sequenceOrder: '2',
  amount: '0.1350',
  evidenceKind: 'provider_notice',
  evidenceSource: 'Manager notice 2026-07-09 / page 2',
  externalEventId: 'notice-42',
  reinvestmentNav: '1.2846',
  reinvestmentEvidenceKind: 'manual_verified',
  reinvestmentEvidenceSource: 'Custodian reinvestment statement / page 4',
  externalReinvestmentEvidenceId: 'custodian-42',
}

const action: FundNavEventRevision = {
  fund_nav_event_id: 'event-v1',
  fund_nav_action_id: 'action-1',
  revision_number: 1,
  revision_kind: 'original',
  supersedes_fund_nav_event_id: null,
  instrument_id: 'fund-1',
  event_type: 'cash_distribution',
  announcement_date: '2026-07-09',
  record_date: '2026-07-10',
  effective_date: '2026-07-10',
  payable_date: '2026-07-12',
  sequence_order: 2,
  cash_per_unit: '0.135',
  unit_ratio: null,
  evidence_kind: 'provider_notice',
  source: 'Manager notice 2026-07-09 / page 2',
  external_event_id: 'notice-42',
  provenance: { notice_sha256: 'abc' },
  recorded_by: 'alice.ops',
  revision_reason: 'Confirmed manager distribution notice',
  created_at: '2026-07-12T12:00:00Z',
  updated_at: '2026-07-12T12:00:00Z',
}

const evidence: FundNavReinvestmentEvidenceRevision = {
  fund_nav_reinvestment_evidence_id: 'evidence-v1',
  instrument_id: 'fund-1',
  fund_nav_event_id: 'event-v1',
  revision_number: 1,
  revision_kind: 'original',
  supersedes_fund_nav_reinvestment_evidence_id: null,
  reinvestment_nav: '1.2846',
  evidence_kind: 'provider_notice',
  source: 'Manager notice 2026-07-09 / page 2',
  external_evidence_id: 'notice-42',
  provenance: { notice_sha256: 'abc' },
  recorded_by: 'alice.ops',
  revision_reason: 'Recorded exact reinvestment price',
  created_at: '2026-07-12T12:00:00Z',
  updated_at: '2026-07-12T12:00:00Z',
}

const candidate: FundNavActionCandidate = {
  fund_nav_action_candidate_id: 'candidate-1',
  instrument_id: 'fund-1',
  candidate_type: 'cash_distribution_signal',
  interval_start_date: '2026-07-09',
  interval_end_date: '2026-07-12',
  observed_cash_balance_before: '1.450000',
  observed_cash_balance_after: '0.050000',
  observed_cash_delta: '-1.400000',
  expected_cash_balance: null,
  measurement_uncertainty: '0.002500',
  status: 'open',
  source_provider: 'mailbox_statement',
  source_revision: 'message-42/attachment-1',
  source_evidence: { message_id: 'message-42', sheet: 'NAV' },
  resolved_fund_nav_event_id: null,
  rejection_reason: null,
  decision_by: null,
  confirmation_client_mutation_id: null,
  confirmation_request_fingerprint: null,
  created_at: '2026-07-12T12:00:00Z',
  updated_at: '2026-07-12T12:00:00Z',
}

const unavailableDetail: FundNavInstrumentDetail = {
  instrument_id: 'fund-1',
  fund_nav_events: [action],
  fund_nav_event_revisions: [action],
  fund_nav_reinvestment_evidence: [],
  fund_nav_reinvestment_evidence_revisions: [evidence],
  current_fund_nav_projection_run_id: 'run-unavailable',
  fund_nav_projection_runs: [{
    fund_nav_projection_run_id: 'run-unavailable',
    instrument_id: 'fund-1',
    input_fingerprint: 'a'.repeat(64),
    source_observation_fingerprint: 'b'.repeat(64),
    projection_kind: 'event_derived',
    projection_status: 'unavailable',
    method_version: 'fund-nav-reinvestment-v2',
    anchor_date: null,
    source_provider: 'mailbox_statement',
    evidence: { unavailable_reason: 'reinvestment_nav_not_observed' },
    created_by: 'projection_worker',
    fund_nav_event_ids: ['event-v1'],
    fund_nav_reinvestment_evidence_ids: [],
    created_at: '2026-07-12T13:00:00Z',
  }],
  fund_nav_adjustment_factors: [],
  fund_nav_adjustment_factor_history: [],
}

describe('fund NAV action mutation builders', () => {
  it('builds a nested original action and explicit reinvestment evidence', () => {
    expect(buildFundNavActionPayload({
      draft: actionDraft,
      audit: { actor: '  alice.ops ', reason: '  Confirmed exact manager notice  ' },
      mutationId: 'mutation-1',
    })).toEqual({
      client_mutation_id: 'mutation-1',
      recorded_by: 'alice.ops',
      revision_reason: 'Confirmed exact manager notice',
      action: {
        event_type: 'cash_distribution',
        announcement_date: '2026-07-09',
        record_date: '2026-07-10',
        effective_date: '2026-07-10',
        payable_date: '2026-07-12',
        sequence_order: 2,
        cash_per_unit: '0.1350',
        evidence_kind: 'provider_notice',
        source: 'Manager notice 2026-07-09 / page 2',
        external_event_id: 'notice-42',
        provenance: { evidence_source: 'Manager notice 2026-07-09 / page 2' },
      },
      reinvestment_evidence: {
        reinvestment_nav: '1.2846',
        evidence_kind: 'manual_verified',
        source: 'Custodian reinvestment statement / page 4',
        external_evidence_id: 'custodian-42',
        provenance: { evidence_source: 'Custodian reinvestment statement / page 4' },
      },
    })
  })

  it('omits reinvestment evidence instead of fabricating total return', () => {
    const payload = buildFundNavActionPayload({
      draft: { ...actionDraft, reinvestmentNav: '   ' },
      audit: { actor: 'alice.ops', reason: 'Action known; reinvestment price absent' },
      mutationId: 'mutation-2',
    })
    expect(payload).not.toHaveProperty('reinvestment_evidence')
  })

  it('builds a full correction and a fact-free cancellation against exact predecessors', () => {
    const correction = buildFundNavActionPayload({
      draft: { ...actionDraft, amount: '0.1400', reinvestmentNav: '' },
      audit: { actor: 'bob.ops', reason: 'Corrected notice amount' },
      mutationId: 'mutation-3',
      revisionKind: 'correction',
      supersedesEventId: 'event-v1',
    })
    expect(correction).toMatchObject({
      revision_kind: 'correction',
      predecessor_fund_nav_event_id: 'event-v1',
      action: { cash_per_unit: '0.1400' },
    })
    expect(correction).not.toHaveProperty('reinvestment_evidence')

    const cancellation = buildFundNavActionPayload({
      draft: { ...actionDraft, effectiveDate: '', amount: '' },
      audit: { actor: 'bob.ops', reason: 'Notice was withdrawn' },
      mutationId: 'mutation-4',
      revisionKind: 'cancellation',
      supersedesEventId: 'event-v1',
    })
    expect(cancellation).toEqual({
      client_mutation_id: 'mutation-4',
      recorded_by: 'bob.ops',
      revision_reason: 'Notice was withdrawn',
      revision_kind: 'cancellation',
      predecessor_fund_nav_event_id: 'event-v1',
    })
  })

  it.each([
    [{ ...actionDraft, effectiveDate: '2026-02-30' }, 'valid effective date'],
    [{ ...actionDraft, amount: '0' }, 'exact positive decimal'],
    [{ ...actionDraft, reinvestmentNav: '1e-3' }, 'exact positive decimal'],
    [{ ...actionDraft, reinvestmentEvidenceSource: '' }, 'exact reinvestment evidence source'],
    [{ ...actionDraft, evidenceKind: '' as const }, 'Select the evidence type'],
    [{ ...actionDraft, evidenceSource: '   ' }, 'exact evidence source'],
    [{ ...actionDraft, sequenceOrder: '0' }, 'positive integer'],
  ])('rejects incomplete or inexact action facts', (draft, message) => {
    expect(() => buildFundNavActionPayload({
      draft,
      audit: { actor: 'alice.ops', reason: 'test reason' },
      mutationId: 'mutation-x',
    })).toThrow(message)
  })

  it('rejects a unit split ratio that does not change unit count', () => {
    expect(() => buildFundNavActionPayload({
      draft: {
        ...actionDraft,
        eventType: 'unit_split',
        amount: '1.000',
        reinvestmentNav: '',
      },
      audit: { actor: 'alice.ops', reason: 'Record split' },
      mutationId: 'mutation-split',
    })).toThrow('cannot equal 1')
  })
})

describe('reinvestment evidence and candidate decisions', () => {
  it('builds nested create, correction, and fact-free cancellation bodies', () => {
    const draft = {
      reinvestmentNav: '1.2846', evidenceKind: 'provider_notice' as const,
      evidenceSource: 'Manager notice page 2', externalEvidenceId: 'notice-42',
    }
    expect(buildFundNavEvidencePayload({
      draft, audit: { actor: 'alice.ops', reason: 'Exact evidence' }, mutationId: 'e-1',
    })).toMatchObject({ evidence: { reinvestment_nav: '1.2846' } })
    expect(buildFundNavEvidencePayload({
      draft, audit: { actor: 'alice.ops', reason: 'Corrected page' }, mutationId: 'e-2',
      revisionKind: 'correction', supersedesEvidenceId: 'evidence-v1',
    })).toMatchObject({
      revision_kind: 'correction',
      predecessor_fund_nav_reinvestment_evidence_id: 'evidence-v1',
      evidence: { reinvestment_nav: '1.2846' },
    })
    expect(buildFundNavEvidencePayload({
      draft: { ...draft, reinvestmentNav: '' },
      audit: { actor: 'alice.ops', reason: 'Provider withdrew evidence' }, mutationId: 'e-3',
      revisionKind: 'cancellation', supersedesEvidenceId: 'evidence-v1',
    })).toEqual({
      client_mutation_id: 'e-3', recorded_by: 'alice.ops',
      revision_reason: 'Provider withdrew evidence', revision_kind: 'cancellation',
      predecessor_fund_nav_reinvestment_evidence_id: 'evidence-v1',
    })
  })

  it('requires and normalizes the rejecting operator and reason', () => {
    expect(buildCandidateRejectPayload({ actor: ' alice.ops ', reason: ' Duplicate row ' })).toEqual({
      decision_by: 'alice.ops', reason: 'Duplicate row',
    })
    expect(() => buildCandidateRejectPayload({ actor: ' ', reason: 'Duplicate' })).toThrow('operator identity')
    expect(() => buildCandidateRejectPayload({ actor: 'alice', reason: ' ' })).toThrow('reason for rejecting')
  })
})

describe('clean NAV administration request contracts', () => {
  it('encodes every path segment and exposes no legacy confirm-event path', () => {
    expect(instrumentDetailPath('fund/a b')).toBe('/api/instruments/fund%2Fa%20b')
    expect(fundNavActionCandidatesPath('fund/a b')).toBe('/api/instruments/fund%2Fa%20b/nav-action-candidates')
    expect(fundNavActionCandidateConfirmPath('fund/a b', 'signal/1?')).toBe('/api/instruments/fund%2Fa%20b/nav-action-candidates/signal%2F1%3F/confirm')
    expect(fundNavActionCandidateRejectPath('fund/a b', 'signal/1?')).toBe('/api/instruments/fund%2Fa%20b/nav-action-candidates/signal%2F1%3F/reject')
    expect(fundNavActionCandidateResumePath('fund/a b', 'signal/1?')).toBe('/api/instruments/fund%2Fa%20b/nav-action-candidates/signal%2F1%3F/resume-confirmation')
    expect(fundNavActionsPath('fund/a b')).toBe('/api/instruments/fund%2Fa%20b/fund-nav-actions')
    expect(fundNavActionRevisionsPath('fund/a b', 'action/1')).toBe('/api/instruments/fund%2Fa%20b/fund-nav-actions/action%2F1/revisions')
    expect(fundNavEventReinvestmentEvidencePath('fund/a b', 'event/1')).toBe('/api/instruments/fund%2Fa%20b/fund-nav-events/event%2F1/reinvestment-evidence')
    expect(fundNavEvidenceRevisionsPath('fund/a b', 'evidence/1')).toBe('/api/instruments/fund%2Fa%20b/fund-nav-reinvestment-evidence/evidence%2F1/revisions')
  })

  it('uses the canonical detail, action, evidence, and candidate endpoints', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = []
    const request: FundNavActionRequest = async <T,>(path: string, init?: RequestInit) => {
      calls.push({ path, init })
      if (path === '/api/instruments/fund-1') return unavailableDetail as T
      if (path.endsWith('/nav-action-candidates') && !init) return [candidate] as T
      return {} as T
    }
    const createPayload = buildFundNavActionPayload({
      draft: { ...actionDraft, reinvestmentNav: '' },
      audit: { actor: 'alice', reason: 'record' }, mutationId: 'm-1',
    })
    const revisionPayload = buildFundNavActionPayload({
      draft: { ...actionDraft, reinvestmentNav: '' },
      audit: { actor: 'alice', reason: 'correct' }, mutationId: 'm-2',
      revisionKind: 'correction', supersedesEventId: 'event-v1',
    })
    const evidencePayload = buildFundNavEvidencePayload({
      draft: { reinvestmentNav: '1.2', evidenceKind: 'provider_notice', evidenceSource: 'notice', externalEvidenceId: '' },
      audit: { actor: 'alice', reason: 'evidence' }, mutationId: 'm-3',
    })
    await loadFundNavReviewData(request, 'fund-1')
    await createFundNavAction(request, 'fund-1', createPayload)
    await confirmFundNavActionCandidate(request, 'fund-1', 'candidate-1', createPayload)
    await resumeFundNavActionCandidateConfirmation(request, 'fund-1', 'candidate-1')
    await reviseFundNavAction(request, 'fund-1', 'action-1', revisionPayload)
    await addFundNavReinvestmentEvidence(request, 'fund-1', 'event-v1', evidencePayload)
    await reviseFundNavReinvestmentEvidence(request, 'fund-1', 'evidence-v1', {
      client_mutation_id: 'm-4', recorded_by: 'alice', revision_reason: 'cancel',
      revision_kind: 'cancellation', predecessor_fund_nav_reinvestment_evidence_id: 'evidence-v1',
    })

    expect(calls.map((call) => call.path)).toEqual([
      '/api/instruments/fund-1',
      '/api/instruments/fund-1/nav-action-candidates',
      '/api/instruments/fund-1/fund-nav-actions',
      '/api/instruments/fund-1/nav-action-candidates/candidate-1/confirm',
      '/api/instruments/fund-1/nav-action-candidates/candidate-1/resume-confirmation',
      '/api/instruments/fund-1/fund-nav-actions/action-1/revisions',
      '/api/instruments/fund-1/fund-nav-events/event-v1/reinvestment-evidence',
      '/api/instruments/fund-1/fund-nav-reinvestment-evidence/evidence-v1/revisions',
    ])
    expect(calls.slice(2).every((call) => call.init?.method === 'POST')).toBe(true)
  })
})

describe('fund NAV audit rendering', () => {
  it('renders candidate observations without guessing event facts', () => {
    const markup = renderToStaticMarkup(<FundNavActionCandidateFacts candidate={candidate} />)
    expect(markup).toContain('2026-07-09')
    expect(markup).toContain('-1.400000')
    expect(markup).toContain('± 0.002500')
    expect(markup).toContain('Not available')
    expect(markup).toContain('message-42/attachment-1')
  })

  it('makes a durable pending confirmation visible with its mutation identity', () => {
    const markup = renderToStaticMarkup(
      <FundNavActionCandidateFacts candidate={{
        ...candidate,
        status: 'confirming',
        confirmation_client_mutation_id: 'mutation-pending-1',
        confirmation_request_fingerprint: 'a'.repeat(64),
      }} />,
    )
    expect(markup).toContain('Confirmation reserved')
    expect(markup).toContain('mutation-pending-1')
  })

  it('shows an unavailable projection as NA and never as a cash-added cumulative NAV', () => {
    const markup = renderToStaticMarkup(<FundNavProjectionAudit detail={unavailableDetail} />)
    expect(markup).toContain('reinvestment_nav_not_observed')
    expect(markup).toContain('total-return NAV is NA')
    expect(markup).toContain('no cash-added pseudo-cumulative value is published')
    expect(markup).toContain('fund-nav-reinvestment-v2')
  })

  it('renders immutable administrator actor, reason, and evidence history', () => {
    const markup = renderToStaticMarkup(<FundNavRevisionHistory detail={unavailableDetail} />)
    expect(markup).toContain('alice.ops')
    expect(markup).toContain('Confirmed manager distribution notice')
    expect(markup).toContain('Recorded exact reinvestment price')
    expect(markup).toContain('notice_sha256')
  })
})
