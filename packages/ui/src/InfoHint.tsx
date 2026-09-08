import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import './info-hint.css'

type InfoHintProps = {
  label: string
  detail: string | readonly string[]
  tone?: 'info' | 'warning'
}

export default function InfoHint({ label, detail, tone = 'info' }: InfoHintProps) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ left: number; top: number; maxHeight?: number }>({ left: 0, top: 0 })
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(false)
  const returningFocusRef = useRef(false)
  const closeTimerRef = useRef<number | null>(null)
  const popupId = useId()
  const text = typeof detail === 'string' ? detail : detail.join(' ')

  function cancelClose() {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current)
    closeTimerRef.current = null
  }

  function close() {
    cancelClose()
    pinnedRef.current = false
    setOpen(false)
  }

  function reveal() {
    cancelClose()
    setOpen(true)
  }

  function leave() {
    cancelClose()
    // Keep the popup reachable across the small gap between it and the icon.
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null
      const focused = document.activeElement
      if (!pinnedRef.current && !triggerRef.current?.contains(focused) && !popupRef.current?.contains(focused)) setOpen(false)
    }, 150)
  }

  useEffect(() => () => {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current)
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    const trigger = triggerRef.current
    const popup = popupRef.current
    if (!trigger || !popup) return
    const anchor = trigger.getBoundingClientRect()
    const bounds = popup.getBoundingClientRect()
    const margin = 12
    const gap = 8
    const viewportWidth = document.documentElement.clientWidth || window.innerWidth
    const viewportHeight = document.documentElement.clientHeight || window.innerHeight
    const availableBelow = Math.max(0, viewportHeight - margin - anchor.bottom - gap)
    const availableAbove = Math.max(0, anchor.top - gap - margin)
    const fullHeight = Math.max(bounds.height, popup.scrollHeight + popup.offsetHeight - popup.clientHeight)
    const placeBelow = fullHeight <= availableBelow || availableBelow >= availableAbove
    const maxHeight = placeBelow ? availableBelow : availableAbove
    setPosition({
      left: Math.max(margin, Math.min(anchor.left, viewportWidth - bounds.width - margin)),
      top: placeBelow ? Math.max(margin, anchor.bottom + gap) : Math.max(margin, anchor.top - Math.min(fullHeight, maxHeight) - gap),
      maxHeight,
    })
    function closeOutside(event: Event) {
      if (!trigger?.contains(event.target as Node) && !popup?.contains(event.target as Node)) close()
    }
    function closeWithEscape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      close()
      if (popup?.contains(document.activeElement)) {
        returningFocusRef.current = true
        trigger?.focus({ preventScroll: true })
        returningFocusRef.current = false
      }
    }
    function closeOnMove(event: Event) {
      if (!(event.target instanceof Node) || !popup?.contains(event.target)) close()
    }
    document.addEventListener('pointerdown', closeOutside)
    document.addEventListener('focusin', closeOutside)
    document.addEventListener('keydown', closeWithEscape, true)
    window.addEventListener('resize', closeOnMove)
    window.addEventListener('scroll', closeOnMove, true)
    return () => {
      document.removeEventListener('pointerdown', closeOutside)
      document.removeEventListener('focusin', closeOutside)
      document.removeEventListener('keydown', closeWithEscape, true)
      window.removeEventListener('resize', closeOnMove)
      window.removeEventListener('scroll', closeOnMove, true)
    }
  }, [open, label, text])

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`investment-studio-info-hint investment-studio-info-hint-${tone}`}
        aria-label={`${label}: ${text}`}
        aria-expanded={open}
        aria-controls={open ? popupId : undefined}
        aria-describedby={open ? popupId : undefined}
        onPointerEnter={(event) => { if (event.pointerType !== 'touch') reveal() }}
        onPointerLeave={leave}
        onFocus={() => { if (!returningFocusRef.current) reveal() }}
        onKeyDown={(event) => {
          const popup = popupRef.current
          if (!popup || popup.scrollHeight <= popup.clientHeight) return
          const distance = { ArrowDown: 24, ArrowUp: -24, PageDown: popup.clientHeight, PageUp: -popup.clientHeight, Home: -popup.scrollHeight, End: popup.scrollHeight }[event.key]
          if (distance === undefined) return
          event.preventDefault()
          event.stopPropagation()
          popup.scrollTop += distance
        }}
        onClick={(event) => {
          event.stopPropagation()
          if (pinnedRef.current) close()
          else {
            pinnedRef.current = true
            reveal()
          }
        }}
      >
        <span aria-hidden="true">!</span>
      </button>
      {open && createPortal(
        <div ref={popupRef} id={popupId} className="investment-studio-info-popup" role="tooltip" style={position} onPointerEnter={reveal} onPointerLeave={leave} onClick={(event) => event.stopPropagation()}>
          <strong>{label}</strong>
          {typeof detail === 'string' ? <p>{detail}</p> : <ul>{detail.map((item) => <li key={item}>{item}</li>)}</ul>}
        </div>,
        document.body,
      )}
    </>
  )
}
