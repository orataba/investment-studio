import {
  useCallback,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react'

type DragState = {
  pointerId: number
  startX: number
  startScrollLeft: number
}

export function useHorizontalTablePan() {
  const ref = useRef<HTMLDivElement | null>(null)
  const dragState = useRef<DragState | null>(null)
  const [isPanning, setIsPanning] = useState(false)

  const finishPan = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragState.current?.pointerId !== event.pointerId) {
      return
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    dragState.current = null
    setIsPanning(false)
  }, [])

  return {
    ref,
    isPanning,
    handlers: {
      onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => {
        if (
          event.pointerType !== 'mouse' ||
          event.button !== 0 ||
          (event.target as HTMLElement).closest(
            'thead, button, a, input, select, textarea, [role="separator"]',
          )
        ) {
          return
        }
        dragState.current = {
          pointerId: event.pointerId,
          startX: event.clientX,
          startScrollLeft: event.currentTarget.scrollLeft,
        }
        event.currentTarget.setPointerCapture(event.pointerId)
        event.preventDefault()
      },
      onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => {
        const current = dragState.current
        if (!current || current.pointerId !== event.pointerId) {
          return
        }
        const deltaX = event.clientX - current.startX
        if (!isPanning && Math.abs(deltaX) < 4) {
          return
        }
        if (!isPanning) {
          setIsPanning(true)
        }
        event.currentTarget.scrollLeft = current.startScrollLeft - deltaX
        event.preventDefault()
      },
      onPointerUp: finishPan,
      onPointerCancel: finishPan,
    },
  }
}
