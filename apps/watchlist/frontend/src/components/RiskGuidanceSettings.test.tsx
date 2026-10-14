// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import RiskGuidanceSettings from '../../../../../packages/ui/src/RiskGuidanceSettings'
afterEach(cleanup)
const general = { text: '保留重要风险', revision: 2, updated_at: '2026-10-14', versions: [{ text: '旧指导', revision: 1, updated_at: '2026-10-13', author: { display_name: 'A', reason: '初版' } }] }
const response = { general, instrument: { ...general, text: '关注信用', revision: 3, instrument_id: 'a' }, constraints: [] }
it('loads only on demand and saves a restored version with the current concurrency revision and scope', async () => {
  window.history.replaceState({}, '', '/?lang=zh-Hans')
  const request = vi.fn().mockResolvedValue(response)
  render(<LanguageProvider><RiskGuidanceSettings request={request} instrumentId="a" canWrite /></LanguageProvider>)
  expect(request).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('风险提示设置 · Agent 指导'))
  await screen.findByDisplayValue('保留重要风险')
  fireEvent.click(screen.getAllByText('修改记录与恢复')[0])
  fireEvent.click(screen.getAllByRole('button', { name: '采用此版本内容' })[0])
  fireEvent.click(screen.getAllByRole('button', { name: '保存指导' })[0])
  await waitFor(() => expect(request).toHaveBeenCalledWith('/risk/guidance', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ scope: 'general', instrument_id: 'a', text: '旧指导', expected_revision: 2, reason: '恢复版本 1' }) })))
  expect(request.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
})
it('keeps reader guidance read only', async () => {
  const request = vi.fn().mockResolvedValue(response)
  render(<LanguageProvider><RiskGuidanceSettings request={request} instrumentId="a" canWrite={false} /></LanguageProvider>)
  fireEvent.click(screen.getByText('风险提示设置 · Agent 指导'))
  await screen.findByDisplayValue('保留重要风险')
  expect((screen.getByDisplayValue('保留重要风险') as HTMLTextAreaElement).readOnly).toBe(true)
  expect(screen.queryByRole('button', { name: '保存指导' })).toBeNull()
})
