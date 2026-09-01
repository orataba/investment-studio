import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import {
  getPortfolioTableViewStore,
  savePortfolioTableViewStore,
  type PortfolioTableViewScope,
} from '../lib/api'
import PortfolioTableViewControls, {
  type PortfolioTableViewOption,
} from './PortfolioTableViewControls'

export type HoldingsSectionSystemView = {
  id: string
  name: string
  columns: string[]
}

type HoldingsSectionViewState = {
  columns: string[]
}

type HoldingsSectionTableView = PortfolioTableViewOption & {
  state: HoldingsSectionViewState
  createdAt?: string
  updatedAt?: string
}

type HoldingsSectionViewStore = {
  activeViewId: string
  views: HoldingsSectionTableView[]
}

type ConfigurableColumn = {
  key: string
  label: string
}

type ConfigurableHoldingsSectionProps = {
  portfolioId: string
  viewScope: PortfolioTableViewScope
  sectionKey: string
  id: string
  title: string
  count?: number
  className?: string
  columns: ConfigurableColumn[]
  requiredColumnKey: string
  systemViews: HoldingsSectionSystemView[]
  onVisibleColumnsChange?: (sectionKey: string, columns: string[]) => void
  children: (visibleColumnKeys: string[]) => ReactNode
}

function normalizeColumns(
  value: unknown,
  allColumnKeys: string[],
  requiredColumnKey: string,
  fallbackColumns: string[],
) {
  const requested = Array.isArray(value)
    ? value.filter((column): column is string => typeof column === 'string')
    : fallbackColumns
  const requestedSet = new Set(requested)
  const normalized = allColumnKeys.filter(
    (column) => column === requiredColumnKey || requestedSet.has(column),
  )
  return normalized.length
    ? normalized
    : allColumnKeys.filter((column) => column === requiredColumnKey)
}

function viewStateEquals(left: HoldingsSectionViewState, right: HoldingsSectionViewState) {
  return JSON.stringify(left.columns) === JSON.stringify(right.columns)
}

function createViewId() {
  return `custom:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 8)}`
}

