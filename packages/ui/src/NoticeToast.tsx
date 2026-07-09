import { useEffect, useRef, type ReactNode } from 'react'

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

export default function NoticeToast({ notice, durationMs = 2800, onDismiss }: NoticeToastProps) {
  const onDismissRef = useRef(onDismiss)

  useEffect(() => {
    onDismissRef.current = onDismiss
  }, [onDismiss])

  useEffect(() => {
    if (!notice) {
      return undefined
    }
    const timer = window.setTimeout(() => onDismissRef.current(), durationMs)
    return () => window.clearTimeout(timer)
  }, [durationMs, notice?.id])

  if (!notice) {
    return null
  }

  const tone = notice.tone || 'info'
  return (
    <div className={`portfolio-ops-notice-toast portfolio-ops-notice-toast-${tone}`} role="status" aria-live="polite">
      {notice.message}
    </div>
  )
}
