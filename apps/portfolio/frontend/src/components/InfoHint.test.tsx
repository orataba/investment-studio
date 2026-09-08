import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import InfoHint from './InfoHint'
import QualityWarningsNotice from './QualityWarningsNotice'

it('uses the same exclamation and anchored explanation for hover, focus and click', async () => {
  const user = userEvent.setup()
  const { container } = render(<section><h2>Performance</h2><InfoHint label="Return basis" detail="Uses the selected period." /><button>Outside</button></section>)
  const hint = screen.getByRole('button', { name: 'Return basis: Uses the selected period.' })
  expect(hint).toHaveTextContent('!')
  expect(hint).not.toHaveAttribute('title')
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  await user.hover(hint)
  const popup = screen.getByRole('tooltip')
  expect(popup).toHaveTextContent('Uses the selected period.')
  expect(container).not.toContainElement(popup)
  await user.unhover(hint)
  await user.hover(popup)
  expect(screen.getByRole('tooltip')).toBe(popup)
  await user.unhover(popup)
  await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())

  // A click after hovering pins the same explanation instead of hiding it.
  await user.hover(hint)
  await user.click(hint)
  await user.unhover(hint)
  expect(screen.getByRole('tooltip')).toBeInTheDocument()
  await user.click(screen.getByText('Uses the selected period.'))
  expect(hint).toHaveAttribute('aria-expanded', 'true')
  await user.click(hint)
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Outside' }))

  await user.tab({ shift: true })
  expect(hint).toHaveFocus()
  expect(screen.getByRole('tooltip')).toBeInTheDocument()
  await user.keyboard('{Enter}')
  expect(hint).toHaveAttribute('aria-expanded', 'true')
  await user.tab()
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
})

it('opens from a touch click and closes on outside pointerdown', () => {
  render(<><InfoHint label="Settlement" detail="Review the confirmation." tone="warning" /><button>Outside</button></>)
  const hint = screen.getByRole('button', { name: /Settlement:/ })
  expect(hint).toHaveTextContent('!')
  fireEvent.pointerEnter(hint, { pointerType: 'touch' })
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  fireEvent.click(hint)
  expect(screen.getByRole('tooltip')).toHaveTextContent('Review the confirmation.')
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Outside' }))
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
})

it('fits a long explanation inside the usable viewport and keeps its own scrolling open', () => {
  const bounds = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    return this.matches('.investment-studio-info-hint')
      ? { left: 290, right: 306, top: 350, bottom: 366, width: 16, height: 16, x: 290, y: 350, toJSON() {} }
      : { left: 0, right: 280, top: 0, bottom: 600, width: 280, height: 600, x: 0, y: 0, toJSON() {} }
  })
  vi.stubGlobal('innerWidth', 320)
  vi.stubGlobal('innerHeight', 480)
  const viewportWidth = vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(305)
  try {
    render(<InfoHint label="Coverage" detail={['First coverage note.', 'Second coverage note.']} />)
    fireEvent.click(screen.getByRole('button'))
    const popup = screen.getByRole('tooltip')
    expect(popup).toHaveStyle({ left: '13px', top: '12px', maxHeight: '330px' })
    Object.defineProperties(popup, { clientHeight: { value: 330 }, scrollHeight: { value: 600 } })
    fireEvent.keyDown(screen.getByRole('button'), { key: 'PageDown' })
    expect(popup.scrollTop).toBe(330)
    fireEvent.scroll(popup)
    expect(screen.getByRole('tooltip')).toBe(popup)
    fireEvent.scroll(window)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  } finally {
    bounds.mockRestore()
    viewportWidth.mockRestore()
    vi.unstubAllGlobals()
  }
})

it('returns focus from a scrolled popup on Escape without reopening it', async () => {
  const user = userEvent.setup()
  render(<InfoHint label="Coverage" detail="Detailed coverage notes." />)
  const hint = screen.getByRole('button')
  await user.click(hint)
  const popup = screen.getByRole('tooltip')
  popup.tabIndex = -1
  popup.focus()
  await user.keyboard('{Escape}')
  expect(hint).toHaveFocus()
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
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
