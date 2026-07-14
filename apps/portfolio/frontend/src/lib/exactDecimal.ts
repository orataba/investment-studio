/**
 * Minimal exact arithmetic for canonical decimal strings received from the
 * calculation API. Financial facts remain strings until all additions finish;
 * conversion to a JavaScript number happens only at the final display boundary.
 */

const CANONICAL_DECIMAL_TEXT = /^(0|-?([1-9][0-9]*(\.[0-9]*[1-9])?|0\.[0-9]*[1-9]))$/
const PLAIN_DECIMAL_TEXT = /^[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/

type ParsedDecimal = {
  coefficient: bigint
  scale: number
}

function parseCanonicalDecimal(value: string): ParsedDecimal {
  if (!CANONICAL_DECIMAL_TEXT.test(value)) {
    throw new Error(`Invalid canonical decimal: ${value}`)
  }
  if (value === '0') {
    return { coefficient: 0n, scale: 0 }
  }
  const negative = value.startsWith('-')
  const unsigned = negative ? value.slice(1) : value
  const [integerPart, fractionalPart = ''] = unsigned.split('.')
  const coefficient = BigInt(`${integerPart}${fractionalPart}`)
  return {
    coefficient: negative ? -coefficient : coefficient,
    scale: fractionalPart.length,
  }
}

function parsePlainDecimal(value: string): ParsedDecimal {
  if (!PLAIN_DECIMAL_TEXT.test(value)) {
    throw new Error(`Invalid plain decimal: ${value}`)
  }
  const negative = value.startsWith('-')
  const unsigned = value.startsWith('-') || value.startsWith('+') ? value.slice(1) : value
  const [integerPart, fractionalPart = ''] = unsigned.split('.')
  const coefficient = BigInt(`${integerPart}${fractionalPart}`)
  return {
    coefficient: negative ? -coefficient : coefficient,
    scale: fractionalPart.length,
  }
}

function formatCanonicalDecimal(coefficient: bigint, scale: number): string {
  if (coefficient === 0n) {
    return '0'
  }
  const negative = coefficient < 0n
  const digits = (negative ? -coefficient : coefficient).toString().padStart(scale + 1, '0')
  if (scale === 0) {
    return `${negative ? '-' : ''}${digits}`
  }
  const integerPart = digits.slice(0, -scale)
  const fractionalPart = digits.slice(-scale).replace(/0+$/, '')
  return `${negative ? '-' : ''}${integerPart}${fractionalPart ? `.${fractionalPart}` : ''}`
}

export function exactDecimalSum(values: readonly string[]): string {
  let coefficient = 0n
  let scale = 0
  values.forEach((rawValue) => {
    const value = parseCanonicalDecimal(rawValue)
    if (value.scale > scale) {
      coefficient *= 10n ** BigInt(value.scale - scale)
      scale = value.scale
    }
    coefficient += value.coefficient * 10n ** BigInt(scale - value.scale)
  })
  return formatCanonicalDecimal(coefficient, scale)
}

export function exactDecimalMultiply(left: string, right: string): string {
  const leftValue = parsePlainDecimal(left)
  const rightValue = parsePlainDecimal(right)
  return formatCanonicalDecimal(
    leftValue.coefficient * rightValue.coefficient,
    leftValue.scale + rightValue.scale,
  )
}

function halfEvenQuotient(numerator: bigint, denominator: bigint): bigint {
  if (denominator <= 0n || numerator < 0n) {
    throw new Error('Half-even quotient requires a non-negative numerator and positive denominator.')
  }
  const quotient = numerator / denominator
  const remainder = numerator % denominator
  const doubledRemainder = remainder * 2n
  if (doubledRemainder > denominator || (doubledRemainder === denominator && quotient % 2n !== 0n)) {
    return quotient + 1n
  }
  return quotient
}

export function exactDecimalQuantizeHalfEven(value: string, scale: number): string {
  if (!Number.isSafeInteger(scale) || scale < 0) {
    throw new Error('Decimal scale must be a non-negative safe integer.')
  }
  const parsed = parsePlainDecimal(value)
  if (parsed.scale <= scale) {
    return formatCanonicalDecimal(parsed.coefficient, parsed.scale)
  }
  const divisor = 10n ** BigInt(parsed.scale - scale)
  const negative = parsed.coefficient < 0n
  const magnitude = negative ? -parsed.coefficient : parsed.coefficient
  const rounded = halfEvenQuotient(magnitude, divisor)
  return formatCanonicalDecimal(negative ? -rounded : rounded, scale)
}

export function exactDecimalDivideHalfEven(
  numerator: string,
  denominator: string,
  scale: number,
): string {
  if (!Number.isSafeInteger(scale) || scale < 0) {
    throw new Error('Decimal scale must be a non-negative safe integer.')
  }
  const left = parsePlainDecimal(numerator)
  const right = parsePlainDecimal(denominator)
  if (right.coefficient === 0n) {
    throw new Error('Cannot divide by zero.')
  }
  const negative = (left.coefficient < 0n) !== (right.coefficient < 0n)
  const leftMagnitude = left.coefficient < 0n ? -left.coefficient : left.coefficient
  const rightMagnitude = right.coefficient < 0n ? -right.coefficient : right.coefficient
  const scaledNumerator = leftMagnitude * 10n ** BigInt(right.scale + scale)
  const scaledDenominator = rightMagnitude * 10n ** BigInt(left.scale)
  const rounded = halfEvenQuotient(scaledNumerator, scaledDenominator)
  return formatCanonicalDecimal(negative ? -rounded : rounded, scale)
}

export function exactDecimalToDisplayNumber(value: string): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    throw new Error('Exact decimal is outside the browser display range.')
  }
  return parsed
}
