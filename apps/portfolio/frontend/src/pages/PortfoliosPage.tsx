import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import {
  createPortfolio,
  copyPortfolio,
  deletePortfolio,
  getPortfolios,
  reorderPortfolios,
  SUPPORTED_PORTFOLIO_CURRENCIES,
  updatePortfolioSettings,
  type PortfolioEntryRecord,
  type SupportedPortfolioCurrency,
} from '../lib/api'
import { formatCurrency, formatPercent, formatSignedCurrency } from '../lib/format'
import { buildPortfolioSectionPath, PLATFORM_HOME_URL } from '../lib/navigation'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'

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

function localTodayIso() {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 10)
}

function isValidIsoDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false
  }
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

export default function PortfoliosPage() {
  const navigate = useNavigate()
  const [portfolios, setPortfolios] = useState<PortfolioEntryRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<PortfolioEntryRecord | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createBaseCurrency, setCreateBaseCurrency] = useState<SupportedPortfolioCurrency>('CNY')
  const [createInceptionDate, setCreateInceptionDate] = useState(localTodayIso)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [settingsPortfolio, setSettingsPortfolio] = useState<PortfolioEntryRecord | null>(null)
  const [settingsBaseCurrency, setSettingsBaseCurrency] = useState<SupportedPortfolioCurrency>('CNY')
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
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

  const resolvedPortfolios = portfolios
  const baseCurrencies = new Set(resolvedPortfolios.map((item) => item.base_currency))
  const commonBaseCurrency = baseCurrencies.size === 1
    ? resolvedPortfolios[0]?.base_currency
    : undefined
  const aggregateAvailable = Boolean(
    !loading &&
      !error &&
      resolvedPortfolios.length &&
      commonBaseCurrency &&
      resolvedPortfolios.every((item) => item.nav != null),
  )
  const totalNav = aggregateAvailable
    ? resolvedPortfolios.reduce((sum, item) => sum + (item.nav ?? 0), 0)
    : null
  const totalDayChange =
    aggregateAvailable && resolvedPortfolios.every((item) => item.day_change_value != null)
      ? resolvedPortfolios.reduce((sum, item) => sum + (item.day_change_value ?? 0), 0)
      : null
  const totalDayChangePct = totalNav != null && totalDayChange != null && totalNav !== 0
    ? totalDayChange / (totalNav - totalDayChange || totalNav)
    : null
  const totalNavLabel = loading
    ? 'Loading…'
    : error
      ? 'Unavailable'
      : !resolvedPortfolios.length
        ? 'No portfolios'
        : resolvedPortfolios.some((item) => item.nav == null)
          ? 'Recalculating'
          : !commonBaseCurrency
            ? 'Multiple base currencies'
            : formatCurrency(totalNav, commonBaseCurrency)
  const totalChangeLabel = aggregateAvailable
    ? `${formatSignedCurrency(totalDayChange, commonBaseCurrency)} (${formatPercent(totalDayChangePct)})`
    : !loading && !error && resolvedPortfolios.length && !commonBaseCurrency
      ? 'Totals shown per portfolio'
      : ''
  const totalChangeClassName =
    totalDayChange != null && totalDayChange < 0
      ? 'portfolio-entry-change-negative'
      : totalDayChange != null && totalDayChange > 0
        ? 'portfolio-entry-change-positive'
        : 'portfolio-entry-change-neutral'

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

  function movePortfolioByOffset(portfolioId: string, offset: -1 | 1) {
    const currentIndex = portfolios.findIndex((item) => item.portfolio_id === portfolioId)
    const target = portfolios[currentIndex + offset]
    if (currentIndex === -1 || !target) {
      return
    }
    movePortfolio(portfolioId, target.portfolio_id)
  }

  function openCreatePortfolio() {
    setCreateName('')
    setCreateBaseCurrency('CNY')
    setCreateInceptionDate(localTodayIso())
    setCreateError(null)
    setCreateOpen(true)
  }

  async function handleCreatePortfolio(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (creating) {
      return
    }
    const name = createName.trim()
    if (!name) {
      setCreateError('Enter a portfolio name.')
      return
    }
    const inceptionDate = createInceptionDate.trim()
    if (!isValidIsoDate(inceptionDate)) {
      setCreateError('Enter a valid portfolio inception date.')
      return
    }

    setCreating(true)
    setCreateError(null)
    try {
      const created = await createPortfolio({
        name,
        base_currency: createBaseCurrency,
        inception_date: inceptionDate,
      })
      setPortfolios((current) => [...current, created])
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
    setDeleteError(null)
    try {
      await deletePortfolio(pendingDelete.portfolio_id)
      setPortfolios((current) =>
        current.filter((item) => item.portfolio_id !== pendingDelete.portfolio_id),
      )
      setNotice(`Deleted portfolio "${pendingDelete.portfolio_name}".`)
      setPendingDelete(null)
    } catch (requestError) {
      setDeleteError(
        requestError instanceof Error ? requestError.message : 'Failed to delete portfolio.',
      )
    } finally {
      setDeleting(false)
    }
  }

  function openPortfolioSettings(portfolio: PortfolioEntryRecord) {
    setSettingsPortfolio(portfolio)
    setSettingsBaseCurrency(portfolio.base_currency as SupportedPortfolioCurrency)
    setSettingsError(null)
    setMenuOpenId(null)
  }

  async function handleSavePortfolioSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!settingsPortfolio || settingsSaving) {
      return
    }
    setSettingsSaving(true)
    setSettingsError(null)
    try {
      const updated = await updatePortfolioSettings(settingsPortfolio.portfolio_id, {
        base_currency: settingsBaseCurrency,
      })
      setPortfolios((current) =>
        current.map((item) => (item.portfolio_id === updated.portfolio_id ? updated : item)),
      )
      setSettingsPortfolio(null)
      setNotice(
        `Base currency changed to ${updated.base_currency}; historical values are recalculating.`,
      )
    } catch (requestError) {
      setSettingsError(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to update portfolio settings.',
      )
    } finally {
      setSettingsSaving(false)
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
          {totalChangeLabel ? <span className={totalChangeClassName}>{totalChangeLabel}</span> : null}
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
              <div className="portfolio-entry-grip" aria-label={`Reorder ${portfolio.portfolio_name}`}>
                <button
                  type="button"
                  onClick={() => movePortfolioByOffset(portfolio.portfolio_id, -1)}
                  disabled={resolvedPortfolios[0]?.portfolio_id === portfolio.portfolio_id}
                  aria-label={`Move ${portfolio.portfolio_name} up`}
                  title="Move up"
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={() => movePortfolioByOffset(portfolio.portfolio_id, 1)}
                  disabled={resolvedPortfolios[resolvedPortfolios.length - 1]?.portfolio_id === portfolio.portfolio_id}
                  aria-label={`Move ${portfolio.portfolio_name} down`}
                  title="Move down"
                >
                  ↓
                </button>
              </div>
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
                      onClick={() => openPortfolioSettings(portfolio)}
                    >
                      Portfolio Settings
                    </button>
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
                        setDeleteError(null)
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
                  {portfolio.securities_count} Securities | Started {formatAsOfDate(portfolio.inception_date)} | As of {formatAsOfDate(portfolio.as_of_date)}
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
            onClick={openCreatePortfolio}
          >
            + Create Portfolio
          </button>
        </div>
      </section>
      {settingsPortfolio ? (
        <div className="portfolio-settings-modal-backdrop">
          <section
            className="portfolio-settings-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="portfolio-settings-title"
          >
            <header className="portfolio-settings-modal-header">
              <h2 id="portfolio-settings-title">Portfolio Settings</h2>
              <button
                type="button"
                aria-label="Close portfolio settings"
                disabled={settingsSaving}
                onClick={() => setSettingsPortfolio(null)}
              >
                Close
              </button>
            </header>
            <form className="portfolio-settings-form" onSubmit={handleSavePortfolioSettings}>
              {settingsError ? (
                <div className="portfolio-settings-notice error-state" role="alert">
                  {settingsError}
                </div>
              ) : null}
              <div className="portfolio-settings-grid">
                <label htmlFor="portfolio-base-currency">
                  <span>Base Currency</span>
                  <select
                    id="portfolio-base-currency"
                    aria-label="Base Currency"
                    value={settingsBaseCurrency}
                    onChange={(event) =>
                      setSettingsBaseCurrency(event.target.value as SupportedPortfolioCurrency)
                    }
                  >
                    {SUPPORTED_PORTFOLIO_CURRENCIES.map((currencyCode) => (
                      <option key={currencyCode} value={currencyCode}>{currencyCode}</option>
                    ))}
                  </select>
                  <small>
                    Transactions keep their original currencies. Historical NAV, returns, and P&amp;L
                    are recalculated in the selected base currency.
                  </small>
                </label>
              </div>
              <footer className="portfolio-settings-modal-actions">
                <button
                  type="button"
                  disabled={settingsSaving}
                  onClick={() => setSettingsPortfolio(null)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={
                    settingsSaving ||
                    settingsBaseCurrency === settingsPortfolio.base_currency
                  }
                >
                  {settingsSaving ? 'Saving…' : 'Save Settings'}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}
      {createOpen ? (
        <div className="portfolio-settings-modal-backdrop">
          <section
            className="portfolio-settings-modal portfolio-create-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-portfolio-title"
          >
            <header className="portfolio-settings-modal-header">
              <h2 id="create-portfolio-title">Create Portfolio</h2>
              <button
                type="button"
                aria-label="Close create portfolio"
                disabled={creating}
                onClick={() => setCreateOpen(false)}
              >
                Close
              </button>
            </header>
            <form className="portfolio-settings-form" onSubmit={handleCreatePortfolio}>
              {createError ? (
                <div className="portfolio-settings-notice error-state" role="alert">
                  {createError}
                </div>
              ) : null}
              <div className="portfolio-settings-grid portfolio-create-grid">
                <label htmlFor="create-portfolio-name">
                  <span>Portfolio Name</span>
                  <input
                    id="create-portfolio-name"
                    autoFocus
                    required
                    value={createName}
                    onChange={(event) => setCreateName(event.target.value)}
                  />
                </label>
                <label htmlFor="create-portfolio-currency">
                  <span>Base Currency</span>
                  <select
                    id="create-portfolio-currency"
                    value={createBaseCurrency}
                    onChange={(event) =>
                      setCreateBaseCurrency(event.target.value as SupportedPortfolioCurrency)
                    }
                  >
                    {SUPPORTED_PORTFOLIO_CURRENCIES.map((currencyCode) => (
                      <option key={currencyCode} value={currencyCode}>{currencyCode}</option>
                    ))}
                  </select>
                </label>
                <label htmlFor="create-portfolio-inception-date">
                  <span>Inception Date</span>
                  <input
                    id="create-portfolio-inception-date"
                    type="date"
                    required
                    max={localTodayIso()}
                    value={createInceptionDate}
                    onChange={(event) => setCreateInceptionDate(event.target.value)}
                  />
                  <small>Opening balances, if any, must use this date.</small>
                </label>
              </div>
              <footer className="portfolio-settings-modal-actions">
                <button
                  type="button"
                  disabled={creating}
                  onClick={() => setCreateOpen(false)}
                >
                  Cancel
                </button>
                <button type="submit" disabled={creating}>
                  {creating ? 'Creating…' : 'Create Portfolio'}
                </button>
              </footer>
            </form>
          </section>
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
        error={deleteError}
        onCancel={() => {
          setDeleteError(null)
          setPendingDelete(null)
        }}
        onConfirm={handleDeletePortfolio}
      />
    </section>
  )
}
