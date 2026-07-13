const currencyFormatters = new Map<string, Intl.NumberFormat>()

export type NumericValue = number | string

export function toFiniteNumber(
  value: NumericValue | null | undefined,
): number | null {
  if (value == null || value === '') {
    return null
  }
  const resolved = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(resolved) ? resolved : null
}

function getCurrencyFormatter(currency: string, digits = 2) {
  const normalizedCurrency = currency.trim().toUpperCase()
  if (!/^[A-Z]{3}$/.test(normalizedCurrency)) {
    return null
  }
  const key = `${normalizedCurrency}:${digits}`
  if (!currencyFormatters.has(key)) {
    try {
      currencyFormatters.set(
        key,
        new Intl.NumberFormat('en-US', {
          style: 'currency',
          currency: normalizedCurrency,
          minimumFractionDigits: digits,
          maximumFractionDigits: digits,
        }),
      )
    } catch {
      return null
    }
  }

  return currencyFormatters.get(key)!
}

export function formatCurrency(
  value: NumericValue | null | undefined,
  currency: string | null | undefined,
  digits = 2,
) {
  const resolved = toFiniteNumber(value)
  if (resolved == null || !currency) {
    return '—'
  }

  return getCurrencyFormatter(currency, digits)?.format(resolved) ?? '—'
}

export function formatNumber(value: NumericValue | null | undefined, digits = 3) {
  const resolved = toFiniteNumber(value)
  if (resolved == null) {
    return '—'
  }

  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(resolved)
}

export function formatQuantity(value: NumericValue | null | undefined) {
  return formatNumber(value, 2)
}

export function formatUnitPrice(value: NumericValue | null | undefined, currency: string | null | undefined) {
  return formatCurrency(value, currency, 4)
}

export function formatPercent(value: number | null | undefined, digits = 2) {
  if (value == null || !Number.isFinite(value)) {
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

export function formatSignedCurrency(value: NumericValue | null | undefined, currency: string | null | undefined) {
  const resolved = toFiniteNumber(value)
  if (resolved == null) {
    return '—'
  }

  const absolute = formatCurrency(Math.abs(resolved), currency)
  if (absolute === '—') {
    return '—'
  }
  return resolved > 0 ? `+${absolute}` : resolved < 0 ? `-${absolute}` : absolute
}

export function signedValueClass(value: NumericValue | null | undefined) {
  const resolved = toFiniteNumber(value)
  if (resolved == null) {
    return ''
  }
  if (resolved > 0) {
    return 'positive-cell'
  }
  if (resolved < 0) {
    return 'negative-cell'
  }
  return 'neutral-cell'
}

export function formatLabel(value: string) {
  const formatted = value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
  return formatted.replace(/\b(Etf|Fx|Nav|Twr|Irr)\b/g, (token) => token.toUpperCase())
}
