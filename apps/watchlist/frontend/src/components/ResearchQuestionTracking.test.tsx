// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ResearchQuestionTracking from './ResearchQuestionTracking'
import type { ResearchUpdate } from '../lib/researchDossierApi'

afterEach(cleanup)
const question = (overrides: Partial<ResearchUpdate> = {}): ResearchUpdate => ({
  update_id: 'research:original:questions:cash', kind: 'question', title: '现金流能否持续', body: '当期得到支持',
  recorded_at: '2026-09-10T08:00:00Z', author: '研究员', author_role: 'researcher', theme_ids: [], sources: [],
  status: 'supported', reference: { instrument_id: 'private-fund', notebook_version_id: 'original' }, ...overrides,
})

it('requires a stop reason and sends an explicit research publication instruction with the original question reference', () => {
  const ask = vi.fn()
  render(<ResearchQuestionTracking update={question()} onAskAssistant={ask} />)
  fireEvent.click(screen.getByText('调整跟踪'))
  fireEvent.change(screen.getByLabelText('跟踪安排'), { target: { value: 'paused' } })
  expect((screen.getByRole('button', { name: '交给助手保存' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('调整原因'), { target: { value: '等待管理人再次披露' } })
  fireEvent.click(screen.getByRole('button', { name: '交给助手保存' }))
  expect(ask).toHaveBeenCalledWith(expect.stringContaining('已暂停跟踪'), { ...question().reference, research_update_id: question().update_id })
  expect(ask.mock.calls[0][0]).toContain('保存并发布')
  expect(ask.mock.calls[0][0]).toContain('等待管理人再次披露')
  expect(ask.mock.calls[0][0]).toContain('保留证据判断')
})

it('resumes a closed current question explicitly while keeping historical revisions read only', () => {
  const ask = vi.fn()
  const { rerender } = render(<ResearchQuestionTracking update={question({ tracking_status: 'closed' })} onAskAssistant={ask} />)
  fireEvent.click(screen.getByText('调整跟踪'))
  fireEvent.change(screen.getByLabelText('跟踪安排'), { target: { value: 'active' } })
  fireEvent.click(screen.getByRole('button', { name: '交给助手保存' }))
  expect(ask.mock.calls[0][0]).toContain('持续跟踪')
  rerender(<ResearchQuestionTracking update={question({ superseded: true })} onAskAssistant={ask} />)
  expect(screen.queryByText('调整跟踪')).toBeNull()
})
