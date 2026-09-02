// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { LanguageProvider, LanguageSelector } from '../../../../packages/ui/src/i18n'

afterEach(() => cleanup())

describe('DOM translation context', () => {
  it('distinguishes a close-dialog action from a market close label', async () => {
    render(
      <LanguageProvider>
        <LanguageSelector />
        <button type="button">Close</button>
        <span>Close</span>
      </LanguageProvider>,
    )

    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '关闭' })).toBeTruthy()
      expect(screen.getByText('收盘价')).toBeTruthy()
    })

    fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy()
      expect(screen.getByText('Close', { selector: 'span' })).toBeTruthy()
    })
  })
})
