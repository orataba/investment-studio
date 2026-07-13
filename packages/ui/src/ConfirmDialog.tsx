import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

import './confirm-dialog.css'
import { useModalDialog } from './useModalDialog'

type ConfirmDialogProps = {
  open: boolean
  title: string
  description: ReactNode
  confirmLabel: string
  confirmationText?: string
  error?: ReactNode
  busy?: boolean
  confirmDisabled?: boolean
  onCancel: () => void
  onConfirm: () => void | Promise<void>
}

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  confirmationText,
  error,
  busy = false,
  confirmDisabled = false,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const [typedConfirmation, setTypedConfirmation] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const titleId = useId()
  const descriptionId = useId()
  const closeDialog = () => {
    if (!busy) {
      onCancel()
    }
  }
  const dialogRef = useModalDialog(open, closeDialog, inputRef)

  useEffect(() => {
    if (open) {
      setTypedConfirmation('')
    }
  }, [open, confirmationText])

  if (!open) {
    return null
  }

  const confirmationMatches = !confirmationText || typedConfirmation === confirmationText

  return (
    <div
      className="portfolio-ops-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          closeDialog()
        }
      }}
    >
      <div
        ref={dialogRef}
        className="portfolio-ops-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
      >
        <div className="portfolio-ops-dialog-header">
          <h2 id={titleId}>{title}</h2>
        </div>
        <div className="portfolio-ops-dialog-body">
          <div id={descriptionId}>{description}</div>
          {confirmationText ? (
            <label className="portfolio-ops-dialog-confirmation">
              <span>
                Type <strong>{confirmationText}</strong> to confirm.
              </span>
              <input
                ref={inputRef}
                value={typedConfirmation}
                autoComplete="off"
                disabled={busy}
                onChange={(event) => setTypedConfirmation(event.target.value)}
              />
            </label>
          ) : null}
          {error ? (
            <div className="portfolio-ops-dialog-error" role="alert">
              {error}
            </div>
          ) : null}
        </div>
        <div className="portfolio-ops-dialog-actions">
          <button type="button" disabled={busy} onClick={closeDialog}>
            Cancel
          </button>
          <button
            type="button"
            className="portfolio-ops-dialog-danger"
            disabled={busy || confirmDisabled || !confirmationMatches}
            onClick={() => {
              void Promise.resolve(onConfirm()).catch(() => undefined)
            }}
          >
            {busy ? 'Deleting…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
