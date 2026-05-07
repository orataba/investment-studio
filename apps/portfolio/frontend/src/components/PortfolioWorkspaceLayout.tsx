import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { formatCurrency, formatPercent, formatSignedCurrency, signedValueClass } from '../lib/format'
import {
  copyPortfolio,
  deletePortfolio,
  getPortfolios,
  getWorkspaceSummaryForPortfolio,
  type PortfolioWorkspaceSummary,
} from '../lib/api'
import {
  buildPortfolioSectionPath,
  PLATFORM_HOME_URL,
} from '../lib/navigation'
import { workspacePrimaryNavigation } from '../lib/portfolioIa'
import { preloadPortfolioRouteModules, preloadPortfolioTabData } from '../lib/preload'

type WorkspaceTab = {
  label: string
  href: string
  active?: boolean
}

type PortfolioWorkspaceLayoutProps = {
  activeSection: string
  children: React.ReactNode
  toolbarLabel?: string
  controls?: React.ReactNode
}

type PortfolioSelectorOption = {
  portfolio_id: string
  portfolio_name: string
}

const portfolioTabs: WorkspaceTab[] = [...workspacePrimaryNavigation]

const FALLBACK_SUMMARY: PortfolioWorkspaceSummary = {
  portfolio_id: '',
  portfolio_name: 'Portfolio',
  base_currency: 'USD',
  as_of_date: '—',
  nav: 0,
  day_change_value: 0,
  day_change_pct: 0,
  toolbar_label: 'View: Portfolio Summary',
  badges: [],
  sections: [],
}

