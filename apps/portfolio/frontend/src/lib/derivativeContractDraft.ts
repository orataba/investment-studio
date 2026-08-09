import type {
  PortfolioDerivativeContractCreate,
  PortfolioDerivativeContractRecord,
} from './api'

export type DerivativeContractDraft = {
  derivative_contract_id: string
  contract_name: string
  contract_type: 'fcn' | 'option'
  external_reference: string
  option_underlying_instrument_id: string
  option_type: 'call' | 'put'
  option_expiry_date: string
  option_strike: string
  option_contract_multiplier: string
  option_settlement_type: 'physical' | 'cash'
  fcn_notional: string
  fcn_issue_date: string
  fcn_maturity_date: string
  fcn_issuer: string
  fcn_counterparty: string
  fcn_underlying_instrument_ids: string
  fcn_deliverable_instrument_ids: string
  fcn_barrier_type: 'none' | 'knock_in' | 'knock_out' | 'dual'
  fcn_barrier_level: string
}

function localTodayIso() {
  const now = new Date()
  const timezoneOffsetMs = now.getTimezoneOffset() * 60 * 1000
  return new Date(now.getTime() - timezoneOffsetMs).toISOString().slice(0, 10)
}

export function buildInitialDerivativeContractDraft(
  contractType: 'fcn' | 'option' = 'option',
  today = localTodayIso(),
): DerivativeContractDraft {
  return {
    derivative_contract_id: '',
    contract_name: '',
    contract_type: contractType,
    external_reference: '',
    option_underlying_instrument_id: '',
    option_type: 'call',
    option_expiry_date: today,
    option_strike: '',
    option_contract_multiplier: '100',
    option_settlement_type: 'physical',
    fcn_notional: '',
    fcn_issue_date: today,
    fcn_maturity_date: today,
    fcn_issuer: '',
    fcn_counterparty: '',
    fcn_underlying_instrument_ids: '',
    fcn_deliverable_instrument_ids: '',
    fcn_barrier_type: 'none',
    fcn_barrier_level: '',
  }
}

export function derivativeContractDraftFromRecord(
  contract: PortfolioDerivativeContractRecord | null,
): DerivativeContractDraft {
  if (!contract) {
    return buildInitialDerivativeContractDraft()
  }
  const draft = buildInitialDerivativeContractDraft(contract.contract_type)
  draft.derivative_contract_id = contract.derivative_contract_id
  draft.contract_name = contract.contract_name
  draft.external_reference = contract.external_reference || ''
  if (contract.contract_type === 'option') {
    draft.option_underlying_instrument_id = contract.terms.underlying_instrument_id
    draft.option_type = contract.terms.option_type
    draft.option_expiry_date = contract.terms.expiry_date
    draft.option_strike = String(contract.terms.strike)
    draft.option_contract_multiplier = String(contract.terms.contract_multiplier)
    draft.option_settlement_type = contract.terms.settlement_type
  } else {
    draft.fcn_notional = String(contract.terms.notional)
    draft.fcn_issue_date = contract.terms.issue_date
    draft.fcn_maturity_date = contract.terms.maturity_date
    draft.fcn_issuer = contract.terms.issuer
    draft.fcn_counterparty = contract.terms.counterparty
    draft.fcn_underlying_instrument_ids = contract.terms.underlying_instrument_ids.join('; ')
    draft.fcn_deliverable_instrument_ids = contract.terms.deliverable_instrument_ids.join('; ')
    draft.fcn_barrier_type = contract.terms.barrier_type
    draft.fcn_barrier_level = contract.terms.barrier_level == null ? '' : String(contract.terms.barrier_level)
  }
  return draft
}

function parseInstrumentIds(value: string) {
  return value
    .split(';')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function derivativeContractFromDraft(
  draft: DerivativeContractDraft,
): PortfolioDerivativeContractCreate {
  const derivativeContractId = draft.derivative_contract_id.trim()
  const contractName = draft.contract_name.trim()
  if (!derivativeContractId || !contractName) {
    throw new Error('New derivative contracts require an id and name.')
  }

  if (draft.contract_type === 'option') {
    const strike = Number(draft.option_strike)
    const contractMultiplier = Number(draft.option_contract_multiplier)
    if (!draft.option_underlying_instrument_id || !draft.option_expiry_date) {
      throw new Error('Option underlying and expiry date are required.')
    }
    if (
      !Number.isFinite(strike) ||
      strike <= 0 ||
      !Number.isFinite(contractMultiplier) ||
      contractMultiplier <= 0
    ) {
      throw new Error('Option strike and contract multiplier must be positive.')
    }
    return {
      derivative_contract_id: derivativeContractId,
      contract_name: contractName,
      contract_type: 'option',
      external_reference: draft.external_reference.trim() || null,
      terms: {
        underlying_instrument_id: draft.option_underlying_instrument_id,
        option_type: draft.option_type,
        expiry_date: draft.option_expiry_date,
        strike,
        contract_multiplier: contractMultiplier,
        settlement_type: draft.option_settlement_type,
      },
    }
  }

  const notional = Number(draft.fcn_notional)
  const barrierLevel = draft.fcn_barrier_level.trim()
    ? Number(draft.fcn_barrier_level)
    : null
  const underlyingInstrumentIds = parseInstrumentIds(
    draft.fcn_underlying_instrument_ids,
  )
  if (!Number.isFinite(notional) || notional <= 0) {
    throw new Error('FCN notional must be positive.')
  }
  if (
    !draft.fcn_issue_date ||
    !draft.fcn_maturity_date ||
    draft.fcn_maturity_date < draft.fcn_issue_date
  ) {
    throw new Error('FCN maturity must be on or after its issue date.')
  }
  if (
    !draft.fcn_issuer.trim() ||
    !draft.fcn_counterparty.trim() ||
    !underlyingInstrumentIds.length
  ) {
    throw new Error('FCN issuer, counterparty, and at least one underlying are required.')
  }
  if (
    (draft.fcn_barrier_type === 'none' && barrierLevel !== null) ||
    (draft.fcn_barrier_type !== 'none' &&
      (barrierLevel === null || !Number.isFinite(barrierLevel) || barrierLevel <= 0))
  ) {
    throw new Error('FCN barrier level must match the selected barrier type.')
  }
  return {
    derivative_contract_id: derivativeContractId,
    contract_name: contractName,
    contract_type: 'fcn',
    external_reference: draft.external_reference.trim() || null,
    terms: {
      notional,
      issue_date: draft.fcn_issue_date,
      maturity_date: draft.fcn_maturity_date,
      issuer: draft.fcn_issuer.trim(),
      counterparty: draft.fcn_counterparty.trim(),
      underlying_instrument_ids: underlyingInstrumentIds,
      deliverable_instrument_ids: parseInstrumentIds(
        draft.fcn_deliverable_instrument_ids,
      ),
      barrier_type: draft.fcn_barrier_type,
      barrier_level: barrierLevel,
    },
  }
}
