import type {
  PortfolioDerivativeContractCreate,
  PortfolioDerivativeContractRecord,
} from './api'

export type FcnUnderlyingDraft = {
  row_id: string
  instrument_id: string
  initial_reference_price: string
  strike_level_pct: string
  knock_in_level_pct: string
  knock_out_level_pct: string
  deliverable: boolean
}

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
  option_settlement_type: '' | 'physical' | 'cash'
  option_exercise_style: '' | 'american' | 'european' | 'bermudan'
  option_exercise_dates: string
  option_strike_currency: string
  option_settlement_formula: string
  terms_reference: string
  fcn_notional: string
  fcn_annual_coupon_rate_pct: string
  fcn_issue_date: string
  fcn_final_observation_date: string
  fcn_maturity_date: string
  fcn_issuer: string
  fcn_counterparty: string
  fcn_underlyings: FcnUnderlyingDraft[]
  fcn_knock_in_observation: '' | 'daily_close' | 'continuous' | 'final_close'
  fcn_knock_out_observation_dates: string
  fcn_coupon_payment_dates: string
  fcn_coupon_day_count: string
  fcn_settlement_type: '' | 'cash' | 'physical' | 'conditional'
  fcn_payoff_description: string
}

function localTodayIso() {
  const now = new Date()
  const timezoneOffsetMs = now.getTimezoneOffset() * 60 * 1000
  return new Date(now.getTime() - timezoneOffsetMs).toISOString().slice(0, 10)
}

let nextUnderlyingRowId = 0

export function buildInitialFcnUnderlyingDraft(): FcnUnderlyingDraft {
  return {
    row_id: `underlying-${++nextUnderlyingRowId}`,
    instrument_id: '',
    initial_reference_price: '',
    strike_level_pct: '',
    knock_in_level_pct: '',
    knock_out_level_pct: '',
    deliverable: false,
  }
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
    option_settlement_type: '',
    option_exercise_style: '',
    option_exercise_dates: '',
    option_strike_currency: '',
    option_settlement_formula: '',
    terms_reference: '',
    fcn_notional: '',
    fcn_annual_coupon_rate_pct: '',
    fcn_issue_date: today,
    fcn_final_observation_date: '',
    fcn_maturity_date: today,
    fcn_issuer: '',
    fcn_counterparty: '',
    fcn_underlyings: [buildInitialFcnUnderlyingDraft()],
    fcn_knock_in_observation: '',
    fcn_knock_out_observation_dates: '',
    fcn_coupon_payment_dates: '',
    fcn_coupon_day_count: '',
    fcn_settlement_type: '',
    fcn_payoff_description: '',
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
  draft.terms_reference = contract.terms.terms_reference || ''
  if (contract.contract_type === 'option') {
    draft.option_underlying_instrument_id = contract.terms.underlying_instrument_id
    draft.option_type = contract.terms.option_type
    draft.option_expiry_date = contract.terms.expiry_date
    draft.option_strike = String(contract.terms.strike)
    draft.option_contract_multiplier = String(contract.terms.contract_multiplier)
    draft.option_settlement_type = contract.terms.settlement_type || ''
    draft.option_exercise_style = contract.terms.exercise_style || ''
    draft.option_exercise_dates = contract.terms.exercise_dates?.join(', ') || ''
    draft.option_strike_currency = contract.terms.strike_currency || ''
    draft.option_settlement_formula = contract.terms.settlement_formula || ''
  } else {
    draft.fcn_notional = String(contract.terms.notional)
    draft.fcn_annual_coupon_rate_pct =
      contract.terms.annual_coupon_rate_pct == null
        ? ''
        : String(contract.terms.annual_coupon_rate_pct)
    draft.fcn_issue_date = contract.terms.issue_date
    draft.fcn_final_observation_date = contract.terms.final_observation_date || ''
    draft.fcn_maturity_date = contract.terms.maturity_date
    draft.fcn_issuer = contract.terms.issuer
    draft.fcn_counterparty = contract.terms.counterparty
    draft.fcn_knock_in_observation = contract.terms.knock_in_observation || ''
    draft.fcn_knock_out_observation_dates = contract.terms.knock_out_observation_dates?.join(', ') || ''
    draft.fcn_coupon_payment_dates = contract.terms.coupon_payment_dates?.join(', ') || ''
    draft.fcn_coupon_day_count = contract.terms.coupon_day_count || ''
    draft.fcn_settlement_type = contract.terms.settlement_type || ''
    draft.fcn_payoff_description = contract.terms.payoff_description || ''
    draft.fcn_underlyings = contract.terms.underlyings.map((underlying) => ({
      row_id: buildInitialFcnUnderlyingDraft().row_id,
      instrument_id: underlying.instrument_id,
      initial_reference_price:
        underlying.initial_reference_price == null
          ? ''
          : String(underlying.initial_reference_price),
      strike_level_pct:
        underlying.strike_level_pct == null
          ? ''
          : String(underlying.strike_level_pct),
      knock_in_level_pct:
        underlying.knock_in_level_pct == null
          ? ''
          : String(underlying.knock_in_level_pct),
      knock_out_level_pct:
        underlying.knock_out_level_pct == null
          ? ''
          : String(underlying.knock_out_level_pct),
      deliverable: underlying.deliverable,
    }))
  }
  return draft
}

