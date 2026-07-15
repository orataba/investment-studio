const EXECUTION_QUOTE_REASON_MESSAGES: Record<string, string> = {
  clean_price_requires_matching_accrued_interest:
    'Clean bond price requires matching same-date accrued interest, so no execution quote was applied.',
  bond_price_contract_unavailable:
    'Bond quote is missing a valid percent-of-par unit or scale, so no execution quote was applied.',
  ambiguous_quote_series_identity:
    'Execution quote is unavailable because multiple market-data series match this instrument.',
  incomplete_quote_series_identity:
    'Execution quote is unavailable because its market-data series identity is incomplete.',
  quote_currency_mismatch:
    'Execution quote currency does not match the instrument currency, so no quote was applied.',
  quote_metric_family_mismatch:
    'Execution quote uses an ineligible metric family, so no quote was applied.',
  duplicate_quote_observation:
    'Execution quote is unavailable because the selected market-data series has duplicate observations.',
  quote_price_contract_incomplete:
    'Execution quote is missing a complete price unit and scale contract.',
  ambiguous_quote_price_contract:
    'Execution quote is unavailable because its price unit or scale is ambiguous.',
  no_eligible_execution_quote:
    'No eligible unadjusted execution quote is available on or before this trade date.',
  bond_quote_basis_unavailable:
    'Bond quote basis is not eligible for transaction pricing.',
  quote_policy_unavailable:
    'Execution quote policy is unavailable for this instrument.',
  instrument_currency_unavailable:
    'Execution quote is unavailable because the instrument currency is missing.',
  invalid_quote_observation:
    'Execution quote is unavailable because the market-data observation is invalid.',
}

export function executionQuoteUnavailableMessage(reason: string | null | undefined) {
  const normalizedReason = reason?.trim()
  if (!normalizedReason) {
    return null
  }

  const mappedReason = EXECUTION_QUOTE_REASON_MESSAGES[normalizedReason]
  if (mappedReason) {
    return mappedReason
  }

  if (/^[a-z0-9]+(?:_[a-z0-9]+)+$/.test(normalizedReason)) {
    return `Execution quote unavailable: ${normalizedReason.replace(/_/g, ' ')}.`
  }

  return normalizedReason
}
