import { useId, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'

/** Supporting evidence stays one step away from the uninterrupted report. */
export default function ResearchReadingAside({ label, title = label, children, onOpenChange }: { label: string; title?: string; children: ReactNode; onOpenChange?: (open: boolean) => void }) {
  const [open, setOpen] = useState(false)
  const titleId = useId()
  const changeOpen = (value: boolean) => { setOpen(value); onOpenChange?.(value) }
  const ref = useModalDialog(open, () => changeOpen(false))
  return <>
    <button type="button" className="research-support-link" onClick={() => changeOpen(true)}>{label}</button>
    {open && createPortal(<div className="research-aside-backdrop" onClick={event => { if (event.target === event.currentTarget) changeOpen(false) }}>
      <div className="research-reading-aside" ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header><h2 id={titleId}>{title}</h2><button type="button" aria-label={`关闭${title}`} onClick={() => changeOpen(false)}>关闭</button></header>
        <div className="research-aside-content">{children}</div>
      </div>
    </div>, document.body)}
  </>
}
