const currencyFormatters = new Map<string, Intl.NumberFormat>()

function getCurrencyFormatter(currency: string, digits = 2) {
  const key = `${currency}:${digits}`
  if (!currencyFormatters.has(key)) {
    currencyFormatters.set(
      key,
      new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency,
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }),
    )
  }

  return currencyFormatters.get(key)!
}

export function formatCurrency(value: number | null | undefined, currency = 'USD', digits = 2) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }

  return getCurrencyFormatter(currency, digits).format(value)
}

export function formatNumber(value: number | null | undefined, digits = 3) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }

  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

export function formatQuantity(value: number | null | undefined) {
  return formatNumber(value, 2)
}

export function formatUnitPrice(value: number | null | undefined, currency = 'USD') {
  return formatCurrency(value, currency, 4)
}

export function formatPercent(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }

  return `${(value * 100).toFixed(digits)}%`
}

export function formatPercentInput(value: number | null | undefined, digits = 4) {
  if (value == null || !Number.isFinite(value)) {
    return ''
  }

  return String(Number((value * 100).toFixed(digits)))
}

export function formatSignedCurrency(value: number | null | undefined, currency = 'USD') {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }

  const absolute = formatCurrency(Math.abs(value), currency)
  return value > 0 ? `+${absolute}` : value < 0 ? `-${absolute}` : absolute
}

export function signedValueClass(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return ''
  }
  if (value > 0) {
    return 'positive-cell'
  }
  if (value < 0) {
    return 'negative-cell'
  }
  return 'neutral-cell'
}

export function formatLabel(value: string) {
  if (value === 'official_nav') {
    return 'Unit NAV'
  }
  if (value === 'total_return_nav') {
    return 'Dividend-Reinvested Total Return NAV'
  }
  const formatted = value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
  return formatted.replace(/\b(Etf|Fx|Nav|Twr|Irr)\b/g, (token) => token.toUpperCase())
}
