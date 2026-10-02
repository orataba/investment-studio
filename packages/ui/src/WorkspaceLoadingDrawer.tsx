import { useLayoutEffect } from 'react'
import { useLanguage } from './i18n'
import { LoadingNotice } from './NoticeToast'
import { WorkspaceToolIcon } from './WorkspaceTools'
import { useModalDialog } from './useModalDialog'
import './workspace-drawer.css'
import './notice-toast.css'

/** Preserve the requested drawer's position and dismissal while its code loads. */
export default function WorkspaceLoadingDrawer({ kind, onClose }: { kind: 'risk' | 'assistant'; onClose: () => void }) {
  const { language, t } = useLanguage()
  const zh = language === 'zh-Hans'
  const title = kind === 'risk' ? (zh ? '风险提示' : 'Risk alerts') : (zh ? '研究助手' : 'Research assistant')
  const dialogRef = useModalDialog(true, onClose)
  useLayoutEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = dialogRef.current
    return () => {
      // The real drawer captures its return target when it mounts. Restore the
      // original trigger before that effect, not to this disappearing fallback.
      if (trigger?.isConnected && dialog?.contains(document.activeElement)) trigger.focus()
    }
  }, [dialogRef])
  return <div className="assistant-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div ref={dialogRef} className="assistant-drawer" role="dialog" aria-modal="true" aria-label={`${title} · ${t('Loading')}`} tabIndex={-1}>
      <header className="workspace-loading-heading"><h2><WorkspaceToolIcon kind={kind} />{title}</h2><button type="button" onClick={onClose}>{t('Close')}</button></header>
      <LoadingNotice active message={t('Loading')} />
    </div>
  </div>
}
