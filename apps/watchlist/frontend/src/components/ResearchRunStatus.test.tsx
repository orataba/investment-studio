// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResearchRunStatus from '../../../../../packages/ui/src/ResearchRunStatus'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'

const requested = '2026-10-09T04:00:00Z'
const started = '2026-10-09T04:08:00Z'
beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-09T04:16:00Z'))
  window.history.replaceState(null, '', '/?lang=zh-Hans')
})
afterEach(() => { cleanup(); vi.useRealTimers(); window.history.replaceState(null, '', '/') })

it('measures the current execution from its persisted start and excludes queue time', () => {
  render(<ResearchRunStatus runId="run" status="running" createdAt={requested}
    execution={{ started_at: started, stage: 'review', attempt: 2 }} shared />)
  expect(screen.getByText('请求时间').parentElement?.textContent).toContain('2026-10-09 04:00:00 UTC')
  expect(screen.getByText('本次执行开始').parentElement?.textContent).toContain('2026-10-09 04:08:00 UTC')
  expect(screen.getByText('8:00')).toBeTruthy()
  expect(screen.queryByText('16:00')).toBeNull()
  expect(screen.getByText('核对研究')).toBeTruthy()
  expect(screen.getByText('每阶段上限（分钟）')).toBeTruthy()
  expect(screen.getByText('以上为本次执行总耗时，不是当前阶段计时。')).toBeTruthy()
  act(() => { vi.advanceTimersByTime(60000) })
  expect(screen.getByText('9:00')).toBeTruthy()
})

it.each([false, true])('does not count a queued run as executing (resume=%s)', (resume) => {
  render(<ResearchRunStatus runId="queued" status="queued" createdAt={requested}
    execution={{ started_at: started, stage: 'generation', resume, attempt: resume ? 2 : 0 }} />)
  expect(screen.getByText('本次执行尚未开始')).toBeTruthy()
  expect(screen.queryByText('恢复运行中')).toBeNull()
  expect(screen.queryByText('本次执行耗时')).toBeNull()
  expect(screen.queryByText('本次执行开始')).toBeNull()
  expect(screen.queryByText('分析与撰写')).toBeNull()
  expect(screen.queryByText('0')).toBeNull()
  if (resume) expect(screen.getByText('等待恢复')).toBeTruthy()
  else expect(screen.getAllByText('等待执行')).toHaveLength(2)
})

it('does not infer an execution start from a historical request or stale completion time', () => {
  const { rerender } = render(<ResearchRunStatus runId="old" status="running" createdAt={requested} />)
  expect(screen.queryByText('本次执行耗时')).toBeNull()
  rerender(<ResearchRunStatus runId="old" status="running" createdAt={requested}
    completedAt="2026-10-09T04:04:00Z" execution={{ started_at: started, attempt: 2 }} />)
  expect(screen.getByText('8:00')).toBeTruthy()
})

it('freezes a completed attempt at its own completion rather than the wall clock', () => {
  render(<ResearchRunStatus runId="done" status="completed" createdAt={requested}
    completedAt="2026-10-09T04:12:00Z" execution={{ started_at: started, attempt: 2 }} />)
  expect(screen.getByText('4:00')).toBeTruthy()
  act(() => { vi.advanceTimersByTime(60000) })
  expect(screen.getByText('4:00')).toBeTruthy()
})

it('translates the distinct request and execution clocks with stage limit explanation', async () => {
  window.history.replaceState(null, '', '/?lang=en')
  render(<LanguageProvider><ResearchRunStatus runId="english" status="running" createdAt={requested}
    execution={{ started_at: started, attempt: 1 }} shared /></LanguageProvider>)
  await act(async () => {})
  expect(screen.getByText('Requested')).toBeTruthy()
  expect(screen.getByText('Current attempt started')).toBeTruthy()
  expect(screen.getByText('Current attempt elapsed')).toBeTruthy()
  expect(screen.getByText('Limit per stage (minutes)')).toBeTruthy()
  expect(screen.getByText('The elapsed time above covers this whole attempt, not the current stage.')).toBeTruthy()
})


it('retains a paused attempt without treating it as complete, failed or still running', () => {
  render(<ResearchRunStatus runId="paused" status="paused" createdAt={requested}
    completedAt="2026-10-09T04:12:00Z" execution={{ started_at: started, stage: 'paused', attempt: 1 }} />)
  expect(screen.getAllByText('已暂停').length).toBeGreaterThan(0)
  expect(screen.getByText('研究已暂停。已有草稿与证据保留，此任务不会自动恢复。')).toBeTruthy()
  expect(screen.queryByText('已完成')).toBeNull()
  expect(screen.queryByText('执行未完成')).toBeNull()
  expect(screen.getByText('4:00')).toBeTruthy()
  act(() => vi.advanceTimersByTime(60000))
  expect(screen.getByText('4:00')).toBeTruthy()
  expect(vi.getTimerCount()).toBe(0)
})