function optionalNumber(value: string, label: string, options?: { allowZero?: boolean }) {
  if (!value.trim()) {
    return null
  }
  const parsed = Number(value)
  const valid =
    Number.isFinite(parsed) && (options?.allowZero ? parsed >= 0 : parsed > 0)
  if (!valid) {
    throw new Error(`${label} must be ${options?.allowZero ? 'non-negative' : 'positive'}.`)
  }
  return parsed
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
        settlement_type: draft.option_settlement_type || null,
        exercise_style: draft.option_exercise_style || null,
        exercise_dates: draft.option_exercise_style === 'bermudan'
          ? parseContractDates(draft.option_exercise_dates) : null,
        strike_currency: draft.option_strike_currency.trim().toUpperCase() || null,
        settlement_formula: draft.option_settlement_formula.trim() || null,
        terms_reference: draft.terms_reference.trim() || null,
      },
    }
  }

  const notional = Number(draft.fcn_notional)
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
    draft.fcn_final_observation_date &&
    (draft.fcn_final_observation_date < draft.fcn_issue_date ||
      draft.fcn_final_observation_date > draft.fcn_maturity_date)
  ) {
    throw new Error('FCN final observation must fall between issue and maturity.')
  }
  if (!draft.fcn_issuer.trim() || !draft.fcn_counterparty.trim()) {
    throw new Error('FCN issuer and counterparty are required.')
  }
  if (!draft.fcn_underlyings.length) {
    throw new Error('FCN requires at least one underlying.')
  }

  const instrumentIds = draft.fcn_underlyings.map((item) => item.instrument_id.trim())
  if (instrumentIds.some((instrumentId) => !instrumentId)) {
    throw new Error('Select a registered security for every FCN underlying.')
  }
  if (new Set(instrumentIds).size !== instrumentIds.length) {
    throw new Error('FCN underlyings must be unique.')
  }

  return {
    derivative_contract_id: derivativeContractId,
    contract_name: contractName,
    contract_type: 'fcn',
    external_reference: draft.external_reference.trim() || null,
    terms: {
      notional,
      annual_coupon_rate_pct: optionalNumber(
        draft.fcn_annual_coupon_rate_pct,
        'FCN annual coupon rate',
        { allowZero: true },
      ),
      issue_date: draft.fcn_issue_date,
      final_observation_date: draft.fcn_final_observation_date || null,
      maturity_date: draft.fcn_maturity_date,
      issuer: draft.fcn_issuer.trim(),
      counterparty: draft.fcn_counterparty.trim(),
      knock_in_observation: draft.fcn_knock_in_observation || null,
      knock_out_observation_dates: parseContractDates(draft.fcn_knock_out_observation_dates),
      coupon_payment_dates: parseContractDates(draft.fcn_coupon_payment_dates),
      coupon_day_count: draft.fcn_coupon_day_count.trim() || null,
      settlement_type: draft.fcn_settlement_type || null,
      payoff_description: draft.fcn_payoff_description.trim() || null,
      terms_reference: draft.terms_reference.trim() || null,
      underlyings: draft.fcn_underlyings.map((underlying) => ({
        instrument_id: underlying.instrument_id.trim(),
        initial_reference_price: optionalNumber(
          underlying.initial_reference_price,
          'FCN initial reference price',
        ),
        strike_level_pct: optionalNumber(
          underlying.strike_level_pct,
          'FCN strike level',
        ),
        knock_in_level_pct: optionalNumber(
          underlying.knock_in_level_pct,
          'FCN knock-in level',
        ),
        knock_out_level_pct: optionalNumber(
          underlying.knock_out_level_pct,
          'FCN knock-out level',
        ),
        deliverable: underlying.deliverable,
      })),
    },
  }
}

function parseContractDates(value: string): string[] | null {
  if (!value.trim()) return null
  const dates = value.split(/[,;\s]+/).filter(Boolean)
  if (dates.some((day) => !/^\d{4}-\d{2}-\d{2}$/.test(day) || Number.isNaN(Date.parse(day)))) {
    throw new Error('Contract dates must use YYYY-MM-DD, separated by commas.')
  }
  if (new Set(dates).size !== dates.length) throw new Error('Contract dates must be unique.')
  return dates
}
