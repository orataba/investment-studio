import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import {
  createPortfolio,
  copyPortfolio,
  deletePortfolio,
  getPortfolios,
  reorderPortfolios,
  type PortfolioEntryRecord,
} from '../lib/api'
import { formatCurrency, formatPercent, formatSignedCurrency } from '../lib/format'
import { buildPortfolioSectionPath, PLATFORM_HOME_URL } from '../lib/navigation'

const FALLBACK_PORTFOLIOS: PortfolioEntryRecord[] = []

export default function PortfoliosPage() {
  const navigate = useNavigate()
  const [portfolios, setPortfolios] = useState<PortfolioEntryRecord[]>([])
  const [error, setError] = useState<string | null>(null)
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)

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

  const resolvedPortfolios = portfolios.length ? portfolios : FALLBACK_PORTFOLIOS
  const totalNav = resolvedPortfolios.reduce((sum, item) => sum + item.nav, 0)
  const totalDayChange = resolvedPortfolios.reduce((sum, item) => sum + (item.day_change_value ?? 0), 0)
  const totalDayChangePct = totalNav === 0 ? 0 : totalDayChange / (totalNav - totalDayChange || totalNav || 1)
  const totalNavLabel = formatCurrency(totalNav, resolvedPortfolios[0]?.base_currency ?? 'USD')
  const totalChangeLabel = formatSignedCurrency(totalDayChange, resolvedPortfolios[0]?.base_currency ?? 'USD')
  const totalChangeClassName =
    totalDayChange < 0
      ? 'portfolio-entry-change-negative'
      : 'portfolio-entry-change-positive'

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

  async function handleCreatePortfolio() {
    const proposedName = window.prompt('Portfolio name')
    const name = proposedName?.trim()
    if (!name) {
      return
    }

    try {
      const created = await createPortfolio({ name })
      setPortfolios((current) => [...current, created])
      setNotice(`Created portfolio "${created.portfolio_name}".`)
      navigate(buildPortfolioSectionPath(created.portfolio_id, '/overview'))
    } catch (requestError) {
      setNotice(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to create portfolio.',
      )
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
          <span className="portfolio-entry-nav">{totalNavLabel}</span>
          <span className={totalChangeClassName}>
            {totalChangeLabel} ({formatPercent(totalDayChangePct)})
          </span>
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
                      onClick={async () => {
                        try {
                          await deletePortfolio(portfolio.portfolio_id)
                          setPortfolios((current) =>
                            current.filter((item) => item.portfolio_id !== portfolio.portfolio_id),
                          )
                          setNotice(`Deleted portfolio "${portfolio.portfolio_name}".`)
                        } catch (requestError) {
                          setNotice(
                            requestError instanceof Error
                              ? requestError.message
                              : 'Failed to delete portfolio.',
                          )
                        } finally {
                          setMenuOpenId(null)
                        }
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
                <span>{portfolio.securities_count} Securities</span>
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
              void handleCreatePortfolio()
            }}
          >
            + Create Portfolio
          </button>
        </div>
      </section>
    </section>
  )
}
