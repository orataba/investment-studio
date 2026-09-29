import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export type NoticeToastTone = 'success' | 'error' | 'info' | 'loading'

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
  const duration = durationMs ?? (notice?.tone === 'loading' ? 0 : notice?.tone === 'error' ? 8000 : 4000)

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
      {tone === 'loading' && <span className="investment-studio-notice-spinner" aria-hidden="true" />}
      <div className="investment-studio-notice-content">{notice.message}</div>
      {tone !== 'loading' && <button type="button" className="investment-studio-notice-close" aria-label="Close notification" onClick={onDismiss}>×</button>}
    </div>, container,
  )
}

/** Page loading is non-blocking and remains visible until that operation finishes. */
export function LoadingNotice({ active, message }: { active: boolean; message: ReactNode }) {
  return <NoticeToast notice={active ? { id: 0, message, tone: 'loading' } : null} onDismiss={() => undefined} />
}
