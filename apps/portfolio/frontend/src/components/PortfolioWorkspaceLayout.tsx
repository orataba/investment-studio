import { LanguageSelector, useLanguage } from '../../../../../packages/ui/src/i18n'
import WorkspaceTools from '../../../../../packages/ui/src/WorkspaceTools'
import { Fragment, type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import { formatCurrency, formatPercent, formatSignedCurrency, signedValueClass } from '../lib/format'
import {
  copyPortfolio,
  deletePortfolio,
  getPortfolios,
  getPortfolioRiskPolicy,
  getWorkspaceSummaryForPortfolio,
  SUPPORTED_PORTFOLIO_CURRENCIES,
  updatePortfolioSettings,
  updatePortfolioRiskPolicy,
  type PortfolioResearchMissingReturnPolicy,
  type PortfolioRiskContributionMode,
  type PortfolioRiskCovarianceModel,
  type PortfolioRiskPolicyRecord,
  type PortfolioWorkspaceSummary,
  type SupportedPortfolioCurrency,
} from '../lib/api'
import {
  buildPortfolioSectionPath,
  HOME_URL,
} from '../lib/navigation'
import { workspacePrimaryNavigation } from '../lib/portfolioIa'
import { preloadPortfolioSection } from '../lib/preload'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import OptionOutcomePrompt from './OptionOutcomePrompt'
import PortfolioRiskDrawer from './PortfolioRiskDrawer'
import PortfolioAssistantDrawer from './PortfolioAssistantDrawer'
import { usePortfolioAccess } from './PortfolioAccessProvider'
import PortfolioMembersSettings from './PortfolioMembersSettings'
import { usePortfolioCapabilities } from './PortfolioCapabilitiesProvider'

type WorkspaceTab = {
  label: string
  href: string
  active?: boolean
}

type PortfolioWorkspaceLayoutProps = {
  activeSection: string
  children: React.ReactNode
  controls?: React.ReactNode
  busy?: boolean
}

type PortfolioSelectorOption = {
  portfolio_id: string
  portfolio_name: string
}

const portfolioTabs: WorkspaceTab[] = [...workspacePrimaryNavigation]
const DEFAULT_RISK_POLICY_WINDOW_DAYS = 90
const RISK_WINDOW_OPTIONS = [
  { value: 30, label: '1M' },
  { value: 90, label: '3M' },
  { value: 180, label: '6M' },
  { value: 366, label: '12M' },
  { value: 730, label: '24M' },
] as const
const RISK_MISSING_RETURN_POLICY_OPTIONS: Array<{ value: PortfolioResearchMissingReturnPolicy; label: string }> = [
  { value: 'strict', label: 'Strict' },
  { value: 'complete_case_drop', label: 'Complete Case Drop' },
]
const RISK_MODEL_OPTIONS: Array<{ value: PortfolioRiskCovarianceModel; label: string }> = [
  { value: 'ewma_vol_shrinkage_corr_covariance', label: 'EWMA + Shrinkage' },
  { value: 'ewma_covariance', label: 'EWMA' },
  { value: 'sample_covariance', label: 'Sample' },
]
const RISK_CONTRIBUTION_MODE_OPTIONS: Array<{ value: PortfolioRiskContributionMode; label: string }> = [
  { value: 'signed', label: 'Signed' },
  { value: 'abs', label: 'Absolute' },
]
const SUPPORTED_RISK_POLICY_WINDOW_DAYS = new Set<number>(RISK_WINDOW_OPTIONS.map((option) => option.value))

function extractErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Request failed.'
}

