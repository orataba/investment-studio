import { useState } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import NoticeToast, { LoadingNotice, type NoticeToastMessage } from '../../../../packages/ui/src/NoticeToast'
import WorkspaceSkeleton from '../../../../packages/ui/src/WorkspaceSkeleton'

afterEach(() => vi.useRealTimers())

it('keeps loading inside its content region and removes it when the request completes', () => {
  const view = render(<section aria-label="Price history"><LoadingNotice active message="Reading prices…" /></section>)
  const loading = screen.getByRole('status')
  expect(screen.getByRole('region', { name: 'Price history' })).toContainElement(loading)
  expect(loading).toHaveAttribute('aria-live', 'polite')
  expect(loading).not.toHaveAttribute('aria-busy')
  expect(loading.closest('#investment-studio-notices')).toBeNull()
  view.rerender(<section aria-label="Price history"><LoadingNotice active={false} message="Reading prices…" /><p>Prices available</p></section>)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('Prices available')).toBeInTheDocument()
})

it('exposes one local loading announcement for a skeleton', () => {
  const { container } = render(<WorkspaceSkeleton />)
  expect(screen.getAllByRole('status')).toHaveLength(1)
  expect(container).toContainElement(screen.getByRole('status'))
})

function Feedback({ tone, durationMs }: { tone: NoticeToastMessage['tone']; durationMs?: number }) {
  const [notice, setNotice] = useState<NoticeToastMessage | null>({ id: 1, tone, message: 'Operation result' })
  return <NoticeToast notice={notice} durationMs={durationMs} onDismiss={() => setNotice(null)} />
}

it('keeps dismissible result notifications in the global stack', () => {
  render(<Feedback tone="info" />)
  expect(screen.getByRole('status').closest('#investment-studio-notices')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Close notification' }))
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('retains an explicit persistent error until it is dismissed', () => {
  vi.useFakeTimers()
  render(<Feedback tone="error" durationMs={0} />)
  act(() => vi.advanceTimersByTime(60000))
  expect(screen.getByRole('alert')).toHaveTextContent('Operation result')
  fireEvent.click(screen.getByRole('button', { name: 'Close notification' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('automatically closes a successful result after the existing four-second interval', () => {
  vi.useFakeTimers()
  render(<Feedback tone="success" />)
  act(() => vi.advanceTimersByTime(3999))
  expect(screen.getByRole('status')).toBeInTheDocument()
  act(() => vi.advanceTimersByTime(1))
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
