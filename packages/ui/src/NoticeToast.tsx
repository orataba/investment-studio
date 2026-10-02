import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export type NoticeToastTone = 'success' | 'error' | 'info'

export type NoticeToastMessage = {
  id: number
  message: ReactNode
  tone?: NoticeToastTone
}

type NoticeToastProps = {
  notice: NoticeToastMessage | null
  durationMs?: number
  onDismiss: () => void
}

function viewport() {
  let root = document.getElementById('investment-studio-notices')
  if (!root) {
    root = document.createElement('div')
    root.id = 'investment-studio-notices'
    root.className = 'investment-studio-notice-stack'
    document.body.appendChild(root)
  }
  return root
}

export default function NoticeToast({ notice, durationMs, onDismiss }: NoticeToastProps) {
  const onDismissRef = useRef(onDismiss)
  const [container, setContainer] = useState<HTMLElement | null>(null)
  const duration = durationMs ?? (notice?.tone === 'error' ? 8000 : 4000)

  useEffect(() => { if (notice) setContainer(viewport()) }, [Boolean(notice)])

  useEffect(() => {
    onDismissRef.current = onDismiss
  }, [onDismiss])

  useEffect(() => {
    if (!notice || duration <= 0) {
      return undefined
    }
    const timer = window.setTimeout(() => onDismissRef.current(), duration)
    return () => window.clearTimeout(timer)
  }, [duration, notice?.id])

  if (!notice || !container) {
    return null
  }

  const tone = notice.tone || 'info'
  return createPortal(
    <div className={`investment-studio-notice-toast investment-studio-notice-toast-${tone}`} role={tone === 'error' ? 'alert' : 'status'} aria-live={tone === 'error' ? 'assertive' : 'polite'}>
      <span className="investment-studio-notice-icon" aria-hidden="true"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        {tone === 'success' ? <path d="m5 10 3.2 3.2L15 6.5" /> : tone === 'error' ? <><path d="M10 5.5v5" /><circle cx="10" cy="14" r=".8" fill="currentColor" stroke="none" /></> : <><circle cx="10" cy="6" r=".8" fill="currentColor" stroke="none" /><path d="M10 9v5" /></>}
      </svg></span>
      <div className="investment-studio-notice-content">{notice.message}</div>
      <button type="button" className="investment-studio-notice-close" aria-label="Close notification" onClick={onDismiss}><svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg></button>
    </div>, container,
  )
}

/** Loading belongs to the region being read or the action being submitted. */
export function LoadingNotice({ active, message, compact = false }: { active: boolean; message: ReactNode; compact?: boolean }) {
  if (!active) return null
  return <div className={`investment-studio-loading${compact ? ' investment-studio-loading-compact' : ''}`} role="status" aria-live="polite">
    <span className="investment-studio-loading-spinner" aria-hidden="true" />
    <span>{message}</span>
  </div>
}
