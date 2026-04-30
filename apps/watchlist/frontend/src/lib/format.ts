export function formatCompactCurrency(value: unknown) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—'
  }

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatPercent(value: unknown, digits = 2) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—'
  }
  return `${value.toFixed(digits)}%`
}

export function formatNumber(value: unknown, digits = 2) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—'
  }
  return value.toFixed(digits)
}

export function signedValueClass(value: unknown) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
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

export function formatDate(value: unknown) {
  if (typeof value !== 'string' || !value) {
    return '—'
  }
  return value.slice(0, 10)
}

export function formatDateTime(value: unknown) {
  if (typeof value !== 'string' || !value) {
    return '—'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatBoolean(value: unknown) {
  if (typeof value !== 'boolean') {
    return '—'
  }
  return value ? 'Yes' : 'No'
}

export function formatLabel(value: string) {
  return value
    .replace(/^attr\./, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}
