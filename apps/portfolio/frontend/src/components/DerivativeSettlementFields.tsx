import type { Dispatch, SetStateAction } from 'react'
import type { DerivativeContractDraft } from '../lib/derivativeContractDraft'
import type { PortfolioDerivativeContractRecord } from '../lib/api'
import { formatLabel } from '../lib/format'
import { useLanguage } from '../../../../../packages/ui/src/i18n'

export function DerivativeSettlementFacts({ contract }: { contract: PortfolioDerivativeContractRecord }) {
  const { t } = useLanguage()
  const terms = contract.terms
  const values: [string, string | null | undefined][] = [
    ['Contract Settlement', terms.settlement_type ? t(formatLabel(terms.settlement_type)) : t('Unconfirmed — verify the contract')],
    ['Contract Evidence', terms.terms_reference],
  ]
  if (contract.contract_type === 'option') {
    values.push(
      ['Exercise Style', contract.terms.exercise_style ? t(formatLabel(contract.terms.exercise_style)) : t('Unconfirmed')],
      ['Exercise Dates', contract.terms.exercise_dates?.join(', ')],
      ['Strike Currency', contract.terms.strike_currency],
      ['Cash Settlement Formula', contract.terms.settlement_formula],
    )
  } else {
    values.push(
      ['Knock-In Observation', contract.terms.knock_in_observation ? t(formatLabel(contract.terms.knock_in_observation)) : t('Unconfirmed')],
      ['Knock-Out Observation Dates', contract.terms.knock_out_observation_dates?.join(', ')],
      ['Coupon Payment Dates', contract.terms.coupon_payment_dates?.join(', ')],
      ['Coupon Day Count', contract.terms.coupon_day_count],
      ['Redemption Terms', contract.terms.payoff_description],
    )
  }
  return <>{values.map(([label, value]) => <div key={label}><dt>{t(label)}</dt><dd translate="no">{value || '—'}</dd></div>)}</>
}

export function DerivativeSettlementFields({ draft, setDraft }: {
  draft: DerivativeContractDraft
  setDraft: Dispatch<SetStateAction<DerivativeContractDraft>>
}) {
  const option = draft.contract_type === 'option'
  const textFields: [keyof DerivativeContractDraft, string, string][] = option
    ? [
        ['option_strike_currency', 'Strike Currency', 'Currency of the exercise price'],
        ['option_settlement_formula', 'Cash Settlement Formula', 'Settlement price source, multiplier and any FX conversion'],
        ...(draft.option_exercise_style === 'bermudan'
          ? [['option_exercise_dates', 'Exercise Dates', 'YYYY-MM-DD, YYYY-MM-DD'] as [keyof DerivativeContractDraft, string, string]] : []),
      ]
    : [
        ['fcn_knock_out_observation_dates', 'Knock-Out Observation Dates', 'YYYY-MM-DD, YYYY-MM-DD'],
        ['fcn_coupon_payment_dates', 'Coupon Payment Dates', 'YYYY-MM-DD, YYYY-MM-DD'],
        ['fcn_coupon_day_count', 'Coupon Day Count', 'For example ACT/365 or 30/360, per term sheet'],
        ['fcn_payoff_description', 'Redemption Terms', 'Worst-of rule, cash or stock delivery, fractional cash and settlement currency'],
      ]
  return <>
    <label className="transaction-ticket-field">
      <span>Contract Settlement</span>
      <select value={option ? draft.option_settlement_type : draft.fcn_settlement_type}
        onChange={(event) => setDraft((current) => option
          ? { ...current, option_settlement_type: event.target.value as DerivativeContractDraft['option_settlement_type'] }
          : { ...current, fcn_settlement_type: event.target.value as DerivativeContractDraft['fcn_settlement_type'] })}>
        <option value="">Unconfirmed — verify the contract</option>
        <option value="physical">Physical delivery</option>
        <option value="cash">Cash settlement</option>
        {!option && <option value="conditional">Conditional cash / physical delivery</option>}
      </select>
    </label>
    {option ? <label className="transaction-ticket-field">
      <span>Exercise Style</span>
      <select value={draft.option_exercise_style}
        onChange={(event) => setDraft((current) => ({ ...current, option_exercise_style: event.target.value as DerivativeContractDraft['option_exercise_style'] }))}>
        <option value="">Unconfirmed</option>
        <option value="american">American</option>
        <option value="european">European</option>
        <option value="bermudan">Bermudan</option>
      </select>
    </label> : <label className="transaction-ticket-field">
      <span>Knock-In Observation</span>
      <select value={draft.fcn_knock_in_observation}
        onChange={(event) => setDraft((current) => ({ ...current, fcn_knock_in_observation: event.target.value as DerivativeContractDraft['fcn_knock_in_observation'] }))}>
        <option value="">Unconfirmed</option>
        <option value="daily_close">Daily close</option>
        <option value="continuous">Continuous</option>
        <option value="final_close">Final observation only</option>
      </select>
    </label>}
    {textFields.map(([key, label, placeholder]) => <label className="transaction-ticket-field" key={key}>
      <span>{label}</span>
      <input value={String(draft[key])} placeholder={placeholder}
        onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))} />
    </label>)}
    <label className="transaction-ticket-field">
      <span>Contract Evidence</span>
      <input value={draft.terms_reference} placeholder="Term sheet, broker confirmation or document reference"
        onChange={(event) => setDraft((current) => ({ ...current, terms_reference: event.target.value }))} />
    </label>
  </>
}