export default function ConfigurableHoldingsSection({
  portfolioId,
  viewScope,
  sectionKey,
  id,
  title,
  count,
  className,
  columns,
  requiredColumnKey,
  systemViews,
  onVisibleColumnsChange,
  children,
}: ConfigurableHoldingsSectionProps) {
  const columnSignature = columns.map((column) => column.key).join('\u0000')
  const systemViewSignature = systemViews
    .map((view) => `${view.id}:${view.name}:${view.columns.join(',')}`)
    .join('\u0000')
  const allColumnKeys = useMemo(
    () => columns.map((column) => column.key),
    [columnSignature],
  )
  const normalizedSystemViews = useMemo<HoldingsSectionTableView[]>(
    () =>
      systemViews.map((view) => ({
        ...view,
        readonly: true,
        state: {
          columns: normalizeColumns(
            view.columns,
            allColumnKeys,
            requiredColumnKey,
            allColumnKeys,
          ),
        },
      })),
    [allColumnKeys, requiredColumnKey, systemViewSignature],
  )
  const defaultView = normalizedSystemViews[0]!

  function normalizeViewStore(value: unknown): HoldingsSectionViewStore {
    const record = value && typeof value === 'object'
      ? (value as Partial<HoldingsSectionViewStore>)
      : {}
    const customViews = Array.isArray(record.views)
      ? record.views.flatMap((candidate) => {
          if (!candidate || typeof candidate !== 'object') {
            return []
          }
          const view = candidate as Partial<HoldingsSectionTableView>
          if (typeof view.id !== 'string' || !view.id.startsWith('custom:')) {
            return []
          }
          return [{
            id: view.id,
            name:
              typeof view.name === 'string' && view.name.trim()
                ? view.name.trim()
                : 'Custom View',
            description: typeof view.description === 'string' ? view.description : null,
            readonly: false,
            createdAt: typeof view.createdAt === 'string' ? view.createdAt : undefined,
            updatedAt: typeof view.updatedAt === 'string' ? view.updatedAt : undefined,
            state: {
              columns: normalizeColumns(
                view.state?.columns,
                allColumnKeys,
                requiredColumnKey,
                defaultView.state.columns,
              ),
            },
          }]
        })
      : []
    const views = [...normalizedSystemViews, ...customViews]
    const activeViewId =
      typeof record.activeViewId === 'string' &&
      views.some((view) => view.id === record.activeViewId)
        ? record.activeViewId
        : defaultView.id
    return { activeViewId, views }
  }

  const defaultStore = useMemo(
    () => normalizeViewStore(null),
    [columnSignature, systemViewSignature],
  )
  const [viewStore, setViewStore] = useState<HoldingsSectionViewStore>(defaultStore)
  const [activeViewId, setActiveViewId] = useState(defaultStore.activeViewId)
  const [visibleColumnKeys, setVisibleColumnKeys] = useState(
    defaultView.state.columns,
  )
  const [columnDraft, setColumnDraft] = useState(defaultView.state.columns)
  const [columnsOpen, setColumnsOpen] = useState(false)
  const [columnSearch, setColumnSearch] = useState('')
  const [ready, setReady] = useState(false)
  const [viewError, setViewError] = useState<string | null>(null)
  const persistedStoreRef = useRef<{ portfolioId: string; serialized: string } | null>(null)
  const columnsDialogRef = useModalDialog(columnsOpen, () => setColumnsOpen(false))

  const activeView =
    viewStore.views.find((view) => view.id === activeViewId) ?? defaultView
  const normalizedVisibleColumnKeys = normalizeColumns(
    visibleColumnKeys,
    allColumnKeys,
    requiredColumnKey,
    defaultView.state.columns,
  )
  const viewEdited = !viewStateEquals(
    { columns: normalizedVisibleColumnKeys },
    activeView.state,
  )
  const fieldsEdited = viewEdited
  const filteredColumns = columns.filter((column) => {
    const query = columnSearch.trim().toLowerCase()
    return !query || column.label.toLowerCase().includes(query) || column.key.toLowerCase().includes(query)
  })

  useEffect(() => {
    let cancelled = false
    const nextDefaultStore = normalizeViewStore(null)
    persistedStoreRef.current = null
    setReady(false)
    setViewError(null)
    setViewStore(nextDefaultStore)
    setActiveViewId(nextDefaultStore.activeViewId)
    setVisibleColumnKeys(defaultView.state.columns)

    getPortfolioTableViewStore<HoldingsSectionViewStore>(portfolioId, viewScope)
      .then((response) => {
        if (cancelled) {
          return
        }
        const nextStore = response.store
          ? normalizeViewStore(response.store)
          : nextDefaultStore
        const nextActiveView =
          nextStore.views.find((view) => view.id === nextStore.activeViewId) ?? defaultView
        persistedStoreRef.current = {
          portfolioId,
          serialized: JSON.stringify(nextStore),
        }
        setViewStore(nextStore)
        setActiveViewId(nextActiveView.id)
        setVisibleColumnKeys(nextActiveView.state.columns)
        setReady(true)
      })
      .catch((requestError: unknown) => {
        if (cancelled) {
          return
        }
        setViewError(
          requestError instanceof Error ? requestError.message : 'Table views unavailable.',
        )
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId, viewScope, columnSignature, systemViewSignature])

  useEffect(() => {
    if (!ready) {
      return
    }
    const serialized = JSON.stringify(viewStore)
    if (
      persistedStoreRef.current?.portfolioId === portfolioId &&
      persistedStoreRef.current.serialized === serialized
    ) {
      return
    }
    persistedStoreRef.current = { portfolioId, serialized }
    setViewError(null)
    savePortfolioTableViewStore(portfolioId, viewScope, viewStore).catch(
      (requestError: unknown) => {
        setViewError(
          requestError instanceof Error ? requestError.message : 'Failed to save table views.',
        )
      },
    )
  }, [portfolioId, ready, viewScope, viewStore])

  useEffect(() => {
    onVisibleColumnsChange?.(sectionKey, normalizedVisibleColumnKeys)
  }, [onVisibleColumnsChange, sectionKey, normalizedVisibleColumnKeys.join('\u0000')])

  function handleSelectView(viewId: string) {
    const nextView = viewStore.views.find((view) => view.id === viewId) ?? defaultView
    setActiveViewId(nextView.id)
    setVisibleColumnKeys(nextView.state.columns)
    setViewStore((current) => ({ ...current, activeViewId: nextView.id }))
  }

  function handleSaveView() {
    const timestamp = new Date().toISOString()
    setViewStore((current) => ({
      ...current,
      activeViewId,
      views: current.views.map((view) =>
        view.id === activeViewId
          ? {
              ...view,
              state: { columns: normalizedVisibleColumnKeys },
              updatedAt: timestamp,
            }
          : view,
      ),
    }))
  }

  function handleSaveViewAs(name: string, description: string | null) {
    const timestamp = new Date().toISOString()
    const nextView: HoldingsSectionTableView = {
      id: createViewId(),
      name,
      description,
      readonly: false,
      createdAt: timestamp,
      updatedAt: timestamp,
      state: { columns: normalizedVisibleColumnKeys },
    }
    setViewStore((current) => ({
      ...current,
      activeViewId: nextView.id,
      views: [...current.views, nextView],
    }))
    setActiveViewId(nextView.id)
  }

  function handleDeleteView(viewId: string) {
    const target = viewStore.views.find((view) => view.id === viewId)
    if (!target || target.readonly) {
      return
    }
    const deletingActiveView = viewId === activeViewId
    setViewStore((current) => ({
      ...current,
      activeViewId: deletingActiveView ? defaultView.id : current.activeViewId,
      views: current.views.filter((view) => view.id !== viewId),
    }))
    if (deletingActiveView) {
      setActiveViewId(defaultView.id)
      setVisibleColumnKeys(defaultView.state.columns)
    }
  }

  function toggleDraftColumn(columnKey: string, checked: boolean) {
    const nextDraft = checked
      ? [...columnDraft, columnKey]
      : columnDraft.filter((key) => key !== columnKey)
    setColumnDraft(
      normalizeColumns(
        nextDraft,
        allColumnKeys,
        requiredColumnKey,
        defaultView.state.columns,
      ),
    )
  }

  return (
    <section
      className={`holdings-section ${className ?? ''}`.trim()}
      aria-labelledby={id}
    >
      <div className="holdings-section-heading">
        <h2 id={id}>{title}</h2>
        <div className="holdings-section-heading-actions">
          {count != null ? (
            <span className="holdings-section-count">{count.toLocaleString()}</span>
          ) : null}
          {ready ? (
            <PortfolioTableViewControls
              views={viewStore.views}
              activeViewId={activeViewId}
              edited={viewEdited}
              canSave={!activeView.readonly}
              canDelete
              onSelect={handleSelectView}
              onSave={handleSaveView}
              onSaveAs={handleSaveViewAs}
              onDelete={handleDeleteView}
              labelPrefix={`${title} View`}
            />
          ) : (
            <span className="portfolio-table-view-status" title={viewError ?? undefined}>
              {viewError ? 'Views unavailable' : 'Loading views'}
            </span>
          )}
          <button
            type="button"
            className={`portfolio-table-toolbar-button ${
              fieldsEdited ? 'portfolio-table-toolbar-button-active' : ''
            }`}
            onClick={() => {
              setColumnDraft(normalizedVisibleColumnKeys)
              setColumnSearch('')
              setColumnsOpen(true)
            }}
          >
            {title} Fields
          </button>
          {ready && viewError ? (
            <span className="portfolio-table-view-status" role="status" title={viewError}>
              View save failed
            </span>
          ) : null}
        </div>
      </div>

      {children(normalizedVisibleColumnKeys)}

      {columnsOpen ? (
        <div className="portfolio-table-config-backdrop" onClick={() => setColumnsOpen(false)}>
          <div
            ref={columnsDialogRef}
            className="portfolio-table-config-modal portfolio-table-config-columns-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Choose ${title} columns`}
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="portfolio-table-config-header">
              <div className="panel-title">{title} Fields</div>
              <button type="button" onClick={() => setColumnsOpen(false)}>
                Close
              </button>
            </div>
            <div className="portfolio-table-config-search">
              <input
                className="portfolio-table-config-search-input"
                placeholder="Search fields"
                value={columnSearch}
                onChange={(event) => setColumnSearch(event.target.value)}
              />
            </div>
            <div className="portfolio-table-config-fields holdings-section-config-fields">
              {filteredColumns.length ? (
                filteredColumns.map((column) => {
                  const locked = column.key === requiredColumnKey
                  return (
                    <label className="portfolio-table-config-field-item" key={column.key}>
                      <input
                        type="checkbox"
                        checked={columnDraft.includes(column.key)}
                        disabled={locked}
                        onChange={(event) => toggleDraftColumn(column.key, event.target.checked)}
                      />
                      <div>
                        <div className="portfolio-table-config-field-label">{column.label}</div>
                        {locked ? (
                          <div className="portfolio-table-config-field-meta">required</div>
                        ) : null}
                      </div>
                    </label>
                  )
                })
              ) : (
                <div className="portfolio-table-config-field-empty">No fields.</div>
              )}
            </div>
            <div className="portfolio-table-config-actions portfolio-table-config-actions-sticky">
              <button
                type="button"
                onClick={() => {
                  setColumnDraft(normalizedVisibleColumnKeys)
                  setColumnsOpen(false)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                onClick={() => {
                  setVisibleColumnKeys(
                    normalizeColumns(
                      columnDraft,
                      allColumnKeys,
                      requiredColumnKey,
                      defaultView.state.columns,
                    ),
                  )
                  setColumnsOpen(false)
                }}
              >
                Update
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  )
}
