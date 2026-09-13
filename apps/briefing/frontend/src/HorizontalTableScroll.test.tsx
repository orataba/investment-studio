// @vitest-environment jsdom
import { createRef } from 'react'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import HorizontalTableScroll from '../../../../packages/ui/src/HorizontalTableScroll'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
function fixture(overflows = true) {
  const clicked = vi.fn()
  const forwardedRef = createRef<HTMLDivElement>()
  const view = render(<HorizontalTableScroll ref={forwardedRef}><table><thead><tr><th>Sort</th></tr></thead><tbody><tr onClick={clicked}><td>Value</td><td><button>Open</button><input aria-label="Edit" /></td></tr></tbody></table></HorizontalTableScroll>)
  const container = view.container.firstElementChild as HTMLDivElement
  Object.defineProperties(container, { scrollWidth: { value: overflows ? 800 : 300, configurable: true }, clientWidth: { value: 300, configurable: true } })
  const capture = vi.fn()
  container.setPointerCapture = capture
  container.hasPointerCapture = () => false
  const cell = view.getByText('Value')
  function pointer(node: Element, name: string, values = {}) {
    const event = new MouseEvent(name, { bubbles: true, cancelable: true, clientX: 200, button: 0, buttons: 1, ...values })
    Object.defineProperties(event, { pointerId: { value: 1 }, pointerType: { value: 'mouse' } })
    act(() => { node.dispatchEvent(event) })
    return event
  }
  return { ...view, container, cell, capture, clicked, forwardedRef, pointer }
}
it('forwards the container ref, scrolls a genuine drag and suppresses its row click', () => {
  const f = fixture()
  expect(f.forwardedRef.current).toBe(f.container)
  f.pointer(f.cell, 'pointerdown')
  expect(f.capture).not.toHaveBeenCalled()
  f.pointer(f.cell, 'pointermove', { clientX: 198 })
  expect(f.container.scrollLeft).toBe(0)
  f.pointer(f.cell, 'pointermove', { clientX: 120 })
  expect(f.container.scrollLeft).toBe(80)
  expect(f.capture).toHaveBeenCalledWith(1)
  f.pointer(f.container, 'pointerup')
  fireEvent.click(f.cell, { detail: 1 })
  expect(f.clicked).not.toHaveBeenCalled()
  f.pointer(f.cell, 'pointerdown')
  f.pointer(f.cell, 'pointerup')
  fireEvent.click(f.cell, { detail: 1 })
  expect(f.clicked).toHaveBeenCalledTimes(1)
})
it('leaves non-overflowing content, Shift selection, headers and editing controls native', () => {
  const narrow = fixture(false)
  expect(narrow.pointer(narrow.cell, 'pointerdown').defaultPrevented).toBe(false)
  narrow.unmount()
  const f = fixture()
  for (const node of [f.getByText('Sort'), f.getByRole('button'), f.getByRole('textbox')]) {
    expect(f.pointer(node, 'pointerdown').defaultPrevented).toBe(false)
    f.pointer(node, 'pointermove', { clientX: 100 })
    expect(f.container.scrollLeft).toBe(0)
  }
  expect(f.pointer(f.cell, 'pointerdown', { shiftKey: true }).defaultPrevented).toBe(false)
  f.pointer(f.cell, 'pointermove', { clientX: 100, shiftKey: true })
  expect(f.container.scrollLeft).toBe(0)
})
it('stops after capture loss and keeps keyboard activation available', () => {
  const f = fixture()
  f.pointer(f.cell, 'pointerdown')
  f.pointer(f.cell, 'pointermove', { clientX: 100 })
  f.pointer(f.container, 'lostpointercapture')
  f.pointer(f.container, 'pointermove', { clientX: 40 })
  expect(f.container.scrollLeft).toBe(100)
  expect(f.container.hasAttribute('data-table-panning')).toBe(false)
  fireEvent.click(f.cell, { detail: 0 })
  expect(f.clicked).toHaveBeenCalledTimes(1)
})
