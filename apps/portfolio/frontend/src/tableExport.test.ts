import { describe, expect, it } from 'vitest'

import { csvEscape, sanitizeSpreadsheetText } from '../../../../packages/ui/src/tableExport'
import { formatPercent } from './lib/format'

describe('spreadsheet exports', () => {
  it.each(['=1+1', '+cmd', '-cmd', '@SUM(A1)', '  =HYPERLINK("https://example.com")'])(
    'neutralizes formula-like text: %s',
    (value) => {
      expect(sanitizeSpreadsheetText(value)).toBe(`'${value}`)
    },
  )

  it('keeps numeric cells numeric while escaping unsafe string cells', () => {
    expect(csvEscape(-12.5)).toBe('-12.5')
    expect(csvEscape('=1,2')).toBe('"\'=1,2"')
  })
})

describe('signed performance formatting', () => {
  it('preserves a negative return', () => {
    expect(formatPercent(-0.0123)).toContain('-1.23%')
  })
})
