import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it } from 'vitest'
import { LANGUAGE_STORAGE_KEY, LanguageProvider, LanguageSelector } from '../../../../../packages/ui/src/i18n'
import CorrelationMatrixView from './CorrelationMatrixView'

it('localizes the synthetic unassigned group while preserving an identically named user category', async () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-Hans')
  const matrix = {
    groups: [
      { key: 'unassigned:taxonomy-1', label: 'Unassigned', observationCount: 22, weight: 0.4 },
      { key: 'user-category', label: 'Unassigned', observationCount: 22, weight: 0.6 },
    ],
    cells: [[{ value: 1, observationCount: 22 }, { value: 0.3, observationCount: 22 }],
      [{ value: 0.3, observationCount: 22 }, { value: 1, observationCount: 22 }]],
    maxAbs: 1,
  }
  render(<LanguageProvider><LanguageSelector /><CorrelationMatrixView matrix={matrix} emptyLabel="No matrix." /></LanguageProvider>)
  await waitFor(() => expect(screen.getAllByRole('button', { name: '未归类' })).toHaveLength(2))
  expect(screen.getAllByRole('button', { name: 'Unassigned' })).toHaveLength(2)
  fireEvent.focus(screen.getAllByRole('button', { name: '未归类' })[0])
  expect(screen.getByText('未归类', { selector: '.risk-matrix-identity-detail' })).toBeVisible()
  expect(screen.getByTitle('未归类 × Unassigned · 22 个共同观测')).toBeVisible()
  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Unassigned' })).toHaveLength(4))
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Unassigned' })).toHaveLength(2))
})
