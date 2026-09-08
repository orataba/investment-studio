import { useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import './info-hint.css'

type InfoHintProps = {
  label: string
  detail: string | readonly string[]
  tone?: 'info' | 'warning'
}

export default function InfoHint({ label, detail, tone = 'info' }: InfoHintProps) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState({ left: 0, top: 0 })
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const popupId = useId()
  const text = typeof detail === 'string' ? detail : detail.join(' ')

  useLayoutEffect(() => {
    if (!open) return
    const trigger = triggerRef.current
    const popup = popupRef.current
    if (!trigger || !popup) return
    const anchor = trigger.getBoundingClientRect()
    const bounds = popup.getBoundingClientRect()
    setPosition({
      left: Math.max(12, Math.min(anchor.left, window.innerWidth - bounds.width - 12)),
      top: anchor.bottom + bounds.height + 8 <= window.innerHeight - 12
        ? anchor.bottom + 8
        : Math.max(12, anchor.top - bounds.height - 8),
    })
    function closeOutside(event: Event) {
      if (!trigger?.contains(event.target as Node) && !popup?.contains(event.target as Node)) setOpen(false)
    }
    function closeWithEscape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
      trigger?.focus()
    }
    function closeOnMove(event: Event) {
      if (!(event.target instanceof Node) || !popup?.contains(event.target)) setOpen(false)
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
  }, [open])

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
        title={open ? undefined : text}
        onClick={(event) => { event.stopPropagation(); setOpen((value) => !value) }}
      >
        <span aria-hidden="true">{tone === 'warning' ? '!' : 'i'}</span>
      </button>
      {open && createPortal(
        <div ref={popupRef} id={popupId} className="investment-studio-info-popup" role="tooltip" style={position} onClick={(event) => event.stopPropagation()}>
          <strong>{label}</strong>
          {typeof detail === 'string' ? <p>{detail}</p> : <ul>{detail.map((item) => <li key={item}>{item}</li>)}</ul>}
        </div>,
        document.body,
      )}
    </>
  )
}
