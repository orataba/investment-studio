const POSITION_ASSET_TYPES = new Set(['fund', 'etf', 'bond', 'equity', 'fcn', 'option', 'other'])
const LIFECYCLE_EVENT_ASSET_TYPES: Record<string, 'fcn' | 'option'> = {
  fcn_knock_in: 'fcn',
  fcn_knock_out: 'fcn',
  fcn_maturity: 'fcn',
  option_long_expiry: 'option',
  option_long_exercise: 'option',
  option_writer_expiry: 'option',
  option_assignment: 'option',
}
const INCOME_ASSET_TYPES: Record<string, Set<string>> = {
  dividend: new Set(['fund', 'etf', 'equity']),
  dividend_reinvestment: new Set(['fund', 'etf', 'equity']),
  coupon: new Set(['bond', 'fcn']),
  return_of_capital: new Set(['fund', 'etf', 'equity']),
  maturity_redemption: new Set(['bond', 'fcn', 'option']),
  option_write: new Set(['option']),
  option_buy_to_close: new Set(['option']),
}

export function requiredDerivativeContractType(
  transactionType: string,
  lifecycleEventType?: string | null,
): 'fcn' | 'option' | null {
  if (lifecycleEventType) {
    return LIFECYCLE_EVENT_ASSET_TYPES[lifecycleEventType] ?? null
  }
  if (transactionType === 'option_write' || transactionType === 'option_buy_to_close') {
    return 'option'
  }
  if (transactionType === 'coupon') {
    return 'fcn'
  }
  return null
}

export function supportsTransactionAssetType(
  transactionType: string,
  assetType: string,
  lifecycleEventType?: string | null,
) {
  const normalizedAssetType = assetType.trim().toLowerCase()
  if (!normalizedAssetType) {
    return false
  }

  const requiredContractType = requiredDerivativeContractType(
    transactionType,
    lifecycleEventType,
  )
  if (requiredContractType && requiredContractType !== normalizedAssetType) {
    return false
  }

  if (transactionType === 'lifecycle_event') {
    return requiredContractType === normalizedAssetType
  }

  if (
    transactionType === 'buy' ||
    transactionType === 'sell' ||
    transactionType === 'opening_balance'
  ) {
    return POSITION_ASSET_TYPES.has(normalizedAssetType)
  }

  if (transactionType === 'fee' || transactionType === 'tax') {
    return POSITION_ASSET_TYPES.has(normalizedAssetType)
  }

  const allowedInstrumentTypes = INCOME_ASSET_TYPES[transactionType]
  if (!allowedInstrumentTypes) {
    return true
  }
  return allowedInstrumentTypes.has(normalizedAssetType)
}
