import { describe, expect, it } from 'vitest'

import {
  buildInitialDerivativeContractDraft,
  derivativeContractDraftFromRecord,
  derivativeContractFromDraft,
} from './lib/derivativeContractDraft'

describe('derivative contract form mapping', () => {
  it('builds a canonical option contract payload', () => {
    const draft = buildInitialDerivativeContractDraft('option', '2026-08-09')
    Object.assign(draft, {
      derivative_contract_id: 'option-001',
      contract_name: 'Covered Call Aug 2026',
      external_reference: 'broker-option-001',
      option_underlying_instrument_id: 'equity-001',
      option_type: 'call',
      option_expiry_date: '2026-08-28',
      option_strike: '125.50',
      option_contract_multiplier: '100',
    })

    expect(derivativeContractFromDraft(draft)).toEqual({
      derivative_contract_id: 'option-001',
      contract_name: 'Covered Call Aug 2026',
      contract_type: 'option',
      external_reference: 'broker-option-001',
      terms: {
        underlying_instrument_id: 'equity-001',
        option_type: 'call',
        expiry_date: '2026-08-28',
        strike: 125.5,
        contract_multiplier: 100,
      },
    })
  })

  it('normalizes FCN Registry references without duplicating contract terms', () => {
    const draft = buildInitialDerivativeContractDraft('fcn', '2026-08-09')
    Object.assign(draft, {
      derivative_contract_id: 'fcn-001',
      contract_name: 'USD FCN 2027',
      fcn_notional: '100000',
      fcn_issue_date: '2026-08-09',
      fcn_maturity_date: '2027-02-09',
      fcn_issuer: 'Issuer Bank',
      fcn_counterparty: 'Broker Account',
      fcn_underlying_instrument_ids: 'equity-001; equity-002 ;equity-003',
      fcn_deliverable_instrument_ids: 'equity-001; equity-002',
      fcn_barrier_type: 'knock_in',
      fcn_barrier_level: '70',
    })

    const contract = derivativeContractFromDraft(draft)
    expect(contract.contract_type).toBe('fcn')
    if (contract.contract_type !== 'fcn') {
      throw new Error('Expected FCN contract')
    }
    expect(contract.terms.underlying_instrument_ids).toEqual([
      'equity-001',
      'equity-002',
      'equity-003',
    ])
    expect(contract.terms.deliverable_instrument_ids).toEqual([
      'equity-001',
      'equity-002',
    ])
    expect(contract.terms.barrier_level).toBe(70)
  })

  it('round-trips persisted option terms into an immutable form draft', () => {
    const draft = derivativeContractDraftFromRecord({
      derivative_contract_id: 'option-002',
      portfolio_id: '1',
      account_id: 'broker-1',
      contract_name: 'Put Sep 2026',
      contract_type: 'option',
      currency: 'USD',
      external_reference: null,
      created_at: '2026-08-09T00:00:00Z',
      terms: {
        underlying_instrument_id: 'equity-004',
        option_type: 'put',
        expiry_date: '2026-09-25',
        strike: 90,
        contract_multiplier: 100,
      },
    })

    expect(draft.option_underlying_instrument_id).toBe('equity-004')
    expect(draft.option_type).toBe('put')
    expect(draft.option_strike).toBe('90')
    expect(draft.option_contract_multiplier).toBe('100')
  })
})
