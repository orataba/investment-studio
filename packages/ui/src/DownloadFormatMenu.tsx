import { useEffect, useRef, useState } from 'react'

import type { TableExportFormat } from './tableExport'

type DownloadFormatMenuProps = {
  buttonLabel?: string
  disabled?: boolean
  wrapperClassName?: string
  buttonClassName?: string
  menuClassName?: string
  itemClassName?: string
  onBeforeOpen?: () => void
  onSelect: (format: TableExportFormat) => void
}

const DOWNLOAD_FORMATS: Array<{ format: TableExportFormat; label: string }> = [
  { format: 'csv', label: 'CSV' },
  { format: 'xlsx', label: 'Excel' },
]

export default function DownloadFormatMenu({
  buttonLabel = 'Download',
  disabled = false,
  wrapperClassName = 'download-format-menu',
  buttonClassName,
  menuClassName = 'download-format-menu-list',
  itemClassName = 'download-format-menu-item',
  onBeforeOpen,
  onSelect,
}: DownloadFormatMenuProps) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) {
      return
    }
    function handlePointerDown(event: MouseEvent) {
      const target = event.target instanceof Node ? event.target : null
      if (target && menuRef.current?.contains(target)) {
        return
      }
      setOpen(false)
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  return (
    <div className={wrapperClassName} ref={menuRef}>
      <button
        type="button"
        className={buttonClassName}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          if (disabled) {
            return
          }
          if (!open) {
            onBeforeOpen?.()
          }
          setOpen((current) => !current)
        }}
      >
        {buttonLabel}
      </button>
      {open ? (
        <div className={menuClassName} role="menu">
          {DOWNLOAD_FORMATS.map((option) => (
            <button
              type="button"
              role="menuitem"
              className={itemClassName}
              key={option.format}
              onClick={() => {
                setOpen(false)
                onSelect(option.format)
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
