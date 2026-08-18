import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import {
  createWatchlist,
  copyWatchlist,
  deleteWatchlist,
  getWatchlists,
  reorderWatchlists,
  type WatchlistRecord,
} from '../lib/api'
import { buildWatchlistPath, PLATFORM_HOME_URL } from '../lib/navigation'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'

function isSystemWatchlist(watchlist: WatchlistRecord) {
  return watchlist.is_default && watchlist.owner_type === 'system'
}

export default function WatchlistEntryPage() {
  const navigate = useNavigate()
  const [watchlists, setWatchlists] = useState<WatchlistRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [reordering, setReordering] = useState(false)
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [createWatchlistName, setCreateWatchlistName] = useState('')
  const [createWatchlistDescription, setCreateWatchlistDescription] = useState('')
  const [isCreatingWatchlist, setIsCreatingWatchlist] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<WatchlistRecord | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const createNameRef = useRef<HTMLInputElement | null>(null)
  const createDialogRef = useModalDialog(
    createModalOpen,
    () => {
      if (!isCreatingWatchlist) {
        resetCreateWatchlistForm()
        setCreateModalOpen(false)
      }
    },
    createNameRef,
  )

  useEffect(() => {
    let cancelled = false

    getWatchlists()
      .then((response) => {
        if (!cancelled) {
          setWatchlists(response)
          setError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : 'Failed to load watchlists.')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      const target = event.target as Node | null
      if (menuOpenId && menuRef.current && target && !menuRef.current.contains(target)) {
        setMenuOpenId(null)
      }
    }

    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [menuOpenId])

  useEffect(() => {
    if (!notice) {
      return undefined
    }
    const timeoutId = window.setTimeout(() => setNotice(null), 2800)
    return () => window.clearTimeout(timeoutId)
  }, [notice])

  const totalProducts = watchlists
    .filter(isSystemWatchlist)
    .reduce((sum, item) => sum + item.item_count, 0)

  async function moveWatchlist(sourceId: string, targetId: string) {
    if (sourceId === targetId || reordering) {
      return
    }
    const sourceIndex = watchlists.findIndex((item) => item.watchlist_id === sourceId)
    const targetIndex = watchlists.findIndex((item) => item.watchlist_id === targetId)
    if (sourceIndex === -1 || targetIndex === -1) {
      return
    }
    const nextOrder = [...watchlists]
    const [moved] = nextOrder.splice(sourceIndex, 1)
    nextOrder.splice(targetIndex, 0, moved)
    setWatchlists(nextOrder)
    setReordering(true)
    try {
      const savedOrder = await reorderWatchlists(
        nextOrder.map((item) => item.watchlist_id),
      )
      setWatchlists(savedOrder)
    } catch (requestError) {
      setNotice(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to reorder watchlists.',
      )
      try {
        setWatchlists(await getWatchlists())
      } catch {
        setError('Failed to reload watchlist order after a save error.')
      }
    } finally {
      setReordering(false)
    }
  }

  function moveWatchlistByOffset(watchlistId: string, offset: -1 | 1) {
    const movableWatchlists = watchlists.filter((item) => !isSystemWatchlist(item))
    const currentIndex = movableWatchlists.findIndex((item) => item.watchlist_id === watchlistId)
    const target = movableWatchlists[currentIndex + offset]
    if (currentIndex === -1 || !target) {
      return
    }
    void moveWatchlist(watchlistId, target.watchlist_id)
  }

  function resetCreateWatchlistForm() {
    setCreateWatchlistName('')
    setCreateWatchlistDescription('')
    setIsCreatingWatchlist(false)
  }

  async function handleCreateWatchlist() {
    const name = createWatchlistName.trim()
    if (!name) {
      return
    }

    setIsCreatingWatchlist(true)
    setCreateError(null)
    setNotice(null)
    try {
      const created = await createWatchlist({
        name,
        description: createWatchlistDescription.trim() || null,
      })
      const nextWatchlists = await getWatchlists()
      setWatchlists(nextWatchlists)
      resetCreateWatchlistForm()
      setCreateModalOpen(false)
      setNotice(`Created watchlist "${created.name}".`)
      navigate(buildWatchlistPath(created.watchlist_id))
    } catch (requestError) {
      setCreateError(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to create watchlist.',
      )
      setIsCreatingWatchlist(false)
    }
  }

  async function handleDeleteWatchlist() {
    if (!pendingDelete || deleting) {
      return
    }
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteWatchlist(pendingDelete.watchlist_id)
      setWatchlists((current) =>
        current.filter((item) => item.watchlist_id !== pendingDelete.watchlist_id),
      )
      setNotice(`Deleted watchlist "${pendingDelete.name}".`)
      setPendingDelete(null)
    } catch (requestError) {
      setDeleteError(
        requestError instanceof Error ? requestError.message : 'Failed to delete watchlist.',
      )
    } finally {
      setDeleting(false)
    }
  }

  return (
    <section className="terminal-page">
      <header className="watchlist-entry-shell">
        <div className="watchlist-breadcrumbs">
          <a href={PLATFORM_HOME_URL} className="watchlist-breadcrumb-link">
            Home
          </a>
          <span className="watchlist-breadcrumb-separator">/</span>
          <span className="watchlist-breadcrumb-current">Watchlist</span>
        </div>
        <div className="watchlist-entry-hero">
          <h1 className="watchlist-entry-title">All Watchlists</h1>
          <span className="watchlist-entry-hero-meta">
            {loading
              ? 'Loading watchlists…'
              : error
                ? 'Watchlist totals unavailable'
                : `${watchlists.length} Watchlists · ${totalProducts} Securities`}
          </span>
        </div>
      </header>

      {error ? <div className="panel error-state">{error}</div> : null}
      {notice ? <div className="inline-notice">{notice}</div> : null}

      <section className="watchlist-entry-list-shell">
        <div className="watchlist-entry-grid">
          {watchlists.map((watchlist) => {
            const systemWatchlist = isSystemWatchlist(watchlist)
            return (
            <article
              key={watchlist.watchlist_id}
              className={`watchlist-entry-card ${systemWatchlist ? 'watchlist-entry-card-system' : ''} ${
                draggingId === watchlist.watchlist_id ? 'entry-card-dragging' : ''
              }`}
              draggable={!systemWatchlist && !reordering}
              onDragStart={() => {
                if (!systemWatchlist && !reordering) {
                  setDraggingId(watchlist.watchlist_id)
                }
              }}
              onDragEnd={() => setDraggingId(null)}
              onDragOver={(event) => {
                if (!systemWatchlist) {
                  event.preventDefault()
                }
              }}
              onDrop={() => {
                if (draggingId && !systemWatchlist) {
                  void moveWatchlist(draggingId, watchlist.watchlist_id)
                }
                setDraggingId(null)
              }}
            >
              <div className="watchlist-entry-card-leading">
                {!systemWatchlist ? (
                  <div className="watchlist-entry-grip" aria-label={`Reorder ${watchlist.name}`}>
                    <button
                      type="button"
                      onClick={() => moveWatchlistByOffset(watchlist.watchlist_id, -1)}
                      disabled={reordering || watchlists.filter((item) => !isSystemWatchlist(item))[0]?.watchlist_id === watchlist.watchlist_id}
                      aria-label={`Move ${watchlist.name} up`}
                      title="Move up"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      onClick={() => moveWatchlistByOffset(watchlist.watchlist_id, 1)}
                      disabled={reordering || watchlists.filter((item) => !isSystemWatchlist(item)).slice(-1)[0]?.watchlist_id === watchlist.watchlist_id}
                      aria-label={`Move ${watchlist.name} down`}
                      title="Move down"
                    >
                      ↓
                    </button>
                  </div>
                ) : (
                  <span className="watchlist-entry-grip watchlist-entry-grip-placeholder" aria-hidden="true">
                    <svg viewBox="0 0 12 16">
                      <circle cx="4" cy="4" r="1" fill="currentColor" />
                      <circle cx="8" cy="4" r="1" fill="currentColor" />
                      <circle cx="4" cy="8" r="1" fill="currentColor" />
                      <circle cx="8" cy="8" r="1" fill="currentColor" />
                      <circle cx="4" cy="12" r="1" fill="currentColor" />
                      <circle cx="8" cy="12" r="1" fill="currentColor" />
                    </svg>
                  </span>
                )}
                <div
                  className="watchlist-menu-shell"
                  ref={menuOpenId === watchlist.watchlist_id ? menuRef : null}
                >
                  <button
                    type="button"
                    className="watchlist-menu-trigger"
                    onClick={() =>
                      setMenuOpenId((current) =>
                        current === watchlist.watchlist_id ? null : watchlist.watchlist_id,
                      )
                    }
                    aria-label={`${watchlist.name} actions`}
                  >
                    ...
                  </button>
                  {menuOpenId === watchlist.watchlist_id ? (
                    <div className="watchlist-menu">
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            const copied = await copyWatchlist(watchlist.watchlist_id)
                            setWatchlists((current) => [...current, copied])
                            setNotice(`Copied watchlist "${watchlist.name}".`)
                          } catch (requestError) {
                            setNotice(
                              requestError instanceof Error
                                ? requestError.message
                                : 'Failed to copy watchlist.',
                            )
                          } finally {
                            setMenuOpenId(null)
                          }
                        }}
                      >
                        Copy Watchlist
                      </button>
                      {!systemWatchlist ? (
                        <button
                          type="button"
                          onClick={() => {
                            setDeleteError(null)
                            setPendingDelete(watchlist)
                            setMenuOpenId(null)
                          }}
                        >
                          Delete Watchlist
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
              <Link className="watchlist-entry-card-main" to={buildWatchlistPath(watchlist.watchlist_id)}>
                <div className="watchlist-entry-card-title-stack">
                  <strong className="watchlist-entry-name">{watchlist.name}</strong>
                  <span className="watchlist-entry-meta">
                    {systemWatchlist ? 'System List' : 'Watchlist'}
                  </span>
                </div>
                <div className="watchlist-entry-card-metrics">
                  <strong>{watchlist.item_count}</strong>
                  <span>Securities</span>
                </div>
                <span className="watchlist-entry-card-arrow" aria-hidden="true">
                  &#8250;
                </span>
              </Link>
            </article>
            )
          })}
        </div>
        <div className="watchlist-entry-create-card">
          <button
            type="button"
            className="watchlist-create-link"
            onClick={() => {
              resetCreateWatchlistForm()
              setCreateError(null)
              setCreateModalOpen(true)
              setError(null)
              setNotice(null)
            }}
          >
            + Create Watchlist
          </button>
        </div>
      </section>

      {createModalOpen ? (
        <div
          className="watchlists-modal-backdrop"
          onClick={() => {
            if (!isCreatingWatchlist) {
              resetCreateWatchlistForm()
              setCreateModalOpen(false)
            }
          }}
        >
          <div
            ref={createDialogRef}
            className="watchlists-modal watchlists-save-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-watchlist-title"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="watchlists-modal-header">
              <div>
                <div className="panel-title" id="create-watchlist-title">Create Watchlist</div>
                <div className="section-heading">Create A New List For Instruments And Views</div>
              </div>
              <button
                type="button"
                disabled={isCreatingWatchlist}
                onClick={() => {
                  resetCreateWatchlistForm()
                  setCreateModalOpen(false)
                }}
              >
                Close
              </button>
            </div>

            <div className="watchlists-modal-body">
              {createError ? <div className="panel error-state">{createError}</div> : null}
              <label className="form-field">
                <span>Name</span>
                <input
                  ref={createNameRef}
                  className="form-input"
                  value={createWatchlistName}
                  onChange={(event) => setCreateWatchlistName(event.target.value)}
                  placeholder="Coverage"
                />
              </label>
              <label className="form-field">
                <span>Description</span>
                <textarea
                  className="form-textarea"
                  value={createWatchlistDescription}
                  onChange={(event) => setCreateWatchlistDescription(event.target.value)}
                  placeholder="Optional description for this watchlist."
                />
              </label>
            </div>

            <div className="watchlists-modal-actions">
              <button
                type="button"
                disabled={isCreatingWatchlist}
                onClick={() => {
                  resetCreateWatchlistForm()
                  setCreateModalOpen(false)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={isCreatingWatchlist || !createWatchlistName.trim()}
                onClick={() => {
                  void handleCreateWatchlist()
                }}
              >
                {isCreatingWatchlist ? 'Creating...' : 'Create Watchlist'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Delete Watchlist"
        description="This permanently deletes the watchlist, its saved views, and its list membership. Shared instruments are not deleted. This action cannot be undone."
        confirmLabel="Delete Watchlist"
        confirmationText={pendingDelete?.name}
        busy={deleting}
        error={deleteError}
        onCancel={() => {
          setDeleteError(null)
          setPendingDelete(null)
        }}
        onConfirm={handleDeleteWatchlist}
      />
    </section>
  )
}
