import { describe, expect, it } from 'vitest'

import taxonomiesSource from './pages/TaxonomiesPage.tsx?raw'
import transactionsSource from './pages/TransactionsPage.tsx?raw'

describe('destructive confirmations', () => {
  const nativeConfirmCall = ['window', 'confirm('].join('.')

  it.each([
    ['TransactionsPage.tsx', transactionsSource],
    ['TaxonomiesPage.tsx', taxonomiesSource],
  ])('does not use a native window.confirm in %s', (_filename, source) => {
      expect(source).not.toContain(nativeConfirmCall)
      expect(source).toContain('<ConfirmDialog')
  })
})
