// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  LANGUAGE_STORAGE_KEY,
  LanguageProvider,
  LanguageSelector,
} from '../../../../packages/ui/src/i18n'

beforeEach(() => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
})

afterEach(() => cleanup())

describe('FCN risk translations', () => {
  it('keeps English source text in English mode and restores it after switching languages', async () => {
    render(
      <LanguageProvider>
        <LanguageSelector />
        <span>Delivery buffer</span>
        <span>10.00% above delivery strike</span>
      </LanguageProvider>,
    )

    expect(screen.getByText('Delivery buffer')).toBeTruthy()
    expect(screen.getByText('10.00% above delivery strike')).toBeTruthy()
    expect(screen.queryByText('距接票价')).toBeNull()

    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
    await waitFor(() => {
      expect(screen.getByText('距接票价')).toBeTruthy()
      expect(screen.getByText('10.00% 高于接票价')).toBeTruthy()
    })

    fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
    await waitFor(() => {
      expect(screen.getByText('Delivery buffer')).toBeTruthy()
      expect(screen.getByText('10.00% above delivery strike')).toBeTruthy()
      expect(screen.queryByText('距接票价')).toBeNull()
    })
  })
})
