import { useState } from 'react'

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
  onSelect: (viewId: string) => void
  onSave: () => void | Promise<void>
  onSaveAs: (name: string, description: string | null) => void | Promise<void>
}

export default function PortfolioTableViewControls({
  views,
  activeViewId,
  edited,
  canSave,
  onSelect,
  onSave,
  onSaveAs,
}: PortfolioTableViewControlsProps) {
  const activeView = views.find((view) => view.id === activeViewId) ?? views[0] ?? null
  const [saveAsOpen, setSaveAsOpen] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [draftDescription, setDraftDescription] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSave() {
    if (!edited || !canSave || saving) {
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
    setSaveAsOpen(true)
  }

  async function handleSaveAs() {
    const name = draftName.trim()
    if (!name || saving) {
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

  return (
    <>
      <div className="portfolio-table-view-controls">
        <select
          className="portfolio-table-view-select"
          value={activeView?.id ?? ''}
          onChange={(event) => onSelect(event.target.value)}
        >
          {views.map((view) => (
            <option key={view.id} value={view.id}>
              {`View\u00A0: ${view.name}${view.id === activeViewId && edited ? ' (Edited)' : ''}`}
            </option>
          ))}
        </select>
        <button
          type="button"
          className={`portfolio-table-view-button portfolio-table-view-quick-action ${edited ? 'portfolio-table-view-quick-action-active' : ''}`}
          disabled={saving}
          onClick={() => {
            if (edited && canSave) {
              void handleSave()
              return
            }
            openSaveAs()
          }}
          aria-label={edited && canSave ? 'Save view' : 'Create view'}
          title={edited && canSave ? 'Save view' : 'Create view'}
        >
          {edited && canSave ? (
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
            '+'
          )}
        </button>
      </div>

      {saveAsOpen ? (
        <div className="portfolio-table-view-modal-backdrop" onClick={() => setSaveAsOpen(false)}>
          <div className="portfolio-table-view-modal" onClick={(event) => event.stopPropagation()}>
            <div className="portfolio-table-view-modal-header">
              <div>
                <div className="panel-title">Create View</div>
                <div className="section-heading">Save Current Table Layout</div>
              </div>
              <button type="button" onClick={() => setSaveAsOpen(false)}>
                Close
              </button>
            </div>
            <div className="portfolio-table-view-modal-body">
              <label className="portfolio-table-view-field">
                <span>View Name</span>
                <input
                  className="transaction-filter-input"
                  value={draftName}
                  onChange={(event) => setDraftName(event.target.value)}
                  placeholder="Custom View"
                />
              </label>
              <label className="portfolio-table-view-field">
                <span>Description</span>
                <textarea
                  className="portfolio-table-view-description"
                  value={draftDescription}
                  onChange={(event) => setDraftDescription(event.target.value)}
                  placeholder="Optional notes about this view."
                />
              </label>
            </div>
            <div className="portfolio-table-view-modal-actions">
              <button type="button" onClick={() => setSaveAsOpen(false)}>
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
