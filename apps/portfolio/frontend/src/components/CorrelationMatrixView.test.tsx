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


it('selects a full asset pair with keyboard arrows, preserves zero, and resets stale identities', () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
  const groups = ['Long Alpha name / USD', 'Long Beta name / CNY'].map((label, index) => ({ key: String(index), label, observationCount: 41, weight: .5 }))
  const matrix = { groups, cells: [[{ value: 1, observationCount: 41 }, { value: 0, observationCount: 41 }], [{ value: 0, observationCount: 41 }, { value: 1, observationCount: 41 }]], maxAbs: 1 }
  const diagnostics = { status: 'available' as const, asOfDate: '2026-10-09', windowStartDate: '2023-10-10', observedStartDate: '2026-08-14', observedEndDate: '2026-10-09', periodStartDate: '2026-08-13', observationCount: 41, requiredObservationCount: 20, scopeMemberCount: 2, issues: [] }
  const view = render(<LanguageProvider><CorrelationMatrixView matrix={matrix} diagnostics={diagnostics} emptyLabel="No matrix." /></LanguageProvider>)
  const first = screen.getByRole('button', { name: /Long Alpha name \/ USD × Long Alpha name/ })
  first.focus()
  fireEvent.keyDown(first, { key: 'ArrowRight' })
  const pair = screen.getByRole('button', { name: /Long Alpha name \/ USD × Long Beta name/ })
  expect(pair).toHaveFocus()
  expect(pair).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('status')).toHaveTextContent('Correlation: 0.00 · 41 common observations')
  expect(screen.getByRole('status')).toHaveTextContent('2026-08-13 → 2026-10-09')
  expect(screen.getAllByRole('columnheader').find(node => node.textContent?.includes('Long Beta'))).toHaveAttribute('data-active', 'true')
  expect(screen.getAllByRole('rowheader')[0]).toHaveAttribute('data-active', 'true')
  expect(pair.closest('tbody')?.querySelectorAll('.risk-matrix-cell-button[tabindex="0"]')).toHaveLength(1)
  view.rerender(<LanguageProvider><CorrelationMatrixView matrix={{ ...matrix, groups: [groups[1]], cells: [[{ value: null, observationCount: 0 }]] }} diagnostics={diagnostics} emptyLabel="No matrix." /></LanguageProvider>)
  expect(screen.getByRole('status')).toHaveTextContent('Long Beta name / CNY × Long Beta name / CNY')
  expect(screen.getByRole('status')).toHaveTextContent('Correlation: — · 0 common observations')
})
