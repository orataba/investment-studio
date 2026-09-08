import { useEffect, useMemo, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'

import {
  getPortfolioInstrumentEventTasks,
  reconcilePortfolioInstrumentEventTasks,
  reviewPortfolioInstrumentEventTask,
  type PortfolioInstrumentEventTaskRecord,
} from '../lib/api'
import { formatNumber, formatQuantity } from '../lib/format'
import { usePortfolioAccess } from './PortfolioAccessProvider'
import { usePortfolioSession } from './PortfolioSessionProvider'
import './fund-distribution-tasks.css'


type Props = {
  portfolioId: string
  accountNames: Record<string, string>
  refreshKey: number
  onRecord: (
    task: PortfolioInstrumentEventTaskRecord,
    transactionType: 'dividend' | 'dividend_reinvestment',
    reviewedBy: string,
  ) => void
}


export default function FundDistributionTasksPanel({
  portfolioId,
  accountNames,
  refreshKey,
  onRecord,
}: Props) {
  const { language, t } = useLanguage()
  const [open, setOpen] = useState(false)
  const dialogRef = useModalDialog(open, () => setOpen(false))
  const canEditPortfolio = Boolean(usePortfolioAccess()?.can_edit)
  const [tasks, setTasks] = useState<PortfolioInstrumentEventTaskRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [workingTaskId, setWorkingTaskId] = useState<string | null>(null)
  const [reconciling, setReconciling] = useState(false)
  const [dismissTaskId, setDismissTaskId] = useState<string | null>(null)
  const [dismissReason, setDismissReason] = useState('')
  const reviewedBy = usePortfolioSession()?.display_name ?? ''
  const [localRefreshKey, setLocalRefreshKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getPortfolioInstrumentEventTasks(portfolioId, true)
      .then((response) => {
        if (!cancelled) {
          setTasks(response.tasks)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load fund distribution reviews.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [portfolioId, refreshKey, localRefreshKey])

  const orderedTasks = useMemo(
    () =>
      [...tasks].sort((left, right) =>
        `${right.effective_date}:${right.instrument_name ?? right.instrument_id}`.localeCompare(
          `${left.effective_date}:${left.instrument_name ?? left.instrument_id}`,
        ),
      ),
    [tasks],
  )

  async function submitReview(
    task: PortfolioInstrumentEventTaskRecord,
    decision: 'processed' | 'not_applicable' | 'reopened',
    transactionIds: string[],
    note: string,
  ) {
    if (!canEditPortfolio) return
    const normalizedReviewer = reviewedBy.trim()
    if (!normalizedReviewer) {
      setError('Operator identity is required before reviewing a distribution.')
      return
    }
    setWorkingTaskId(task.instrument_event_task_id)
    setError(null)
    try {
      await reviewPortfolioInstrumentEventTask(
        portfolioId,
        task.instrument_event_task_id,
        {
          decision,
          transaction_ids: transactionIds,
          note,
          reviewed_by: normalizedReviewer,
          expected_row_version: task.row_version,
        },
      )
      setDismissTaskId(null)
      setDismissReason('')
      setLocalRefreshKey((current) => current + 1)
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to save the fund distribution review.',
      )
    } finally {
      setWorkingTaskId(null)
    }
  }

  async function reconcileDistributionEvents() {
    if (!canEditPortfolio) return
    setReconciling(true)
    setError(null)
    try {
      const response = await reconcilePortfolioInstrumentEventTasks(portfolioId, true)
      setTasks(response.tasks)
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to refresh distribution events.',
      )
    } finally {
      setReconciling(false)
    }
  }

  const triggerStatus = error
    ? language === 'zh-Hans' ? '读取或处理失败' : 'Loading or review failed'
    : loading
      ? t('Loading')
      : orderedTasks.length
        ? `${orderedTasks.length} ${t('need attention')}`
        : t('No distribution events currently need attention.')
  const triggerLabel = `${t('Fund distribution reviews')}: ${triggerStatus}`

  function recordTask(task: PortfolioInstrumentEventTaskRecord, transactionType: 'dividend' | 'dividend_reinvestment') {
    setOpen(false)
    onRecord(task, transactionType, reviewedBy.trim())
  }

  return <>
    <button
      type="button"
      className={`fund-distribution-tasks-trigger${error ? ' has-error' : orderedTasks.length ? ' needs-attention' : ''}`}
      aria-label={triggerLabel}
      title={triggerLabel}
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={() => setOpen(true)}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z" />
        <path d="M10 21h4" />
      </svg>
      {error || loading || orderedTasks.length ? <span className="fund-distribution-tasks-badge" aria-hidden="true">
        {error ? '!' : loading ? '…' : orderedTasks.length}
      </span> : null}
    </button>
    {open ? <div className="portfolio-settings-modal-backdrop fund-distribution-review-backdrop" onClick={(event) => {
      if (event.target === event.currentTarget) setOpen(false)
    }}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('Fund distribution reviews')}
        tabIndex={-1}
        className="portfolio-settings-modal fund-distribution-review-modal"
      >
        <header className="portfolio-settings-modal-header">
          <div className="panel-title">Fund Distribution Review</div>
          <button type="button" onClick={() => setOpen(false)} aria-label={language === 'zh-Hans' ? '关闭基金分红复核' : 'Close fund distribution reviews'}>×</button>
        </header>
        <div className="fund-distribution-review-body" aria-busy={loading}>
          <div className="fund-distribution-task-header">
            <div className="portfolio-detail-meta">
              Confirmed distribution events never post cash or units automatically.
            </div>
            {orderedTasks.length ? <label className="fund-distribution-reviewer-field">
              <span>Operator identity</span>
              <input type="text" value={reviewedBy} disabled={!canEditPortfolio} readOnly />
            </label> : null}
          </div>
          {error ? <div className="fund-distribution-review-error" role="alert">
            <span>{error}</span>
            <button type="button" className="toolbar-link" disabled={loading} onClick={() => setLocalRefreshKey((current) => current + 1)}>Retry</button>
          </div> : null}
          {loading ? <div className="portfolio-detail-meta" role="status">Loading</div> : null}
          {!loading && !error && !orderedTasks.length ? <div className="portfolio-detail-meta">No distribution events currently need attention.</div> : null}

          <div className="fund-distribution-task-list">
            {orderedTasks.map((task) => {
              const entitlementDate = task.record_date ?? task.effective_date
              const isWorking = workingTaskId === task.instrument_event_task_id
              const linkedTransactionIds = task.linked_transactions.map(
                (transaction) => transaction.transaction_id,
              )
              return (
                <article className="fund-distribution-task-card" key={task.instrument_event_task_id}>
                  <div className="fund-distribution-task-copy">
                    <div className="fund-distribution-task-title" translate="no">
                      {task.instrument_name ?? task.instrument_id}
                    </div>
                    <div className="portfolio-detail-meta">
                      {accountNames[task.account_id] ?? task.account_id} · entitlement {entitlementDate}
                      {' · '}{formatQuantity(task.entitled_quantity)} units
                    </div>
                    <div className="fund-distribution-task-values">
                      <span>Cash / unit {formatNumber(task.cash_per_unit, 6)}</span>
                      <span>Expected gross {formatNumber(task.expected_gross_amount, 2)}</span>
                      {task.reinvestment_nav != null ? (
                        <span>Reinvestment NAV {formatNumber(task.reinvestment_nav, 6)}</span>
                      ) : null}
                    </div>
                    {task.attention_reason ? (
                      <div className="fund-distribution-task-reason">{task.attention_reason}</div>
                    ) : null}
                    {linkedTransactionIds.length ? (
                      <div className="portfolio-detail-meta">
                        Linked: {linkedTransactionIds.join(', ')}
                      </div>
                    ) : null}
                  </div>

                  <div className="fund-distribution-task-actions">
                    {task.status === 'pending' ? (
                      <>
                        <button
                          type="button"
                          className="toolbar-link button-primary"
                          disabled={!canEditPortfolio || isWorking || !reviewedBy.trim()}
                          onClick={() => recordTask(task, 'dividend')}
                        >
                          Record Cash Dividend
                        </button>
                        <button
                          type="button"
                          className="toolbar-link"
                          disabled={!canEditPortfolio || isWorking || !reviewedBy.trim()}
                          onClick={() => recordTask(task, 'dividend_reinvestment')}
                        >
                          Record Reinvestment
                        </button>
                      </>
                    ) : null}
                    {task.status === 'needs_review' && linkedTransactionIds.length ? (
                      <button
                        type="button"
                        className="toolbar-link button-primary"
                        disabled={!canEditPortfolio || isWorking || !reviewedBy.trim()}
                        onClick={() =>
                          submitReview(
                            task,
                            'processed',
                            linkedTransactionIds,
                            'Reconfirmed against the current distribution event revision.',
                          )
                        }
                      >
                        Reconfirm Linked Facts
                      </button>
                    ) : null}
                    {task.status === 'needs_review' ? (
                      <button
                        type="button"
                        className="toolbar-link"
                        disabled={!canEditPortfolio || isWorking || !reviewedBy.trim()}
                        onClick={() =>
                          submitReview(
                            task,
                            'reopened',
                            [],
                            'Reopened for corrected Portfolio transaction entry.',
                          )
                        }
                      >
                        Detach and Reopen
                      </button>
                    ) : null}
                    {dismissTaskId === task.instrument_event_task_id ? (
                      <div className="fund-distribution-dismiss-form">
                        <input
                          value={dismissReason}
                          placeholder="Required reason"
                          onChange={(event) => setDismissReason(event.target.value)}
                        />
                        <button
                          type="button"
                          className="toolbar-link"
                          disabled={!canEditPortfolio || isWorking || !reviewedBy.trim() || !dismissReason.trim()}
                          onClick={() =>
                            submitReview(
                              task,
                              'not_applicable',
                              [],
                              dismissReason.trim(),
                            )
                          }
                        >
                          Confirm Not Applicable
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="toolbar-link"
                        disabled={!canEditPortfolio || isWorking || !reviewedBy.trim() || linkedTransactionIds.length > 0}
                        onClick={() => {
                          setDismissTaskId(task.instrument_event_task_id)
                          setDismissReason('')
                        }}
                      >
                        Mark Not Applicable
                      </button>
                    )}
                  </div>
                </article>
              )
            })}
          </div>
        </div>
        <footer className="portfolio-settings-modal-actions">
          <button
            type="button"
            disabled={!canEditPortfolio || loading || reconciling || workingTaskId !== null}
            onClick={reconcileDistributionEvents}
          >
            {reconciling ? 'Refreshing Distribution Events…' : 'Refresh Distribution Events'}
          </button>
          <button type="button" onClick={() => setOpen(false)}>Close</button>
        </footer>
      </div>
    </div> : null}
  </>
}
