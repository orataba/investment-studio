import { useEffect, useState } from 'react'
import './research-run-status.css'

export type ResearchRunExecution = {
  stage?: string; attempt?: number; started_at?: string; resume?: boolean
  failures?: Array<{ attempt?: number; failed_at?: string; error?: { type?: string; summary?: string }; manual_recovery?: boolean }>
}
export type ResearchRunError = { type?: string; summary?: string; retryable?: boolean }
const stages: Record<string, string> = { queued: '等待执行', preparation: '准备证据', generation: '分析与撰写', review: '核对研究', publication: '保存成果' }
export function researchStamp(value?: string | null) {
  if (!value) return '—'
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : `${date.toISOString().slice(0, 19).replace('T', ' ')} UTC`
}

/** Shows persisted execution facts; a local timer never declares a run failed. */
export default function ResearchRunStatus({ runId, status, createdAt, completedAt, execution, error, shared = false, timeoutSeconds = 1800 }: {
  runId: string; status: string; createdAt?: string | null; completedAt?: string | null
  execution?: ResearchRunExecution; error?: ResearchRunError; shared?: boolean; timeoutSeconds?: number | null
}) {
  const active = status === 'queued' || status === 'running'
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active, runId])
  const start = createdAt ? Date.parse(createdAt) : NaN
  const end = completedAt ? Date.parse(completedAt) : active ? now : NaN
  const elapsed = Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, Math.floor((end - start) / 1000)) : null
  const stage = execution?.stage || (status === 'queued' ? 'queued' : '')
  return <section className="assistant-run-status" aria-label="任务运行状态">
    <p><span>任务编号</span> <code translate="no">{runId}</code> · <span>{active ? execution?.resume || (execution?.attempt || 1) > 1 ? '恢复运行中' : status === 'queued' ? '等待执行' : '运行中' : status === 'failed' ? '未完成' : status === 'limited' ? '已完成，覆盖受限' : '已完成'}</span></p>
    <p><span>开始时间</span> <time dateTime={createdAt || undefined}>{researchStamp(createdAt)}</time>{elapsed !== null && <> · <span>经过时间</span> <span translate="no">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}</span></>}{active && stage && <> · <span>当前阶段</span> <span>{stages[stage] || stage}</span></>}{execution?.attempt && <> · <span>执行次数</span> {execution.attempt}</>}</p>
    {active && <p>关闭面板或离开页面不会停止任务；重新打开后可继续查看同一任务。请等待终态，避免重复提交。</p>}
    {active && timeoutSeconds !== null && <p>{shared ? <span>分析与核对阶段分别计时</span> : <span>本轮分析</span>} · <span>运行上限（分钟）</span> {Math.ceil(timeoutSeconds / 60)} · <span>耗时本身不代表失败。</span></p>}
    {status === 'failed' && <p>{shared && error?.retryable && (execution?.attempt || 1) < 3 ? '本次是可恢复的暂时性错误；后台任务仅在仍符合调度与授权范围时自动重试（最多两次），人工任务需重新发起。' : '本轮已停止；检查错误与已有证据后，可重新发起。'}</p>}
    {error?.type && <p><span>错误类别</span> <code translate="no">{error.type}</code></p>}
    {execution?.failures && execution.failures.length > 0 && <details><summary>重试记录</summary>{execution.failures.map((failure, index) => <p key={index}><span>{failure.manual_recovery ? '人工恢复' : '自动恢复'}</span> · {researchStamp(failure.failed_at)} · <code translate="no">{failure.error?.type}</code></p>)}</details>}
  </section>
}
