import { useEffect, useMemo, useState } from 'react'

import {
  getPortfolioInstrumentEventTasks,
  reviewPortfolioInstrumentEventTask,
  type PortfolioInstrumentEventTaskRecord,
} from '../lib/api'
import { formatNumber, formatQuantity } from '../lib/format'


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
  const [tasks, setTasks] = useState<PortfolioInstrumentEventTaskRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [workingTaskId, setWorkingTaskId] = useState<string | null>(null)
  const [dismissTaskId, setDismissTaskId] = useState<string | null>(null)
  const [dismissReason, setDismissReason] = useState('')
  const [reviewedBy, setReviewedBy] = useState('')
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

  if (!loading && !error && !orderedTasks.length) {
    return null
  }

  return (
    <section className="fund-distribution-task-panel" aria-label="Fund distribution reviews">
      <div className="fund-distribution-task-header">
        <div>
          <div className="panel-title">Fund Distribution Review</div>
          <div className="portfolio-detail-meta">
            Confirmed Registry events never post cash or units automatically.
          </div>
        </div>
        {orderedTasks.length ? (
          <div className="fund-distribution-task-header-actions">
            <label className="fund-distribution-reviewer-field">
              <span>Operator identity</span>
              <input
                type="text"
                value={reviewedBy}
                maxLength={200}
                placeholder="Name or operations account"
                onChange={(event) => setReviewedBy(event.target.value)}
              />
            </label>
            <span className="fund-distribution-task-count">{orderedTasks.length} need attention</span>
          </div>
        ) : null}
      </div>

      {error ? <div className="inline-notice inline-notice-error">{error}</div> : null}
      {loading ? <div className="portfolio-detail-meta">Checking Registry events…</div> : null}

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
                <div className="fund-distribution-task-title">
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
                      disabled={isWorking || !reviewedBy.trim()}
                      onClick={() => onRecord(task, 'dividend', reviewedBy.trim())}
                    >
                      Record Cash Dividend
                    </button>
                    <button
                      type="button"
                      className="toolbar-link"
                      disabled={isWorking || !reviewedBy.trim()}
                      onClick={() => onRecord(task, 'dividend_reinvestment', reviewedBy.trim())}
                    >
                      Record Reinvestment
                    </button>
                  </>
                ) : null}
                {task.status === 'needs_review' && linkedTransactionIds.length ? (
                  <button
                    type="button"
                    className="toolbar-link button-primary"
                    disabled={isWorking || !reviewedBy.trim()}
                    onClick={() =>
                      submitReview(
                        task,
                        'processed',
                        linkedTransactionIds,
                        'Reconfirmed against the current Registry event revision.',
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
                    disabled={isWorking || !reviewedBy.trim()}
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
                      disabled={isWorking || !reviewedBy.trim() || !dismissReason.trim()}
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
                    disabled={isWorking || !reviewedBy.trim() || linkedTransactionIds.length > 0}
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
    </section>
  )
}
