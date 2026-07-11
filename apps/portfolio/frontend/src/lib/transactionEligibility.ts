const POSITION_ASSET_TYPES = new Set(['fund', 'etf', 'bond', 'equity', 'other'])
const INCOME_ASSET_TYPES: Record<string, Set<string>> = {
  dividend: new Set(['fund', 'etf', 'equity']),
  dividend_reinvestment: new Set(['fund', 'etf', 'equity']),
  coupon: new Set(['bond']),
  return_of_capital: new Set(['fund', 'etf', 'equity']),
  maturity_redemption: new Set(['bond']),
}

export function supportsTransactionInstrumentType(transactionType: string, instrumentType: string) {
  const normalizedInstrumentType = instrumentType.trim().toLowerCase()
  if (!normalizedInstrumentType) {
    return false
  }

  if (transactionType === 'buy' || transactionType === 'sell' || transactionType === 'opening_balance') {
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
