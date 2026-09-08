import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import InfoHint from './InfoHint'
import QualityWarningsNotice from './QualityWarningsNotice'

it('keeps explanations out of the page flow and opens them by click or keyboard', async () => {
  const user = userEvent.setup()
  const { container } = render(<section><h2>Performance</h2><InfoHint label="Return basis" detail="Uses the selected period." /><button>Outside</button></section>)
  const hint = screen.getByRole('button', { name: 'Return basis: Uses the selected period.' })
  expect(hint).toHaveAttribute('title', 'Uses the selected period.')
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  await user.click(hint)
  expect(screen.getByRole('tooltip')).toHaveTextContent('Uses the selected period.')
  expect(container).not.toContainElement(screen.getByRole('tooltip'))
  await user.click(screen.getByText('Uses the selected period.'))
  expect(hint).toHaveAttribute('aria-expanded', 'true')
  await user.click(screen.getByRole('button', { name: 'Outside' }))
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  hint.focus()
  await user.keyboard('{Enter}')
  expect(screen.getByRole('tooltip')).toBeInTheDocument()
})

it('closes an explanation with Escape without closing its parent dialog', async () => {
  const close = vi.fn()
  function Dialog() {
    const ref = useModalDialog(true, close)
    return <div ref={ref} role="dialog"><InfoHint label="Settlement" detail="Review the broker confirmation." /></div>
  }
  const user = userEvent.setup()
  render(<Dialog />)
  const hint = screen.getByRole('button')
  await user.click(hint)
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  expect(close).not.toHaveBeenCalled()
  await waitFor(() => expect(hint).toHaveFocus())
  await user.keyboard('{Escape}')
  expect(close).toHaveBeenCalledOnce()
})

it('hides absent warnings and exposes every distinct warning from a compact entry', async () => {
  const user = userEvent.setup()
  const view = render(<QualityWarningsNotice warnings={[]} />)
  expect(view.container).toBeEmptyDOMElement()
  view.rerender(<QualityWarningsNotice warnings={['Missing USD/HKD on 2026-09-01.', ' Missing USD/HKD on 2026-09-01. ', 'Quote coverage ends early.']} />)
  const button = screen.getByRole('button', { name: /Data quality warnings \(2\)/ })
  expect(screen.queryByText('Missing USD/HKD on 2026-09-01.')).not.toBeInTheDocument()
  await user.click(button)
  expect(screen.getAllByRole('listitem')).toHaveLength(2)
  expect(screen.getByRole('tooltip')).toHaveTextContent('Quote coverage ends early.')
})