export default function PortfolioWorkspaceLayout({
  activeSection,
  children,
  toolbarLabel,
  controls,
}: PortfolioWorkspaceLayoutProps) {
  const navigate = useNavigate()
  const { portfolioId = '' } = useParams()
  const [summary, setSummary] = useState<PortfolioWorkspaceSummary | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [portfolioOptions, setPortfolioOptions] = useState<PortfolioSelectorOption[]>([])
  const [selectorMenuOpen, setSelectorMenuOpen] = useState(false)
  const [selectorNotice, setSelectorNotice] = useState<string | null>(null)
  const selectorMenuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let cancelled = false

    if (!portfolioId) {
      setSummary(null)
      setSummaryError('Portfolio id is required.')
      return () => {
        cancelled = true
      }
    }

    getWorkspaceSummaryForPortfolio(portfolioId)
      .then((response) => {
        if (!cancelled) {
          setSummary(response)
          setSummaryError(null)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setSummaryError(error instanceof Error ? error.message : 'Failed to load workspace summary.')
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId) {
      return
    }

    preloadPortfolioRouteModules()
    preloadPortfolioTabData(portfolioId)
  }, [portfolioId])

  useEffect(() => {
    let cancelled = false

    getPortfolios()
      .then((response) => {
        if (!cancelled) {
          setPortfolioOptions(
            response.map((portfolio) => ({
              portfolio_id: portfolio.portfolio_id,
              portfolio_name: portfolio.portfolio_name,
            })),
          )
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPortfolioOptions([])
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      const target = event.target as Node | null
      if (selectorMenuOpen && selectorMenuRef.current && target && !selectorMenuRef.current.contains(target)) {
        setSelectorMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [selectorMenuOpen])

  const resolvedSummary = summary ?? FALLBACK_SUMMARY
  const resolvedPortfolioId = portfolioId || resolvedSummary.portfolio_id
  const portfolioHomePath = resolvedPortfolioId
    ? buildPortfolioSectionPath(resolvedPortfolioId, '/overview')
    : '/portfolios'
  const changeToneClassName = signedValueClass(resolvedSummary.day_change_value)
  const changeClassName = changeToneClassName
    ? `portfolio-change-value ${changeToneClassName}`
    : 'portfolio-change-value neutral-cell'
  const badges = summaryError
    ? [...resolvedSummary.badges, 'Workspace summary unavailable']
    : resolvedSummary.badges
  const selectorPortfolios =
    resolvedPortfolioId && !portfolioOptions.some((portfolio) => portfolio.portfolio_id === resolvedPortfolioId)
      ? [
          {
            portfolio_id: resolvedPortfolioId,
            portfolio_name: resolvedSummary.portfolio_name,
          },
          ...portfolioOptions,
        ]
      : portfolioOptions

  async function handleSelectorAction(action: 'copy' | 'delete') {
    try {
      if (action === 'copy') {
        const copied = await copyPortfolio(resolvedPortfolioId)
        setPortfolioOptions((current) => [
          ...current,
          {
            portfolio_id: copied.portfolio_id,
            portfolio_name: copied.portfolio_name,
          },
        ])
        setSelectorNotice(`Copied portfolio "${resolvedSummary.portfolio_name}".`)
        navigate(buildPortfolioSectionPath(copied.portfolio_id, '/overview'))
        return
      }

      await deletePortfolio(resolvedPortfolioId)
      setPortfolioOptions((current) => current.filter((portfolio) => portfolio.portfolio_id !== resolvedPortfolioId))
      setSelectorNotice(`Deleted portfolio "${resolvedSummary.portfolio_name}".`)
      navigate('/portfolios')
    } catch (requestError) {
      setSelectorNotice(
        requestError instanceof Error
          ? requestError.message
          : action === 'copy'
          ? 'Failed to copy portfolio.'
          : 'Failed to delete portfolio.',
      )
    } finally {
      setSelectorMenuOpen(false)
    }
  }

  return (
    <section className="terminal-page portfolio-workspace-page">
      <header className="portfolio-workspace-shell">
        <div className="portfolio-toolbar-band">
          <div className="workspace-breadcrumbs">
            <a href={PLATFORM_HOME_URL} className="workspace-breadcrumb-link">
              Home
            </a>
            <span className="workspace-breadcrumb-separator">/</span>
            <Link to="/portfolios" className="workspace-breadcrumb-link">
              Portfolio
            </Link>
            <span className="workspace-breadcrumb-separator">/</span>
            <Link to={portfolioHomePath} className="workspace-breadcrumb-link">
              {resolvedSummary.portfolio_name}
            </Link>
            <span className="workspace-breadcrumb-separator">/</span>
            <span className="workspace-breadcrumb-current">{activeSection}</span>
          </div>
          <div className="workspace-app-heading">
            <div className="workspace-app-title">Portfolio</div>
            <div className="workspace-app-as-of">As of {resolvedSummary.as_of_date || '—'}</div>
          </div>
          <div className="portfolio-selector-row">
            <Link className="workspace-selector-chip workspace-selector-chip-inactive workspace-selector-chip-home" to="/portfolios">
              <span className="workspace-selector-home-icon" aria-hidden="true">
                <svg viewBox="0 0 16 16">
                  <path d="M2.5 7.2 8 2.8l5.5 4.4v5.5H9.8V9.5H6.2v3.2H2.5Z" fill="currentColor" />
                </svg>
              </span>
              <span className="workspace-selector-chip-label">All</span>
            </Link>
            {selectorPortfolios.map((portfolio) =>
              portfolio.portfolio_id === resolvedPortfolioId ? (
                <div className="workspace-selector-menu-shell" key={portfolio.portfolio_id} ref={selectorMenuRef}>
                  <div className="workspace-selector-chip workspace-selector-chip-active">
                    <Link
                      className="workspace-selector-chip-label workspace-selector-chip-label-active"
                      to={portfolioHomePath}
                    >
                      {portfolio.portfolio_name}
                    </Link>
                    <button
                      type="button"
                      className="workspace-selector-menu-trigger workspace-selector-menu-trigger-active"
                      onClick={() => setSelectorMenuOpen((current) => !current)}
                      aria-label="Portfolio actions"
                    >
                      ...
                    </button>
                  </div>
                  {selectorMenuOpen ? (
                    <div className="workspace-selector-menu">
                      <button
                        type="button"
                        onClick={() => {
                          void handleSelectorAction('copy')
                        }}
                      >
                        Copy Portfolio
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          void handleSelectorAction('delete')
                        }}
                      >
                        Delete Portfolio
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : (
                <Link
                  className="workspace-selector-chip workspace-selector-chip-inactive"
                  key={portfolio.portfolio_id}
                  to={buildPortfolioSectionPath(portfolio.portfolio_id, '/overview')}
                >
                  <span className="workspace-selector-chip-label">{portfolio.portfolio_name}</span>
                </Link>
              ),
            )}
            <Link className="workspace-create-link" to="/portfolios">
              + Create Portfolio
            </Link>
          </div>
          <div className="portfolio-header-row">
            <div className="portfolio-title-stack">
              <div className="portfolio-headline">
                <span className="portfolio-name">{resolvedSummary.portfolio_name}</span>
                <span className="portfolio-nav-value">
                  {formatCurrency(resolvedSummary.nav, resolvedSummary.base_currency)}
                </span>
                <span className={changeClassName}>
                  {formatSignedCurrency(resolvedSummary.day_change_value, resolvedSummary.base_currency)} (
                  {formatPercent(
                    resolvedSummary.day_change_pct == null
                      ? null
                      : Math.abs(resolvedSummary.day_change_pct),
                  )}
                  )
                </span>
              </div>
              <div className="portfolio-subhead-row">
                {badges.map((badge) => (
                  <span className="portfolio-subhead-meta" key={badge}>
                    {badge}
                  </span>
                ))}
              </div>
            </div>
          </div>
          <nav className="portfolio-tabs" aria-label="Portfolio sections">
            {portfolioTabs.map((item) => (
              <Link
                key={item.label}
                className={`portfolio-tab ${activeSection === item.label ? 'portfolio-tab-active' : ''}`}
                to={buildPortfolioSectionPath(resolvedPortfolioId, item.href)}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          {summaryError ? <div className="inline-notice inline-notice-error">{summaryError}</div> : null}
          {selectorNotice ? <div className="inline-notice">{selectorNotice}</div> : null}
          {controls ? <div className="portfolio-extra-controls">{controls}</div> : null}
        </div>
      </header>

      {children}
    </section>
  )
}
