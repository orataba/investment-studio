import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import {
  createPortfolio,
  copyPortfolio,
  deletePortfolio,
  getPortfolios,
  reorderPortfolios,
  SUPPORTED_PORTFOLIO_CURRENCIES,
  type PortfolioEntryRecord,
} from '../lib/api'
import { formatCurrency, formatPercent, formatSignedCurrency } from '../lib/format'
import { buildPortfolioSectionPath, PLATFORM_HOME_URL } from '../lib/navigation'
import { groupPortfolioTotalsByBaseCurrency } from '../lib/portfolioTotals'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'

const FALLBACK_PORTFOLIOS: PortfolioEntryRecord[] = []

function formatAsOfDate(value: string | null | undefined) {
  if (!value) {
    return '-'
  }
  const [year, month, day] = value.split('-')
  if (!year || !month || !day) {
    return value
  }
  return `${year}-${month}-${day}`
}

export default function PortfoliosPage() {
  const navigate = useNavigate()
  const [portfolios, setPortfolios] = useState<PortfolioEntryRecord[]>([])
  const [error, setError] = useState<string | null>(null)
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<PortfolioEntryRecord | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createBaseCurrency, setCreateBaseCurrency] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const createNameInputRef = useRef<HTMLInputElement>(null)
  const closeCreateDialog = () => {
    if (!creating) {
      setCreateOpen(false)
      setCreateError(null)
    }
  }
  const createDialogRef = useModalDialog(createOpen, closeCreateDialog, createNameInputRef)

  useEffect(() => {
    let cancelled = false

    getPortfolios()
      .then((response) => {
        if (!cancelled) {
          setPortfolios(response)
          setError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setPortfolios(FALLBACK_PORTFOLIOS)
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load portfolios entry.',
          )
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

  useEffect(() => {
    if (!notice) {
      return undefined
    }
    const timeoutId = window.setTimeout(() => setNotice(null), 2800)
    return () => window.clearTimeout(timeoutId)
  }, [notice])

  const resolvedPortfolios = portfolios.length ? portfolios : FALLBACK_PORTFOLIOS
  const totalsByBaseCurrency = groupPortfolioTotalsByBaseCurrency(resolvedPortfolios)

  function movePortfolio(sourceId: string, targetId: string) {
    if (sourceId === targetId) {
      return
    }

    let nextOrder: PortfolioEntryRecord[] = []
    setPortfolios((current) => {
      const sourceIndex = current.findIndex((item) => item.portfolio_id === sourceId)
      const targetIndex = current.findIndex((item) => item.portfolio_id === targetId)
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
      void reorderPortfolios(nextOrder.map((item) => item.portfolio_id)).catch((requestError) => {
        setNotice(
          requestError instanceof Error
            ? requestError.message
            : 'Failed to reorder portfolios.',
        )
        void getPortfolios().then(setPortfolios).catch(() => undefined)
      })
    }
  }

  async function handleCreatePortfolio(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = createName.trim()
    if (!name) {
      setCreateError('Enter a portfolio name.')
      return
    }
    if (!SUPPORTED_PORTFOLIO_CURRENCIES.includes(
      createBaseCurrency as (typeof SUPPORTED_PORTFOLIO_CURRENCIES)[number],
    )) {
      setCreateError('Select the portfolio base currency.')
      return
    }

    setCreating(true)
    setCreateError(null)
    try {
      const created = await createPortfolio({
        name,
        base_currency: createBaseCurrency as (typeof SUPPORTED_PORTFOLIO_CURRENCIES)[number],
      })
      setPortfolios((current) => [...current, created])
      setNotice(`Created portfolio "${created.portfolio_name}".`)
      setCreateOpen(false)
      navigate(buildPortfolioSectionPath(created.portfolio_id, '/overview'))
    } catch (requestError) {
      setCreateError(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to create portfolio.',
      )
    } finally {
      setCreating(false)
    }
  }

  async function handleDeletePortfolio() {
    if (!pendingDelete || deleting) {
      return
    }
    setDeleting(true)
    try {
      await deletePortfolio(pendingDelete.portfolio_id)
      setPortfolios((current) =>
        current.filter((item) => item.portfolio_id !== pendingDelete.portfolio_id),
      )
      setNotice(`Deleted portfolio "${pendingDelete.portfolio_name}".`)
      setPendingDelete(null)
    } catch (requestError) {
      setNotice(
        requestError instanceof Error ? requestError.message : 'Failed to delete portfolio.',
      )
    } finally {
      setDeleting(false)
    }
  }

  return (
    <section className="terminal-page">
      <header className="portfolio-entry-shell">
        <div className="workspace-breadcrumbs">
          <a href={PLATFORM_HOME_URL} className="workspace-breadcrumb-link">
            Home
          </a>
          <span className="workspace-breadcrumb-separator">/</span>
          <span className="workspace-breadcrumb-current">Portfolio</span>
        </div>
        <div className="portfolio-entry-hero">
          <h1 className="portfolio-entry-title">All Portfolios</h1>
          {totalsByBaseCurrency.map((total) => (
            <span className="portfolio-entry-nav" key={total.baseCurrency} title={`${total.portfolioCount} portfolios`}>
              {total.baseCurrency ? formatCurrency(total.nav, total.baseCurrency) : 'Base currency unavailable'}
              {' · '}
              <span
                className={
                  total.dayChangeValue != null && total.dayChangeValue < 0
                    ? 'portfolio-entry-change-negative'
                    : 'portfolio-entry-change-positive'
                }
              >
                {total.baseCurrency ? formatSignedCurrency(total.dayChangeValue, total.baseCurrency) : '—'}
              </span>
            </span>
          ))}
        </div>
      </header>

      {error ? <div className="panel error-state">{error}</div> : null}
      {notice ? <div className="inline-notice">{notice}</div> : null}

      <section className="portfolio-entry-list-shell">
        {resolvedPortfolios.map((portfolio) => (
          <article
            key={portfolio.portfolio_id}
            className={`portfolio-entry-card ${draggingId === portfolio.portfolio_id ? 'entry-card-dragging' : ''}`}
            draggable
            onDragStart={() => setDraggingId(portfolio.portfolio_id)}
            onDragEnd={() => setDraggingId(null)}
            onDragOver={(event) => event.preventDefault()}
            onDrop={() => {
              if (draggingId) {
                movePortfolio(draggingId, portfolio.portfolio_id)
              }
              setDraggingId(null)
            }}
          >
            <div className="portfolio-entry-card-leading">
              <button
                type="button"
                className="portfolio-entry-grip"
                onClick={() => setNotice('Drag cards to reorder portfolios.')}
                aria-label={`Reorder ${portfolio.portfolio_name}`}
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
              <div className="workspace-selector-menu-shell" ref={menuOpenId === portfolio.portfolio_id ? menuRef : null}>
                <button
                  type="button"
                  className="workspace-selector-menu-trigger"
                  onClick={() => setMenuOpenId((current) => (current === portfolio.portfolio_id ? null : portfolio.portfolio_id))}
                  aria-label={`${portfolio.portfolio_name} actions`}
                >
                  ...
                </button>
                {menuOpenId === portfolio.portfolio_id ? (
                  <div className="workspace-selector-menu">
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          const copied = await copyPortfolio(portfolio.portfolio_id)
                          setPortfolios((current) => [...current, copied])
                          setNotice(`Copied portfolio "${portfolio.portfolio_name}".`)
                        } catch (requestError) {
                          setNotice(
                            requestError instanceof Error
                              ? requestError.message
                              : 'Failed to copy portfolio.',
                          )
                        } finally {
                          setMenuOpenId(null)
                        }
                      }}
                    >
                      Copy Portfolio
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setPendingDelete(portfolio)
                        setMenuOpenId(null)
                      }}
                    >
                      Delete Portfolio
                    </button>
                  </div>
                ) : null}
              </div>
            </div>
            <Link className="portfolio-entry-card-main" to={buildPortfolioSectionPath(portfolio.portfolio_id, '/overview')}>
              <div className="portfolio-entry-card-title-stack">
                <strong>{portfolio.portfolio_name}</strong>
                <span>
                  {portfolio.securities_count} Securities | As of {formatAsOfDate(portfolio.as_of_date)}
                </span>
              </div>
              <div className="portfolio-entry-card-metrics">
                <strong>{formatCurrency(portfolio.nav, portfolio.base_currency)}</strong>
                <span className={portfolio.day_change_value != null && portfolio.day_change_value < 0 ? 'portfolio-entry-change-negative' : 'portfolio-entry-change-positive'}>
                  {formatSignedCurrency(portfolio.day_change_value, portfolio.base_currency)} ({formatPercent(portfolio.day_change_pct)})
                </span>
              </div>
              <span className="portfolio-entry-card-arrow" aria-hidden="true">
                &#8250;
              </span>
            </Link>
          </article>
        ))}
        <div className="portfolio-entry-create-card">
          <button
            type="button"
            className="workspace-create-link"
            onClick={() => {
              setCreateName('')
              setCreateBaseCurrency('')
              setCreateError(null)
              setCreateOpen(true)
            }}
          >
            + Create Portfolio
          </button>
        </div>
      </section>
      {createOpen ? (
        <div
          className="portfolio-settings-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeCreateDialog()
            }
          }}
        >
          <div
            ref={createDialogRef}
            className="portfolio-settings-modal portfolio-create-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-portfolio-title"
            tabIndex={-1}
          >
            <div className="portfolio-settings-modal-header">
              <strong id="create-portfolio-title">Create Portfolio</strong>
              <button type="button" disabled={creating} onClick={closeCreateDialog}>Close</button>
            </div>
            <form className="portfolio-settings-form" onSubmit={(event) => void handleCreatePortfolio(event)}>
              {createError ? <div className="portfolio-settings-notice error-state">{createError}</div> : null}
              <div className="portfolio-settings-grid">
                <label>
                  <span>Portfolio Name</span>
                  <input
                    ref={createNameInputRef}
                    value={createName}
                    disabled={creating}
                    maxLength={200}
                    onChange={(event) => setCreateName(event.target.value)}
                  />
                </label>
                <label>
                  <span>Base Currency</span>
                  <select
                    value={createBaseCurrency}
                    disabled={creating}
                    onChange={(event) => setCreateBaseCurrency(event.target.value)}
                  >
                    <option value="" disabled>Select currency</option>
                    {SUPPORTED_PORTFOLIO_CURRENCIES.map((currencyCode) => (
                      <option key={currencyCode} value={currencyCode}>{currencyCode}</option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="portfolio-settings-modal-actions">
                <button type="button" disabled={creating} onClick={closeCreateDialog}>Cancel</button>
                <button type="submit" disabled={creating}>
                  {creating ? 'Creating…' : 'Create Portfolio'}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Delete Portfolio"
        description={
          <>
            This permanently deletes the portfolio, including its accounts, transactions,
            classifications, and snapshots. This action cannot be undone.
          </>
        }
        confirmLabel="Delete Portfolio"
        confirmationText={pendingDelete?.portfolio_name}
        busy={deleting}
        onCancel={() => setPendingDelete(null)}
        onConfirm={handleDeletePortfolio}
      />
    </section>
  )
}
