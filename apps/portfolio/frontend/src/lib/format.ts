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

export function formatSignedCurrency(value: number | null | undefined, currency = 'USD') {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }

  const absolute = formatCurrency(Math.abs(value), currency)
  return value > 0 ? `+${absolute}` : value < 0 ? `-${absolute}` : absolute
}

export function formatLabel(value: string) {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}
