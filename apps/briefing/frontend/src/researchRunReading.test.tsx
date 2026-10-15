// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import ResearchRunStatus from '../../../../packages/ui/src/ResearchRunStatus'

afterEach(cleanup)
it('keeps failure and actionable retry guidance visible while retaining technical evidence in details', () => {
  render(<ResearchRunStatus runId="run-503" status="failed" createdAt="2026-10-15T00:00:00Z"
    error={{ type: 'service_unavailable', summary: '上游服务暂时不可用，已有证据已保留。', status_code: 503, retryable: true }} />)
  const details = screen.getByText('运行详情').closest('details')!
  expect(details.open).toBe(false)
  expect(screen.getByRole('alert').textContent).toBe('上游服务暂时不可用，已有证据已保留。')
  expect(screen.getByRole('alert').closest('details')).toBeNull()
  expect(screen.getByText('run-503').closest('details')).toBe(details)
  expect(screen.getByText('HTTP 503').closest('details')).toBe(details)
  expect(screen.getByText('此错误允许重试；是否自动重试取决于任务调度与授权。').closest('details')).toBe(details)
})
