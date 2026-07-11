import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'

export type PortfolioTableViewOption = {
  id: string
  name: string
  description?: string | null
  readonly?: boolean
}

type PortfolioTableViewControlsProps = {
  views: PortfolioTableViewOption[]
  activeViewId: string
  edited: boolean
  canSave: boolean
  canDelete?: boolean
  onSelect: (viewId: string) => void
  onSave: () => void | Promise<void>
  onSaveAs: (name: string, description: string | null) => void | Promise<void>
  onDelete?: (viewId: string) => void | Promise<void>
}

export default function PortfolioTableViewControls({
  views,
  activeViewId,
  edited,
  canSave,
  canDelete = true,
  onSelect,
  onSave,
  onSaveAs,
  onDelete,
}: PortfolioTableViewControlsProps) {
  const activeView = views.find((view) => view.id === activeViewId) ?? views[0] ?? null
  const controlsRef = useRef<HTMLDivElement | null>(null)
  const [viewMenuOpen, setViewMenuOpen] = useState(false)
  const [saveAsOpen, setSaveAsOpen] = useState(false)
  const [pendingDeleteView, setPendingDeleteView] = useState<PortfolioTableViewOption | null>(null)
  const [draftName, setDraftName] = useState('')
  const [draftDescription, setDraftDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const draftNameRef = useRef<HTMLInputElement | null>(null)

  function closeSaveAsDialog() {
    if (!saving) {
      setSaveAsOpen(false)
    }
  }

  const saveDialogRef = useModalDialog(saveAsOpen, closeSaveAsDialog, draftNameRef)
  const deleteEnabled = canDelete && Boolean(onDelete)
  const busy = saving || deleting
  const activeViewLabel = `View\u00A0: ${activeView?.name ?? 'Default'}${edited ? ' (Edited)' : ''}`
  const quickActionLabel = edited ? (canSave ? 'Save view' : 'Save as view') : 'Create view'

  useEffect(() => {
    if (!viewMenuOpen) {
      return undefined
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target
      if (target instanceof Node && controlsRef.current?.contains(target)) {
        return
      }
      setViewMenuOpen(false)
    }

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') {
        setViewMenuOpen(false)
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [viewMenuOpen])

  async function handleSave() {
    if (!edited || !canSave || busy) {
      return
    }
    setSaving(true)
    try {
      await onSave()
    } finally {
      setSaving(false)
    }
  }

  function openSaveAs() {
    setDraftName(activeView?.name ? `${activeView.name} Copy` : 'Custom View')
    setDraftDescription('')
    setViewMenuOpen(false)
    setSaveAsOpen(true)
  }

  async function handleSaveAs() {
    const name = draftName.trim()
    if (!name || busy) {
      return
    }
    setSaving(true)
    try {
      await onSaveAs(name, draftDescription.trim() || null)
      setSaveAsOpen(false)
      setDraftDescription('')
    } finally {
      setSaving(false)
    }
  }

  function canDeleteView(view: PortfolioTableViewOption) {
    return deleteEnabled && !view.readonly
  }

  function selectView(viewId: string) {
    if (busy) {
      return
    }
    setViewMenuOpen(false)
    onSelect(viewId)
  }

  function openDeleteView(view: PortfolioTableViewOption) {
    if (!canDeleteView(view) || busy) {
      return
    }
    setViewMenuOpen(false)
    setPendingDeleteView(view)
  }

  function handleViewContextMenu(event: MouseEvent<HTMLDivElement>, view: PortfolioTableViewOption) {
    event.preventDefault()
    openDeleteView(view)
  }

  function handleViewKeyDown(event: KeyboardEvent<HTMLDivElement>, view: PortfolioTableViewOption) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      selectView(view.id)
      return
    }
    if ((event.key === 'Delete' || event.key === 'Backspace') && canDeleteView(view)) {
      event.preventDefault()
      openDeleteView(view)
    }
  }

  async function handleDelete() {
    if (!pendingDeleteView || !canDeleteView(pendingDeleteView) || !onDelete || busy) {
      return
    }
    setDeleting(true)
    try {
      await onDelete(pendingDeleteView.id)
      setPendingDeleteView(null)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <>
      <div className="portfolio-table-view-controls" ref={controlsRef}>
        <button
          type="button"
          className="portfolio-table-view-select"
          disabled={busy}
          onClick={() => setViewMenuOpen((open) => !open)}
          aria-haspopup="listbox"
          aria-expanded={viewMenuOpen}
        >
          <span className="portfolio-table-view-select-label">{activeViewLabel}</span>
        </button>
        <button
          type="button"
          className={`portfolio-table-view-button portfolio-table-view-quick-action ${edited ? 'portfolio-table-view-quick-action-active' : ''}`}
          disabled={busy}
          onClick={() => {
            if (edited && canSave) {
              void handleSave()
              return
            }
            openSaveAs()
          }}
          aria-label={quickActionLabel}
          title={quickActionLabel}
        >
          {edited ? (
            saving ? (
              '...'
            ) : (
              <span className="portfolio-table-view-save-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path
                    d="M4 3h12l4 4v14H4V3zm2 2v4h10V5H6zm0 8v6h12v-6H6zm2 2h4v2H8v-2z"
                    fill="currentColor"
                  />
                </svg>
              </span>
            )
          ) : (
            <span className="portfolio-table-view-plus-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path
                  d="M12 5v14M5 12h14"
                  fill="none"
                  stroke="currentColor"
                  strokeLinecap="square"
                  strokeWidth="2"
                />
              </svg>
            </span>
          )}
        </button>
        {viewMenuOpen ? (
          <div className="portfolio-table-view-menu" role="listbox" aria-label="Table views">
            {views.map((view) => {
              const viewEdited = view.id === activeViewId && edited
              const deletable = canDeleteView(view)
              return (
                <div
                  key={view.id}
                  role="option"
                  aria-selected={view.id === activeViewId}
                  tabIndex={0}
                  className={`portfolio-table-view-option ${view.id === activeViewId ? 'portfolio-table-view-option-active' : ''}`}
                  onClick={() => selectView(view.id)}
                  onContextMenu={(event) => handleViewContextMenu(event, view)}
                  onKeyDown={(event) => handleViewKeyDown(event, view)}
                >
                  <span className="portfolio-table-view-option-copy">
                    <span className="portfolio-table-view-option-name">
                      {view.name}
                      {viewEdited ? <span className="portfolio-table-view-option-edited">Edited</span> : null}
                    </span>
                  </span>
                  {deletable ? (
                    <button
                      type="button"
                      className="portfolio-table-view-option-delete"
                      disabled={busy}
                      aria-label={`Delete ${view.name}`}
                      title="Delete view"
                      onClick={(event) => {
                        event.stopPropagation()
                        openDeleteView(view)
                      }}
                    >
                      <span className="portfolio-table-view-delete-icon" aria-hidden="true">
                        <svg viewBox="0 0 24 24">
                          <path
                            d="M9 3h6l1 2h4v2H4V5h4l1-2zm-2 6h10l-.8 12H7.8L7 9zm3 2v8h2v-8h-2zm4 0v8h2v-8h-2z"
                            fill="currentColor"
                          />
                        </svg>
                      </span>
                    </button>
                  ) : null}
                </div>
              )
            })}
          </div>
        ) : null}
      </div>

      <ConfirmDialog
        open={Boolean(pendingDeleteView)}
        title="Delete View"
        description={`Delete "${pendingDeleteView?.name ?? ''}"? This removes the saved table layout.`}
        confirmLabel="Delete View"
        busy={deleting}
        onCancel={() => setPendingDeleteView(null)}
        onConfirm={handleDelete}
      />

      {saveAsOpen ? (
        <div className="portfolio-table-view-modal-backdrop" onClick={closeSaveAsDialog}>
          <div
            ref={saveDialogRef}
            className="portfolio-table-view-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="portfolio-create-view-title"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="portfolio-table-view-modal-header">
              <div>
                <div className="panel-title" id="portfolio-create-view-title">Create View</div>
                <div className="section-heading">Save Current Table Layout</div>
              </div>
              <button type="button" disabled={saving} onClick={closeSaveAsDialog}>
                Close
              </button>
            </div>
            <div className="portfolio-table-view-modal-body">
              <label className="portfolio-table-view-field">
                <span>View Name</span>
                <input
                  ref={draftNameRef}
                  className="transaction-filter-input"
                  value={draftName}
                  onChange={(event) => setDraftName(event.target.value)}
                  placeholder="Custom View"
                />
              </label>
              <label className="portfolio-table-view-field">
                <span>Notes</span>
                <textarea
                  className="portfolio-table-view-description"
                  value={draftDescription}
                  onChange={(event) => setDraftDescription(event.target.value)}
                />
              </label>
            </div>
            <div className="portfolio-table-view-modal-actions">
              <button type="button" disabled={saving} onClick={closeSaveAsDialog}>
                Cancel
              </button>
              <button type="button" className="button-primary" disabled={!draftName.trim() || saving} onClick={handleSaveAs}>
                {saving ? 'Saving...' : 'Save View'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  )
}
