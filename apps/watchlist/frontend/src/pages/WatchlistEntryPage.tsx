import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import {
  createWatchlist,
  copyWatchlist,
  deleteWatchlist,
  getWatchlists,
  reorderWatchlists,
  type WatchlistRecord,
} from '../lib/api'
import { buildWatchlistPath, PLATFORM_HOME_URL } from '../lib/navigation'

export default function WatchlistEntryPage() {
  const navigate = useNavigate()
  const [watchlists, setWatchlists] = useState<WatchlistRecord[]>([])
  const [error, setError] = useState<string | null>(null)
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)

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

  const totalProducts = watchlists.reduce((sum, item) => sum + item.item_count, 0)

  function moveWatchlist(sourceId: string, targetId: string) {
    if (sourceId === targetId) {
      return
    }

    let nextOrder: WatchlistRecord[] = []
    setWatchlists((current) => {
      const sourceIndex = current.findIndex((item) => item.watchlist_id === sourceId)
      const targetIndex = current.findIndex((item) => item.watchlist_id === targetId)

      if (sourceIndex === -1 || targetIndex === -1) {
        return current
      }

      const next = [...current]
      const [moved] = next.splice(sourceIndex, 1)
      next.splice(targetIndex, 0, moved)
      nextOrder = next
      return next
    })

    if (nextOrder.length) {
      void reorderWatchlists(nextOrder.map((item) => item.watchlist_id)).catch((requestError) => {
        setNotice(
          requestError instanceof Error
            ? requestError.message
            : 'Failed to reorder watchlists.',
        )
        void getWatchlists().then(setWatchlists).catch(() => undefined)
      })
    }
  }

  async function handleCreateWatchlist() {
    const proposedName = window.prompt('Watchlist name')
    const name = proposedName?.trim()
    if (!name) {
      return
    }

    try {
      const created = await createWatchlist({ name })
      setWatchlists((current) => [...current, created])
      setNotice(`Created watchlist "${created.name}".`)
      navigate(buildWatchlistPath(created.watchlist_id))
    } catch (requestError) {
      setNotice(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to create watchlist.',
      )
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
            {watchlists.length} Watchlists · {totalProducts} Securities
          </span>
        </div>
        <div className="toolbar">
          <Link to="/instruments" className="toolbar-link">
            Instruments
          </Link>
          <Link to="/monitoring" className="toolbar-link">
            Monitoring
          </Link>
          <a href={PLATFORM_HOME_URL} className="toolbar-link">
            Platform Home
          </a>
        </div>
      </header>

      {error ? <div className="panel error-state">{error}</div> : null}
      {notice ? <div className="inline-notice">{notice}</div> : null}

      <section className="watchlist-entry-list-shell">
        <div className="watchlist-entry-list-toolbar">
          <button type="button" className="watchlist-entry-sort-button">
            Sort By: Name
            <span className="watchlist-entry-sort-caret" aria-hidden="true" />
          </button>
        </div>
        <div className="watchlist-entry-grid">
          {watchlists.map((watchlist) => (
            <article
              key={watchlist.watchlist_id}
              className={`watchlist-entry-card ${draggingId === watchlist.watchlist_id ? 'entry-card-dragging' : ''}`}
              draggable
              onDragStart={() => setDraggingId(watchlist.watchlist_id)}
              onDragEnd={() => setDraggingId(null)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={() => {
                if (draggingId) {
                  moveWatchlist(draggingId, watchlist.watchlist_id)
                }
                setDraggingId(null)
              }}
            >
              <div className="watchlist-entry-card-leading">
                <button
                  type="button"
                  className="watchlist-entry-grip"
                  onClick={() => setNotice('Drag cards to reorder watchlists.')}
                  aria-label={`Reorder ${watchlist.name}`}
                  title="Drag to reorder"
                >
                  <svg viewBox="0 0 12 16">
                    <circle cx="4" cy="4" r="1" fill="currentColor" />
                    <circle cx="8" cy="4" r="1" fill="currentColor" />
                    <circle cx="4" cy="8" r="1" fill="currentColor" />
                    <circle cx="8" cy="8" r="1" fill="currentColor" />
                    <circle cx="4" cy="12" r="1" fill="currentColor" />
                    <circle cx="8" cy="12" r="1" fill="currentColor" />
                  </svg>
                </button>
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
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            await deleteWatchlist(watchlist.watchlist_id)
                            setWatchlists((current) =>
                              current.filter((item) => item.watchlist_id !== watchlist.watchlist_id),
                            )
                            setNotice(`Deleted watchlist "${watchlist.name}".`)
                          } catch (requestError) {
                            setNotice(
                              requestError instanceof Error
                                ? requestError.message
                                : 'Failed to delete watchlist.',
                            )
                          } finally {
                            setMenuOpenId(null)
                          }
                        }}
                      >
                        Delete Watchlist
                      </button>
                    </div>
                  ) : null}
                </div>
                <div className="watchlist-entry-card-icon">
                  <svg viewBox="0 0 24 24">
                    <path
                      d="M6 7.5h12M6 12h12M6 16.5h8"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                    />
                  </svg>
                </div>
              </div>
              <Link className="watchlist-entry-card-main" to={buildWatchlistPath(watchlist.watchlist_id)}>
                <div className="watchlist-entry-card-title-stack">
                  <strong className="watchlist-entry-name">{watchlist.name}</strong>
                  <span className="watchlist-entry-meta">Watchlist</span>
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
          ))}
        </div>
        <div className="watchlist-entry-create-card">
          <button
            type="button"
            className="watchlist-create-link"
            onClick={() => {
              void handleCreateWatchlist()
            }}
          >
            + Create Watchlist
          </button>
        </div>
      </section>
    </section>
  )
}
