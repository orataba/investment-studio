const POSITION_ASSET_TYPES = new Set(['fund', 'etf', 'bond', 'equity', 'fcn', 'option', 'other'])
const INCOME_ASSET_TYPES: Record<string, Set<string>> = {
  dividend: new Set(['fund', 'etf', 'equity']),
  dividend_reinvestment: new Set(['fund', 'etf', 'equity']),
  coupon: new Set(['bond', 'fcn']),
  return_of_capital: new Set(['fund', 'etf', 'equity']),
  maturity_redemption: new Set(['bond', 'fcn', 'option']),
  option_write: new Set(['option']),
  option_buy_to_close: new Set(['option']),
}

export function supportsTransactionInstrumentType(transactionType: string, instrumentType: string) {
  const normalizedInstrumentType = instrumentType.trim().toLowerCase()
  if (!normalizedInstrumentType) {
    return false
  }

  if (transactionType === 'lifecycle_event') {
    return normalizedInstrumentType === 'option'
  }

  if (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'opening_balance'
  ) {
    return POSITION_ASSET_TYPES.has(normalizedInstrumentType)
  }

  if (transactionType === 'fee' || transactionType === 'tax') {
    return POSITION_ASSET_TYPES.has(normalizedInstrumentType)
  }

  const allowedInstrumentTypes = INCOME_ASSET_TYPES[transactionType]
  if (!allowedInstrumentTypes) {
    return true
  }
  return allowedInstrumentTypes.has(normalizedInstrumentType)
}
