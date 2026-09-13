import { useCallback, useRef, useState, type MouseEvent, type PointerEvent } from 'react'

// Keep editing, navigation, sorting and native row/column dragging independent
// from horizontal browsing. Shift preserves native text selection for copying.
const INTERACTIVE_TARGET = 'thead, a, button, input, select, textarea, summary, [role="button"], [role="link"], [role="separator"], [contenteditable]:not([contenteditable="false"]), [draggable="true"], [data-table-pan="off"]'
type Pan = { pointerId: number; startX: number; startScrollLeft: number; moved: boolean }

export function useHorizontalTablePan() {
  const ref = useRef<HTMLDivElement | null>(null)
  const pan = useRef<Pan | null>(null)
  const suppressClick = useRef(false)
  const [isPanning, setIsPanning] = useState(false)

  const finish = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (pan.current?.pointerId !== event.pointerId) return
    pan.current = null
    setIsPanning(false)
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }, [])

  return {
    ref,
    isPanning,
    handlers: {
      onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
        suppressClick.current = false
        if (event.pointerType !== 'mouse' || event.button !== 0 || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return
        const container = event.currentTarget
        if (container.scrollWidth <= container.clientWidth || !(event.target instanceof Element) || event.target.closest(INTERACTIVE_TARGET)) return
        pan.current = { pointerId: event.pointerId, startX: event.clientX, startScrollLeft: container.scrollLeft, moved: false }
        // Delay capture until a real drag so an ordinary cell/row click keeps
        // its original target. Prevent only native mouse text selection here.
        event.preventDefault()
      },
      onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
        const current = pan.current
        if (!current || current.pointerId !== event.pointerId) return
        if ((event.buttons & 1) === 0) { finish(event); return }
        const delta = event.clientX - current.startX
        if (!current.moved && Math.abs(delta) < 4) return
        if (!current.moved) {
          current.moved = true
          suppressClick.current = true
          setIsPanning(true)
          event.currentTarget.setPointerCapture?.(event.pointerId)
        }
        event.currentTarget.scrollLeft = current.startScrollLeft - delta
        event.preventDefault()
      },
      onPointerUp: finish,
      onPointerCancel: finish,
      onLostPointerCapture: finish,
      onPointerLeave: (event: PointerEvent<HTMLDivElement>) => {
        if (!pan.current?.moved) finish(event)
      },
      onClickCapture: (event: MouseEvent<HTMLDivElement>) => {
        if (suppressClick.current && event.detail !== 0) {
          suppressClick.current = false
          event.preventDefault()
          event.stopPropagation()
        }
      },
    },
  }
}