export default function PortfolioWorkspaceLayout({
  activeSection,
  children,
  controls,
  busy = false,
}: PortfolioWorkspaceLayoutProps) {
  const { language } = useLanguage()
  const access = usePortfolioAccess()
  const { research_enabled } = usePortfolioCapabilities()
  const visiblePortfolioTabs = portfolioTabs.filter((item) => item.href !== '/research' || research_enabled)
  const navigate = useNavigate()
  const { portfolioId = '', holdingId } = useParams()
  const [pageParams] = useSearchParams()
  const [summary, setSummary] = useState<PortfolioWorkspaceSummary | null>(null)
  const [summaryRevision, setSummaryRevision] = useState(0)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [portfolioOptions, setPortfolioOptions] = useState<PortfolioSelectorOption[]>([])
  const [selectorMenuOpen, setSelectorMenuOpen] = useState(false)
  const [selectorNotice, setSelectorNotice] = useState<string | null>(null)
  const [pendingPortfolioDelete, setPendingPortfolioDelete] = useState<PortfolioSelectorOption | null>(null)
  const [deletingPortfolio, setDeletingPortfolio] = useState(false)
  const [deletePortfolioError, setDeletePortfolioError] = useState<string | null>(null)
  const [riskSettingsOpen, setRiskSettingsOpen] = useState(false)
  const [riskDrawerOpen, setRiskDrawerOpen] = useState(false)
  const [assistantParams, setAssistantParams] = useState<URLSearchParams | null>(null)
  const [riskSettingsLoading, setRiskSettingsLoading] = useState(false)
  const [riskSettingsSaving, setRiskSettingsSaving] = useState(false)
  const [riskSettingsError, setRiskSettingsError] = useState<string | null>(null)
  const [settingsBaseCurrency, setSettingsBaseCurrency] =
    useState<SupportedPortfolioCurrency>('CNY')
  const [riskPolicyLookbackDays, setRiskPolicyLookbackDays] = useState(String(DEFAULT_RISK_POLICY_WINDOW_DAYS))
  const [riskPolicyMissingReturnPolicy, setRiskPolicyMissingReturnPolicy] =
    useState<PortfolioResearchMissingReturnPolicy>('strict')
  const [riskPolicyModelId, setRiskPolicyModelId] =
    useState<PortfolioRiskCovarianceModel>('ewma_vol_shrinkage_corr_covariance')
  const [riskPolicyContributionMode, setRiskPolicyContributionMode] =
    useState<PortfolioRiskContributionMode>('signed')
  const selectorMenuRef = useRef<HTMLDivElement | null>(null)
  const navigationPreloadTimerRef = useRef<number | null>(null)
  const riskSettingsDialogRef = useModalDialog(riskSettingsOpen, () => {
    if (!riskSettingsSaving) {
      setRiskSettingsOpen(false)
    }
  })

  useEffect(() => {
    let cancelled = false

    if (!portfolioId) {
      setSummary(null)
      setSummaryLoading(false)
      setSummaryError('Portfolio id is required.')
      return () => {
        cancelled = true
      }
    }

    setSummary(null)
    setSummaryLoading(true)
    setSummaryError(null)
    getWorkspaceSummaryForPortfolio(portfolioId)
      .then((response) => {
        if (!cancelled) {
          setSummary(response)
          setSummaryError(null)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setSummary(null)
          setSummaryError(error instanceof Error ? error.message : 'Failed to load workspace summary.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setSummaryLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId, summaryRevision])

  useEffect(() => {
    return () => {
      if (navigationPreloadTimerRef.current != null) {
        window.clearTimeout(navigationPreloadTimerRef.current)
      }
    }
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

  useEffect(() => {
    if (!selectorNotice) {
      return undefined
    }
    const timeoutId = window.setTimeout(() => setSelectorNotice(null), 2800)
    return () => window.clearTimeout(timeoutId)
  }, [selectorNotice])

  useEffect(() => {
    setRiskSettingsOpen(false)
    setRiskDrawerOpen(false)
    setAssistantParams(null)
    setRiskSettingsError(null)
  }, [portfolioId])

  const activeSummary = summary?.portfolio_id === portfolioId ? summary : null
  const summaryBusy = summaryLoading || Boolean(summary && !activeSummary)
  const portfolioName = activeSummary?.portfolio_name ?? 'Portfolio'
  const resolvedPortfolioId = portfolioId || activeSummary?.portfolio_id || ''
  const portfolioHomePath = resolvedPortfolioId
    ? buildPortfolioSectionPath(resolvedPortfolioId, '/overview')
    : '/portfolios'
  const changeToneClassName = signedValueClass(activeSummary?.day_change_value)
  const changeClassName = changeToneClassName
    ? `portfolio-change-value ${changeToneClassName}`
    : 'portfolio-change-value neutral-cell'
  const badges = summaryError ? ['Workspace summary unavailable'] : []
  const selectorPortfolios =
    resolvedPortfolioId && !portfolioOptions.some((portfolio) => portfolio.portfolio_id === resolvedPortfolioId)
      ? [
          {
            portfolio_id: resolvedPortfolioId,
            portfolio_name: portfolioName,
          },
          ...portfolioOptions,
        ]
      : portfolioOptions

  function cancelNavigationPreload() {
    if (navigationPreloadTimerRef.current != null) {
      window.clearTimeout(navigationPreloadTimerRef.current)
      navigationPreloadTimerRef.current = null
    }
  }

  function scheduleNavigationPreload(section: string, immediate = false) {
    cancelNavigationPreload()
    if (!resolvedPortfolioId || section === activeSection) {
      return
    }
    if (immediate) {
      preloadPortfolioSection(section, resolvedPortfolioId)
      return
    }
    navigationPreloadTimerRef.current = window.setTimeout(() => {
      navigationPreloadTimerRef.current = null
      preloadPortfolioSection(section, resolvedPortfolioId)
    }, 140)
  }

  async function handleSelectorAction(action: 'copy' | 'delete') {
    if (action === 'delete') {
      setSelectorMenuOpen(false)
      setDeletePortfolioError(null)
      setPendingPortfolioDelete({
        portfolio_id: resolvedPortfolioId,
        portfolio_name: portfolioName,
      })
      return
    }
    try {
      const copied = await copyPortfolio(resolvedPortfolioId)
      setPortfolioOptions((current) => [
        ...current,
        {
          portfolio_id: copied.portfolio_id,
          portfolio_name: copied.portfolio_name,
        },
      ])
      setSelectorNotice(`Copied portfolio "${portfolioName}".`)
      navigate(buildPortfolioSectionPath(copied.portfolio_id, '/overview'))
    } catch (requestError) {
      setSelectorNotice(
        requestError instanceof Error ? requestError.message : 'Failed to copy portfolio.',
      )
    } finally {
      setSelectorMenuOpen(false)
    }
  }

  async function handleConfirmedDelete() {
    if (!pendingPortfolioDelete || deletingPortfolio) {
      return
    }
    const target = pendingPortfolioDelete
    setDeletingPortfolio(true)
    setDeletePortfolioError(null)
    try {
      await deletePortfolio(target.portfolio_id)
      setPortfolioOptions((current) =>
        current.filter((portfolio) => portfolio.portfolio_id !== target.portfolio_id),
      )
      setPendingPortfolioDelete(null)
      navigate('/portfolios')
    } catch (requestError) {
      setDeletePortfolioError(
        requestError instanceof Error ? requestError.message : 'Failed to delete portfolio.',
      )
    } finally {
      setDeletingPortfolio(false)
    }
  }

  function applyRiskPolicy(policy: PortfolioRiskPolicyRecord) {
    setRiskPolicyLookbackDays(String(policy.lookback_days))
    setRiskPolicyMissingReturnPolicy(policy.missing_return_policy)
    setRiskPolicyModelId(policy.covariance_model_id)
    setRiskPolicyContributionMode(policy.contribution_mode)
  }

  async function handleOpenRiskSettings() {
    if (!resolvedPortfolioId) {
      return
    }
    setRiskSettingsOpen(true)
    setRiskSettingsLoading(true)
    setRiskSettingsError(null)
    if (
      activeSummary
      && SUPPORTED_PORTFOLIO_CURRENCIES.includes(
        activeSummary.base_currency as SupportedPortfolioCurrency,
      )
    ) {
      setSettingsBaseCurrency(activeSummary.base_currency as SupportedPortfolioCurrency)
    }
    try {
      const policy = await getPortfolioRiskPolicy(resolvedPortfolioId)
      applyRiskPolicy(policy)
    } catch (error) {
      setRiskSettingsError(extractErrorMessage(error))
    } finally {
      setRiskSettingsLoading(false)
    }
  }

  async function handleSaveRiskSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!resolvedPortfolioId) {
      return
    }
    const parsedLookbackDays = Number(riskPolicyLookbackDays)
    if (!SUPPORTED_RISK_POLICY_WINDOW_DAYS.has(parsedLookbackDays)) {
      setRiskSettingsError('Risk window must be 1M, 3M, 6M, 12M, or 24M.')
      return
    }
    const resolvedLookbackDays = parsedLookbackDays
    setRiskSettingsSaving(true)
    setRiskSettingsError(null)
    try {
      const baseCurrencyChanged = settingsBaseCurrency !== activeSummary?.base_currency
      const [policy] = await Promise.all([
        updatePortfolioRiskPolicy(resolvedPortfolioId, {
          covariance_model_id: riskPolicyModelId,
          lookback_days: resolvedLookbackDays,
          calculation_frequency: 'daily',
          missing_return_policy: riskPolicyMissingReturnPolicy,
          contribution_mode: riskPolicyContributionMode,
        }),
        baseCurrencyChanged
          ? updatePortfolioSettings(resolvedPortfolioId, {
              base_currency: settingsBaseCurrency,
            })
          : Promise.resolve(null),
      ])
      applyRiskPolicy(policy)
      window.dispatchEvent(
        new CustomEvent('portfolio-risk-policy-updated', {
          detail: {
            portfolioId: resolvedPortfolioId,
            policy,
          },
        }),
      )
      setSummaryRevision((current) => current + 1)
      setRiskSettingsOpen(false)
      setSelectorNotice(
        baseCurrencyChanged
          ? `Reporting currency changed to ${settingsBaseCurrency}; portfolio values are recalculating.`
          : 'Portfolio settings updated.',
      )
    } catch (error) {
      setRiskSettingsError(extractErrorMessage(error))
    } finally {
      setRiskSettingsSaving(false)
    }
  }

  function handleOptionOutcomeRecorded(message: string) {
    setSummaryRevision((current) => current + 1)
    setSelectorNotice(message)
    if (activeSection !== 'Holdings') {
      navigate(buildPortfolioSectionPath(resolvedPortfolioId, '/holdings'))
    }
  }

  function openAssistant(instrumentId?: string, question?: string) {
    const next = new URLSearchParams({ portfolio: resolvedPortfolioId, tab: activeSection })
    for (const key of ['currency', 'benchmark', 'start', 'end', 'as_of_date', 'account_id']) {
      const value = pageParams.get(key) ?? (key === 'start' || key === 'end' ? pageParams.get(`${key}_date`) : null)
      if (value !== null) next.set(key, value)
    }
    if (holdingId) next.set('holding_id', holdingId)
    if (instrumentId) next.set('instruments', instrumentId)
    if (question) next.set('question', question)
    setAssistantParams(next)
  }

  return (
    <section
      className="terminal-page portfolio-workspace-page"
      aria-busy={summaryBusy || busy}
    >
      <header className="portfolio-workspace-shell" aria-busy={summaryBusy}>
        <div className="portfolio-toolbar-band">
          <div className="portfolio-topbar">
            <div className="workspace-breadcrumbs">
              <a data-workspace-link href={HOME_URL} className="workspace-breadcrumb-link">
                Home
              </a>
              <span className="workspace-breadcrumb-separator">/</span>
              <Link to="/portfolios" className="workspace-breadcrumb-link">
                Portfolio
              </Link>
              <span className="workspace-breadcrumb-separator">/</span>
              <Link to={portfolioHomePath} className="workspace-breadcrumb-link" translate="no">
                {portfolioName}
              </Link>
              <span className="workspace-breadcrumb-separator">/</span>
              <span className="workspace-breadcrumb-current">{activeSection}</span>
            </div>
            <LanguageSelector />
          </div>
          <div className="workspace-app-heading">
            <div className="workspace-app-title">Portfolio</div>
            <div className="workspace-app-as-of">
              {summaryBusy ? (
                <span
                  className="portfolio-summary-skeleton portfolio-summary-skeleton-as-of"
                  aria-hidden="true"
                />
              ) : (
                <>As of {activeSummary?.as_of_date || '—'}</>
              )}
            </div>
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
                     translate="no">
                      {portfolio.portfolio_name}
                    </Link>
                    <button
                      type="button"
                      className="workspace-selector-menu-trigger workspace-selector-menu-trigger-active"
                      disabled={!access?.can_manage}
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
                  <span className="workspace-selector-chip-label" translate="no">{portfolio.portfolio_name}</span>
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
                {summaryBusy ? (
                  <>
                    <span className="sr-only" role="status" aria-live="polite">
                      Loading portfolio summary.
                    </span>
                    <span
                      className="portfolio-summary-skeleton portfolio-summary-skeleton-name"
                      aria-hidden="true"
                    />
                    <span
                      className="portfolio-summary-skeleton portfolio-summary-skeleton-nav"
                      aria-hidden="true"
                    />
                    <span
                      className="portfolio-summary-skeleton portfolio-summary-skeleton-change"
                      aria-hidden="true"
                    />
                  </>
                ) : (
                  <>
                    <span className="portfolio-name" translate="no">{portfolioName}</span>
                    {activeSummary ? (
                      <>
                        <span className="portfolio-nav-value">
                          {formatCurrency(activeSummary.nav, activeSummary.base_currency)}
                        </span>
                        <span className={changeClassName}>
                          {formatSignedCurrency(activeSummary.day_change_value, activeSummary.base_currency)} (
                          {formatPercent(activeSummary.day_change_pct)}
                          )
                        </span>
                      </>
                    ) : null}
                  </>
                )}
              </div>
              <div className="portfolio-subhead-row">
                {badges.map((badge) => (
                  <span className="portfolio-subhead-meta" key={badge}>
                    {badge}
                  </span>
                ))}
              </div>
            </div>
            <div className="portfolio-header-actions">
              {resolvedPortfolioId && access?.can_edit ? (
                <OptionOutcomePrompt
                  portfolioId={resolvedPortfolioId}
                  onRecorded={handleOptionOutcomeRecorded}
                />
              ) : null}
              <WorkspaceTools
                settings={{ label: language === 'zh-Hans' ? '组合设置' : 'Portfolio Settings', onClick: () => void handleOpenRiskSettings(), disabled: !access?.can_edit || !resolvedPortfolioId || summaryBusy }}
                risk={{ onClick: () => setRiskDrawerOpen(true), disabled: !resolvedPortfolioId || summaryBusy }}
                assistant={{ onClick: () => openAssistant(), disabled: !resolvedPortfolioId || summaryBusy }}
              />
            </div>
          </div>
          {riskDrawerOpen && resolvedPortfolioId ? <PortfolioRiskDrawer key={`risk:${resolvedPortfolioId}`} portfolioId={resolvedPortfolioId} onClose={() => setRiskDrawerOpen(false)} onAskAssistant={openAssistant} /> : null}
          {assistantParams && resolvedPortfolioId ? <PortfolioAssistantDrawer key={`assistant:${resolvedPortfolioId}:${assistantParams.toString()}`} initialParams={assistantParams} onClose={() => setAssistantParams(null)} /> : null}
          {riskSettingsOpen ? (
            <div
              className="portfolio-settings-modal-backdrop"
              role="presentation"
              onMouseDown={(event) => {
                if (event.target === event.currentTarget && !riskSettingsSaving) {
                  setRiskSettingsOpen(false)
                }
              }}
            >
              <div
                ref={riskSettingsDialogRef}
                className="portfolio-settings-modal"
                role="dialog"
                aria-modal="true"
                aria-labelledby="portfolio-settings-title"
                aria-busy={riskSettingsLoading || riskSettingsSaving}
                tabIndex={-1}
              >
                <div className="portfolio-settings-modal-header">
                  <div>
                    <div className="panel-title" id="portfolio-settings-title">
                      Portfolio Settings
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setRiskSettingsOpen(false)}
                    disabled={riskSettingsSaving}
                    aria-label="Close settings"
                  >
                    Close
                  </button>
                </div>
                {riskSettingsError ? (
                  <div className="inline-notice inline-notice-error portfolio-settings-notice">
                    {riskSettingsError}
                  </div>
                ) : null}
                {riskSettingsLoading ? (
                  <div className="empty-state" role="status" aria-live="polite">
                    Loading risk settings.
                  </div>
                ) : (
                  <form className="portfolio-settings-form" onSubmit={(event) => void handleSaveRiskSettings(event)}>
                    <div className="portfolio-settings-grid">
                      <label className="portfolio-settings-field-wide">
                        <span>Reporting / Base Currency</span>
                        <select
                          aria-label="Reporting / Base Currency"
                          value={settingsBaseCurrency}
                          onChange={(event) =>
                            setSettingsBaseCurrency(event.target.value as SupportedPortfolioCurrency)
                          }
                          disabled={riskSettingsSaving}
                        >
                          {SUPPORTED_PORTFOLIO_CURRENCIES.map((currencyCode) => (
                            <option key={currencyCode} value={currencyCode}>
                              {currencyCode}
                            </option>
                          ))}
                        </select>
                        <small>
                          Transactions keep their original currencies. Historical NAV,
                          returns, attribution, and P&amp;L are recalculated in this currency.
                        </small>
                      </label>
                      <div className="portfolio-settings-section-title portfolio-settings-field-wide">
                        Production Risk Model
                      </div>
                      <label>
                        <span>Risk Window</span>
                        <select
                          value={riskPolicyLookbackDays}
                          onChange={(event) => setRiskPolicyLookbackDays(event.target.value)}
                          disabled={riskSettingsSaving}
                        >
                          {RISK_WINDOW_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>Missing Returns</span>
                        <select
                          value={riskPolicyMissingReturnPolicy}
                          onChange={(event) =>
                            setRiskPolicyMissingReturnPolicy(event.target.value as PortfolioResearchMissingReturnPolicy)
                          }
                          disabled={riskSettingsSaving}
                        >
                          {RISK_MISSING_RETURN_POLICY_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>Risk Model</span>
                        <select
                          value={riskPolicyModelId}
                          onChange={(event) => setRiskPolicyModelId(event.target.value as PortfolioRiskCovarianceModel)}
                          disabled={riskSettingsSaving}
                        >
                          {RISK_MODEL_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>RC Mode</span>
                        <select
                          value={riskPolicyContributionMode}
                          onChange={(event) =>
                            setRiskPolicyContributionMode(event.target.value as PortfolioRiskContributionMode)
                          }
                          disabled={riskSettingsSaving}
                        >
                          {RISK_CONTRIBUTION_MODE_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <div className="portfolio-settings-modal-actions">
                      <button
                        type="button"
                        onClick={() => setRiskSettingsOpen(false)}
                        disabled={riskSettingsSaving}
                      >
                        Cancel
                      </button>
                      <button type="submit" className="button-primary" disabled={riskSettingsSaving}>
                        {riskSettingsSaving ? 'Saving…' : 'Save Settings'}
                      </button>
                    </div>
                  </form>
                )}
                {access?.can_manage && <PortfolioMembersSettings portfolioId={portfolioId} />}
              </div>
            </div>
          ) : null}
          <nav className="portfolio-tabs" aria-label="Portfolio sections">
            {visiblePortfolioTabs.map((item) => (
              <Link
                key={item.label}
                className={`portfolio-tab ${activeSection === item.label ? 'portfolio-tab-active' : ''}`}
                to={buildPortfolioSectionPath(resolvedPortfolioId, item.href)}
                onMouseEnter={() => scheduleNavigationPreload(item.label)}
                onMouseLeave={cancelNavigationPreload}
                onFocus={() => scheduleNavigationPreload(item.label)}
                onBlur={cancelNavigationPreload}
                onPointerDown={() => scheduleNavigationPreload(item.label, true)}
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

      <Fragment key={`${resolvedPortfolioId}:${summaryRevision}`}>{children}</Fragment>
      <ConfirmDialog
        open={Boolean(pendingPortfolioDelete)}
        title="Delete Portfolio"
        description="This permanently deletes the portfolio and all of its accounts, transactions, classifications, and snapshots. This action cannot be undone."
        confirmLabel="Delete Portfolio"
        confirmationText={pendingPortfolioDelete?.portfolio_name}
        busy={deletingPortfolio}
        error={deletePortfolioError}
        onCancel={() => {
          setDeletePortfolioError(null)
          setPendingPortfolioDelete(null)
        }}
        onConfirm={handleConfirmedDelete}
      />
    </section>
  )
}
