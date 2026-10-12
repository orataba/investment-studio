import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ResearchAssistant from '../../../../packages/ui/src/ResearchAssistant'
import { readResearchAssistant } from './lib/researchAssistantApi'

afterEach(() => vi.unstubAllGlobals())

it('loads the authorized receipt on expansion and pages its complete original without refetching', async () => {
  const sourceId = 'computed:portfolio/a b?currency=HKD&snapshot=原件+#1'
  const topic = { topic_id: 'topic-1', title: 'Saved research', instrument_ids: [], portfolio_id: null, status: 'active', updated_at: '2026-10-02T00:00:00Z' }
  const receipt = { source_id: sourceId, tool: 'comparison', retrieved_at: '2026-10-02T00:00:00Z', request: { limit: 1000, instrument_ids: ['asset-1'] }, result_storage: 'computed_metrics' }
  const result = { text: 'first segment ' + 'x'.repeat(26000) + ' retained final fact', limit: 1000 }
  const evidenceUrl = `/api/research-assistant/runs/run-1/evidence?source_id=${encodeURIComponent(sourceId)}`
  const responses: Record<string, unknown> = {
    '/api/research-assistant/topics': [topic],
    '/api/research-assistant/catalogue': { instruments: [] },
    '/api/research-assistant/connections': { assistant_available: true, portfolios: [] },
    '/api/research-assistant/topics/topic-1': { topic, entries: [{ entry_id: 'run-1', kind: 'analysis', title: 'Question', body: 'Saved answer', source: '', context_json: { tool_evidence: [receipt] }, status: 'draft', created_at: topic.updated_at }] },
    [evidenceUrl]: { ...receipt, result },
  }
  const fetcher = vi.fn(async (url: string) => {
    if (!(url in responses)) throw new Error(`Unexpected test request: ${url}`)
    return new Response(JSON.stringify(responses[url]))
  })
  vi.stubGlobal('fetch', fetcher)
  render(<ResearchAssistant params={new URLSearchParams('topic=topic-1')} onParamsChange={() => {}}
    read={readResearchAssistant} write={vi.fn() as never} uploadTopicFile={vi.fn()}
    canSaveNote={false} attachmentHref={value => value} renderMarkdown={value => value} />)
  await screen.findByText('Saved answer')
  expect(fetcher.mock.calls.filter(([url]) => url === evidenceUrl)).toHaveLength(0)
  fireEvent.click(screen.getByText('查阅依据（1）'))
  fireEvent.click(screen.getByText('当时证据快照'))
  await waitFor(() => expect(screen.getByText(/first segment/)).toBeInTheDocument())
  expect(screen.queryByText(/retained final fact/)).not.toBeInTheDocument()
  const selector = screen.getByRole('combobox', { name: '跳转证据字段' })
  const limit = Array.from(selector.querySelectorAll('option')).find(option => option.textContent === 'limit')!
  expect(Number(limit.value)).toBeGreaterThan(24000)
  fireEvent.change(selector, { target: { value: limit.value } })
  expect(screen.getByText(/retained final fact/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '下一段' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '下载完整证据' })).toBeInTheDocument()
  expect(fetcher.mock.calls.filter(([url]) => url === evidenceUrl)).toHaveLength(1)
  expect(fetcher).toHaveBeenCalledWith(evidenceUrl, expect.objectContaining({ credentials: 'include' }))
})
