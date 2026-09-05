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
        settlement_type: null,
        exercise_style: null,
        exercise_dates: null,
        strike_currency: null,
        settlement_formula: null,
        terms_reference: null,
      },
    })
  })

  it('normalizes FCN Registry references without duplicating contract terms', () => {
    const draft = buildInitialDerivativeContractDraft('fcn', '2026-08-09')
    Object.assign(draft, {
      derivative_contract_id: 'fcn-001',
      contract_name: 'USD FCN 2027',
      fcn_notional: '100000',
      fcn_annual_coupon_rate_pct: '12',
      fcn_issue_date: '2026-08-09',
      fcn_final_observation_date: '2027-02-05',
      fcn_maturity_date: '2027-02-09',
      fcn_issuer: 'Issuer Bank',
      fcn_counterparty: 'Broker Account',
      fcn_underlyings: [
        {
          instrument_id: 'equity-001',
          initial_reference_price: '100',
          strike_level_pct: '100',
          knock_in_level_pct: '70',
          knock_out_level_pct: '105',
          deliverable: true,
        },
        {
          instrument_id: 'equity-002',
          initial_reference_price: '200',
          strike_level_pct: '95',
          knock_in_level_pct: '65',
          knock_out_level_pct: '',
          deliverable: false,
        },
      ],
    })

    const contract = derivativeContractFromDraft(draft)
    expect(contract.contract_type).toBe('fcn')
    if (contract.contract_type !== 'fcn') {
      throw new Error('Expected FCN contract')
    }
    expect(contract.terms.annual_coupon_rate_pct).toBe(12)
    expect(contract.terms.final_observation_date).toBe('2027-02-05')
    expect(contract.terms.underlyings).toEqual([
      {
        instrument_id: 'equity-001',
        initial_reference_price: 100,
        strike_level_pct: 100,
        knock_in_level_pct: 70,
        knock_out_level_pct: 105,
        deliverable: true,
      },
      {
        instrument_id: 'equity-002',
        initial_reference_price: 200,
        strike_level_pct: 95,
        knock_in_level_pct: 65,
        knock_out_level_pct: null,
        deliverable: false,
      },
    ])
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
