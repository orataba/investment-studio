// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import GenerateEditionForm from './GenerateEditionForm'

afterEach(() => { cleanup(); vi.useRealTimers() })

function form(onGenerate = vi.fn(), onCancel = vi.fn(), busy = false) {
  window.history.replaceState(null, '', '/?lang=en')
  render(<LanguageProvider enableDomTranslation={false}>
    <GenerateEditionForm kind="daily" busy={busy} onGenerate={onGenerate} onCancel={onCancel} />
  </LanguageProvider>)
  return { onGenerate, onCancel }
}

it.each([
  ['+08:00', '2026-10-02T00:30', '2026-10-01T16:30:00.000Z'],
  ['+00:00', '2026-10-02T00:30', '2026-10-02T00:30:00.000Z'],
])('submits the explicit %s cutoff across calendar boundaries', (offset, input, expected) => {
  const { onGenerate } = form()
  fireEvent.click(screen.getByRole('radio', { name: 'Choose a date and time' }))
  fireEvent.change(screen.getByLabelText('Date and time'), { target: { value: input } })
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: offset } })
  expect(onGenerate).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Start generation' }))
  expect(onGenerate).toHaveBeenCalledExactlyOnceWith(expected)
})

it('takes the current cutoff at submission after the form has been left open', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-02T23:59:00Z'))
  const { onGenerate } = form()
  vi.setSystemTime(new Date('2026-10-03T00:01:00Z'))
  fireEvent.click(screen.getByRole('button', { name: 'Start generation' }))
  expect(onGenerate).toHaveBeenCalledExactlyOnceWith('2026-10-03T00:01:00.000Z')
})

it('keeps an in-flight submission from creating another click or closing its form', () => {
  const { onGenerate, onCancel } = form(vi.fn(), vi.fn(), true)
  fireEvent.click(screen.getByRole('button', { name: 'Submitting…' }))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  fireEvent.keyDown(screen.getByRole('heading'), { key: 'Escape' })
  expect(onGenerate).not.toHaveBeenCalled()
  expect(onCancel).not.toHaveBeenCalled()
})
